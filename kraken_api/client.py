"""Raw REST surface over the Kraken API.

``KrakenClient`` is the equivalent of what ``spotipy.Spotify`` is to the
Spotify project: a thin, mostly unopinionated mapping of Kraken's REST
endpoints to Python methods.  It returns the *raw* ``result`` payloads from
the ``{error, result}`` envelope — nested dicts, string prices, everything.
Want friendly typed views and higher-level operations?  Use
:class:`~kraken_api.manager.KrakenManager`, which wraps exactly this class.

Endpoints are grouped into market-data (public, unauthenticated) and
account/orders (private, signed).  Pair arguments may be given in any Kraken
spelling (``BTC/USD``, ``XBTUSD``, ``XXBTZUSD``) and are resolved via the
pair catalog.
"""

from __future__ import annotations

import logging
from typing import Any

from .catalog import PairCatalog
from .errors import OrderError
from .logging_config import log_event
from .transport import KrakenTransport

_USER_LOG = logging.getLogger("kraken_api.user")


class KrakenClient:
    """Raw Kraken REST client: one method per endpoint, raw payloads back.

    Parameters
    ----------
    transport:
        A configured :class:`~kraken_api.transport.KrakenTransport`.
    catalog:
        A :class:`~kraken_api.catalog.PairCatalog` used to resolve pair
        spellings.  Created lazily (and cached) on first use; pass a
        pre-loaded catalog to avoid the discovery call.
    """

    def __init__(
        self,
        transport: KrakenTransport,
        catalog: PairCatalog | None = None,
    ) -> None:
        self.transport = transport
        self.catalog = catalog

    # ------------------------------------------------------------------ #
    # Construction helpers
    # ------------------------------------------------------------------ #

    @classmethod
    def from_env(cls, **kwargs: Any) -> "KrakenClient":
        """Build a client from ``KRAKEN_*`` environment variables."""
        from .auth import client_from_env

        return client_from_env(**kwargs)

    # ------------------------------------------------------------------ #
    # Pair resolution
    # ------------------------------------------------------------------ #

    def _catalog(self) -> PairCatalog:
        if self.catalog is None:
            self.catalog = PairCatalog(transport=self.transport)
        return self.catalog

    def resolve_pair(self, pair: str, style: str = "pub") -> str:
        """Resolve any pair spelling to ``"pub"``/``"alt"``/``"ws"`` canonical."""
        return self._catalog().resolve(pair, style=style)

    # ------------------------------------------------------------------ #
    # Market data (public)
    # ------------------------------------------------------------------ #

    def server_time(self) -> dict[str, Any]:
        """Return the exchange server's time (UTC)."""
        return self.transport.public("Time")

    def assets(self, **params: Any) -> dict[str, Any]:
        """Return the asset catalog: asset code -> asset info dict."""
        return self.transport.public("Assets", params=params or None)

    def asset_pairs(self, **params: Any) -> dict[str, Any]:
        """Return the pair catalog: REST pair name -> pair info dict."""
        return self.transport.public("AssetPairs", params=params or None)

    def ticker(self, pair: str | list[str]) -> dict[str, Any]:
        """Return current ticker rows keyed by REST pair name.

        ``pair`` is one symbol or a list; any spelling is accepted.
        """
        pairs = [pair] if isinstance(pair, str) else list(pair)
        resolved = [self.resolve_pair(p, style="pub") for p in pairs]
        return self.transport.public("Ticker", params={"pair": ",".join(resolved)})

    def ohlc(
        self,
        pair: str,
        interval: int = 5,
        since: int | None = None,
    ) -> dict[str, Any]:
        """Return OHLC candles for a pair.

        The response is ``{"last": <ts>, "<REST_PAIR>": [[time, open, high,
        low, close, vwap, volume, count], ...]}``.  ``interval`` is minutes
        (1, 5, 15, 30, 60, 240, 1440, 10080, 21600).
        """
        params: dict[str, Any] = {"pair": self.resolve_pair(pair, style="pub"), "interval": interval}
        if since is not None:
            params["since"] = since
        return self.transport.public("OHLC", params=params)

    def depth(self, pair: str, count: int | None = None) -> dict[str, Any]:
        """Return the order book: ``{"<REST_PAIR>": {"asks": [...], "bids": [...]}}``."""
        params: dict[str, Any] = {"pair": self.resolve_pair(pair, style="pub")}
        if count is not None:
            params["count"] = count
        return self.transport.public("Depth", params=params)

    def trades(self, pair: str, since: int | None = None) -> dict[str, Any]:
        """Return recent trades for a pair plus ``last`` cursor."""
        params: dict[str, Any] = {"pair": self.resolve_pair(pair, style="pub")}
        if since is not None:
            params["since"] = since
        return self.transport.public("Trades", params=params)

    def spread(self, pair: str, since: int | None = None) -> dict[str, Any]:
        """Return recent spread (best bid/ask) rows plus ``last`` cursor."""
        params: dict[str, Any] = {"pair": self.resolve_pair(pair, style="pub")}
        if since is not None:
            params["since"] = since
        return self.transport.public("Spread", params=params)

    # ------------------------------------------------------------------ #
    # Account (private)
    # ------------------------------------------------------------------ #

    def balance(self) -> dict[str, str]:
        """Return all asset balances: asset code -> balance string."""
        return self.transport.private("Balance") or {}

    def trade_balance(self, asset: str | None = None) -> dict[str, Any]:
        """Return the account's trade balance summary (equity, margin, ...)."""
        data = {} if asset is None else {"asset": asset}
        return self.transport.private("TradeBalance", data) or {}

    def open_orders(
        self,
        trades: bool = False,
        userref: int | None = None,
    ) -> dict[str, Any]:
        """Return currently-open orders: ``{"open": {txid: order, ...}}``."""
        data: dict[str, Any] = {"trades": "true" if trades else "false"}
        if userref is not None:
            data["userref"] = userref
        return self.transport.private("OpenOrders", data)

    def closed_orders(
        self,
        trades: bool = False,
        userref: int | None = None,
        start: int | None = None,
        end: int | None = None,
        ofs: int | None = None,
        closetime: str | None = None,
    ) -> dict[str, Any]:
        """Return closed orders: ``{"closed": {txid: order, ...}, "count": N}``."""
        data: dict[str, Any] = {"trades": "true" if trades else "false"}
        if userref is not None:
            data["userref"] = userref
        if start is not None:
            data["start"] = start
        if end is not None:
            data["end"] = end
        if ofs is not None:
            data["ofs"] = ofs
        if closetime is not None:
            data["closetime"] = closetime
        return self.transport.private("ClosedOrders", data)

    def query_orders(self, txids: str | list[str]) -> dict[str, Any]:
        """Return order details for one or more transaction ids."""
        ids = [txids] if isinstance(txids, str) else list(txids)
        return self.transport.private("QueryOrders", {"txid": ",".join(ids)})

    def get_websockets_token(self) -> str:
        """Return a session token for the private WebSocket endpoint."""
        result = self.transport.private("GetWebSocketsToken")
        # Also cache the raw result (some callers want "expires" too).
        self._ws_token_result = result or {}
        return (result or {}).get("token", "")

    # ------------------------------------------------------------------ #
    # Orders (private)
    # ------------------------------------------------------------------ #

    def add_order(
        self,
        pair: str,
        side: str,
        ordertype: str,
        volume: str | float,
        price: str | float | None = None,
        price2: str | float | None = None,
        leverage: str | None = None,
        oflags: str | list[str] | None = None,
        starttm: str | None = None,
        expiretm: str | None = None,
        userref: int | None = None,
        validate: bool = False,
        close: dict[str, Any] | None = None,
        timeinforce: str | None = None,
    ) -> dict[str, Any]:
        """Submit an order and return ``{"txid": [...], "descr": {...}}``.

        ``pair`` may be any spelling (resolved to the private altname).
        ``side`` is ``"buy"``/``"sell"``, ``ordertype`` is one of Kraken's
        order types (``market``, ``limit``, ``stop-loss``, ``take-profit``,
        ``stop-loss-limit``, ...).  Validate a hypothetical order without
        placing it by passing ``validate=True`` (returns an empty txid).
        """
        altname = self.resolve_pair(pair, style="alt")
        data: dict[str, Any] = {
            "pair": altname,
            "type": side,
            "ordertype": ordertype,
            "volume": str(volume),
        }
        if price is not None:
            data["price"] = str(price)
        if price2 is not None:
            data["price2"] = str(price2)
        if leverage is not None:
            data["leverage"] = str(leverage)
        if oflags is not None:
            data["oflags"] = ",".join(oflags) if isinstance(oflags, list) else oflags
        if starttm is not None:
            data["starttm"] = starttm
        if expiretm is not None:
            data["expiretm"] = expiretm
        if userref is not None:
            data["userref"] = userref
        if validate:
            data["validate"] = "true"
        if close is not None:
            data["close"] = close
        if timeinforce is not None:
            data["timeinforce"] = timeinforce

        result = self.transport.private("AddOrder", data) or {}
        log_event(
            _USER_LOG,
            "order.add",
            pair=altname,
            side=side,
            ordertype=ordertype,
            volume=str(volume),
            txid=result.get("txid"),
            validate="true" in data.get("validate", ""),
        )
        return result

    def edit_order(
        self,
        txid: str,
        pair: str | None = None,
        price: str | float | None = None,
        price2: str | float | None = None,
        volume: str | float | None = None,
        oflags: str | list[str] | None = None,
        userref: int | None = None,
    ) -> dict[str, Any]:
        """Amend an existing order in place. Returns ``{"status": "ok", "txid": ...}``."""
        data: dict[str, Any] = {"txid": txid}
        if pair is not None:
            data["pair"] = self.resolve_pair(pair, style="alt")
        if price is not None:
            data["price"] = str(price)
        if price2 is not None:
            data["price2"] = str(price2)
        if volume is not None:
            data["volume"] = str(volume)
        if oflags is not None:
            data["oflags"] = ",".join(oflags) if isinstance(oflags, list) else oflags
        if userref is not None:
            data["userref"] = userref
        return self.transport.private("EditOrder", data) or {}

    def cancel_order(self, txid: str) -> dict[str, Any]:
        """Cancel one order. Returns ``{"count": N}`` (0 means already gone)."""
        result = self.transport.private("CancelOrder", {"txid": txid}) or {}
        log_event(_USER_LOG, "order.cancel", txid=txid, count=result.get("count"))
        return result

    def cancel_all(self) -> dict[str, Any]:
        """Cancel every open order. Returns ``{"count": N}``."""
        result = self.transport.private("CancelAll") or {}
        log_event(_USER_LOG, "order.cancel_all", count=result.get("count"))
        return result

    def cancel_all_orders_after(self, timeout_seconds: int) -> dict[str, Any]:
        """Schedule a blanket cancel 'x' seconds from now (dead-man switch)."""
        result = self.transport.private(
            "CancelAllOrdersAfter", {"timeout": timeout_seconds}
        ) or {}
        log_event(_USER_LOG, "order.cancel_all_after", timeout_seconds=timeout_seconds)
        return result

    # ------------------------------------------------------------------ #
    # History (private)
    # ------------------------------------------------------------------ #

    def trades_history(
        self,
        types: str | None = None,
        start: int | None = None,
        end: int | None = None,
        ofs: int | None = None,
    ) -> dict[str, Any]:
        """Return the user's trade history: ``{"trades": {txid: trade}, "count": N}``."""
        data: dict[str, Any] = {}
        if types is not None:
            data["type"] = types
        if start is not None:
            data["start"] = start
        if end is not None:
            data["end"] = end
        if ofs is not None:
            data["ofs"] = ofs
        return self.transport.private("TradesHistory", data) or {}

    def query_trades(self, txids: str | list[str]) -> dict[str, Any]:
        """Return trade details for one or more transaction ids."""
        ids = [txids] if isinstance(txids, str) else list(txids)
        return self.transport.private("QueryTrades", {"txid": ",".join(ids)})

    def ledgers(
        self,
        aclass: str | None = None,
        asset: str | None = None,
        types: str | None = None,
        start: int | None = None,
        end: int | None = None,
        ofs: int | None = None,
    ) -> dict[str, Any]:
        """Return the account ledger: ``{"ledger": {id: entry}, "count": N}``."""
        data: dict[str, Any] = {}
        if aclass is not None:
            data["aclass"] = aclass
        if asset is not None:
            data["asset"] = asset
        if types is not None:
            data["type"] = types
        if start is not None:
            data["start"] = start
        if end is not None:
            data["end"] = end
        if ofs is not None:
            data["ofs"] = ofs
        return self.transport.private("Ledgers", data) or {}

    def query_ledgers(self, ids: str | list[str]) -> dict[str, Any]:
        """Return ledger entries for one or more ids."""
        ids_list = [ids] if isinstance(ids, str) else list(ids)
        return self.transport.private("QueryLedgers", {"id": ",".join(ids_list)}) or {}


def find_errant_transaction(result: dict[str, Any]) -> None:
    """Raise :class:`OrderError` when a private mutation reports a refusal.

    Kraken replies ``{"error": [...]}`` for outright failures, but certain
    order endpoints return a result that still signals a rejected order
    (e.g. batch orders with per-item errors).  This helper normalises those
    into :class:`OrderError`.
    """
    errs = result.get("errors")
    if not errs:
        return
    raise OrderError(errs)