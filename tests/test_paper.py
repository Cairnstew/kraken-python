"""Unit tests for the paper-trading simulation — no real network required.

A FakeLive transport supplies canned public market data (Ticker/Depth); the
rest of the stack (KrakenClient / KrakenManager / export) is exercised as
peers would use it.  The pair catalog uses its offline fallback table.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from kraken_api import KrakenManager
from kraken_api.client import KrakenClient
from kraken_api.errors import OrderError
from kraken_api.export import extract
from kraken_api.logging_config import log_event
from kraken_api.paper import PaperAccount, PaperTransport
from kraken_api.transport import KrakenTransport

TAKER = Decimal("0.0026")
MAKER = Decimal("0.0016")

START = Decimal("10000")


class FakeLive:
    """Stands in for the real (credential-less) public-market transport."""

    base_url = "https://fake.api"

    def __init__(self, *, bid: str = "100.00", ask: str = "101.00", last: str = "100.50") -> None:
        self.prices = {"XXBTZUSD": {"bid": bid, "ask": ask, "last": last}}
        self.calls: list[tuple[str, dict | None]] = []

    def set_price(self, *, bid: str | None = None, ask: str | None = None, last: str | None = None) -> None:
        row = self.prices["XXBTZUSD"]
        if bid is not None:
            row["bid"] = bid
        if ask is not None:
            row["ask"] = ask
        if last is not None:
            row["last"] = last

    def public(self, path: str, params: dict | None = None):
        self.calls.append((path, params))
        pair = (params or {}).get("pair", "XXBTZUSD")
        if path == "Ticker":
            p = self.prices[pair]
            return {pair: {"a": [p["ask"]], "b": [p["bid"]], "c": [p["last"]],
                           "v": [], "p": [], "t": [], "l": [], "h": [], "o": []}}
        if path == "Depth":
            p = self.prices[pair]
            return {pair: {"asks": [[p["ask"], "1"]], "bids": [[p["bid"], "1"]]}}
        if path == "AssetPairs":
            return {}
        raise AssertionError(f"unexpected public call {path} {params}")


@pytest.fixture
def live():
    return FakeLive()


@pytest.fixture
def account(live):
    return PaperAccount(live=live)


@pytest.fixture
def mgr(live, account):
    return KrakenManager(KrakenClient(PaperTransport(live=live, account=account)))


def _mk(mode: str = "book") -> tuple[KrakenManager, FakeLive, PaperAccount]:
    """Build manager + live + account for one-off scenarios."""
    live = FakeLive()
    acct = PaperAccount(live=live, price_mode=mode)
    return KrakenManager(KrakenClient(PaperTransport(live=live, account=acct))), live, acct


# ---------------------------------------------------------------------- #
# Market orders
# ---------------------------------------------------------------------- #


def test_market_buy_fills_immediately_at_ask_with_taker_fee(mgr, live) -> None:
    result = mgr.buy("BTC/USD", volume="0.5")
    txid = result["txid"][0]
    order = mgr.order(txid)
    assert order.status == "closed"

    gross = Decimal("101") * Decimal("0.5")          # 50.50
    fee = gross * TAKER                              # 0.1313
    balances = {b.asset: b.decimal for b in mgr.balances()}
    assert balances["USD"] == START - (gross + fee)
    assert balances["XBT"] == Decimal("0.5")
    assert order.cost == "50.50000000"
    assert order.fee == "0.13130000"


def test_market_sell_fills_immediately_at_bid(mgr, live) -> None:
    mgr.buy("BTC/USD", volume="1")  # seed base
    result = mgr.sell("BTC/USD", volume="0.4")
    order = mgr.order(result["txid"][0])
    assert order.status == "closed"

    proceeds = Decimal("100") * Decimal("0.4")       # 40.00
    fee = proceeds * TAKER
    balances = {b.asset: b.decimal for b in mgr.balances()}
    assert balances["USD"] == START - (Decimal("101") + Decimal("101") * TAKER) + (proceeds - fee)
    assert balances["XBT"] == Decimal("0.6")


def test_market_order_records_trades_and_ledger(mgr) -> None:
    mgr.buy("BTC/USD", volume="0.5")
    trades, tcount = mgr.trade_history()
    assert tcount == 1
    assert trades[0].side == "buy"
    assert trades[0].price == "101.00000000"
    entries, lcount = mgr.ledger()
    assert lcount == 2  # one row per asset
    assert all(e.entry_type == "trade" for e in entries)


def test_market_price_mode_last() -> None:
    mgr, live, acct = _mk(mode="last")
    mgr.buy("BTC/USD", volume="0.5")
    balances = {b.asset: b.decimal for b in mgr.balances()}
    gross = Decimal("100.50") * Decimal("0.5")       # fills at ticker last
    assert balances["USD"] == START - (gross + gross * TAKER)


# ---------------------------------------------------------------------- #
# Limit orders
# ---------------------------------------------------------------------- #


def test_limit_buy_rests_then_fills_when_ask_crosses(mgr, live, account) -> None:
    txid = mgr.buy("BTC/USD", volume="0.1", price="90.00")["txid"][0]
    assert [o.status for o in mgr.open_orders()] == ["open"]

    live.set_price(bid="89.00", ask="89.50", last="89.00")
    account._price_cache.clear()  # "time passes" past the price cache TTL
    assert mgr.open_orders() == []  # settle ran lazily on the read

    order = mgr.order(txid)
    assert order.status == "closed"
    balances = {b.asset: b.decimal for b in mgr.balances()}
    gross = Decimal("90") * Decimal("0.1")
    fee = gross * MAKER                                # fills at limit, maker fee
    assert balances["USD"] == START - (gross + fee)
    assert balances["XBT"] == Decimal("0.1")


def test_limit_sell_rests_then_fills_when_bid_crosses(mgr, live, account) -> None:
    mgr.buy("BTC/USD", volume="1")  # seed base
    txid = mgr.sell("BTC/USD", volume="0.3", price="105.00")["txid"][0]
    assert len(mgr.open_orders()) == 1

    live.set_price(bid="106.00", ask="107.00", last="106.50")
    account._price_cache.clear()
    assert mgr.settle() == 1

    order = mgr.order(txid)
    assert order.status == "closed"
    balances = {b.asset: b.decimal for b in mgr.balances()}
    proceeds = Decimal("105") * Decimal("0.3")
    fee = proceeds * MAKER
    assert balances["XBT"] == Decimal("0.7")
    assert balances["USD"] == START - (Decimal("101") + Decimal("101") * TAKER) + (proceeds - fee)


def test_limit_not_crossed_stays_open(mgr, live, account) -> None:
    mgr.buy("BTC/USD", volume="0.1", price="90.00")
    # Market never reaches the limit.
    live.set_price(bid="95.00", ask="96.00", last="95.50")
    account._price_cache.clear()
    assert list(mgr.open_orders())[0].status == "open"


# ---------------------------------------------------------------------- #
# Cancel / edit
# ---------------------------------------------------------------------- #


def test_cancel_order(mgr) -> None:
    txid = mgr.buy("BTC/USD", volume="0.1", price="90.00")["txid"][0]
    assert mgr.cancel(txid) == 1
    assert mgr.cancel(txid) == 0  # already gone
    order = mgr.order(txid)
    assert order.status == "canceled"
    assert [o.status for o in mgr.closed_orders()] == ["canceled"]


def test_cancel_all(mgr) -> None:
    mgr.buy("BTC/USD", volume="1")  # seed base first
    mgr.buy("BTC/USD", volume="0.1", price="90.00")
    mgr.sell("BTC/USD", volume="0.1", price="999999")
    assert mgr.cancel_all() == 2
    assert mgr.open_orders() == []


def test_edit_open_order(mgr) -> None:
    txid = mgr.buy("BTC/USD", volume="0.1", price="90.00")["txid"][0]
    result = mgr.client.edit_order(txid, pair="BTC/USD", price="95.00", volume="0.2")
    assert result["status"] == "ok"
    order = mgr.order(txid)
    assert order.price == "95.00000000"
    assert order.volume == "0.20000000"


# ---------------------------------------------------------------------- #
# Validation & rejects
# ---------------------------------------------------------------------- #


def test_validate_true_places_nothing(mgr) -> None:
    result = mgr.client.add_order("BTC/USD", side="buy", ordertype="limit",
                                  volume="0.1", price="90.00", validate=True)
    assert result["txid"] == []
    balances = {b.asset: b.decimal for b in mgr.balances()}
    assert balances == {"USD": START}   # only the untouched starting balance
    assert mgr.open_orders() == []
    assert not mgr.trade_history()[0]


def test_insufficient_quote_raises(mgr) -> None:
    with pytest.raises(OrderError):
        mgr.buy("BTC/USD", volume="500")  # 500 * 101 >> 10000


def test_insufficient_base_raises(mgr) -> None:
    with pytest.raises(OrderError):
        mgr.sell("BTC/USD", volume="1")


def test_validate_true_still_checks_balance(mgr) -> None:
    with pytest.raises(OrderError):
        mgr.client.add_order("BTC/USD", side="buy", ordertype="limit",
                             volume="500", price="101", validate=True)


def test_dead_man_switch_armed(mgr, account) -> None:
    mgr.buy("BTC/USD", volume="0.1", price="90.00")
    # The manager surface returns the open-order count; the raw client
    # response carries the trigger timestamps.
    assert mgr.cancel_all_after(999) == 1
    result = mgr.client.cancel_all_orders_after(999)
    assert result["currentTime"]
    assert result["triggerTime"]
    assert account._deadline is not None


def test_dead_man_switch_fires_on_settle(mgr, account) -> None:
    mgr.buy("BTC/USD", volume="0.1", price="90.00")
    account._deadline = _now_past()
    assert mgr.settle() == 0  # no fills
    statuses = {o.status for o in mgr.closed_orders()}
    assert statuses == {"triggered"}
    assert mgr.open_orders() == []


def test_query_orders_spans_open_and_closed(mgr) -> None:
    open_txid = mgr.buy("BTC/USD", volume="0.1", price="90.00")["txid"][0]
    closed_txid = mgr.buy("BTC/USD", volume="0.1")["txid"][0]  # market -> closed
    raw = mgr.client.query_orders([open_txid, closed_txid])
    assert set(raw) == {open_txid, closed_txid}
    assert raw[open_txid]["status"] == "open"
    assert raw[closed_txid]["status"] == "closed"


def _now_past() -> float:
    import time

    return time.time() - 5


def test_ws_token_is_fake(mgr) -> None:
    assert mgr.ws_token() == "paper-token"


# ---------------------------------------------------------------------- #
# Manager integration
# ---------------------------------------------------------------------- #


def test_paper_manager_builds_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("KRAKEN_API_KEY", raising=False)
    monkeypatch.delenv("KRAKEN_API_SECRET", raising=False)
    mgr = KrakenManager.paper()
    assert isinstance(mgr, KrakenManager)
    assert mgr.paper_account is not None
    assert mgr.client.transport is not None


def test_paper_account_attribute_and_settle(mgr, live, account) -> None:
    assert mgr.paper_account is account
    mgr.buy("BTC/USD", volume="0.1", price="90.00")
    live.set_price(ask="89.00")
    account._price_cache.clear()
    assert mgr.settle() == 1


def test_reset_restores_starting_balance(mgr, account) -> None:
    mgr.buy("BTC/USD", volume="0.5")
    assert len(mgr.trade_history()[0]) == 1
    account.reset()
    balances = {b.asset: b.decimal for b in mgr.balances()}
    assert balances == {"USD": START}
    assert mgr.trade_history()[0] == []
    assert mgr.open_orders() == []


def test_export_extracts_paper_account(mgr) -> None:
    mgr.buy("BTC/USD", volume="0.5")
    doc = extract(mgr, "balance")
    assert doc["balances"]
    assert all(isinstance(b["amount"], str) for b in doc["balances"])
    orders = extract(mgr, "orders")
    assert orders == {"orders": []}
    # Everything round-trips as clean JSON.
    import json

    json.dumps(doc)


# ---------------------------------------------------------------------- #
# State persistence
# ---------------------------------------------------------------------- #


def test_state_round_trip(tmp_path) -> None:
    state = tmp_path / "paper.json"
    live = FakeLive()
    acct = PaperAccount(live=live, state_file=state)
    mgr = KrakenManager(KrakenClient(PaperTransport(live=live, account=acct)))
    mgr.buy("BTC/USD", volume="0.5")
    assert state.exists()

    # A fresh account loading the same file sees the stored state.
    live2 = FakeLive()
    acct2 = PaperAccount(live=live2, state_file=state)
    balances = {a: amount for a, amount in acct2.balances.items()}
    assert balances["USD"] == START - (Decimal("101") * Decimal("0.5") * (Decimal("1") + TAKER))
    assert balances["XBT"] == Decimal("0.5")
    assert len(acct2.trades) == 1


def test_state_is_atomic_on_mutation(tmp_path) -> None:
    state = tmp_path / "paper.json"
    live = FakeLive()
    acct = PaperAccount(live=live, state_file=state)
    mgr = KrakenManager(KrakenClient(PaperTransport(live=live, account=acct)))
    mgr.buy("BTC/USD", volume="0.5")
    text = state.read_text(encoding="utf-8")
    assert "PAPER-" in text


# ---------------------------------------------------------------------- #
# Logging
# ---------------------------------------------------------------------- #


def test_log_event_level_kwarg(caplog) -> None:
    with caplog.at_level("DEBUG", logger="kraken_api.paper"):
        log_event("kraken_api.paper", "custom.event", level="DEBUG", foo=1)
        log_event("kraken_api.paper", "custom.event", foo=2)
    debug = [r for r in caplog.records if getattr(r, "foo", None) == 1]
    assert debug and debug[0].levelname == "DEBUG"
    info = [r for r in caplog.records if getattr(r, "foo", None) == 2]
    assert info and info[0].levelname == "INFO"


def test_fill_logged_with_fields(caplog, mgr) -> None:
    with caplog.at_level("INFO", logger="kraken_api.paper"):
        mgr.buy("BTC/USD", volume="0.5")
    fills = [r for r in caplog.records if r.msg == "paper.order.fill"]
    assert fills
    rec = fills[0]
    assert rec.side == "buy"
    assert rec.fee_type == "taker"
    assert rec.fill_price == "101.00000000"
    rests = [r for r in caplog.records if r.msg == "paper.order.rest"]
    assert not rests


def test_limit_rest_and_settle_logging(caplog, mgr, live, account) -> None:
    with caplog.at_level("DEBUG", logger="kraken_api.paper"):
        txid = mgr.buy("BTC/USD", volume="0.1", price="90.00")["txid"][0]
        rests = [r for r in caplog.records if r.msg == "paper.order.rest"]
        assert rests and rests[0].txid == txid

        live.set_price(ask="89.00")
        account._price_cache.clear()
        assert mgr.settle() == 1

    settles = [r for r in caplog.records if r.msg == "paper.settle"]
    assert settles and settles[0].filled_count == 1
    fills = [r for r in caplog.records if r.msg == "paper.order.fill"]
    assert fills and fills[0].fee_type == "maker"


def test_reject_logged_at_warning(caplog, mgr) -> None:
    with caplog.at_level("WARNING", logger="kraken_api.paper"):
        with pytest.raises(OrderError):
            mgr.buy("BTC/USD", volume="500")
    rejects = [r for r in caplog.records if r.msg == "paper.reject"]
    assert rejects and rejects[0].reason == "insufficient_quote"