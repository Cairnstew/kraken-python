"""Data extraction infra: pull any Kraken resource as clean JSON.

This module is the single place to turn a :class:`KrakenManager` into
JSON-ready structures.  Two layers:

- :data:`EXTRACTORS` — a registry mapping a resource name (``"tickers"``,
  ``"ohlc"``, ``"book"``, ``"balance"``, ...) to a fetch function that
  returns a JSON-serialisable dict built from the typed models.
- :func:`extract` / :func:`extract_many` / :func:`extract_snapshot` — the
  entry points, which wrap one, several, or every extractor in an envelope
  with a schema and timestamp.

Everything an extractor returns round-trips through ``json.dumps``: no
Decimals, no datetimes, no surprise keys.  ``write_json`` / ``write_jsonl``
persist single documents or NDJSON streams (one JSON object per line) —
``write_jsonl`` is what the WebSocket streaming helpers feed.

    from kraken_api.export import extract, extract_many, write_json

    doc = extract(mgr, "tickers", pairs=["BTC/USD", "ETH/USD"])
    feed = extract_many(mgr, ["server-time", "book"], pairs=["BTC/USD"])
    write_json(feed, "snapshot.json")

The ``export_*`` helpers that shipped earlier are thin wrappers over the
same registry, kept for backwards compatibility.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .manager import KrakenManager
from .models import (
    Asset,
    Balance,
    Candle,
    LedgerEntry,
    Order,
    OrderBook,
    ServerTime,
    SpreadPoint,
    Ticker,
    Trade,
    TradeBalance,
)

SCHEMA_VERSION = "kraken-extract/1"

# An extractor: (manager, **opts) -> JSON-ready dict.
Extractor = Callable[..., dict[str, Any]]


# ---------------------------------------------------------------------- #
# Per-resource extractors
# ---------------------------------------------------------------------- #


def _server_time(mgr: KrakenManager, **opts: Any) -> dict[str, Any]:
    return mgr.server_time().to_dict()


def _tickers(mgr: KrakenManager, pairs: Iterable[str], **opts: Any) -> dict[str, Any]:
    rows = [t.to_dict() for t in mgr.tickers(pairs).values()]
    return {"pairs": list(pairs), "tickers": rows}


def _ohlc(mgr: KrakenManager, pair: str = "BTC/USD", interval: int = 60, limit: int | None = None, **opts: Any) -> dict[str, Any]:
    candles, last = mgr.ohlc(pair, interval=interval)
    rows = [c.to_dict() for c in candles]
    if limit is not None:
        rows = rows[-limit:]
    return {"pair": pair, "interval": interval, "last": last, "candles": rows}


def _book(mgr: KrakenManager, pair: str = "BTC/USD", count: int | None = None, **opts: Any) -> dict[str, Any]:
    book: OrderBook = mgr.order_book(pair, count=count)
    return {
        "pair": book.pair,
        "bids": [lvl.to_dict() for lvl in book.bids],
        "asks": [lvl.to_dict() for lvl in book.asks],
        "best_bid": _str_or_none(book.best_bid()),
        "best_ask": _str_or_none(book.best_ask()),
        "spread": _str_or_none(book.spread()),
    }


def _trades(mgr: KrakenManager, pair: str = "BTC/USD", since: int | None = None, limit: int | None = None, **opts: Any) -> dict[str, Any]:
    trades, last = mgr.recent_trades(pair, since=since)
    rows = [t.to_dict() for t in trades]
    if limit is not None:
        rows = rows[-limit:]
    return {"pair": pair, "last": last, "trades": rows}


def _spread(mgr: KrakenManager, pair: str = "BTC/USD", since: int | None = None, limit: int | None = None, **opts: Any) -> dict[str, Any]:
    points, last = mgr.spread(pair, since=since)
    rows = [p.to_dict() for p in points]
    if limit is not None:
        rows = rows[-limit:]
    return {"pair": pair, "last": last, "spread": rows}


def _balance(mgr: KrakenManager, **opts: Any) -> dict[str, Any]:
    return {"balances": [b.to_dict() for b in mgr.balances()]}


def _trade_balance(mgr: KrakenManager, asset: str | None = None, **opts: Any) -> dict[str, Any]:
    return {"asset": "currency" if asset is None else asset, "trade_balance": mgr.trade_balance(asset).to_dict()}


def _orders(mgr: KrakenManager, **opts: Any) -> dict[str, Any]:
    return {"orders": [o.to_dict() for o in mgr.open_orders()]}


def _closed_orders(mgr: KrakenManager, limit: int | None = None, **opts: Any) -> dict[str, Any]:
    rows = [o.to_dict() for o in mgr.closed_orders()]
    if limit is not None:
        rows = rows[-limit:]
    return {"orders": rows}


def _order(mgr: KrakenManager, txid: str = "", **opts: Any) -> dict[str, Any]:
    return {"order": mgr.order(txid).to_dict()}


def _history(mgr: KrakenManager, limit: int | None = None, **opts: Any) -> dict[str, Any]:
    trades, count = mgr.trade_history()
    rows = [t.to_dict() for t in trades]
    if limit is not None:
        rows = rows[-limit:]
    return {"count": count, "trades": rows}


def _ledger(mgr: KrakenManager, limit: int | None = None, **opts: Any) -> dict[str, Any]:
    entries, count = mgr.ledger()
    rows = [e.to_dict() for e in entries]
    if limit is not None:
        rows = rows[-limit:]
    return {"count": count, "ledger": rows}


def _assets(mgr: KrakenManager, **opts: Any) -> dict[str, Any]:
    rows = {code: a.to_dict() for code, a in mgr.assets().items()}
    return {"assets": rows}


def _pairs(mgr: KrakenManager, **opts: Any) -> dict[str, Any]:
    return {"pairs": [p.to_dict() for p in mgr.catalog.known_pairs()]}


def _str_or_none(value: Any) -> str | None:
    return str(value) if value is not None else None


# ---------------------------------------------------------------------- #
# Registry
# ---------------------------------------------------------------------- #

# name -> (extractor, one-line help).  `pairs`/`pair` may be any Kraken
# spelling; `limit`/`count`/`since`/`interval` follow the manager's surface.
EXTRACTORS: dict[str, tuple[Extractor, str]] = {
    "server-time": (_server_time, "exchange UTC time"),
    "tickers": (_tickers, "one or more markets (pairs=[...])"),
    "ohlc": (_ohlc, "candles for a pair (pair=, interval=, limit=)"),
    "book": (_book, "order book snapshot (pair=, count=)"),
    "trades": (_trades, "recent public trades (pair=, since=, limit=)"),
    "spread": (_spread, "recent spread samples (pair=, since=, limit=)"),
    "balance": (_balance, "account balances (authenticated)"),
    "trade-balance": (_trade_balance, "equity/margin snapshot (authenticated)"),
    "orders": (_orders, "open orders (authenticated)"),
    "closed-orders": (_closed_orders, "recent closed orders (authenticated, limit=)"),
    "order": (_order, "one order by txid (authenticated)"),
    "history": (_history, "trade history (authenticated, limit=)"),
    "ledger": (_ledger, "account ledger (authenticated, limit=)"),
    "assets": (_assets, "asset catalog"),
    "pairs": (_pairs, "pair catalog (all known markets)"),
}

PUBLIC_RESOURCES = frozenset(
    {"server-time", "tickers", "ohlc", "book", "trades", "spread", "assets", "pairs"}
)


# ---------------------------------------------------------------------- #
# Entry points
# ---------------------------------------------------------------------- #


def extract(mgr: KrakenManager, resource: str, **opts: Any) -> dict[str, Any]:
    """Pull one resource as a clean JSON-ready dict.

    ``opts`` are passed to the extractor (see the registry entries above);
    unsupported options are ignored by the extractor's ``**opts``.
    """
    if resource not in EXTRACTORS:
        known = ", ".join(sorted(EXTRACTORS))
        raise KeyError(f"unknown resource {resource!r}; known resources: {known}")
    fn, _help = EXTRACTORS[resource]
    return fn(mgr, **opts)


def extract_many(
    mgr: KrakenManager,
    resources: Iterable[str],
    *,
    pairs: Iterable[str] | None = None,
    **opts: Any,
) -> dict[str, Any]:
    """Pull several resources and wrap them in a self-describing envelope.

    ``pairs`` is forwarded to every extractor that accepts it (tickers, ohlc,
    ...); a resource that does not use pairs ignores it.
    """
    resources = list(resources)
    if not resources:
        raise ValueError("extract_many requires at least one resource")
    shared: dict[str, Any] = dict(opts)
    if pairs is not None:
        shared["pairs"] = list(pairs)
    data: dict[str, Any] = {}
    for name in resources:
        data[name] = EXTRACTORS[name][0](mgr, **shared)
    return _envelope(data)


def extract_snapshot(
    mgr: KrakenManager,
    *,
    pairs: Iterable[str] | None = None,
    resources: Iterable[str] | None = None,
    include_account: bool = True,
) -> dict[str, Any]:
    """Pull a full snapshot: market data plus (optionally) the account.

    Defaults to all public resources plus balance/orders when authenticated;
    pass ``resources`` to override.  Returns the envelope dict — use
    :func:`write_json` / :func:`write_jsonl` to persist it.
    """
    if resources is None:
        names = ["server-time", "book", "tickers", "ohlc", "trades", "spread"]
        if include_account:
            names += ["balance", "orders"]
    else:
        names = list(resources)
    return extract_many(mgr, names, pairs=pairs)


def _envelope(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "resources": data,
    }


# ---------------------------------------------------------------------- #
# Persistence / streaming
# ---------------------------------------------------------------------- #

def dumps(document: dict[str, Any], *, pretty: bool = False, sort_keys: bool = True) -> str:
    """JSON-dump a document with the extraction layer's defaults."""
    return json.dumps(
        document,
        indent=2 if pretty else None,
        sort_keys=sort_keys,
        ensure_ascii=False,
        separators=None if pretty else (",", ":"),
    )


