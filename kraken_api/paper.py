"""Paper trading: a simulated account over real market data.

This module turns the wrapper into a paper-trading engine that needs *no API
credentials*: public market data still comes from the real Kraken REST API
while every private endpoint (balances, orders, history, ledger) is served
from an in-memory simulated account.  Strategies written against
:class:`~kraken_api.manager.KrakenManager` run unchanged against the
simulation::

    from kraken_api import KrakenManager

    mgr = KrakenManager.paper()           # no KRAKEN_API_KEY needed
    txid = mgr.buy("BTC/USD", volume="0.01")["txid"][0]
    print(mgr.balances())                 # simulated balances

The simulation is wired in at the *transport* layer — the same duck-typed
seam the test suite already uses — so the client, the manager, the
extraction registry and the CLI all work on top of it without modification.

Fills
-----
Market orders fill immediately at the top of the real order book (taker
fee).  Limit orders rest in the simulated book and fill, at the limit price
(maker fee), when the market crosses them — checked lazily on every account
read via :meth:`PaperAccount.settle`, so a poll-driven strategy needs no
extra step.  ``KRAKEN_PAPER_PRICE=last`` switches the fill trigger to the
ticker's last price instead of best bid/ask.

State
-----
By default the account lives in memory and starts fresh each run.  Set
``KRAKEN_PAPER_STATE`` to a JSON path to persist balances, orders, trades
and the ledger between runs (saved after every mutation).
"""

from __future__ import annotations

import json
import logging
import os
import time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from .catalog import Pair, PairCatalog
from .client import KrakenClient
from .errors import APIError, InvalidPairError, OrderError
from .logging_config import log_event
from .transport import KrakenTransport
from .utils import as_decimal, now_ms, unix_to_iso

_PAPER_LOG = logging.getLogger("kraken_api.paper")

# Balance / money precision used throughout the simulation.  Real Kraken
# balances carry 8 decimals; keeping the same scale makes the sim output
# look like the exchange's.
_MONEY = Decimal("0.00000001")

# Env vars, mirroring the existing KRAKEN_* naming convention.
ENV_PAPER_BALANCE = "KRAKEN_PAPER_BALANCE"
ENV_PAPER_QUOTE = "KRAKEN_PAPER_QUOTE"
ENV_PAPER_FEE_TAKER = "KRAKEN_PAPER_FEE_TAKER"
ENV_PAPER_FEE_MAKER = "KRAKEN_PAPER_FEE_MAKER"
ENV_PAPER_PRICE = "KRAKEN_PAPER_PRICE"
ENV_PAPER_STATE = "KRAKEN_PAPER_STATE"

# RFC 3339 seconds for the ISO-8601 fields Kraken's responses carry.
_ISO_MS = "%Y-%m-%dT%H:%M:%S.%fZ"


def _quantize(value: Decimal) -> Decimal:
    """Round a Decimal to the sim's money precision (half-up)."""
    return value.quantize(_MONEY, rounding=ROUND_HALF_UP)


def _plain(value: Decimal) -> str:
    """Render a money Decimal as a plain decimal string (no exponents)."""
    return format(_quantize(value), "f")


def _now() -> int:
    """Current epoch seconds (the sim's ``opentm``/``closetm`` unit)."""
    return int(time.time())


def _first_str(values: Any) -> str | None:
    """Best ask/bid/last from a Kraken ticker tuple (``a``, ``b``, ``c``)."""
    if isinstance(values, (list, tuple)) and values:
        return str(values[0])
    if isinstance(values, str) and values:
        return values
    return None


# ---------------------------------------------------------------------- #
# PaperAccount
# ---------------------------------------------------------------------- #


