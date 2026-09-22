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
    python cli.py extract tickers --pair BTC/USD --json

Every command accepts ``--json`` to emit clean JSON on stdout (the
``ws`` command emits a JSON object per line).  ``extract`` pulls a named
resource through the :mod:`kraken_api.export` registry and can write
``--jsonl`` streams.

Authenticates against KRAKEN_API_KEY / KRAKEN_API_SECRET (see .env.example).
"""

from __future__ import annotations

import argparse
import json as _json
import sys
from typing import Any, Callable

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
    p.add_argument("--json", action="store_true", help="print raw JSON")
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
    p.add_argument("--json", action="store_true", help="print raw JSON")
    p.set_defaults(func=cmd_book)

    p = sub.add_parser("spread", help="show recent best bid/ask spread samples")
    p.add_argument("pair")
    p.add_argument("--limit", type=int, default=10, help="max rows to print (default: 10)")
    p.add_argument("--json", action="store_true", help="print raw JSON")
    p.set_defaults(func=cmd_spread)

    p = sub.add_parser("pairs", help="list known markets from the pair catalog")
    p.add_argument("--json", action="store_true", help="print raw JSON")
    p.set_defaults(func=cmd_pairs)

    p = sub.add_parser("assets", help="list asset catalog entries")
    p.add_argument("--json", action="store_true", help="print raw JSON")
    p.set_defaults(func=cmd_assets)

    p = sub.add_parser("snapshot", help="export tickers (and account) to a JSON file")
    p.add_argument("output", nargs="?", default="kraken-snapshot.json")
    p.add_argument("--pair", action="append", default=[], help="repeatable; defaults to BTC/ETH/SOL vs USD")
    p.add_argument("--include-account", action="store_true", help="also include balances + open orders")
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser(
        "extract",
        help="pull a named resource (or 'all') through the extraction registry as JSON",
    )
    p.add_argument("resource", help="resource name, or 'all' for a full snapshot")
    p.add_argument("--pair", action="append", default=[], help="repeatable market(s) for pair-scoped resources")
    p.add_argument("--interval", type=int, default=60, help="ohlc interval in minutes (default: 60)")
    p.add_argument("--limit", type=int, default=None, help="max rows for limit-scoped resources")
    p.add_argument("--count", type=int, default=None, help="book depth levels per side")
    p.add_argument("--txid", default=None, help="order id for the 'order' resource")
    p.add_argument("--output", default=None, help="write JSON (or JSONL) to this file instead of stdout")
    p.add_argument("--jsonl", action="store_true", help="write one JSON object per line")
    p.set_defaults(func=cmd_extract)

    # -- authenticated -------------------------------------------------
    p = sub.add_parser("balance", help="show account balances")
    p.add_argument("--json", action="store_true", help="print raw JSON")
    p.set_defaults(func=cmd_balance)

    p = sub.add_parser("trade-balance", help="show equity/margin/trade-balance summary")
    p.add_argument("--json", action="store_true", help="print raw JSON")
    p.set_defaults(func=cmd_trade_balance)

    p = sub.add_parser("orders", help="show open orders")
    p.add_argument("--json", action="store_true", help="print raw JSON")
    p.set_defaults(func=cmd_orders)

    p = sub.add_parser("closed", help="show recent closed orders")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true", help="print raw JSON")
    p.set_defaults(func=cmd_closed)

    p = sub.add_parser("order", help="show one order by txid")
    p.add_argument("txid")
    p.add_argument("--json", action="store_true", help="print raw JSON")
    p.set_defaults(func=cmd_order)

    p = sub.add_parser("history", help="show recent trades")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true", help="print raw JSON")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("ledger", help="show recent ledger entries")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true", help="print raw JSON")
    p.set_defaults(func=cmd_ledger)

    p = sub.add_parser("buy", help="place a buy (market without PRICE, limit with)")
    p.add_argument("pair")
    p.add_argument("volume")
    p.add_argument("price", nargs="?", default=None)
    p.add_argument("--json", action="store_true", help="print the raw result as JSON")
    p.set_defaults(func=cmd_buy)

    p = sub.add_parser("sell", help="place a sell (market without PRICE, limit with)")
    p.add_argument("pair")
    p.add_argument("volume")
    p.add_argument("price", nargs="?", default=None)
    p.add_argument("--json", action="store_true", help="print the raw result as JSON")
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
    p.add_argument("--jsonl", action="store_true", help="emit one JSON object per line (default)")
    p.add_argument("--pretty", action="store_true", help="indent each JSON object instead of compact lines")
    p.add_argument("--output", default=None, help="write the stream as NDJSON to this file (no stdout)")
    p.set_defaults(func=cmd_ws)

    return parser


def _emit_json(payload: Any, args: argparse.Namespace, pretty: bool = False) -> None:
    """Print ``payload`` as clean JSON; a bool flag is always respected."""
    if pretty or getattr(args, "pretty", False) or getattr(args, "json", False):
        print(_json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(_json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _mgr(args: argparse.Namespace) -> KrakenManager:
    return KrakenManager.from_env()


def cmd_server_time(mgr: KrakenManager, args: argparse.Namespace) -> None:
    st = mgr.server_time()
    if args.json:
        _emit_json(st.to_dict(), args)
        return
    print(st)


def cmd_ticker(mgr: KrakenManager, args: argparse.Namespace) -> None:
    tickers = mgr.tickers(args.pair)
    if args.json:
        _emit_json({k: t.to_dict() for k, t in tickers.items()}, args)
        return
    for symbol, ticker in tickers.items():
        print(f"{symbol}\tlast={ticker.to_dict()['last']}")


def cmd_ohlc(mgr: KrakenManager, args: argparse.Namespace) -> None:
    candles, _last = mgr.ohlc(args.pair, interval=args.interval)
    if args.json:
        _emit_json([c.to_dict() for c in candles], args)
        return
    for candle in candles[-args.limit:]:
        print(candle)


def cmd_book(mgr: KrakenManager, args: argparse.Namespace) -> None:
    book = mgr.order_book(args.pair, count=args.count)
    if args.json:
        _emit_json(book.to_dict(), args)
        return
    if not book.bids and not book.asks:
        print("empty book")
        return
    print(f"# {book.pair}")
    for level in book.asks[:5]:
        print(f"  ask {level.price:>14}  {level.volume}")
    print("  --")
    for level in book.bids[:5]:
        print(f"  bid {level.price:>14}  {level.volume}")


def cmd_spread(mgr: KrakenManager, args: argparse.Namespace) -> None:
    points, _last = mgr.spread(args.pair)
    if args.json:
        _emit_json([p.to_dict() for p in points[-args.limit:]], args)
        return
    for point in points[-args.limit:]:
        print(point)


def cmd_pairs(mgr: KrakenManager, args: argparse.Namespace) -> None:
    pairs = mgr.catalog.known_pairs()
    if args.json:
        _emit_json([p.to_dict() for p in pairs], args)
        return
    for pair in pairs[:200]:
        print(pair.ws)


def cmd_assets(mgr: KrakenManager, args: argparse.Namespace) -> None:
    if args.json:
        _emit_json({code: a.to_dict() for code, a in mgr.assets().items()}, args)
        return
    for asset in mgr.assets().values():
        print(asset)


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


def cmd_extract(mgr: KrakenManager, args: argparse.Namespace) -> None:
    from kraken_api.export import EXTRACTORS, extract, extract_snapshot, write_json, write_jsonl

    if args.resource == "all":
        doc = extract_snapshot(mgr, pairs=args.pair or None, include_account=True)
    elif args.resource in EXTRACTORS:
        opts: dict[str, Any] = {}
        if args.pair and args.resource in ("tickers",):
            opts["pairs"] = args.pair
        if args.resource in ("ohlc", "spread", "book", "trades", "order"):
            opts["pair"] = args.pair[0] if args.pair else "BTC/USD"
        if args.resource == "ohlc":
            opts["interval"] = args.interval
        if args.resource in ("ohlc", "trades", "spread", "closed-orders", "history", "ledger"):
            opts["limit"] = args.limit
        if args.resource == "book":
            opts["count"] = args.count
        if args.resource == "order":
            opts["txid"] = args.txid or ""
        doc = extract(mgr, args.resource, **opts)
    else:
        known = ", ".join(sorted(EXTRACTORS))
        print(f"error: unknown resource {args.resource!r}; known: {known}, all", file=sys.stderr)
        return

    if args.output:
        path = write_jsonl(args.output, [doc]) if args.jsonl else write_json(doc, args.output)
        print(f"extract -> {path}")
    else:
        _emit_json(doc, args, pretty=not args.jsonl)


def cmd_balance(mgr: KrakenManager, args: argparse.Namespace) -> None:
    balances = mgr.balances()
    if args.json:
        _emit_json([b.to_dict() for b in balances], args)
        return
    for balance in balances:
        print(balance)


def cmd_trade_balance(mgr: KrakenManager, args: argparse.Namespace) -> None:
    tb = mgr.trade_balance()
    if args.json:
        _emit_json(tb.to_dict(), args)
        return
    print(tb)


def cmd_orders(mgr: KrakenManager, args: argparse.Namespace) -> None:
    orders = mgr.open_orders()
    if args.json:
        _emit_json([o.to_dict() for o in orders], args)
        return
    if not orders:
        print("no open orders")
        return
    for order in orders:
        print(order)


def cmd_closed(mgr: KrakenManager, args: argparse.Namespace) -> None:
    orders = mgr.closed_orders()[-args.limit:]
    if args.json:
        _emit_json([o.to_dict() for o in orders], args)
        return
    for order in orders:
        print(order)


def cmd_order(mgr: KrakenManager, args: argparse.Namespace) -> None:
    order = mgr.order(args.txid)
    _emit_json(order.to_dict(), args, pretty=True)


def cmd_history(mgr: KrakenManager, args: argparse.Namespace) -> None:
    trades, _count = mgr.trade_history()
    if args.json:
        _emit_json([t.to_dict() for t in trades[-args.limit:]], args)
        return
    for trade in trades[-args.limit:]:
        print(trade)


def cmd_ledger(mgr: KrakenManager, args: argparse.Namespace) -> None:
    entries, _count = mgr.ledger()
    if args.json:
        _emit_json([e.to_dict() for e in entries[-args.limit:]], args)
        return
    for entry in entries[-args.limit:]:
        print(entry)


def cmd_buy(mgr: KrakenManager, args: argparse.Namespace) -> None:
    result = mgr.buy(args.pair, args.volume, args.price)
    if args.json:
        _emit_json(result, args)
        return
    print(result.get("descr", {}).get("order") or result)


def cmd_sell(mgr: KrakenManager, args: argparse.Namespace) -> None:
    result = mgr.sell(args.pair, args.volume, args.price)
    if args.json:
        _emit_json(result, args)
        return
    print(result.get("descr", {}).get("order") or result)


def cmd_cancel(mgr: KrakenManager, args: argparse.Namespace) -> None:
    count = mgr.cancel(args.txid)
    if args.json:
        _emit_json({"txid": args.txid, "cancelled": count}, args)
        return
    print(f"cancelled {count} order(s)" if count else f"txid {args.txid} not open")


def cmd_cancel_all(mgr: KrakenManager, args: argparse.Namespace) -> None:
    if args.json:
        _emit_json({"cancelled": mgr.cancel_all()}, args)
        return
    print(f"cancelled {mgr.cancel_all()} order(s)")


def cmd_ws(mgr: KrakenManager, args: argparse.Namespace) -> None:
    from kraken_api.export import iter_ws_jsonl, write_jsonl
    from kraken_api.websocket import SpotWebSocket

    ws = SpotWebSocket.connect_public()
    ws.subscribe(args.channel, list(args.pair))
    try:
        feed = iter_ws_jsonl(ws, channel=args.channel, timeout=args.timeout)
        if args.output:
            path = write_jsonl(args.output, feed)
            print(f"stream -> {path}")
            return
        for decoded in feed:
            _emit_json(decoded, args, pretty=args.pretty)
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