def write_json(document: dict[str, Any], path: str | Path) -> Path:
    """Write a document (usually an envelope from :func:`extract_many`) to ``path``."""
    path = Path(path)
    path.write_text(dumps(document, pretty=True) + "\n", encoding="utf-8")
    return path


def write_jsonl(path: str | Path, documents: Iterable[dict[str, Any]]) -> Path:
    """Write an iterable of documents as NDJSON (one compact object per line)."""
    path = Path(path)
    with path.open("w", encoding="utf-8") as fh:
        for document in documents:
            fh.write(dumps(document) + "\n")
    return path


def iter_ws_jsonl(ws: Any, *, channel: str, timeout: float | None = None) -> Iterable[dict[str, Any]]:
    """Yield decoded WS v2 data payloads as JSON-ready dicts.

    Consumes an already-subscribed :class:`~kraken_api.websocket.SpotWebSocket`
    and yields one *decoded message* per event (see
    :func:`~kraken_api.websocket.decode_message`).  Control messages
    (heartbeat/acl) are skipped.  This is the NDJSON source for
    ``kraken-python ws --jsonl``.
    """
    from .websocket import decode_message

    for message in ws.iter_messages(timeout=timeout, filter_channel=channel):
        decoded = decode_message(message)
        yield decoded


# ---------------------------------------------------------------------- #
# Backwards-compatible export_* helpers
# ---------------------------------------------------------------------- #


