"""Typed views of the objects the Kraken API returns.

The Kraken API returns big nested JSON where prices/volumes are *strings*
(deliberately — no float rounding in trading).  These dataclasses expose the
fields trading code actually uses, keep the raw strings so no precision is
lost, and add convenience accessors (``Decimal`` helpers, ``datetime``
conversions, ``to_dict()``).  ``from_kraken`` classmethods convert raw API
dicts; the classes are plain so callers can construct them freely in tests
and offline tooling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .utils import as_decimal, unix_to_iso


def _fmt_ts(unix: object, ms: bool = False) -> str:
    try:
        return unix_to_iso(unix, ms=ms)
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


@dataclass(slots=True)
class ServerTime:
    """The exchange server's current UTC time."""

    unixtime: int
    rfc1123: str = ""

    @classmethod
    def from_kraken(cls, data: dict[str, Any]) -> "ServerTime":
        return cls(
            unixtime=int(data.get("unixtime") or 0),
            rfc1123=data.get("rfc1123") or "",
        )

    @property
    def iso(self) -> str:
        return _fmt_ts(self.unixtime)

    def to_dict(self) -> dict[str, Any]:
        return {"unixtime": self.unixtime, "rfc1123": self.rfc1123, "iso": self.iso}

    def __str__(self) -> str:
        return self.iso or self.rfc1123


@dataclass(slots=True)
class Ticker:
    """24-hour stats for one market.

    All price fields are the raw strings from the API; use ``decimal()`` to
    get a :class:`decimal.Decimal`.  ``(today, last_24h)`` tuples mirror the
    API's two-window structure.
    """

    pair: str
    ask: list[str] = field(default_factory=list)      # a: [price, whole lot vol, lot vol]
    bid: list[str] = field(default_factory=list)      # b
    last: list[str] = field(default_factory=list)     # c: [price, lot vol]
    volume: list[str] = field(default_factory=list)   # v: [today, 24h]
    vwap: list[str] = field(default_factory=list)     # p
    trade_count: list[int] = field(default_factory=list)  # t
    low: list[str] = field(default_factory=list)      # l
    high: list[str] = field(default_factory=list)     # h
    open_price: list[str] = field(default_factory=list)  # o

    @classmethod
    def from_kraken(cls, pair: str, data: dict[str, Any]) -> "Ticker":
        def first(key: str) -> list[str]:
            value = data.get(key) or []
            if isinstance(value, (list, tuple)):
                return [str(v) for v in value]
            return [str(value)]

        def ints(key: str) -> list[int]:
            value = data.get(key) or []
            if isinstance(value, (list, tuple)):
                return [int(v) for v in value]
            return [int(value)]

        return cls(
            pair=pair,
            ask=first("a"),
            bid=first("b"),
            last=first("c"),
            volume=first("v"),
            vwap=first("p"),
            trade_count=ints("t"),
            low=first("l"),
            high=first("h"),
            open_price=first("o"),
        )

    @property
    def last_price(self) -> Decimal:
        return as_decimal(self.last[0]) if self.last else Decimal("0")

    def decimal(self, key: str) -> Decimal | None:
        """Best-effort Decimal for a single-value field (``ask``, ``bid``, ...)."""
        value = getattr(self, key, None)
        if isinstance(value, list) and value:
            return as_decimal(value[0])
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair": self.pair,
            "ask": self.ask,
            "bid": self.bid,
            "last": self.last,
            "volume": self.volume,
            "vwap": self.vwap,
            "trade_count": self.trade_count,
            "low": self.low,
            "high": self.high,
            "open": self.open_price,
        }

    def __str__(self) -> str:
        last = self.last[0] if self.last else "?"
        vol = self.volume[1] if len(self.volume) > 1 else "?"
        return f"{self.pair} last={last} 24hvol={vol}"