class PaperAccount:
    """An in-memory simulated Kraken account.

    Balances are keyed by asset *altname* (``XBT``, ``USD``, ...).  Orders,
    trades and ledger entries use the same raw-string shapes the client and
    manager already parse, so the typed models work unchanged.

    Parameters
    ----------
    live:
        A real (credential-less) transport used for public market data and
        pair resolution.
    starting_balance:
        Opening balance of the quote asset (default ``"10000"``).
    quote_asset:
        Asset altname used as the account's quote currency (default ``USD``).
    fee_taker:
        Fee rate applied to market fills (default ``"0.0026"``).
    fee_maker:
        Fee rate applied to resting limit fills (default ``"0.0016"``).
    price_mode:
        ``"book"`` (fills trigger on best bid/ask) or ``"last"`` (ticker
        last price).  ``KRAKEN_PAPER_PRICE`` overrides from the environment.
    state_file:
        Optional JSON path to persist the account across runs
        (``KRAKEN_PAPER_STATE``).
    """

    def __init__(
        self,
        *,
        live: KrakenTransport,
        starting_balance: str | Decimal = "10000",
        quote_asset: str = "USD",
        fee_taker: str | Decimal = "0.0026",
        fee_maker: str | Decimal = "0.0016",
        price_mode: str = "book",
        state_file: str | Path | None = None,
    ) -> None:
        self.live = live
        self.catalog = PairCatalog(transport=live)
        self.quote_asset = quote_asset.strip().upper()
        self.fee_taker = as_decimal(fee_taker)
        self.fee_maker = as_decimal(fee_maker)
        self.price_mode = (price_mode or "book").lower()
        if self.price_mode not in ("book", "last"):
            raise ValueError(f"price_mode must be 'book' or 'last', got {self.price_mode!r}")
        self.state_file = Path(state_file) if state_file else None

        # Simulation state.
        self.balances: dict[str, Decimal] = {}
        self.open_orders: dict[str, dict[str, Any]] = {}
        self.closed_orders: dict[str, dict[str, Any]] = {}
        self.trades: dict[str, dict[str, Any]] = {}
        self.ledger: dict[str, dict[str, Any]] = {}
        self._txid_counter = 0
        self._deadline: float | None = None
        self._price_cache: dict[str, tuple[float, dict[str, Decimal] | None]] = {}
        self.price_cache_ttl = 5.0  # seconds; keeps settle() cheap in poll loops
        self._starting_balance = as_decimal(starting_balance)

        self._init_state(starting_balance)

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #

    @classmethod
    def from_env(cls, live: KrakenTransport, **kwargs: Any) -> "PaperAccount":
        """Build a paper account from ``KRAKEN_PAPER_*`` environment vars.

        Explicit ``kwargs`` win over the environment.
        """
        env = os.environ
        defaults: dict[str, Any] = {
            "starting_balance": env.get(ENV_PAPER_BALANCE, "10000"),
            "quote_asset": env.get(ENV_PAPER_QUOTE, "USD"),
            "fee_taker": env.get(ENV_PAPER_FEE_TAKER, "0.0026"),
            "fee_maker": env.get(ENV_PAPER_FEE_MAKER, "0.0016"),
            "price_mode": env.get(ENV_PAPER_PRICE, "book"),
            "state_file": env.get(ENV_PAPER_STATE) or None,
        }
        defaults.update(kwargs)
        return cls(live=live, **defaults)

    def _init_state(self, starting_balance: str | Decimal) -> None:
        if self.state_file and self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                self.load_state(data)
                log_event(
                    _PAPER_LOG,
                    "paper.state.load",
                    path=str(self.state_file),
                    balances={a: _plain(v) for a, v in self.balances.items() if v},
                    open_orders=len(self.open_orders),
                    trades=len(self.trades),
                )
                return
            except (OSError, ValueError, TypeError) as exc:
                log_event(
                    _PAPER_LOG,
                    "paper.state.load_failed",
                    path=str(self.state_file),
                    reason=str(exc),
                    level="WARNING",
                )
        self.balances = {self.quote_asset: as_decimal(starting_balance)}
        log_event(
            _PAPER_LOG,
            "paper.init",
            starting_balance=_plain(self.balances[self.quote_asset]),
            quote_asset=self.quote_asset,
            fee_taker=str(self.fee_taker),
            fee_maker=str(self.fee_maker),
            price_mode=self.price_mode,
            state_file=str(self.state_file) if self.state_file else None,
            live_base_url=self.live.base_url,
        )

    # ------------------------------------------------------------------ #
    # Dispatcher (mirrors the private REST surface)
    # ------------------------------------------------------------------ #

    _HANDLERS: dict[str, str] = {
        "Balance": "_h_balance",
        "TradeBalance": "_h_trade_balance",
        "OpenOrders": "_h_open_orders",
        "ClosedOrders": "_h_closed_orders",
        "QueryOrders": "_h_query_orders",
        "AddOrder": "_h_add_order",
        "EditOrder": "_h_edit_order",
        "CancelOrder": "_h_cancel_order",
        "CancelAll": "_h_cancel_all",
        "CancelAllOrdersAfter": "_h_cancel_all_after",
        "TradesHistory": "_h_trades_history",
        "QueryTrades": "_h_query_trades",
        "Ledgers": "_h_ledgers",
        "QueryLedgers": "_h_query_ledgers",
        "GetWebSocketsToken": "_h_ws_token",
    }

    def handle(self, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Serve a private endpoint path from the simulated account."""
        method = self._HANDLERS.get(path)
        if method is None:
            raise APIError(["unknown endpoint"], endpoint=f"/0/private/{path}")
        return getattr(self, method)(data or {})

    # ------------------------------------------------------------------ #
    # Public helpers
    # ------------------------------------------------------------------ #

    def balance(self, asset: str) -> Decimal:
        """Return one asset's simulated balance (0 when absent)."""
        return self.balances.get(asset.strip().upper(), Decimal("0"))

    def settle(self) -> int:
        """Advance the simulation: fill resting orders the market has crossed.

        Called lazily before every account read, so a strategy that polls
        ``balances()``/``open_orders()`` never has to settle by hand.
        Returns how many orders filled.
        """
        self._check_dead_man()
        resting = [
            (txid, rec)
            for txid, rec in self.open_orders.items()
            if rec.get("status") == "open"
        ]
        if not resting:
            return 0

        filled: list[str] = []
        for txid, rec in resting:
            fill_price = self._crossed_price(rec)
            if fill_price is not None:
                self._apply_fill(rec, fill_price, "maker")
                filled.append(txid)

        log_event(
            _PAPER_LOG,
            "paper.settle",
            resting_count=len(resting),
            filled_count=len(filled),
            filled_txids=filled,
            price_mode=self.price_mode,
            level="DEBUG",
        )
        if filled:
            self._maybe_save()
        return len(filled)

    def reset(self) -> None:
        """Reset the account to a fresh simulated account.

        Keeps the configured starting balance and re-seeds the quote asset.
        """
        self.balances = {self.quote_asset: as_decimal(self._starting_balance)}
        self.open_orders.clear()
        self.closed_orders.clear()
        self.trades.clear()
        self.ledger.clear()
        self._txid_counter = 0
        self._deadline = None
        self._price_cache.clear()
        log_event(_PAPER_LOG, "paper.reset", starting_balance=_plain(self.balances[self.quote_asset]))

    # ------------------------------------------------------------------ #
    # State persistence
    # ------------------------------------------------------------------ #

    def to_state(self) -> dict[str, Any]:
        """Serialize the account to a JSON-ready dict."""
        return {
            "version": 1,
            "balances": {a: _plain(v) for a, v in self.balances.items() if v},
            "open_orders": self.open_orders,
            "closed_orders": self.closed_orders,
            "trades": self.trades,
            "ledger": self.ledger,
            "txid": self._txid_counter,
            "deadline": self._deadline,
        }

    def load_state(self, data: dict[str, Any]) -> None:
        """Restore account state from a :meth:`to_state` dict."""
        self.balances = {
            str(a).upper(): as_decimal(v)
            for a, v in (data.get("balances") or {}).items()
        }
        self.open_orders = dict(data.get("open_orders") or {})
        self.closed_orders = dict(data.get("closed_orders") or {})
        self.trades = dict(data.get("trades") or {})
        self.ledger = dict(data.get("ledger") or {})
        self._txid_counter = int(data.get("txid") or 0)
        self._deadline = data.get("deadline")

    def _maybe_save(self) -> None:
        if self.state_file is None:
            return
        tmp = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_state(), indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.state_file)
        log_event(
            _PAPER_LOG,
            "paper.state.save",
            path=str(self.state_file),
            balances={a: _plain(v) for a, v in self.balances.items() if v},
            open_orders=len(self.open_orders),
            closed_orders=len(self.closed_orders),
            trades=len(self.trades),
            level="DEBUG",
        )

    # ------------------------------------------------------------------ #
    # Market prices
    # ------------------------------------------------------------------ #

    def _fetch_mark(self, pair: Pair) -> dict[str, Decimal] | None:
        """Return ``{"ask", "bid", "last"}`` market prices for a pair.

        Prices come from the real public Ticker endpoint (its ``a``/``b``
        fields are the top-of-book ask/bid), falling back to ``Depth`` when
        Ticker fails.  Results are cached for :attr:`price_cache_ttl`.
        """
        key = pair.pub
        now = time.monotonic()
        cached = self._price_cache.get(key)
        if cached is not None and (now - cached[0]) < self.price_cache_ttl:
            return cached[1]

        mark: dict[str, Decimal] | None = None
        source = "ticker"
        try:
            row = self.live.public("Ticker", params={"pair": key})
            data = (row or {}).get(key) or {}
            ask = _first_str(data.get("a"))
            bid = _first_str(data.get("b"))
            last = _first_str(data.get("c"))
            mark = {
                "ask": as_decimal(ask) if ask else None,
                "bid": as_decimal(bid) if bid else None,
                "last": as_decimal(last) if last else None,
            }
            if not any(mark.values()):
                mark = None
        except Exception as exc:  # noqa: BLE001 - fall back, then report
            source = "depth"
            try:
                row = self.live.public("Depth", params={"pair": key, "count": 1})
                data = (row or {}).get(key) or {}
                asks = data.get("asks") or []
                bids = data.get("bids") or []
                mark = {
                    "ask": as_decimal(asks[0][0]) if asks else None,
                    "bid": as_decimal(bids[0][0]) if bids else None,
                    "last": None,
                }
            except Exception as exc2:  # noqa: BLE001
                log_event(
                    _PAPER_LOG,
                    "paper.error",
                    reason="price_unavailable",
                    pair=key,
                    error=f"{exc}; {exc2}",
                    level="ERROR",
                )
                mark = None

        self._price_cache[key] = (now, mark)
        if mark is not None:
            log_event(
                _PAPER_LOG,
                "paper.price",
                pair=key,
                source=source,
                mode=self.price_mode,
                best_bid=_plain(mark["bid"]) if mark["bid"] is not None else None,
                best_ask=_plain(mark["ask"]) if mark["ask"] is not None else None,
                last=_plain(mark["last"]) if mark["last"] is not None else None,
                level="DEBUG",
            )
        return mark

    def _market_price(self, pair: Pair, side: str) -> Decimal:
        """Price an immediate fill: best ask for buys, best bid for sells.

        In ``last`` mode (or when the book side is unavailable) the ticker's
        last price is used.
        """
        mark = self._fetch_mark(pair)
        if mark is None:
            raise OrderError(f"no market price available for {pair.ws}")
        if self.price_mode == "last" and mark["last"] is not None:
            return mark["last"]
        if side == "buy":
            return mark["ask"] if mark["ask"] is not None else mark["last"]
        return mark["bid"] if mark["bid"] is not None else mark["last"]

    def _crossed_price(self, order: dict[str, Any]) -> Decimal | None:
        """Fill price for a resting order the market has crossed, else None."""
        try:
            pair = self.catalog.pair(order["pair"])
        except InvalidPairError:
            log_event(
                _PAPER_LOG,
                "paper.reject",
                reason="unknown_pair",
                txid=order.get("txid"),
                pair=order.get("pair"),
                level="WARNING",
            )
            return None
        mark = self._fetch_mark(pair)
        if mark is None:
            return None
        limit = as_decimal(order["price"])
        side = order["side"]
        if self.price_mode == "last":
            last = mark["last"]
            if last is None:
                return None
            if side == "buy" and last <= limit:
                return limit
            if side == "sell" and last >= limit:
                return limit
        else:
            if side == "buy":
                ask = mark["ask"]
                if ask is not None and ask <= limit:
                    return limit
            else:
                bid = mark["bid"]
                if bid is not None and bid >= limit:
                    return limit
        return None

    def _find_quote_pair(self, asset: str) -> Pair | None:
        """Find a known pair that quotes ``asset`` against the quote currency."""
        for pair in self.catalog.known_pairs():
            if pair.base == asset and pair.quote == self.quote_asset:
                return pair
        return None

    def _equity(self) -> Decimal:
        """Best-effort equity: quote balance plus holdings valued at market."""
        total = self.balances.get(self.quote_asset, Decimal("0"))
        for asset, amount in self.balances.items():
            if asset == self.quote_asset or not amount:
                continue
            pair = self._find_quote_pair(asset)
            mark = self._fetch_mark(pair) if pair is not None else None
            price = (mark or {}).get("last")
            if price is not None:
                total += amount * price
        return total

    # ------------------------------------------------------------------ #
    # Dead-man's switch
    # ------------------------------------------------------------------ #

    def _check_dead_man(self) -> None:
        if self._deadline is None:
            return
        if time.time() < self._deadline:
            return
        self._deadline = None
        count = self._cancel_all_internal("triggered")
        log_event(
            _PAPER_LOG,
            "paper.order.cancel_all",
            reason="dead_man_switch",
            count=count,
        )
        self._maybe_save()

    # ------------------------------------------------------------------ #
    # Endpoint handlers
    # ------------------------------------------------------------------ #

    def _h_balance(self, data: dict[str, Any]) -> dict[str, str]:
        self.settle()
        return {
            asset: _plain(amount)
            for asset, amount in self.balances.items()
            if amount
        }

    def _h_trade_balance(self, data: dict[str, Any]) -> dict[str, Any]:
        self.settle()
        requested = (data.get("asset") or "").upper()
        total = self.balances.get(requested) if requested else None
        if total is None:
            total = self._equity()
        return {
            "eb": _plain(total),
            "tb": _plain(total),
            "m": "",
            "n": "",
            "c": "",
            "v": "",
            "e": _plain(self._equity()),
            "mf": _plain(self.balances.get(self.quote_asset, Decimal("0"))),
        }

    def _h_open_orders(self, data: dict[str, Any]) -> dict[str, Any]:
        self.settle()
        return {"open": {txid: rec for txid, rec in self.open_orders.items() if rec.get("status") == "open"}}

    def _h_closed_orders(self, data: dict[str, Any]) -> dict[str, Any]:
        self.settle()
        return {"closed": dict(self.closed_orders), "count": len(self.closed_orders)}

    def _h_query_orders(self, data: dict[str, Any]) -> dict[str, Any]:
        self.settle()
        txids = [t.strip() for t in (data.get("txid") or "").split(",") if t.strip()]
        out: dict[str, Any] = {}
        for txid in txids:
            rec = self.open_orders.get(txid) or self.closed_orders.get(txid)
            if rec is not None:
                out[txid] = rec
        return out

    def _h_add_order(self, data: dict[str, Any]) -> dict[str, Any]:
        pair_ref = data.get("pair") or ""
        side = data.get("type") or ""
        ordertype = data.get("ordertype") or ""
        volume = data.get("volume")
        price = data.get("price")
        price2 = data.get("price2")
        userref = data.get("userref")
        validate = data.get("validate") == "true"

        try:
            pair = self.catalog.pair(pair_ref)
        except InvalidPairError:
            log_event(
                _PAPER_LOG,
                "paper.reject",
                reason="unknown_pair",
                pair=pair_ref,
                level="WARNING",
            )
            raise

        if side not in ("buy", "sell"):
            raise OrderError(f"side must be 'buy' or 'sell', got {side!r}")
        try:
            vol = as_decimal(volume)
        except ValueError as exc:
            raise OrderError(f"invalid volume {volume!r}") from exc
        if vol <= 0:
            raise OrderError(f"volume must be positive, got {_plain(vol)}")

        immediate = ordertype == "market"
        limit_price = as_decimal(price) if price not in (None, "") else None

        if immediate:
            fill_price = self._market_price(pair, side)
        else:
            fill_price = None

        # Balance validation up front (works for validate=True too).
        if side == "buy":
            need = (fill_price or limit_price or Decimal("0")) * vol
            need += need * (self.fee_taker if immediate else self.fee_maker)
            if self.balance(self.quote_asset) < need:
                log_event(
                    _PAPER_LOG,
                    "paper.reject",
                    reason="insufficient_quote",
                    side=side,
                    pair=pair.alt,
                    volume=_plain(vol),
                    quote_balance=_plain(self.balance(self.quote_asset)),
                    needed=_plain(need),
                    level="WARNING",
                )
                raise OrderError(
                    f"insufficient {self.quote_asset} balance: need {_plain(need)}, "
                    f"have {_plain(self.balance(self.quote_asset))}"
                )
        else:
            if self.balance(pair.base) < vol:
                log_event(
                    _PAPER_LOG,
                    "paper.reject",
                    reason="insufficient_base",
                    side=side,
                    pair=pair.alt,
                    volume=_plain(vol),
                    base_balance=_plain(self.balance(pair.base)),
                    level="WARNING",
                )
                raise OrderError(
                    f"insufficient {pair.base} balance: need {_plain(vol)}, "
                    f"have {_plain(self.balance(pair.base))}"
                )

        txid = self._new_txid()
        rec = self._order_record(
            txid=txid,
            pair=pair,
            side=side,
            ordertype=ordertype,
            volume=vol,
            price=fill_price if immediate else limit_price,
            price2=as_decimal(price2) if price2 not in (None, "") else None,
            userref=userref,
            status="closed" if immediate else "open",
        )
        descr = rec["descr"]["order"]

        if validate:
            log_event(
                _PAPER_LOG,
                "paper.order.validate",
                order=descr,
                result="ok",
            )
            return {"txid": [], "descr": {"order": descr}}

        if immediate:
            self.open_orders[txid] = rec
            self._apply_fill(rec, fill_price, "taker")
            self.open_orders.pop(txid, None)
        else:
            self.open_orders[txid] = rec
            log_event(
                _PAPER_LOG,
                "paper.order.rest",
                txid=txid,
                pair=pair.alt,
                side=side,
                ordertype=ordertype,
                volume=_plain(vol),
                price=_plain(limit_price) if limit_price is not None else None,
            )

        self._maybe_save()
        log_event(
            _PAPER_LOG,
            "paper.order.place",
            txid=txid,
            pair=pair.alt,
            side=side,
            ordertype=ordertype,
            volume=_plain(vol),
            price=_plain(limit_price) if limit_price is not None else None,
            filled_immediately=immediate,
            validate=validate,
            level="DEBUG",
        )
        return {"txid": [txid] if not validate else [], "descr": {"order": descr}}

    def _h_edit_order(self, data: dict[str, Any]) -> dict[str, Any]:
        txid = data.get("txid") or ""
        rec = self.open_orders.get(txid)
        if rec is None or rec.get("status") != "open":
            log_event(
                _PAPER_LOG,
                "paper.reject",
                reason="order_not_open",
                txid=txid,
                level="WARNING",
            )
            raise OrderError(f"order {txid!r} is not open")
        old_price = rec["descr"].get("price") or rec.get("price")
        old_vol = rec.get("vol")
        if "price" in data and data["price"] not in (None, ""):
            new_price = _plain(as_decimal(data["price"]))
            rec["price"] = new_price
            rec["descr"]["price"] = new_price
        if "volume" in data and data["volume"] not in (None, ""):
            new_vol = _plain(as_decimal(data["volume"]))
            rec["vol"] = new_vol
        log_event(
            _PAPER_LOG,
            "paper.order.edit",
            txid=txid,
            old_price=old_price,
            new_price=rec["descr"].get("price"),
            old_volume=old_vol,
            new_volume=rec.get("vol"),
        )
        self._maybe_save()
        return {"status": "ok", "txid": txid}

    def _h_cancel_order(self, data: dict[str, Any]) -> dict[str, Any]:
        txid = data.get("txid") or ""
        rec = self.open_orders.pop(txid, None)
        if rec is None:
            log_event(_PAPER_LOG, "paper.order.cancel", txid=txid, count=0)
            return {"count": 0}
        rec["status"] = "canceled"
        rec["closetm"] = _now()
        self.closed_orders[txid] = rec
        log_event(_PAPER_LOG, "paper.order.cancel", txid=txid, count=1)
        self._maybe_save()
        return {"count": 1}

    def _h_cancel_all(self, data: dict[str, Any]) -> dict[str, Any]:
        count = self._cancel_all_internal("canceled")
        log_event(_PAPER_LOG, "paper.order.cancel_all", count=count)
        self._maybe_save()
        return {"count": count}

    def _h_cancel_all_after(self, data: dict[str, Any]) -> dict[str, Any]:
        timeout = max(0, int(data.get("timeout") or 0))
        self._deadline = time.time() + timeout
        trigger = unix_to_iso(int(self._deadline))
        result = {
            "currentTime": unix_to_iso(_now()),
            "triggerTime": trigger,
            "cancelled": len(self.open_orders),
        }
        log_event(
            _PAPER_LOG,
            "paper.order.cancel_all_after",
            timeout_seconds=timeout,
            trigger_time=trigger,
            open_orders=len(self.open_orders),
        )
        return result

    def _h_trades_history(self, data: dict[str, Any]) -> dict[str, Any]:
        self.settle()
        return {"trades": dict(self.trades), "count": len(self.trades)}

    def _h_query_trades(self, data: dict[str, Any]) -> dict[str, Any]:
        self.settle()
        txids = [t.strip() for t in (data.get("txid") or "").split(",") if t.strip()]
        return {txid: self.trades[txid] for txid in txids if txid in self.trades}

    def _h_ledgers(self, data: dict[str, Any]) -> dict[str, Any]:
        self.settle()
        return {"ledger": dict(self.ledger), "count": len(self.ledger)}

    def _h_query_ledgers(self, data: dict[str, Any]) -> dict[str, Any]:
        self.settle()
        ids = [i.strip() for i in (data.get("id") or "").split(",") if i.strip()]
        return {lid: self.ledger[lid] for lid in ids if lid in self.ledger}

    def _h_ws_token(self, data: dict[str, Any]) -> dict[str, Any]:
        return {"token": "paper-token", "expires": 900}

    # ------------------------------------------------------------------ #
    # Internal mechanics
    # ------------------------------------------------------------------ #

    def _new_txid(self) -> str:
        self._txid_counter += 1
        return f"PAPER-{self._txid_counter:07d}"

    def _order_record(
        self,
        *,
        txid: str,
        pair: Pair,
        side: str,
        ordertype: str,
        volume: Decimal,
        price: Decimal | None,
        price2: Decimal | None,
        userref: Any,
        status: str,
    ) -> dict[str, Any]:
        price_s = _plain(price) if price is not None else ""
        price2_s = _plain(price2) if price2 is not None else ""
        vol_s = _plain(volume)
        descr = (
            f"{side} {vol_s} {pair.alt} @ {ordertype}"
            + (f" {price_s}" if price_s else "")
        )
        return {
            "txid": txid,
            "status": status,
            "pair": pair.alt,
            "side": side,
            "price": price_s,
            "price2": price2_s,
            "descr": {
                "pair": pair.alt,
                "type": side,
                "ordertype": ordertype,
                "price": price_s,
                "price2": price2_s,
                "leverage": "",
                "order": descr,
            },
            "vol": vol_s,
            "vol_exec": "0.00000000",
            "cost": "0.00000000",
            "fee": "0.00000000",
            "opentm": _now(),
            "closetm": 0,
            "userref": int(userref) if userref not in (None, "") else None,
            "refid": "",
            "trades": [],
            "close": {},
        }

    def _apply_fill(self, order: dict[str, Any], price: Decimal, fill_type: str) -> None:
        """Fill ``order`` at ``price``; ``fill_type`` is ``"taker"``/``"maker"``."""
        txid = order["txid"]
        side = order["side"]
        pair = self.catalog.pair(order["pair"])
        vol = as_decimal(order.get("vol"))
        rate = self.fee_taker if fill_type == "taker" else self.fee_maker
        gross = vol * price
        fee = gross * rate

        base_before = self.balance(pair.base)
        quote_before = self.balance(self.quote_asset)

        if side == "buy":
            self.balances[self.quote_asset] = quote_before - (gross + fee)
            self.balances[pair.base] = base_before + vol
        else:
            self.balances[pair.base] = base_before - vol
            self.balances[self.quote_asset] = quote_before + (gross - fee)

        # Order record -> closed, with realized fields.
        order.update(
            {
                "status": "closed",
                "vol_exec": _plain(vol),
                "cost": _plain(gross),
                "fee": _plain(fee),
                "closetm": _now(),
                "trades": [],
            }
        )
        self.open_orders.pop(txid, None)
        self.closed_orders[txid] = order

        # Trade + ledger entries.
        trade_txid = self._new_txid()
        trade = {
            "txid": trade_txid,
            "pair": order["pair"],
            "type": side,
            "ordertype": order["descr"].get("ordertype", ""),
            "price": _plain(price),
            "vol": _plain(vol),
            "cost": _plain(gross),
            "fee": _plain(fee),
            "time": _now(),
            "ordertxid": txid,
            "margin": "",
        }
        self.trades[trade_txid] = trade
        order["trades"] = [trade_txid]

        # Two ledger rows per fill, mirroring Kraken's per-asset trade rows.
        # A buy credits the base asset and debits the quote (+ fee on the
        # quote row); a sell is the mirror image.
        if side == "buy":
            self._ledger_entry(trade_txid, pair.base, vol, fee=Decimal("0"))
            self._ledger_entry(trade_txid, self.quote_asset, -(gross + fee), fee=fee)
        else:
            self._ledger_entry(trade_txid, pair.base, -vol, fee=Decimal("0"))
            self._ledger_entry(trade_txid, self.quote_asset, gross - fee, fee=fee)

        log_event(
            _PAPER_LOG,
            "paper.order.fill",
            txid=txid,
            trade_txid=trade_txid,
            pair=order["pair"],
            side=side,
            ordertype=order["descr"].get("ordertype", ""),
            fill_price=_plain(price),
            volume=_plain(vol),
            gross=_plain(gross),
            fee=_plain(fee),
            fee_rate=str(rate),
            fee_type=fill_type,
            base_after=_plain(self.balance(pair.base)),
            quote_after=_plain(self.balance(self.quote_asset)),
            base_before=_plain(base_before),
            quote_before=_plain(quote_before),
        )
        log_event(
            _PAPER_LOG,
            "paper.balance",
            asset=pair.base,
            before=_plain(base_before),
            after=_plain(self.balance(pair.base)),
            delta=_plain(self.balance(pair.base) - base_before),
            level="DEBUG",
        )
        log_event(
            _PAPER_LOG,
            "paper.balance",
            asset=self.quote_asset,
            before=_plain(quote_before),
            after=_plain(self.balance(self.quote_asset)),
            delta=_plain(self.balance(self.quote_asset) - quote_before),
            level="DEBUG",
        )

    def _ledger_entry(
        self,
        refid: str,
        asset: str,
        amount: Decimal,
        *,
        fee: Decimal,
    ) -> None:
        lid = f"L{now_ms()}-{len(self.ledger) + 1}"
        self.ledger[lid] = {
            "refid": refid,
            "type": "trade",
            "asset": asset,
            "amount": _plain(amount),
            "fee": _plain(fee),
            "balance": _plain(self.balance(asset)),
            "time": _now(),
        }

    def _cancel_all_internal(self, status: str) -> int:
        """Move every open order to ``closed_orders`` with ``status``."""
        now = _now()
        count = 0
        for txid, rec in list(self.open_orders.items()):
            rec["status"] = status
            rec["closetm"] = now
            self.closed_orders[txid] = rec
            count += 1
        self.open_orders.clear()
        return count


