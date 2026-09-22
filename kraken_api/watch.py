"""Live market-data watcher helpers built on the WebSocket feed.

The WebSocket already pushes every tick; these helpers turn that stream into
small, familiar generators — one that yields every ticker *change* for a
symbol, and one that blocks until the next tick (useful for scripting).

    from kraken_api.websocket import SpotWebSocket
    from kraken_api.watch import watch_ticker

    ws = SpotWebSocket.connect_public()
    ws.subscribe("ticker", ["BTC/USD"])
    for tick in watch_ticker(ws, "BTC/USD"):
        print(tick["last"])
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterator

from .websocket import SpotWebSocket, publish_ticker_update

_WS_LOG = logging.getLogger("kraken_api.ws")


def _changes(key: dict[str, str] | None, new: dict[str, str]) -> list[str]:
    """List the keys that differ between two ticker state dicts."""
    if key is None:
        return list(new.keys())
    return [k for k in new if key.get(k) != new[k]]


def watch_ticker(
    ws: SpotWebSocket,
    symbol: str,
    emit_unchanged: bool = False,
) -> Iterator[dict[str, Any]]:
    """Yield a normalized ticker dict for ``symbol`` on every update.

    On the first (snapshot) message the whole state is yielded; afterwards
    only updates that changed the top-of-book price are yielded unless
    ``emit_unchanged`` is set.  Messages are tagged with ``_changes`` listing
    the fields that moved.

    Runs until the connection closes or is closed.
    """
    last_key: dict[str, str] | None = None
    for message in ws.iter_messages(filter_channel="ticker"):
        for item in message.get("data") or []:
            if item.get("symbol") != symbol:
                continue
            flat = publish_ticker_update(item)
            key = {k: str(v) for k, v in flat.items() if k != "symbol"}
            changed = _changes(last_key, key)
            if emit_unchanged or changed:
                flat["_changes"] = changed
                yield flat
            last_key = key


def blocks_until_tick(
    ws: SpotWebSocket,
    symbol: str,
    timeout: float | None = None,
) -> dict[str, Any] | None:
    """Block until the next ticker update for ``symbol`` (or ``timeout``).

    Returns the first normalized update for the symbol, or ``None`` on
    timeout.  The caller is expected to have subscribed the symbol already
    (``ws.subscribe("ticker", [symbol])``).
    """
    import time as _time

    start = _time.monotonic()
    for tick in watch_ticker(ws, symbol):
        if timeout is not None and _time.monotonic() - start >= timeout:
            return None
        return tick
    return None