@dataclass(slots=True)
class Candle:
    """One OHLC candle.  Time is epoch seconds; prices are raw strings."""

    pair: str
    time: int
    open: str
    high: str
    low: str
    close: str
    vwap: str = ""
    volume: str = ""
    count: int = 0

    @classmethod
    def from_kraken(cls, pair: str, row: list[Any]) -> "Candle":
        # row: [time, open, high, low, close, vwap, volume, count]
        vals = list(row) + [""] * (8 - len(row))
        return cls(
            pair=pair,
            time=int(vals[0]),
            open=str(vals[1]),
            high=str(vals[2]),
            low=str(vals[3]),
            close=str(vals[4]),
            vwap=str(vals[5] or ""),
            volume=str(vals[6] or ""),
            count=int(vals[7] or 0),
        )

    @property
    def iso(self) -> str:
        return _fmt_ts(self.time)

    @property
    def close_decimal(self) -> Decimal:
        return as_decimal(self.close)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair": self.pair,
            "time": self.time,
            "iso": self.iso,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "vwap": self.vwap,
            "volume": self.volume,
            "count": self.count,
        }

    def __str__(self) -> str:
        return f"{self.pair} {self.iso} O={self.open} C={self.close} V={self.volume}"


@dataclass(slots=True)
class BookLevel:
    """One price level in an order book (price and volume are strings)."""

    price: str
    volume: str
    timestamp: int = 0  # seconds since epoch; 0 when the API omits it

    @classmethod
    def from_row(cls, row: list[Any]) -> "BookLevel":
        vals = list(row) + [0, 0]
        return cls(price=str(vals[0]), volume=str(vals[1]), timestamp=int(vals[2] or 0))

    @property
    def price_decimal(self) -> Decimal:
        return as_decimal(self.price)

    @property
    def volume_decimal(self) -> Decimal:
        return as_decimal(self.volume)

    def to_dict(self) -> dict[str, Any]:
        return {"price": self.price, "volume": self.volume, "timestamp": self.timestamp}


@dataclass(slots=True)
class OrderBook:
    """A full (or partial) order book snapshot."""

    pair: str
    asks: list[BookLevel] = field(default_factory=list)
    bids: list[BookLevel] = field(default_factory=list)

    @classmethod
    def from_kraken(cls, pair: str, data: dict[str, Any]) -> "OrderBook":
        return cls(
            pair=pair,
            asks=[BookLevel.from_row(r) for r in (data.get("asks") or [])],
            bids=[BookLevel.from_row(r) for r in (data.get("bids") or [])],
        )

    def best_bid(self) -> Decimal | None:
        return self.bids[0].price_decimal if self.bids else None

    def best_ask(self) -> Decimal | None:
        return self.asks[0].price_decimal if self.asks else None

    def spread(self) -> Decimal | None:
        if not self.asks or not self.bids:
            return None
        return self.asks[0].price_decimal - self.bids[0].price_decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair": self.pair,
            "asks": [lvl.to_dict() for lvl in self.asks],
            "bids": [lvl.to_dict() for lvl in self.bids],
        }

    def __str__(self) -> str:
        return (
            f"{self.pair} {len(self.bids)} bids / {len(self.asks)} asks"
            f"  (spread {self.spread()})"
        )


@dataclass(slots=True)
class Balance:
    """One asset's balance on the account."""

    asset: str       # Kraken asset code (XXBT, ZUSD, ...)
    amount: str      # raw string balance

    @classmethod
    def from_entry(cls, asset: str, amount: str) -> "Balance":
        return cls(asset=asset, amount=str(amount))

    @property
    def decimal(self) -> Decimal:
        return as_decimal(self.amount)

    def to_dict(self) -> dict[str, Any]:
        return {"asset": self.asset, "amount": self.amount}

    def __str__(self) -> str:
        return f"{self.amount} {self.asset}"


