"""Export market data and account snapshots into JSON-ready structures.

The ``KrakenManager`` returns typed dataclasses; this module converts them
into plain nested dicts and (optionally) writes a compact JSON document that
can be grepped, diffed, or fed into downstream tooling:

    tickers = export_tickers(mgr, ["BTC/USD", "ETH/USD"])
    account = export_account(mgr)                  # balances + open orders
    write_export({"tickers": tickers, "account": account}, "snapshot.json")
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .manager import KrakenManager


def export_tickers(manager: KrakenManager, pairs: Iterable[str]) -> list[dict[str, Any]]:
    """Fetch current tickers for ``pairs`` and return them as dicts."""
    tickers = manager.tickers(pairs)
    return [ticker.to_dict() for ticker in tickers.values()]


def export_ohlc(manager: KrakenManager, pair: str, interval: int = 60, limit: int | None = None) -> dict[str, Any]:
    """Fetch OHLC candles for a pair and return ``{"pair", "interval", "candles"}``."""
    candles, last = manager.ohlc(pair, interval=interval)
    rows = [candle.to_dict() for candle in candles]
    if limit is not None:
        rows = rows[-limit:]
    return {"pair": pair, "interval": interval, "last": last, "candles": rows}


def export_account(manager: KrakenManager) -> dict[str, Any]:
    """Snapshot the account: balances, open orders, and trade history count.

    Only includes data available to the credentials in use; a public-only
    client will fail here with :class:`kraken_api.errors.AuthenticationError`.
    """
    return {
        "balances": [b.to_dict() for b in manager.balances()],
        "open_orders": [o.to_dict() for o in manager.open_orders()],
    }


def write_export(document: dict[str, Any], path: str | Path) -> Path:
    """Write an export document to ``path`` with a self-describing envelope."""
    path = Path(path)
    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        **document,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return path


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