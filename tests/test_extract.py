"""Unit tests for the extraction infra (registry, envelope, JSONL) — no network."""

from __future__ import annotations

import json

import pytest

from kraken_api.client import KrakenClient
from kraken_api.export import (
    EXTRACTORS,
    PUBLIC_RESOURCES,
    dumps,
    extract,
    extract_many,
    extract_snapshot,
    iter_ws_jsonl,
    write_json,
    write_jsonl,
)
from kraken_api.manager import KrakenManager
from kraken_api.models import OrderBook, SpreadPoint, TradeBalance

SPREAD_ROWS = [[1700000000, "27000.0", "27001.0"], [1700000001, "27000.1", "27001.1"]]
TRADE_ROWS = [["27000.0", "0.5", 1700000000, "buy", "limit"]]


class FakeTransport:
    """Servers every resource the extractors touch."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _mark(self, path: str) -> None:
        self.calls.append(path)

    def public(self, path: str, params: dict | None = None):
        self._mark(path)
        if path == "Time":
            return {"unixtime": 1700000000}
        if path == "Ticker":
            return {
                "XXBTZUSD": {"a": ["27000.0"], "b": ["26999.0"], "c": ["27000.1"], "v": ["1", "2"],
                             "p": ["1", "2"], "t": [1, 2], "l": ["1", "2"], "h": ["2", "3"], "o": ["1", "1"]},
                "XETHZUSD": {"a": ["1800.0"], "b": [], "c": [], "v": [], "p": [], "t": [], "l": [], "h": [], "o": []},
            }
        if path == "OHLC":
            return {"last": 99, "XXBTZUSD": [[1700000000, "1", "2", "3", "4", "5", "6", 7]]}
        if path == "Depth":
            return {
                "XXBTZUSD": {
                    "asks": [["27001.0", "1.0", 1700000000]],
                    "bids": [["26999.0", "2.0", 1700000000]],
                }
            }
        if path == "Trades":
            return {"last": "1700000001", "XXBTZUSD": TRADE_ROWS}
        if path == "Spread":
            return {"last": "1700000002", "XXBTZUSD": SPREAD_ROWS}
        if path == "Assets":
            return {"XXBT": {"altname": "XBT", "aclass": "currency", "decimals": 8, "display_decimals": 5}}
        if path == "AssetPairs":
            return {
                "XXBTZUSD": {"altname": "XBTUSD", "wsname": "BTC/USD", "base": "XXBT", "quote": "ZUSD",
                             "pair_decimals": 5, "lot_decimals": 8},
            }
        raise AssertionError(f"unexpected public call {path}")

    def private(self, path: str, data: dict | None = None):
        self._mark(path)
        if path == "Balance":
            return {"ZUSD": "100.00"}
        if path == "TradeBalance":
            return {"eb": "1000.0", "tb": "999.0", "e": "1001.0", "mf": "1000.0"}
        if path == "OpenOrders":
            return {"open": {}}
        if path == "ClosedOrders":
            return {"closed": {}, "count": 0}
        raise AssertionError(f"unexpected private call {path}")


def _manager() -> KrakenManager:
    return KrakenManager(KrakenClient(FakeTransport()))


def test_registry_has_every_public_resource() -> None:
    for name in PUBLIC_RESOURCES:
        assert name in EXTRACTORS


def test_extract_server_time() -> None:
    doc = extract(_manager(), "server-time")
    assert doc["unixtime"] == 1700000000
    assert json.dumps(doc)


def test_extract_tickers_with_pairs() -> None:
    doc = extract(_manager(), "tickers", pairs=["BTC/USD", "ETH/USD"])
    assert doc["pairs"] == ["BTC/USD", "ETH/USD"]
    assert len(doc["tickers"]) == 2
    assert {t["pair"] for t in doc["tickers"]} == {"BTC/USD", "ETH/USD"}


def test_extract_book_computes_spread() -> None:
    doc = extract(_manager(), "book", pair="BTC/USD")
    assert doc["best_bid"] == "26999.0"
    assert doc["best_ask"] == "27001.0"
    assert doc["spread"] == "2.0"
    assert len(doc["bids"]) == 1


def test_extract_trades_and_spread() -> None:
    trades = extract(_manager(), "trades", pair="BTC/USD", limit=5)
    assert trades["trades"][0]["side"] == "buy"
    assert trades["last"] == "1700000001"

    spread = extract(_manager(), "spread", pair="BTC/USD", limit=1)
    assert len(spread["spread"]) == 1
    assert spread["spread"][0]["bid"] == "27000.1"  # limit keeps the most recent rows


def test_extract_trade_balance_typed() -> None:
    doc = extract(_manager(), "trade-balance")
    assert doc["trade_balance"]["equity"] == "1001.0"
    assert json.dumps(doc)


def test_extract_assets_and_pairs() -> None:
    doc = extract(_manager(), "assets")
    assert doc["assets"]["XXBT"]["altname"] == "XBT"
    pairs = extract(_manager(), "pairs")
    assert any(p["ws"] == "BTC/USD" for p in pairs["pairs"])  # sorted alphabetically


def test_extract_unknown_resource_raises() -> None:
    with pytest.raises(KeyError):
        extract(_manager(), "nope")


def test_extract_many_envelope() -> None:
    doc = extract_many(_manager(), ["server-time", "book"], pairs=["BTC/USD"])
    assert doc["schema"] == "kraken-extract/1"
    assert "exported_at" in doc
    assert "server-time" in doc["resources"]
    assert "book" in doc["resources"]
    assert json.dumps(doc)  # fully JSON-serialisable


def test_extract_many_requires_resources() -> None:
    with pytest.raises(ValueError):
        extract_many(_manager(), [])


def test_extract_snapshot_includes_account() -> None:
    doc = extract_snapshot(_manager(), pairs=["BTC/USD"], include_account=True)
    names = set(doc["resources"])
    assert "book" in names and "balance" in names and "orders" in names
    assert json.dumps(doc)


def test_dumps_and_write_json(tmp_path) -> None:
    doc = extract(_manager(), "server-time")
    blob = dumps(doc)
    assert json.loads(blob)["unixtime"] == 1700000000
    path = write_json(doc, tmp_path / "doc.json")
    assert json.loads(path.read_text())["unixtime"] == 1700000000


def test_write_jsonl(tmp_path) -> None:
    docs = [extract(_manager(), "server-time"), extract(_manager(), "server-time")]
    path = write_jsonl(tmp_path / "feed.jsonl", docs)
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    for line in lines:
        assert json.loads(line)["unixtime"] == 1700000000  # compact NDJSON parses


def test_iter_ws_jsonl_yields_decoded_messages() -> None:
    class FakeWs:
        def iter_messages(self, timeout=None, filter_channel=None):
            messages = [
                {"channel": "ticker", "type": "update",
                 "data": [{"symbol": "BTC/USD", "bid": "1", "ask": "2", "last": "1.5"}]},
                {"channel": "ticker", "type": "update",
                 "data": [{"symbol": "ETH/USD", "bid": "3", "ask": "4", "last": "3.5"}]},
            ]
            for message in messages:
                if filter_channel and message.get("channel") != filter_channel:
                    continue
                yield message

    rows = list(iter_ws_jsonl(FakeWs(), channel="ticker"))
    assert len(rows) == 2
    assert rows[0]["data"][0]["symbol"] == "BTC/USD"
    assert json.dumps(rows[0])


def test_models_round_trip_through_json() -> None:
    # Every model the fake transports produce must be json.dumps-able.
    mgr = _manager()
    for doc in (
        extract(mgr, "book", pair="BTC/USD"),
        extract(mgr, "trade-balance"),
        extract(mgr, "balance"),
        extract(mgr, "orders"),
        extract(mgr, "assets"),
        extract(mgr, "pairs"),
    ):
        assert json.dumps(doc)