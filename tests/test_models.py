"""Unit tests for typed model conversions — no network required."""

from __future__ import annotations

from decimal import Decimal

from kraken_api.models import (
    Balance,
    BookLevel,
    Candle,
    LedgerEntry,
    Order,
    OrderBook,
    ServerTime,
    Ticker,
    Trade,
)


def test_server_time() -> None:
    st = ServerTime.from_kraken({"unixtime": 0, "rfc1123": "Thu, 01 Jan 70"})
    assert st.unixtime == 0
    assert st.iso == "1970-01-01T00:00:00+00:00"


def test_ticker_from_kraken() -> None:
    ticker = Ticker.from_kraken(
        "BTC/USD",
        {
            "a": ["27000.0", "1", "1.000"],
            "b": ["26999.5", "2", "2.000"],
            "c": ["27000.1", "0.01000000"],
            "v": ["123.0", "4567.0"],
            "p": ["26990.0", "26980.0"],
            "t": [123, 4567],
            "l": ["26000.0", "25000.0"],
            "h": ["27500.0", "28000.0"],
            "o": ["26500.0", "26000.0"],
        },
    )
    assert ticker.last_price == Decimal("27000.1")
    assert ticker.volume[1] == "4567.0"
    assert ticker.to_dict()["pair"] == "BTC/USD"
    assert "27000.1" in str(ticker)


def test_ticker_unknown_fields_default() -> None:
    ticker = Ticker.from_kraken("BTC/USD", {})
    assert ticker.last == []
    assert ticker.last_price == Decimal("0")


def test_candle_from_kraken() -> None:
    candle = Candle.from_kraken("BTC/USD", [1700000000, "100", "101", "99", "100.5", "100.2", "12.3", 42])
    assert candle.time == 1700000000
    assert candle.close == "100.5"
    assert candle.close_decimal == Decimal("100.5")
    assert candle.count == 42
    assert candle.to_dict()["iso"].startswith("2023-")

    # Warms against short rows.
    short = Candle.from_kraken("BTC/USD", [1700000000, "1", "2"])
    assert short.close == ""


def test_order_book() -> None:
    book = OrderBook.from_kraken(
        "BTC/USD",
        {
            "asks": [["25000.0", "1.0", 1700000000], ["25001.0", "2.0", 1700000001]],
            "bids": [["24999.0", "3.0", 1700000000]],
        },
    )
    assert len(book.asks) == 2
    assert book.best_ask() == Decimal("25000.0")
    assert book.best_bid() == Decimal("24999.0")
    assert book.spread() == Decimal("1.0")
    assert isinstance(book.asks[0], BookLevel)
    assert book.asks[0].volume_decimal == Decimal("1.0")


def test_order_from_kraken_open_form() -> None:
    order = Order.from_kraken(
        "TXID123",
        {
            "refid": None,
            "userref": 12345,
            "status": "open",
            "opentm": 1700000000,
            "starttm": 0,
            "expiretm": 0,
            "descr": {
                "pair": "XBTUSD",
                "type": "buy",
                "ordertype": "limit",
                "price": "27000.0",
                "price2": "0",
                "leverage": "none",
                "order": "buy 0.00100000 XBTUSD @ limit 27000.0",
            },
            "vol": "0.00100000",
            "vol_exec": "0.00000000",
            "cost": "0.00000",
            "fee": "0.00000",
            "price": "27000.0",
            "trades": [],
        },
    )
    assert order.side == "buy"
    assert order.pair == "XBTUSD"
    assert order.price == "27000.0"
    assert order.userref == 12345
    assert order.open_iso.startswith("2023-")
    assert order.order_description.startswith("buy 0.001")


def test_trade_from_public_row() -> None:
    trade = Trade.from_public_row("BTC/USD", ["27000.0", "0.5", 1700000000, "buy", "limit"])
    assert trade.side == "buy"
    assert trade.price == "27000.0"
    assert trade.iso.startswith("2023-")


def test_trade_from_history_entry() -> None:
    trade = Trade.from_history_entry(
        "TXID1",
        {
            "ordertxid": "ORDER1",
            "pair": "XBTUSD",
            "time": 1700000000,
            "type": "sell",
            "ordertype": "limit",
            "price": "26999.0",
            "cost": "13499.50",
            "fee": "5.39",
            "vol": "0.5",
            "margin": "0.0",
        },
    )
    assert trade.txid == "TXID1"
    assert trade.side == "sell"
    assert trade.cost == "13499.50"


def test_balance() -> None:
    bal = Balance.from_entry("ZUSD", "1234.50")
    assert bal.amount == "1234.50"
    assert bal.decimal == Decimal("1234.50")
    assert str(bal) == "1234.50 ZUSD"


def test_ledger_entry() -> None:
    entry = LedgerEntry.from_kraken(
        "LEDGER1",
        {
            "refid": "REF1",
            "time": 1700000000,
            "type": "trade",
            "aclass": "currency",
            "asset": "XBT",
            "amount": "0.001",
            "fee": "0.00001",
            "balance": "1.001",
        },
    )
    assert entry.ledger_id == "LEDGER1"
    assert entry.entry_type == "trade"
    assert entry.iso.startswith("2023-")


def test_to_dict_round_trips_through_json() -> None:
    import json

    candle = Candle.from_kraken("BTC/USD", [1700000000, "1", "2", "3", "4", "5", "6", 7])
    blob = json.dumps(candle.to_dict())
    assert '"pair": "BTC/USD"' in blob