@dataclass(slots=True)
class Order:
    """A spot order as returned by OpenOrders/ClosedOrders/QueryOrders."""

    txid: str
    status: str = ""
    pair: str = ""
    side: str = ""            # buy / sell
    order_type: str = ""      # market / limit / stop-loss / ...
    price: str = ""
    price2: str = ""
    leverage: str = ""
    volume: str = ""
    volume_executed: str = ""
    cost: str = ""
    fee: str = ""
    open_time: int = 0
    close_time: int = 0
    userref: int | None = None
    refid: str = ""
    trades: list[str] = field(default_factory=list)
    order_description: str = ""  # the human "descr.order" line

    @classmethod
    def from_kraken(cls, txid: str, data: dict[str, Any]) -> "Order":
        descr = data.get("descr") or {}
        return cls(
            txid=txid,
            status=data.get("status") or "",
            pair=descr.get("pair") or data.get("pair") or "",
            side=descr.get("type") or "",
            order_type=descr.get("ordertype") or "",
            price=str(descr.get("price") or data.get("price") or ""),
            price2=str(descr.get("price2") or ""),
            leverage=str(descr.get("leverage") or ""),
            volume=str(data.get("vol") or ""),
            volume_executed=str(data.get("vol_exec") or ""),
            cost=str(data.get("cost") or ""),
            fee=str(data.get("fee") or ""),
            open_time=int(data.get("opentm") or 0),
            close_time=int(data.get("closetm") or 0),
            userref=int(data["userref"]) if data.get("userref") else None,
            refid=data.get("refid") or "",
            trades=list(data.get("trades") or []),
            order_description=descr.get("order") or "",
        )

    @property
    def open_iso(self) -> str:
        return _fmt_ts(self.open_time)

    @property
    def close_iso(self) -> str:
        return _fmt_ts(self.close_time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "txid": self.txid,
            "status": self.status,
            "pair": self.pair,
            "side": self.side,
            "order_type": self.order_type,
            "price": self.price,
            "price2": self.price2,
            "leverage": self.leverage,
            "volume": self.volume,
            "volume_executed": self.volume_executed,
            "cost": self.cost,
            "fee": self.fee,
            "open_time": self.open_time,
            "close_time": self.close_time,
            "userref": self.userref,
            "refid": self.refid,
            "trades": self.trades,
            "order": self.order_description,
        }

    def __str__(self) -> str:
        head = self.order_description or f"{self.side} {self.order_type} {self.volume} {self.pair}"
        return f"[{self.status}] {self.txid} {head}"


@dataclass(slots=True)
class Trade:
    """A completed fill — public recent-trades row or private TradesHistory entry."""

    txid: str = ""
    pair: str = ""
    side: str = ""          # buy / sell
    order_type: str = ""
    price: str = ""
    volume: str = ""
    cost: str = ""
    fee: str = ""
    time: int = 0
    ordertxid: str = ""
    margin: str = ""

    @classmethod
    def from_public_row(cls, pair: str, row: list[Any]) -> "Trade":
        # [price, volume, time, buy/sell, market/limit, misc]
        vals = list(row) + [""] * (6 - len(row))
        return cls(
            pair=pair,
            price=str(vals[0]),
            volume=str(vals[1]),
            time=int(vals[2] or 0),
            side=str(vals[3] or ""),
            order_type=str(vals[4] or ""),
        )

    @classmethod
    def from_history_entry(cls, txid: str, data: dict[str, Any]) -> "Trade":
        return cls(
            txid=txid,
            pair=data.get("pair") or "",
            side=data.get("type") or "",
            order_type=data.get("ordertype") or "",
            price=str(data.get("price") or ""),
            volume=str(data.get("vol") or ""),
            cost=str(data.get("cost") or ""),
            fee=str(data.get("fee") or ""),
            time=int(data.get("time") or 0),
            ordertxid=data.get("ordertxid") or "",
            margin=str(data.get("margin") or ""),
        )

    @property
    def iso(self) -> str:
        return _fmt_ts(self.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "txid": self.txid,
            "pair": self.pair,
            "side": self.side,
            "order_type": self.order_type,
            "price": self.price,
            "volume": self.volume,
            "cost": self.cost,
            "fee": self.fee,
            "time": self.time,
            "iso": self.iso,
            "ordertxid": self.ordertxid,
            "margin": self.margin,
        }

    def __str__(self) -> str:
        return f"{self.side} {self.volume} {self.pair} @ {self.price} ({self.txid or 'public'})"


