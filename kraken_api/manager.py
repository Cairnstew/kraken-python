"""The high-level facade: everything you can ask Kraken, simply.

Construct one from environment variables::

    from kraken_api import KrakenManager
    mgr = KrakenManager.from_env()            # reads KRAKEN_API_KEY / SECRET

    mgr.server_time()                          # exchange time
    ticker = mgr.ticker("BTC/USD")             # any spelling works
    candles, last = mgr.ohlc("BTC/USD", interval=60)
    book = mgr.order_book("BTC/USD")

    # Trading (requires credentials)
    result = mgr.place_limit("buy", "BTC/USD", volume="0.001", price="27000")
    mgr.cancel(result["txid"][0])
    mgr.balances()

The manager wraps a :class:`~kraken_api.client.KrakenClient` (reachable as
``mgr.client`` for anything beyond the friendly surface) and returns typed
:mod:`kraken_api.models` objects with the raw string decimals preserved.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from .catalog import PairCatalog
from .client import KrakenClient
from .logging_config import log_event
from .models import (
    Balance,
    Candle,
    LedgerEntry,
    Order,
    OrderBook,
    ServerTime,
    Ticker,
    Trade,
)

_USER_LOG = logging.getLogger("kraken_api.user")

# Kraken OHLC intervals (minutes) the API accepts.
OHLC_INTERVALS = (1, 5, 15, 30, 60, 240, 1440, 10080, 21600)


class KrakenManager:
    """Friendly wrapper around a :class:`KrakenClient`, focused on the
    operations a trading/investing script most often needs.

    Parameters
    ----------
    client:
        An authenticated (or public) :class:`~kraken_api.client.KrakenClient`.
    """

    def __init__(self, client: KrakenClient) -> None:
        self.client = client
        if client.catalog is None:
            client.catalog = PairCatalog(transport=client.transport)
        self.catalog = client.catalog

    # ------------------------------------------------------------------ #
    # Construction helpers
    # ------------------------------------------------------------------ #

    @classmethod
    def from_env(cls, **kwargs: Any) -> "KrakenManager":
        """Build a manager from ``KRAKEN_*`` environment variables."""
        from .auth import client_from_env

        return cls(client_from_env(**kwargs))

    @classmethod
    def from_credentials(cls, api_key: str, api_secret: str, **kwargs: Any) -> "KrakenManager":
        """Build a manager from explicit API credentials."""
        from .auth import client_from_credentials

        return cls(client_from_credentials(api_key, api_secret, **kwargs))

    # ------------------------------------------------------------------ #
    # Server / reference data
    # ------------------------------------------------------------------ #

    def server_time(self) -> ServerTime:
        """Return the exchange server's UTC time."""
        return ServerTime.from_kraken(self.client.server_time())

    def known_pairs(self) -> list[str]:
        """Return the catalog's known markets as WebSocket-style symbols."""
        return [p.ws for p in self.catalog.known_pairs()]

    # ------------------------------------------------------------------ #
    # Market data
    # ------------------------------------------------------------------ #

    def ticker(self, pair: str) -> Ticker:
        """Return the current :class:`Ticker` for one market (any spelling)."""
        data = self.client.ticker(pair)
        pub = self.catalog.resolve(pair, style="pub")
        row = data.get(pub) or _first_value(data)
        return Ticker.from_kraken(self.catalog.resolve(pair, style="ws"), row or {})

    def tickers(self, pairs: Iterable[str]) -> dict[str, Ticker]:
        """Return tickers for several markets: ws symbol -> Ticker."""
        pairs = list(pairs)
        if not pairs:
            return {}
        data = self.client.ticker(pairs)
        out: dict[str, Ticker] = {}
        for pair in pairs:
            pub = self.catalog.resolve(pair, style="pub")
            row = data.get(pub)
            if row is None:
                continue
            out[self.catalog.resolve(pair, style="ws")] = Ticker.from_kraken(
                self.catalog.resolve(pair, style="ws"), row
            )
        return out

    def ohlc(
        self,
        pair: str,
        interval: int = 5,
        since: int | None = None,
    ) -> tuple[list[Candle], int]:
        """Return (candles, last) for a market.

        ``interval`` must be one of :data:`OHLC_INTERVALS`.  ``last`` is the
        timestamp to pass back as ``since`` for the next page.
        """
        if interval not in OHLC_INTERVALS:
            raise ValueError(
                f"interval must be one of {OHLC_INTERVALS}, got {interval!r}"
            )
        data = self.client.ohlc(pair, interval=interval, since=since)
        pub = self.catalog.resolve(pair, style="pub")
        ws = self.catalog.resolve(pair, style="ws")
        rows = data.get(pub) or _first_value(data) or []
        candles = [Candle.from_kraken(ws, row) for row in rows]
        return candles, int(data.get("last") or 0)

    def order_book(self, pair: str, count: int | None = None) -> OrderBook:
        """Return an :class:`OrderBook` snapshot for a market."""
        data = self.client.depth(pair, count=count)
        pub = self.catalog.resolve(pair, style="pub")
        row = data.get(pub) or _first_value(data) or {}
        return OrderBook.from_kraken(self.catalog.resolve(pair, style="ws"), row)

    def recent_trades(self, pair: str, since: int | None = None) -> tuple[list[Trade], str]:
        """Return (trades, last) recent public trades for a market."""
        data = self.client.trades(pair, since=since)
        pub = self.catalog.resolve(pair, style="pub")
        ws = self.catalog.resolve(pair, style="ws")
        rows = data.get(pub) or _first_value(data) or []
        return [Trade.from_public_row(ws, row) for row in rows], str(data.get("last") or "")

    # ------------------------------------------------------------------ #
    # Account
    # ------------------------------------------------------------------ #

    def balances(self) -> list[Balance]:
        """Return all non-zero account balances as typed :class:`Balance` rows."""
        raw = self.client.balance()
        return [Balance.from_entry(asset, amount) for asset, amount in raw.items()]

    def balance(self, asset: str) -> Balance | None:
        """Return one asset's balance (looked up by asset code or altname)."""
        raw = self.client.balance()
        needle = asset.upper()
        for code, amount in raw.items():
            if code.upper() == needle or _asset_altnames(code) == needle:
                return Balance.from_entry(code, amount)
        return None

    def trade_balance(self, asset: str | None = None) -> dict[str, Any]:
        """Return the raw trade-balance summary dict (``eb``, ``tb``, ``m``, ...)."""
        return self.client.trade_balance(asset=asset)

    # ------------------------------------------------------------------ #
    # Orders
    # ------------------------------------------------------------------ #

    def place_order(
        self,
        side: str,
        pair: str,
        ordertype: str,
        volume: str | float,
        price: str | float | None = None,
        price2: str | float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Place an order through the friendly surface.

        ``side`` is ``"buy"``/``"sell"``; ``ordertype`` is any Kraken order
        type (``market``, ``limit``, ``stop-loss``, ``take-profit``,
        ``stop-loss-limit``, ...).  Returns the raw AddOrder result
        (``{"txid": [...], "descr": {...}}``).
        """
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
        return self.client.add_order(
            pair=pair,
            side=side,
            ordertype=ordertype,
            volume=volume,
            price=price,
            price2=price2,
            **kwargs,
        )

    def buy(self, pair: str, volume: str | float, price: str | float | None = None, **kwargs: Any) -> dict[str, Any]:
        """Place a buy (market when ``price`` is None, else limit)."""
        return self.client.add_order(
            pair=pair, side="buy",
            ordertype="market" if price is None else "limit",
            volume=volume, price=price, **kwargs,
        )

    def sell(self, pair: str, volume: str | float, price: str | float | None = None, **kwargs: Any) -> dict[str, Any]:
        """Place a sell (market when ``price`` is None, else limit)."""
        return self.client.add_order(
            pair=pair, side="sell",
            ordertype="market" if price is None else "limit",
            volume=volume, price=price, **kwargs,
        )

    def open_orders(self) -> list[Order]:
        """Return currently-open orders as typed :class:`Order` rows."""
        result = self.client.open_orders()
        return [
            Order.from_kraken(txid, data)
            for txid, data in (result.get("open") or {}).items()
        ]

    def closed_orders(self, **kwargs: Any) -> list[Order]:
        """Return closed orders as typed :class:`Order` rows."""
        result = self.client.closed_orders(**kwargs)
        return [
            Order.from_kraken(txid, data)
            for txid, data in (result.get("closed") or {}).items()
        ]

    def order(self, txid: str) -> Order:
        """Return one order by transaction id."""
        result = self.client.query_orders(txid)
        data = result.get(txid)
        if data is None:
            raise KeyError(f"Order {txid!r} not found")
        return Order.from_kraken(txid, data)

    def cancel(self, txid: str) -> int:
        """Cancel one order; returns the number of orders actually cancelled."""
        result = self.client.cancel_order(txid)
        return int(result.get("count") or 0)

    def cancel_all(self) -> int:
        """Cancel all open orders; returns how many were cancelled."""
        result = self.client.cancel_all()
        return int(result.get("count") or 0)

    def cancel_all_after(self, timeout_seconds: int) -> int:
        """Arm a dead-man's-switch: blanket-cancel every order in N seconds."""
        result = self.client.cancel_all_orders_after(timeout_seconds)
        return int(result.get("cancelled") or 0)

    # ------------------------------------------------------------------ #
    # History
    # ------------------------------------------------------------------ #

    def trade_history(self, **kwargs: Any) -> tuple[list[Trade], int]:
        """Return (trades, count) for the user's trade history."""
        result = self.client.trades_history(**kwargs)
        trades = [
            Trade.from_history_entry(txid, data)
            for txid, data in (result.get("trades") or {}).items()
        ]
        return trades, int(result.get("count") or len(trades))

    def ledger(self, **kwargs: Any) -> tuple[list[LedgerEntry], int]:
        """Return (entries, count) for the account ledger."""
        result = self.client.ledgers(**kwargs)
        entries = [
            LedgerEntry.from_kraken(lid, data)
            for lid, data in (result.get("ledger") or {}).items()
        ]
        return entries, int(result.get("count") or len(entries))

    # ------------------------------------------------------------------ #
    # Real-time
    # ------------------------------------------------------------------ #

    def ws_token(self) -> str:
        """Return a token for the private WebSocket endpoint."""
        return self.client.get_websockets_token()

    def public_ws_url(self) -> str:
        import os

        return os.environ.get("KRAKEN_WS_URL", "wss://ws.kraken.com/v2")

    def private_ws_url(self) -> str:
        import os

        return os.environ.get("KRAKEN_WS_AUTH_URL", "wss://ws-auth.kraken.com/v2")


def _first_value(data: dict[str, Any]) -> Any:
    """Return the first list-valued entry of a result dict.

    /0/public/Ticker and friends key their result by pair name; when the key
    is unpredictable (server-side synonyms) taking the sole value is a safe
    fallback.
    """
    for value in data.values():
        if isinstance(value, list):
            return value
    return None


def _asset_altnames(code: str) -> str:
    """Best-effort common altname for a few famous asset codes (offline only)."""
    return {
        "XXBT": "XBT",
        "XETH": "ETH",
        "XLTC": "LTC",
        "XXRP": "XRP",
        "XDG": "DOGE",
        "ZUSD": "USD",
        "ZEUR": "EUR",
    }.get(code.upper(), code.upper())