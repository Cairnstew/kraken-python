"""Unit tests for the export helpers — no network required."""

from __future__ import annotations

import json

from kraken_api.client import KrakenClient
from kraken_api.export import export_account, export_ohlc, export_snapshot, export_tickers
from kraken_api.manager import KrakenManager


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []

    def public(self, path: str, params: dict | None = None):
        self.calls.append(("public", path, params))
        if path == "Ticker":
            return {
                "XXBTZUSD": {"a": ["27000.0"], "b": ["26999.0"], "c": ["27000.1"], "v": ["1", "2"], "p": ["1", "2"],
                             "t": [1, 2], "l": ["1", "2"], "h": ["2", "3"], "o": ["1", "1"]},
                "XETHZUSD": {"a": ["1800.0"], "b": [], "c": [], "v": [], "p": [], "t": [], "l": [], "h": [], "o": []},
            }
        if path == "OHLC":
            return {"last": 99, "XXBTZUSD": [[1700000000, "1", "2", "3", "4", "5", "6", 7]]}
        raise AssertionError(f"unexpected public call {path}")

    def private(self, path: str, data: dict | None = None):
        self.calls.append(("private", path, data))
        if path == "Balance":
            return {"ZUSD": "100.00"}
        if path == "OpenOrders":
            return {"open": {}}
        raise AssertionError(f"unexpected private call {path}")


def _manager() -> KrakenManager:
    client = KrakenClient(FakeTransport())
    return KrakenManager(client)


def test_export_tickers_json_ready(tmp_path) -> None:
    mgr = _manager()
    rows = export_tickers(mgr, ["BTC/USD", "ETH/USD"])
    assert len(rows) == 2
    by_symbol = {row["pair"]: row for row in rows}
    assert by_symbol["BTC/USD"]["last"] == ["27000.1"]

    # Must round-trip through json.dumps.
    blob = json.dumps(rows)
    assert '"pair": "BTC/USD"' in blob


def test_export_ohlc(tmp_path) -> None:
    mgr = _manager()
    doc = export_ohlc(mgr, "BTC/USD", interval=60)
    assert doc["pair"] == "BTC/USD"
    assert doc["last"] == 99
    assert doc["candles"][0]["close"] == "4"

    limited = export_ohlc(mgr, "BTC/USD", interval=60, limit=1)
    assert len(limited["candles"]) == 1


def test_export_account() -> None:
    mgr = _manager()
    doc = export_account(mgr)
    assert doc["balances"] == [{"asset": "ZUSD", "amount": "100.00"}]
    assert doc["open_orders"] == []


def test_export_snapshot_writes_document(tmp_path) -> None:
    mgr = _manager()
    out = tmp_path / "snap.json"
    path = export_snapshot(mgr, out, pairs=["BTC/USD"], include_account=True)
    assert path.exists()
    doc = json.loads(path.read_text())
    assert "exported_at" in doc
    assert doc["tickers"][0]["pair"] == "BTC/USD"
    assert doc["account"]["balances"] == [{"asset": "ZUSD", "amount": "100.00"}]


def test_export_default_pairs(tmp_path) -> None:
    mgr = _manager()
    out = tmp_path / "snap.json"
    export_snapshot(mgr, out)
    doc = json.loads(out.read_text())
    assert len(doc["tickers"]) == 2  # BTC/USD + ETH/USD served by the fake