@dataclass(slots=True)
class LedgerEntry:
    """One account ledger entry."""

    ledger_id: str
    refid: str = ""
    entry_type: str = ""      # trade / deposit / withdrawal / transfer / ...
    asset: str = ""
    amount: str = ""
    fee: str = ""
    balance: str = ""
    time: int = 0

    @classmethod
    def from_kraken(cls, ledger_id: str, data: dict[str, Any]) -> "LedgerEntry":
        return cls(
            ledger_id=ledger_id,
            refid=data.get("refid") or "",
            entry_type=data.get("type") or "",
            asset=data.get("asset") or "",
            amount=str(data.get("amount") or ""),
            fee=str(data.get("fee") or ""),
            balance=str(data.get("balance") or ""),
            time=int(data.get("time") or 0),
        )

    @property
    def iso(self) -> str:
        return _fmt_ts(self.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ledger_id": self.ledger_id,
            "refid": self.refid,
            "type": self.entry_type,
            "asset": self.asset,
            "amount": self.amount,
            "fee": self.fee,
            "balance": self.balance,
            "time": self.time,
            "iso": self.iso,
        }

    def __str__(self) -> str:
        return f"{self.entry_type} {self.amount} {self.asset} -> {self.balance}"


@dataclass(slots=True)
class TradeBalance:
    """One TradeBalance snapshot: equity, margin, buying power, ...

    All values are raw strings from the API; use the ``*_decimal`` accessors
    to get :class:`decimal.Decimal`.  ``from_kraken`` maps the API's short
    keys (``eb``, ``tb``, ``m``, ...) to readable attribute names; unknown
    keys are captured in ``extra``.
    """

    equivalent_balance: str = ""       # eb — balance used for margin/buying power calc
    trade_balance: str = ""            # tb — current trade balance
    margin: str = ""                   # m — current margin amount
    unrealized_pnl: str = ""           # n — unrealised net profit/loss
    cost_basis: str = ""               # c — cost basis
    floating_valuation: str = ""       # v — current floating valuation
    equity: str = ""                   # e — equity
    free_margin: str = ""              # mf — free margin
    margin_used: str = ""              # t (optional) — margin used
    extra: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_kraken(cls, data: dict[str, Any]) -> "TradeBalance":
        mapping = {
            "eb": "equivalent_balance",
            "tb": "trade_balance",
            "m": "margin",
            "n": "unrealized_pnl",
            "c": "cost_basis",
            "v": "floating_valuation",
            "e": "equity",
            "mf": "free_margin",
            "t": "margin_used",
        }
        known = {attr: "" for attr in mapping.values()}
        extra: dict[str, str] = {}
        for key, value in (data or {}).items():
            attr = mapping.get(key)
            if attr is not None:
                known[attr] = str(value)
            else:
                extra[key] = str(value)
        return cls(**known, extra=extra)

    @property
    def equity_decimal(self) -> Decimal:
        return as_decimal(self.equity) if self.equity else Decimal("0")

    @property
    def free_margin_decimal(self) -> Decimal:
        return as_decimal(self.free_margin) if self.free_margin else Decimal("0")

    def decimal(self, key: str) -> Decimal | None:
        """Best-effort Decimal for a named field."""
        if key in ("extra",):
            return None
        value = getattr(self, key, None)
        return as_decimal(value) if value not in (None, "") else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "equivalent_balance": self.equivalent_balance,
            "trade_balance": self.trade_balance,
            "margin": self.margin,
            "unrealized_pnl": self.unrealized_pnl,
            "cost_basis": self.cost_basis,
            "floating_valuation": self.floating_valuation,
            "equity": self.equity,
            "free_margin": self.free_margin,
            "margin_used": self.margin_used,
            **{f"extra.{k}": v for k, v in sorted(self.extra.items())},
        }

    def __str__(self) -> str:
        return f"equity={self.equity} trade_balance={self.trade_balance} free_margin={self.free_margin}"


