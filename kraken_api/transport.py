"""Low-level Kraken REST transport: signing, nonces, and request lifecycle.

This is the part of the stack that knows *how* to talk to Kraken — the
HMAC-SHA512 API-Sign header, form-encoded POST bodies, the ``{error, result}``
envelope, strictly-increasing nonces, and a configurable inter-call throttle.
Higher layers (:mod:`kraken_api.client`, :mod:`kraken_api.manager`) never see
any of this.

Official reference for the signature scheme:
https://docs.kraken.com/exchange/guides/rest/authentication

    API-Sign = base64(
        HMAC-SHA512(
            base64decode(api_secret),
            urlpath + SHA256(str(nonce) + urlencode(payload)),
        )
    )
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import threading
import time
import urllib.parse
from typing import Any

import requests

from .errors import APIError, AuthenticationError, ConfigurationError, RateLimitError
from .logging_config import log_event
from .utils import now_ms

_HTTP_LOG = logging.getLogger("kraken_api.api")

DEFAULT_REST_URL = "https://api.kraken.com"

# Kraken blocks the default python-requests User-Agent.  Identify ourselves.
DEFAULT_USER_AGENT = "kraken-python/0.1.0 (+https://github.com/Cairnstew/kraken-python)"

# Error strings Kraken answers with when we are calling too fast.
_RATE_LIMIT_MARKERS = (
    "EAPI:Rate limit exceeded",
    "EGeneral:Too many requests",
)


def get_kraken_signature(urlpath: str, data: dict[str, object], secret: str) -> str:
    """Compute the ``API-Sign`` header for a private REST call.

    Parameters
    ----------
    urlpath:
        The endpoint path *including* ``/0/private``, e.g. ``/0/private/Balance``.
    data:
        The form payload.  ``data["nonce"]`` must already be set; it is the
        first field and participates in the signature.
    secret:
        The base64-encoded private API secret.

    Verified against the official worked example in the Kraken docs:
    nonce 1616492376594, payload ``nonce=...&ordertype=limit&pair=XBTUSD&
    price=37500&type=buy&volume=1.25``, path ``/0/private/AddOrder``,
    secret ``kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg==``
    must produce ``4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ==``
    (see ``tests/test_signing.py``).
    """
    encoded = (str(data["nonce"]) + urllib.parse.urlencode(data)).encode()
    message = urlpath.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


class KrakenTransport:
    """Owns the HTTP session, nonce counter, and rate throttle for one client.

    Parameters
    ----------
    api_key:
        Public API key.  Leave ``None`` for public-market-data-only use; a
        private call without it raises :class:`AuthenticationError`.
    api_secret:
        Base64-encoded private API secret (the long random string, not the
        key name).
    base_url:
        REST base URL (defaults to the public Kraken API).
    session:
        A ``requests.Session`` (or a stand-in with ``get``/``post``) for
        dependency injection in tests.
    min_interval:
        Minimum seconds between successive requests.  Kraken's spot REST
        limit is ~15-20 calls/s depending on tier; set e.g. ``0.08`` to stay
        safely under it.
    user_agent:
        Value for the ``User-Agent`` header.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str = DEFAULT_REST_URL,
        session: Any | None = None,
        min_interval: float = 0.0,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.session = session if session is not None else requests.Session()
        self.min_interval = min_interval
        self.user_agent = user_agent

        self._last_call = 0.0
        self._last_nonce = 0
        self._nonce_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Public (unauthenticated) requests
    # ------------------------------------------------------------------ #

    def public(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET a public endpoint (``/0/public/<path>``) and return ``result``."""
        return self._request("GET", f"/0/public/{path}", params=params)

    # ------------------------------------------------------------------ #
    # Private (authenticated) requests
    # ------------------------------------------------------------------ #

    def private(
        self,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> Any:
        """POST a signed private endpoint (``/0/private/<path>``).

        ``data`` is form-encoded into the body; ``nonce`` is injected
        automatically (first field, so it signs cleanly).
        """
        if not self.api_key:
            raise AuthenticationError(
                "A private call needs credentials. Provide KRAKEN_API_KEY / "
                "KRAKEN_API_SECRET (or pass api_key/api_secret to the client)."
            )
        if not self.api_secret:
            raise AuthenticationError(
                "A private call needs credentials. Provide KRAKEN_API_KEY / "
                "KRAKEN_API_SECRET (or pass api_key/api_secret to the client)."
            )
        payload = {"nonce": self._next_nonce(), **(data or {})}
        urlpath = f"/0/private/{path}"
        body = urllib.parse.urlencode(payload)

        headers = {
            "API-Key": self.api_key,
            "API-Sign": get_kraken_signature(urlpath, payload, self.api_secret),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        return self._request("POST", urlpath, data=body, headers=headers)

    # ------------------------------------------------------------------ #
    # Internal request plumbing
    # ------------------------------------------------------------------ #

    def _next_nonce(self) -> str:
        """Return a strictly-increasing millisecond nonce (per API key).

        Two clients sharing one key must coordinate externally; the nonce is
        required to always be larger than every previous one for the key.
        """
        with self._nonce_lock:
            candidate = max(now_ms(), self._last_nonce + 1)
            self._last_nonce = candidate
            return str(candidate)

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_call
        wait = self.min_interval - elapsed
        if wait > 0:
            time.sleep(wait)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        self._throttle()
        url = f"{self.base_url}{path}"
        request_headers = {"User-Agent": self.user_agent, **(headers or {})}
        start = time.monotonic()

        try:
            if method == "GET":
                response = self.session.get(url, params=params, headers=request_headers, timeout=30)
            else:
                response = self.session.post(url, data=data, params=params, headers=request_headers, timeout=30)

            response.raise_for_status()
            document = response.json()
        except requests.RequestException as exc:
            log_event(
                _HTTP_LOG,
                f"{method} {path}",
                http_method=method,
                endpoint=path,
                status="error",
                reason=exc.__class__.__name__,
            )
            raise
        finally:
            self._last_call = time.monotonic()

        elapsed_ms = round((time.monotonic() - start) * 1000)

        if not isinstance(document, dict) or "error" not in document:
            log_event(
                _HTTP_LOG,
                f"{method} {path}",
                http_method=method,
                endpoint=path,
                status="error",
                reason="malformed_envelope",
                latency_ms=elapsed_ms,
            )
            raise APIError(["Malformed response envelope"], endpoint=path)

        errors = document.get("error") or []
        if errors:
            reason = "; ".join(errors)
            log_event(
                _HTTP_LOG,
                f"{method} {path}",
                http_method=method,
                endpoint=path,
                status="error",
                errors=errors,
                latency_ms=elapsed_ms,
            )
            if any(marker in reason for marker in _RATE_LIMIT_MARKERS):
                raise RateLimitError(errors, endpoint=path)
            raise APIError(errors, endpoint=path)

        log_event(
            _HTTP_LOG,
            f"{method} {path}",
            http_method=method,
            endpoint=path,
            status="ok",
            latency_ms=elapsed_ms,
        )
        return document.get("result")