# ---------------------------------------------------------------------- #
# PaperTransport
# ---------------------------------------------------------------------- #


class PaperTransport:
    """Duck-typed transport that fakes every private endpoint.

    Public calls pass through to a real (credential-less)
    :class:`KrakenTransport`; private calls are served by a
    :class:`PaperAccount` after a lazy settle.  This is what makes the whole
    client/manager/export stack work unchanged in paper mode.
    """

    def __init__(self, live: KrakenTransport, account: PaperAccount) -> None:
        self.live = live
        self.account = account

    # The client resolves pairs through the live catalog, so the manager
    # must not confuse the paper transport with a real one.
    @property
    def base_url(self) -> str:
        return self.live.base_url

    def public(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Real Kraken market data, passed through unchanged."""
        return self.live.public(path, params=params)

    def private(self, path: str, data: dict[str, Any] | None = None) -> Any:
        """Simulated account response, after a lazy settle."""
        self.account.settle()
        return self.account.handle(path, data)


# ---------------------------------------------------------------------- #
# Wiring
# ---------------------------------------------------------------------- #


def paper_client(**kwargs: Any) -> KrakenClient:
    """Build a :class:`KrakenClient` wired to the paper simulation.

    Market data comes from the real (public, unauthenticated) API; account
    and order state is simulated.  No ``KRAKEN_API_KEY`` is required.
    ``kwargs`` are forwarded to :meth:`PaperAccount.from_env`.
    """
    from .auth import client_from_credentials

    live = client_from_credentials("", "").transport
    account = PaperAccount.from_env(live=live, **kwargs)
    return KrakenClient(PaperTransport(live=live, account=account))