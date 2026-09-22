"""Unit tests for the watch helpers — no network required."""

from __future__ import annotations

import json

from kraken_api.watch import blocks_until_tick, watch_ticker
from kraken_api.websocket import SpotWebSocket


class FakeSocket:
    def __init__(self, messages: list[dict]) -> None:
        self.messages = [json.dumps(m) for m in messages]
        self.sent: list[str] = []

    def send(self, raw: str) -> None:
        self.sent.append(raw)

    def recv(self):
        if not self.messages:
            raise ConnectionError("closed")
        return self.messages.pop(0)

    def close(self) -> None:
        pass


def _ws(messages: list[dict]) -> SpotWebSocket:
    ws = SpotWebSocket()
    ws._ws = FakeSocket(messages)
    return ws


def _ticker_update(symbol: str, last: str) -> dict:
    return {"channel": "ticker", "type": "update", "data": [
        {"symbol": symbol, "bid": "26999.0", "bid_qty": "1", "ask": "27000.0",
         "ask_qty": "2", "last": last, "volume": "123.0"}
    ]}


def test_watch_ticker_yields_only_matching_symbol() -> None:
    ws = _ws([
        _ticker_update("ETH/USD", "1800.0"),
        _ticker_update("BTC/USD", "27000.1"),
        _ticker_update("BTC/USD", "27000.2"),
    ])
    got = list(watch_ticker(ws, "BTC/USD"))
    assert [t["last"] for t in got] == ["27000.1", "27000.2"]
    assert got[0]["_changes"]  # snapshot: everything changed


def test_watch_ticker_change_detection() -> None:
    # Two updates for the same symbol where the top-of-book did not move.
    ws = _ws([
        _ticker_update("BTC/USD", "27000.1"),
        _ticker_update("BTC/USD", "27000.1"),  # same last; no change
        _ticker_update("BTC/USD", "27000.5"),
    ])
    got = list(watch_ticker(ws, "BTC/USD"))
    assert len(got) == 2  # unchanged update suppressed


def test_watch_ticker_emit_unchanged() -> None:
    ws = _ws([
        _ticker_update("BTC/USD", "27000.1"),
        _ticker_update("BTC/USD", "27000.1"),
    ])
    got = list(watch_ticker(ws, "BTC/USD", emit_unchanged=True))
    assert len(got) == 2


def test_blocks_until_tick_returns_first_tick() -> None:
    ws = _ws([_ticker_update("BTC/USD", "27100.0")])
    tick = blocks_until_tick(ws, "BTC/USD", timeout=1.0)
    assert tick is not None
    assert tick["last"] == "27100.0"


def test_blocks_until_tick_times_out() -> None:
    ws = _ws([_ticker_update("ETH/USD", "1800.0")])  # never our symbol
    tick = blocks_until_tick(ws, "BTC/USD", timeout=0.2)
    assert tick is None