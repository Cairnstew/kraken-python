"""CLI entry point for kraken-python.

This module lives inside the package so setuptools can register it as a
console_scripts entry point (see pyproject.toml).  The repo-root cli.py
just delegates here, so `python cli.py ...` and `kraken-python ...` behave
identically.

Usage (from the dev shell):

    python cli.py ticker BTC/USD
    python cli.py ohlc BTC/USD --interval 60
    python cli.py book BTC/USD
    python cli.py balance
    python cli.py orders
    python cli.py buy BTC/USD 0.001 27000        # limit buy
    python cli.py sell BTC/USD 0.001             # market sell
    python cli.py ws ticker BTC/USD              # stream live ticks

Authenticates against KRAKEN_API_KEY / KRAKEN_API_SECRET (see .env.example).
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from kraken_api import KrakenManager
from kraken_api.logging_config import setup_logging, _xdg_state_dir


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kraken-python",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="enable debug logging (both console and file)",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="path to the JSON log file (default: $XDG_STATE_HOME/kraken-python/logs/kraken.log)",
    )
    parser.add_argument(
        "--no-log-file",
        action="store_true",
        help="disable file logging entirely",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("server-time", help="print the exchange's UTC time")
    p.set_defaults(func=cmd_server_time)

    p = sub.add_parser("ticker", help="show tickers for one or more markets")
    p.add_argument("pair", nargs="+", help="market(s): ws, altname, or pub spelling")
    p.add_argument("--json", action="store_true", help="print raw JSON")
    p.set_defaults(func=cmd_ticker)

    p = sub.add_parser("ohlc", help="show OHLC candles for a market")
    p.add_argument("pair")
    p.add_argument("interval", type=int, nargs="?", default=5, choices=[1, 5, 15, 30, 60, 240, 1440, 10080, 21600],
                   help="candle interval in minutes (default: 5)")
    p.add_argument("--limit", type=int, default=24, help="max candles to print (default: 24)")
    p.add_argument("--json", action="store_true", help="print all candles as JSON")
    p.set_defaults(func=cmd_ohlc)

    p = sub.add_parser("book", help="show the order book for a market")
    p.add_argument("pair")
    p.add_argument("--count", type=int, default=None, help="depth levels per side")
    p.set_defaults(func=cmd_book)

    p = sub.add_parser("pairs", help="list known markets from the pair catalog")
    p.set_defaults(func=cmd_pairs)

    p = sub.add_parser("snapshot", help="export tickers (and account) to a JSON file")
    p.add_argument("output", nargs="?", default="kraken-snapshot.json")
    p.add_argument("--pair", action="append", default=[], help="repeatable; defaults to BTC/ETH/SOL vs USD")
    p.add_argument("--include-account", action="store_true", help="also include balances + open orders")
    p.set_defaults(func=cmd_snapshot)

    # -- authenticated -------------------------------------------------
    p = sub.add_parser("balance", help="show account balances")
    p.set_defaults(func=cmd_balance)

    p = sub.add_parser("orders", help="show open orders")
    p.set_defaults(func=cmd_orders)

    p = sub.add_parser("closed", help="show recent closed orders")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_closed)

    p = sub.add_parser("order", help="show one order by txid")
    p.add_argument("txid")
    p.set_defaults(func=cmd_order)

    p = sub.add_parser("history", help="show recent trades")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("buy", help="place a buy (market without PRICE, limit with)")
    p.add_argument("pair")
    p.add_argument("volume")
    p.add_argument("price", nargs="?", default=None)
    p.set_defaults(func=cmd_buy)

    p = sub.add_parser("sell", help="place a sell (market without PRICE, limit with)")
    p.add_argument("pair")
    p.add_argument("volume")
    p.add_argument("price", nargs="?", default=None)
    p.set_defaults(func=cmd_sell)

    p = sub.add_parser("cancel", help="cancel one order by txid")
    p.add_argument("txid")
    p.set_defaults(func=cmd_cancel)

    p = sub.add_parser("cancel-all", help="cancel every open order")
    p.set_defaults(func=cmd_cancel_all)

    p = sub.add_parser("ws", help="stream live updates from the WebSocket v2 feed")
    p.add_argument("channel", choices=("ticker", "book", "trade"), default="ticker")
    p.add_argument("pair", nargs="+", help="market(s) to subscribe to")
    p.add_argument("--timeout", type=float, default=None, help="seconds to stream before stopping")
    p.set_defaults(func=cmd_ws)

    return parser


def _mgr(args: argparse.Namespace) -> KrakenManager:
    return KrakenManager.from_env()


def cmd_server_time(mgr: KrakenManager, args: argparse.Namespace) -> None:
    print(mgr.server_time())


def cmd_ticker(mgr: KrakenManager, args: argparse.Namespace) -> None:
    import json as _json

    tickers = mgr.tickers(args.pair)
    if args.json:
        print(_json.dumps({k: t.to_dict() for k, t in tickers.items()}, indent=2))
        return
    for symbol, ticker in tickers.items():
        print(f"{symbol}\tlast={ticker.to_dict()['last']}")


def cmd_ohlc(mgr: KrakenManager, args: argparse.Namespace) -> None:
    import json as _json

    candles, _last = mgr.ohlc(args.pair, interval=args.interval)
    if args.json:
        print(_json.dumps([c.to_dict() for c in candles], indent=2))
        return
    for candle in candles[-args.limit:]:
        print(candle)


def cmd_book(mgr: KrakenManager, args: argparse.Namespace) -> None:
    book = mgr.order_book(args.pair, count=args.count)
    if not book.bids and not book.asks:
        print("empty book")
        return
    print(f"# {book.pair}")
    for level in book.asks[:5]:
        print(f"  ask {level.price:>14}  {level.volume}")
    print("  --")
    for level in book.bids[:5]:
        print(f"  bid {level.price:>14}  {level.volume}")


def cmd_pairs(mgr: KrakenManager, args: argparse.Namespace) -> None:
    for ws in mgr.known_pairs()[:200]:
        print(ws)


def cmd_snapshot(mgr: KrakenManager, args: argparse.Namespace) -> None:
    from kraken_api.export import export_snapshot

    pairs = args.pair or ["BTC/USD", "ETH/USD", "SOL/USD"]
    path = export_snapshot(
        mgr,
        args.output,
        pairs=pairs,
        include_account=args.include_account,
    )
    print(f"snapshot -> {path}")


def cmd_balance(mgr: KrakenManager, args: argparse.Namespace) -> None:
    for balance in mgr.balances():
        print(balance)


def cmd_orders(mgr: KrakenManager, args: argparse.Namespace) -> None:
    orders = mgr.open_orders()
    if not orders:
        print("no open orders")
        return
    for order in orders:
        print(order)


def cmd_closed(mgr: KrakenManager, args: argparse.Namespace) -> None:
    for order in mgr.closed_orders()[-args.limit:]:
        print(order)


def cmd_order(mgr: KrakenManager, args: argparse.Namespace) -> None:
    print(mgr.order(args.txid))


def cmd_history(mgr: KrakenManager, args: argparse.Namespace) -> None:
    trades, _count = mgr.trade_history()
    for trade in trades[-args.limit:]:
        print(trade)


def cmd_buy(mgr: KrakenManager, args: argparse.Namespace) -> None:
    result = mgr.buy(args.pair, args.volume, args.price)
    print(result.get("descr", {}).get("order") or result)


def cmd_sell(mgr: KrakenManager, args: argparse.Namespace) -> None:
    result = mgr.sell(args.pair, args.volume, args.price)
    print(result.get("descr", {}).get("order") or result)


def cmd_cancel(mgr: KrakenManager, args: argparse.Namespace) -> None:
    count = mgr.cancel(args.txid)
    print(f"cancelled {count} order(s)" if count else f"txid {args.txid} not open")


def cmd_cancel_all(mgr: KrakenManager, args: argparse.Namespace) -> None:
    print(f"cancelled {mgr.cancel_all()} order(s)")


def cmd_ws(mgr: KrakenManager, args: argparse.Namespace) -> None:
    from kraken_api.websocket import SpotWebSocket

    ws = SpotWebSocket.connect_public()
    ws.subscribe(args.channel, list(args.pair))
    try:
        for message in ws.iter_messages(timeout=args.timeout, filter_channel=args.channel):
            print(message)
    finally:
        ws.close()


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # -- Logging setup (precedence: CLI flag > env var > default) ----------
    log_level = "DEBUG" if args.verbose else "WARNING"
    file_level = "DEBUG" if args.verbose else "INFO"

    if args.no_log_file:
        log_file = None
    elif args.log_file is not None:
        log_file = args.log_file
    else:
        log_file = str(_xdg_state_dir() / "kraken.log")

    setup_logging(
        level=log_level,
        log_file=log_file,
        file_level=file_level,
        console=True,
    )

    # -- Run the command ---------------------------------------------------
    try:
        mgr = _mgr(args)
        args.func(mgr, args)
    except Exception as exc:  # surface friendly errors
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())