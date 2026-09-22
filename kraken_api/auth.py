"""Authentication: turn API credentials into a Kraken client.

Kraken authentication is different from Spotify's OAuth dance — there is no
browser flow.  You generate an **API key-pair** in your Kraken account
(Pro web app -> Settings -> API), where the public key names the key and the
private secret signs every request.  This package then needs two environment
variables:

    export KRAKEN_API_KEY=...
    export KRAKEN_API_SECRET=...

Market data (public endpoints) needs no credentials at all — the client is
simply used without them and any private call raises a clear error.

Fluent entry points:

    from kraken_api import KrakenClient
    client = KrakenClient.from_env()                    # KRAKEN_* env vars

    from kraken_api import KrakenManager
    mgr = KrakenManager.from_env()                      # friendly facade
"""

from __future__ import annotations

import logging
import os
from typing import Any

from dotenv import load_dotenv

from .client import KrakenClient
from .errors import ConfigurationError
from .logging_config import log_event
from .transport import DEFAULT_REST_URL, KrakenTransport

_AUTH_LOG = logging.getLogger("kraken_api.auth")

ENV_API_KEY = "KRAKEN_API_KEY"
ENV_API_SECRET = "KRAKEN_API_SECRET"
ENV_REST_URL = "KRAKEN_REST_URL"
ENV_MIN_INTERVAL = "KRAKEN_MIN_INTERVAL"

load_dotenv()


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def credentials_from_env() -> tuple[str | None, str | None]:
    """Return ``(api_key, api_secret)`` from the environment (either may be None)."""
    key = _env(ENV_API_KEY) or None
    secret = _env(ENV_API_SECRET) or None
    return key, secret


def client_from_env(**kwargs: Any) -> KrakenClient:
    """Build a :class:`~kraken_api.client.KrakenClient` from the environment.

    Credentials are optional (public market data still works).  ``KRAKEN_*``
    variables and a ``.env`` file are both consulted (``python-dotenv``).
    """
    key, secret = credentials_from_env()
    if kwargs.get("require_credentials") and (not key or not secret):
        raise ConfigurationError(
            f"Missing {ENV_API_KEY} and/or {ENV_API_SECRET}. "
            "Copy .env.example, fill them in, or pass api_key/api_secret."
        )

    base_url = _env(ENV_REST_URL) or DEFAULT_REST_URL
    try:
        min_interval = float(_env(ENV_MIN_INTERVAL) or 0.0)
    except ValueError:
        min_interval = 0.0

    log_event(
        _AUTH_LOG,
        "auth.init",
        authenticated=bool(key and secret),
        base_url=base_url,
    )
    return KrakenClient(
        KrakenTransport(
            api_key=key,
            api_secret=secret,
            base_url=base_url,
            min_interval=min_interval,
        )
    )


def client_from_credentials(api_key: str, api_secret: str, **kwargs: Any) -> KrakenClient:
    """Build a client from explicit credential values (embedding in apps).

    Same behavior as :func:`client_from_env` but driven by parameters.  An
    empty ``api_secret`` marks the client as public-only.
    """
    return KrakenClient(
        KrakenTransport(
            api_key=api_key or None,
            api_secret=api_secret or None,
            base_url=kwargs.pop("base_url", DEFAULT_REST_URL),
            min_interval=kwargs.pop("min_interval", 0.0),
        )
    )