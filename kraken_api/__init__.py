"""Kraken API wrapper.

A thin, friendly wrapper around the Kraken Spot REST and WebSocket v2 APIs
that just needs your API key-pair and gives you readable functions for market
data, account balances, and order management — without touching request
signing, pair-name dialects, pagination-less envelopes, or string-vs-decimal
conversions yourself.
"""

from __future__ import annotations

import logging
from typing import Any

from .auth import client_from_credentials, client_from_env
from .catalog import Pair, PairCatalog
from .client import KrakenClient
from .errors import (
    APIError,
    AuthenticationError,
    ConfigurationError,
    InvalidPairError,
    KrakenError,
    OrderError,
    RateLimitError,
    WebSocketError,
)
from .manager import KrakenManager
from .models import (
    Asset,
    Balance,
    BookLevel,
    Candle,
    LedgerEntry,
    Order,
    OrderBook,
    ServerTime,
    SpreadPoint,
    Ticker,
    Trade,
    TradeBalance,
    WsBook,
    WsTicker,
    WsTrade,
)
from .websocket import SpotWebSocket, decode_message
from .watch import blocks_until_tick, watch_ticker

__version__ = "0.3.0"

# Library-safe default: attach a NullHandler so that importing the package
# never configures logging or emits output.  Call setup_logging() from the
# CLI entry-point (or your application) to attach real handlers.
logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = [
    "KrakenClient",
    "KrakenManager",
    "SpotWebSocket",
    "Pair",
    "PairCatalog",
    "watch_ticker",
    "blocks_until_tick",
    "client_from_env",
    "client_from_credentials",
    "ServerTime",
    "Ticker",
    "Candle",
    "BookLevel",
    "OrderBook",
    "Balance",
    "Order",
    "Trade",
    "LedgerEntry",
    "TradeBalance",
    "SpreadPoint",
    "Asset",
    "WsTicker",
    "WsTrade",
    "WsBook",
    "decode_message",
    "extract",
    "extract_many",
    "extract_snapshot",
    "write_json",
    "write_jsonl",
    "KrakenError",
    "AuthenticationError",
    "ConfigurationError",
    "APIError",
    "RateLimitError",
    "InvalidPairError",
    "OrderError",
    "WebSocketError",
    "__version__",
]


def __getattr__(name: str) -> Any:
    """Lazy re-exports that avoid import cycles (export imports manager)."""
    if name in ("extract", "extract_many", "extract_snapshot", "write_json", "write_jsonl"):
        from . import export as _export

        return getattr(_export, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")