def export_tickers(manager: KrakenManager, pairs: Iterable[str]) -> list[dict[str, Any]]:
    """Fetch current tickers for ``pairs`` and return them as dicts."""
    doc = _tickers(manager, pairs=pairs)
    return doc["tickers"]


def export_ohlc(manager: KrakenManager, pair: str, interval: int = 60, limit: int | None = None) -> dict[str, Any]:
    """Fetch OHLC candles for a pair and return ``{"pair", "interval", "candles"}``."""
    doc = _ohlc(manager, pair, interval=interval, limit=limit)
    return {"pair": doc["pair"], "interval": doc["interval"], "last": doc["last"], "candles": doc["candles"]}


def export_account(manager: KrakenManager) -> dict[str, Any]:
    """Snapshot the account: balances, open orders, and trade history count.

    Only includes data available to the credentials in use; a public-only
    client will fail here with :class:`kraken_api.errors.AuthenticationError`.
    """
    return {
        "balances": [b.to_dict() for b in manager.balances()],
        "open_orders": [o.to_dict() for o in manager.open_orders()],
    }


def export_snapshot(
    manager: KrakenManager,
    path: str | Path,
    pairs: Iterable[str] | None = None,
    include_account: bool = False,
) -> Path:
    """One-shot helper: pull a snapshot and write it to ``path``.

    ``pairs`` defaults to a few well-known markets when omitted.  With
    ``include_account`` the (authenticated) balance + open-order snapshot is
    added under the ``"account"`` key.
    """
    if pairs is None:
        pairs = ["BTC/USD", "ETH/USD", "SOL/USD"]
    document: dict[str, Any] = {
        "tickers": export_tickers(manager, pairs),
    }
    if include_account:
        document["account"] = export_account(manager)
    return write_export(document, path)


def write_export(document: dict[str, Any], path: str | Path) -> Path:
    """Write an export document to ``path`` with a flat self-describing header.

    The legacy shape is preserved: ``{"exported_at": ..., **document}`` with
    the resource keys at the top level (not nested under ``resources``).
    Prefer :func:`extract_many` for the nested envelope.
    """
    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        **document,
    }
    return write_json(payload, path)


__all__ = [
    "SCHEMA_VERSION",
    "EXTRACTORS",
    "PUBLIC_RESOURCES",
    "extract",
    "extract_many",
    "extract_snapshot",
    "dumps",
    "write_json",
    "write_jsonl",
    "iter_ws_jsonl",
    "export_tickers",
    "export_ohlc",
    "export_account",
    "export_snapshot",
    "write_export",
]