@dataclass(slots=True)
class SpreadPoint:
    """One spread sample: timestamp plus best bid/ask prices.

    Public ``Spread`` rows arrive as ``[time, bid, ask]``; prices are raw
    strings and there is no volume or size component.
    """

    pair: str
    time: int
    bid: str
    ask: str

    @classmethod
    def from_row(cls, pair: str, row: list[Any]) -> "SpreadPoint":
        vals = list(row) + ["", ""]
        return cls(pair=pair, time=int(vals[0] or 0), bid=str(vals[1]), ask=str(vals[2]))

    @property
    def iso(self) -> str:
        return _fmt_ts(self.time)

    @property
    def bid_decimal(self) -> Decimal:
        return as_decimal(self.bid)

    @property
    def ask_decimal(self) -> Decimal:
        return as_decimal(self.ask)

    def to_dict(self) -> dict[str, Any]:
        return {"pair": self.pair, "time": self.time, "iso": self.iso, "bid": self.bid, "ask": self.ask}

    def __str__(self) -> str:
        return f"{self.pair} {self.iso} bid={self.bid} ask={self.ask}"


@dataclass(slots=True)
class Asset:
    """One asset from the ``/0/public/Assets`` catalog.

    ``asset`` is the REST asset code (``XXBT``); ``altname`` is the
    exchange-facing short name (``XBT``).  Decimals reflect the precision the
    API uses for this asset.
    """

    asset: str
    altname: str = ""
    asset_class: str = "currency"
    decimals: int = 8
    display_decimals: int = 8
    collateral_value: str = ""   # sometimes a string; keep raw
    status: str = "online"

    @classmethod
    def from_kraken(cls, asset: str, info: dict[str, Any]) -> "Asset":
        return cls(
            asset=asset,
            altname=info.get("altname") or "",
            asset_class=info.get("aclass") or "currency",
            decimals=int(info.get("decimals") or 8),
            display_decimals=int(info.get("display_decimals") or 8),
            collateral_value=str(info.get("collateral_value") or ""),
            status=info.get("status") or "online",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "altname": self.altname,
            "asset_class": self.asset_class,
            "decimals": self.decimals,
            "display_decimals": self.display_decimals,
            "collateral_value": self.collateral_value,
            "status": self.status,
        }

    def __str__(self) -> str:
        return f"{self.altname or self.asset} ({self.asset_class}, {self.decimals} decimals, {self.status})"


# ------------------------------------------------------------------ #
# WebSocket v2 message models
# ------------------------------------------------------------------ #


@dataclass(slots=True)
class WsTicker:
    """One ticker data item from the WS v2 ``ticker`` channel.

    Unlike the REST :class:`Ticker` (two-window tuples), WS v2 sends a flat
    object with a single bid/ask/last.  All price fields are raw strings;
    use ``*_decimal`` for numbers.
    """

    symbol: str = ""
    bid: str = ""
    bid_qty: str = ""
    ask: str = ""
    ask_qty: str = ""
    last: str = ""
    volume: str = ""
    vwap: str = ""
    low: str = ""
    high: str = ""
    change: str = ""
    change_pct: str = ""

    @classmethod
    def from_ws(cls, item: dict[str, Any]) -> "WsTicker":
        return cls(
            symbol=str(item.get("symbol") or ""),
            bid=str(item.get("bid") or ""),
            bid_qty=str(item.get("bid_qty") or ""),
            ask=str(item.get("ask") or ""),
            ask_qty=str(item.get("ask_qty") or ""),
            last=str(item.get("last") or ""),
            volume=str(item.get("volume") or ""),
            vwap=str(item.get("vwap") or ""),
            low=str(item.get("low") or ""),
            high=str(item.get("high") or ""),
            change=str(item.get("change") or ""),
            change_pct=str(item.get("change_pct") or ""),
        )

    @property
    def last_decimal(self) -> Decimal:
        return as_decimal(self.last) if self.last else Decimal("0")

    def decimal(self, key: str) -> Decimal | None:
        value = getattr(self, key, None) or ""
        return as_decimal(value) if value else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "bid": self.bid,
            "bid_qty": self.bid_qty,
            "ask": self.ask,
            "ask_qty": self.ask_qty,
            "last": self.last,
            "volume": self.volume,
            "vwap": self.vwap,
            "low": self.low,
            "high": self.high,
            "change": self.change,
            "change_pct": self.change_pct,
        }

    def __str__(self) -> str:
        return f"{self.symbol} last={self.last} bid={self.bid} ask={self.ask}"


