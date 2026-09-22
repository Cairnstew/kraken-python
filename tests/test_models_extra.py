"""Unit tests for the extended typed models — no network required."""

from __future__ import annotations

import json
from decimal import Decimal

from kraken_api.models import (
    Asset,
    SpreadPoint,
    TradeBalance,
    WsBook,
    WsTicker,
    WsTrade,
)


def test_trade_balance_maps_short_keys() -> None:
    tb = TradeBalance.from_kraken(
        {
            "eb": "1000.00",
            "tb": "999.00",
            "m": "0.0",
            "n": "1.50",
            "c": "998.00",
            "v": "1001.00",
            "e": "1001.00",
            "mf": "1001.00",
            "zz_future": "42",
        }
    )
    assert tb.equity == "1001.00"
    assert tb.trade_balance == "999.00"
    assert tb.margin_used == ""           # 't' absent
    assert tb.extra == {"zz_future": "42"}
    assert tb.equity_decimal == Decimal("1001.00")
    assert tb.to_dict()["extra.zz_future"] == "42"
    blob = json.dumps(tb.to_dict())
    assert '"equity": "1001.00"' in blob


def test_trade_balance_empty() -> None:
    tb = TradeBalance.from_kraken({})
    assert tb.equity == ""
    assert tb.equity_decimal == Decimal("0")
    assert tb.decimal("equity") is None


def test_spread_point() -> None:
    point = SpreadPoint.from_row("BTC/USD", [1700000000, "27000.0", "27001.0"])
    assert point.time == 1700000000
    assert point.bid == "27000.0"
    assert point.ask_decimal == Decimal("27001.0")
    assert point.to_dict()["iso"].startswith("2023-")
    assert json.dumps(point.to_dict())


def test_asset() -> None:
    asset = Asset.from_kraken(
        "XXBT",
        {"altname": "XBT", "aclass": "currency", "decimals": 8, "display_decimals": 5,
         "collateral_value": "0.5", "status": "online"},
    )
    assert asset.altname == "XBT"
    assert asset.decimals == 8
    assert asset.collateral_value == "0.5"
    assert json.dumps(asset.to_dict())


def test_ws_ticker() -> None:
    item = {
        "symbol": "BTC/USD",
        "bid": "27000.0",
        "bid_qty": "1.5",
        "ask": "27001.0",
        "ask_qty": "2.0",
        "last": "27000.5",
        "volume": "1234.5",
        "vwap": "26999.0",
        "low": "26000.0",
        "high": "27500.0",
        "change": "100.5",
        "change_pct": "0.37",
    }
    ticker = WsTicker.from_ws(item)
    assert ticker.symbol == "BTC/USD"
    assert ticker.last_decimal == Decimal("27000.5")
    assert ticker.decimal("bid") == Decimal("27000.0")
    assert ticker.decimal("missing") is None
    assert json.dumps(ticker.to_dict())


def test_ws_trade() -> None:
    trade = WsTrade.from_ws(
        {"symbol": "BTC/USD", "id": "123456", "side": "buy", "price": "27000.0",
         "qty": "0.5", "time": 1700000000123, "order_type": "limit"},
    )
    assert trade.id == "123456"
    assert trade.price_decimal == Decimal("27000.0")
    assert trade.iso.startswith("2023-")
    assert json.dumps(trade.to_dict())


def test_ws_book_snapshot() -> None:
    book = WsBook.from_ws(
        {
            "symbol": "BTC/USD",
            "bids": [["26999.0", "3.0", 1700000000]],
            "asks": [["27001.0", "1.0", 1700000000]],
            "checksum": 123456789,
        },
        snapshot=True,
    )
    assert book.snapshot is True
    assert book.checksum == 123456789
    assert book.best_bid() == Decimal("26999.0")
    assert book.best_ask() == Decimal("27001.0")
    assert book.spread() == Decimal("2.0")
    assert json.dumps(book.to_dict())


def test_ws_book_update_flag() -> None:
    book = WsBook.from_ws({"symbol": "BTC/USD", "bids": [], "asks": []}, snapshot=False)
    assert book.snapshot is False
    assert book.spread() is None