"""Unit tests for the raw KrakenClient — no network required.

The transport is faked; the pair catalog uses its offline fallback table so
resolution works without touching the wire.
"""

from __future__ import annotations

import pytest

from kraken_api.client import KrakenClient
from kraken_api.errors import APIError


class FakeTransport:
    """Records calls and returns canned envelopes."""

    def __init__(self) -> None:
        self.public_calls: list[tuple[str, dict | None]] = []
        self.private_calls: list[tuple[str, dict]] = []
        self._nonce = 0

    def public(self, path: str, params: dict | None = None):
        self.public_calls.append((path, params))
        if path == "AssetPairs":
            return {
                "XXBTZUSD": {"altname": "XBTUSD", "wsname": "BTC/USD", "base": "XXBT", "quote": "ZUSD",
                             "pair_decimals": 5, "lot_decimals": 8},
                "XETHZUSD": {"altname": "ETHUSD", "wsname": "ETH/USD", "base": "XETH", "quote": "ZUSD",
                             "pair_decimals": 5, "lot_decimals": 8},
            }
        if path == "Ticker":
            return {"XXBTZUSD": {"a": ["27000.0"], "b": [], "c": [], "v": [], "p": [], "t": [], "l": [], "h": [], "o": []}}
        if path == "OHLC":
            return {"last": 123, "XXBTZUSD": [[1700000000, "1", "2", "3", "4", "5", "6", 7]]}
        raise AssertionError(f"unexpected public call {path}")

    def private(self, path: str, data: dict | None = None):
        # Mirror the real transport: a nonce is always injected first.
        self._nonce += 1
        recorded = {"nonce": str(self._nonce), **(data or {})}
        self.private_calls.append((path, recorded))
        if path == "AddOrder":
            return {"txid": ["TXNEW1"], "descr": {"order": "buy 0.001 XBTUSD @ limit 27000.0"}}
        if path == "Balance":
            return {"ZUSD": "100.00", "XXBT": "0.50000000"}
        if path == "OpenOrders":
            return {"open": {}}
        if path == "CancelOrder":
            return {"count": 1}
        if path == "GetWebSocketsToken":
            return {"token": "TOK123", "expires": 900}
        raise AssertionError(f"unexpected private call {path}")


def _find_public(client: KrakenClient, path: str) -> dict | None:
    """Return the params of the first recorded public call for ``path``."""
    for p, params in client.transport.public_calls:
        if p == path:
            return params
    raise AssertionError(f"no public call for {path}")


@pytest.fixture
def client() -> KrakenClient:
    return KrakenClient(FakeTransport())


def test_resolve_pair_uses_offline_catalog(client: KrakenClient) -> None:
    assert client.resolve_pair("BTC/USD", style="pub") == "XXBTZUSD"
    assert client.resolve_pair("XXBTZUSD", style="alt") == "XBTUSD"


def test_ticker_resolves_pair_and_requests_pub(client: KrakenClient) -> None:
    result = client.ticker("BTC/USD")
    assert "XXBTZUSD" in result
    # Pair resolution probes the catalog first (AssetPairs), then Ticker.
    params = _find_public(client, "Ticker")
    assert params == {"pair": "XXBTZUSD"}


def test_ticker_accepts_list(client: KrakenClient) -> None:
    client.ticker(["BTC/USD", "ETH/USD"])
    params = _find_public(client, "Ticker")
    assert params == {"pair": "XXBTZUSD,XETHZUSD"}


def test_ohlc_passes_interval_and_since(client: KrakenClient) -> None:
    client.ohlc("BTC/USD", interval=60, since=1700000000)
    params = _find_public(client, "OHLC")
    assert params["interval"] == 60
    assert params["since"] == 1700000000


def test_add_order_builds_form_payload(client: KrakenClient) -> None:
    result = client.add_order(
        "BTC/USD",
        side="buy",
        ordertype="limit",
        volume=0.001,
        price="27000.00",
        oflags=["post", "fciq"],
        validate=True,
        userref=123,
    )
    assert result["txid"] == ["TXNEW1"]
    path, data = client.transport.private_calls[0]
    assert path == "AddOrder"
    assert data["pair"] == "XBTUSD"            # private altname
    assert data["type"] == "buy"
    assert data["ordertype"] == "limit"
    assert data["volume"] == "0.001"
    assert data["price"] == "27000.00"
    assert data["oflags"] == "post,fciq"
    assert data["validate"] == "true"
    assert data["userref"] == 123
    assert "nonce" in data                      # injected, signed first


def test_cancel_order_returns_count(client: KrakenClient) -> None:
    result = client.cancel_order("TX1")
    path, data = client.transport.private_calls[0]
    assert path == "CancelOrder"
    assert data["txid"] == "TX1"
    assert result == {"count": 1}  # FakeTransport raises otherwise


def test_ws_token(client: KrakenClient) -> None:
    assert client.get_websockets_token() == "TOK123"


def test_unknown_endpoint_error_propagates(client: KrakenClient) -> None:
    with pytest.raises(AssertionError):
        client.ledgers()  # FakeTransport doesn't serve this endpoint


def test_client_rejects_bad_side(client: KrakenClient) -> None:
    import pytest as _pytest

    from kraken_api.manager import KrakenManager

    mgr = KrakenManager(client)
    with _pytest.raises(ValueError):
        mgr.place_order("hold", "BTC/USD", "limit", volume="1")