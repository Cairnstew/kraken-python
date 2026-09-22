"""Unit tests for the low-level transport — no network required."""

from __future__ import annotations

import json

import pytest

from kraken_api.errors import APIError, AuthenticationError, RateLimitError
from kraken_api.transport import KrakenTransport

DOC_SECRET = "kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg=="


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.url = "https://api.kraken.com/0/private/Balance"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            from requests import HTTPError

            raise HTTPError(f"{self.status_code} error", response=self)

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.sent: list[tuple[str, object]] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.sent.append(("GET", (url, params, headers)))
        return self.response

    def post(self, url, data=None, params=None, headers=None, timeout=None):
        self.sent.append(("POST", (url, data, params, headers)))
        return self.response


def _transport(session: FakeSession) -> KrakenTransport:
    return KrakenTransport(api_key="KEY", api_secret=DOC_SECRET, session=session)


class TestEnvelope:
    def test_success_returns_result(self) -> None:
        session = FakeSession(FakeResponse({"error": [], "result": {"ZUSD": "100"}}))
        transport = _transport(session)
        assert transport.private("Balance") == {"ZUSD": "100"}

    def test_error_raises_api_error(self) -> None:
        session = FakeSession(FakeResponse({"error": ["EGeneral:Invalid arguments"], "result": {}}))
        transport = _transport(session)
        with pytest.raises(APIError) as exc:
            transport.private("Balance")
        assert "EGeneral:Invalid arguments" in str(exc.value)
        assert exc.value.endpoint == "/0/private/Balance"

    def test_rate_limit_raises_rate_limit_error(self) -> None:
        session = FakeSession(FakeResponse({"error": ["EAPI:Rate limit exceeded"], "result": {}}))
        transport = _transport(session)
        with pytest.raises(RateLimitError):
            transport.private("Balance")

    def test_http_error_raises(self) -> None:
        session = FakeSession(FakeResponse({"detail": "gateway"}, status=502))
        transport = _transport(session)
        with pytest.raises(Exception):
            transport.public("Time")


class TestSigningDrivesHeaders:
    def test_private_signs_with_nonce_and_path(self) -> None:
        session = FakeSession(FakeResponse({"error": [], "result": {}}))
        transport = _transport(session)
        transport.private("Balance", {"asset": "ZUSD"})

        _method, (url, body, _params, headers) = session.sent[0]
        assert url == "https://api.kraken.com/0/private/Balance"
        assert "nonce=" in body
        assert "asset=ZUSD" in body
        assert headers["API-Key"] == "KEY"
        assert headers["API-Sign"]
        assert "application/x-www-form-urlencoded" in headers["Content-Type"]
        assert "kraken-python" in headers["User-Agent"]

    def test_private_without_credentials_raises(self) -> None:
        transport = KrakenTransport(session=FakeSession(FakeResponse({"error": [], "result": {}})))
        with pytest.raises(AuthenticationError):
            transport.private("Balance")

    def test_public_sends_query_params(self) -> None:
        session = FakeSession(FakeResponse({"error": [], "result": {"unixtime": 1}}))
        transport = _transport(session)
        transport.public("Time", params={"foo": "bar"})
        _method, (url, params, _headers) = session.sent[0]
        assert params == {"foo": "bar"}


def test_min_interval_throttles(monkeypatch) -> None:
    import time as _time

    session = FakeSession(FakeResponse({"error": [], "result": {"unixtime": 1}}))
    transport = _transport(session)
    transport.min_interval = 0.05

    sleeps: list[float] = []
    monkeypatch.setattr(_time, "sleep", lambda s: sleeps.append(s))

    transport.public("Time")
    transport.public("Time")
    assert len(sleeps) >= 1
    assert all(s >= 0.0 for s in sleeps)