@dataclass(slots=True)
class WsTrade:
    """One trade data item from the WS v2 ``trade`` channel.

    ``time`` is epoch *milliseconds* (the WS channel uses ms; `iso` converts).
    """

    symbol: str = ""
    id: str = ""
    side: str = ""
    price: str = ""
    qty: str = ""
    time: int = 0
    order_type: str = ""

    @classmethod
    def from_ws(cls, item: dict[str, Any]) -> "WsTrade":
        return cls(
            symbol=str(item.get("symbol") or ""),
            id=str(item.get("id") or ""),
            side=str(item.get("side") or ""),
            price=str(item.get("price") or ""),
            qty=str(item.get("qty") or ""),
            time=int(item.get("time") or 0),
            order_type=str(item.get("order_type") or ""),
        )

    @property
    def iso(self) -> str:
        return _fmt_ts(self.time, ms=True)

    @property
    def price_decimal(self) -> Decimal:
        return as_decimal(self.price) if self.price else Decimal("0")

    @property
    def qty_decimal(self) -> Decimal:
        return as_decimal(self.qty) if self.qty else Decimal("0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "id": self.id,
            "side": self.side,
            "price": self.price,
            "qty": self.qty,
            "time": self.time,
            "iso": self.iso,
            "order_type": self.order_type,
        }

    def __str__(self) -> str:
        return f"{self.side} {self.qty} {self.symbol} @ {self.price} ({self.id})"


@dataclass(slots=True)
class WsBook:
    """One book data item from the WS v2 ``book`` channel.

    ``bids``/``asks`` hold :class:`BookLevel` entries; a ``snapshot`` marks
    the initial full book (later items are delta updates).  ``checksum`` is
    the CRC32 the client should verify against the levels.
    """

    symbol: str = ""
    bids: list[BookLevel] = field(default_factory=list)
    asks: list[BookLevel] = field(default_factory=list)
    snapshot: bool = True
    checksum: int = 0

    @classmethod
    def from_ws(cls, item: dict[str, Any], *, snapshot: bool = True) -> "WsBook":
        return cls(
            symbol=str(item.get("symbol") or ""),
            bids=[BookLevel.from_row(r) for r in (item.get("bids") or [])],
            asks=[BookLevel.from_row(r) for r in (item.get("asks") or [])],
            snapshot=snapshot,
            checksum=int(item.get("checksum") or 0),
        )

    def best_bid(self) -> Decimal | None:
        return self.bids[0].price_decimal if self.bids else None

    def best_ask(self) -> Decimal | None:
        return self.asks[0].price_decimal if self.asks else None

    def spread(self) -> Decimal | None:
        if not self.asks or not self.bids:
            return None
        return self.asks[0].price_decimal - self.bids[0].price_decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "snapshot": self.snapshot,
            "bids": [lvl.to_dict() for lvl in self.bids],
            "asks": [lvl.to_dict() for lvl in self.asks],
            "checksum": self.checksum,
        }

    def __str__(self) -> str:
        return f"{self.symbol} {'snapshot' if self.snapshot else 'update'} {len(self.bids)} bids / {len(self.asks)} asks"