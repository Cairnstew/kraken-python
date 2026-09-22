#!/usr/bin/env python3
"""Live sanity check of the Kraken wrapper against the real API.

Runs a battery of public (unauthenticated) checks — server time, ticker,
OHLC, order book, pair catalog resolution, and the WS v2 feed — and, when
KRAKEN_API_KEY/SECRET are present, a read-only authenticated check
(balance + WebSocket token).  Safe to re-run; nothing is traded.

Run from the dev shell:  python scripts/verify_live.py
"""

from __future__ import annotations

import os
import sys

from kraken_api import KrakenManager
from kraken_api.websocket import SpotWebSocket


def main() -> int:
    mgr = KrakenManager.from_env()
    failures = 0

    def check(name: str, fn) -> None:
        nonlocal failures
        try:
            fn()
            print(f"OK   {name}")
        except Exception as exc:  # noqa: BLE001 - report and continue
            failures += 1
            print(f"FAIL {name}: {exc}")

    def server_time() -> None:
        assert str(mgr.server_time())

    def ticker() -> None:
        ticker = mgr.ticker("BTC/USD")
        assert ticker.last_price > 0

    def catalog_pub() -> None:
        assert mgr.catalog.resolve("BTC/USD", "pub") == "XXBTZUSD"

    def catalog_alt() -> None:
        assert mgr.catalog.resolve("XXBTZUSD", "alt") == "XBTUSD"

    def ohlc() -> None:
        candles, _last = mgr.ohlc("BTC/USD", interval=60)
        assert len(candles) > 0

    def book() -> None:
        assert len(mgr.order_book("BTC/USD").bids) > 0

    def ws_stream() -> None:
        with SpotWebSocket.connect_public() as ws:
            ws.subscribe("ticker", ["BTC/USD"])
            ws.await_ack(1, timeout=5)
            msg = ws.next_message(timeout=10)
            if not msg or msg.get("channel") != "ticker":
                raise AssertionError(f"expected a ticker message, got {msg}")

    def authenticated() -> None:
        balances = mgr.balances()
        token = mgr.ws_token()
        assert token, "ws token empty"
        print(f"     authenticated: {len(balances)} balance row(s), ws token {token[:12]}...")

    check("server time", server_time)
    check("ticker BTC/USD", ticker)
    check("catalog: BTC/USD -> XXBTZUSD", catalog_pub)
    check("catalog: XXBTZUSD -> XBTUSD", catalog_alt)
    check("ohlc BTC/USD 60", ohlc)
    check("order book BTC/USD", book)
    check("websocket ticker stream", ws_stream)

    if "KRAKEN_API_KEY" in os.environ:
        check("authenticated balance + ws token", authenticated)
    else:
        print("(no KRAKEN_API_KEY set — skipping authenticated checks)")

    if failures:
        print(f"\n{failures} check(s) FAILED")
        return 1
    print("\nall live checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())