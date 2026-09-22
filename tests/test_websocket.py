"""Unit tests for the WebSocket v2 client — no network required.

The client's outbound messages are captured with a fake socket; inbound
messages are fed in through a fake ``recv``.
"""

from __future__ import annotations

import json

import pytest

from kraken_api.errors import WebSocketError
from kraken_api.websocket import SpotWebSocket, publish_ticker_update


class FakeSocket:
    """Stands in for websocket-client's WebSocket object."""

    def __init__(self, messages: list[object] | None = None) -> None:
        self.messages = list(messages or [])
        self.sent: list[str] = []
        self.closed = False

    def send(self, raw: str) -> None:
        self.sent.append(raw)

    def recv(self):
        if not self.messages:
            raise ConnectionError("socket closed")
        return self.messages.pop(0)

    def close(self) -> None:
        self.closed = True


def _wire(ws: SpotWebSocket, socket: FakeSocket) -> None:
    ws._ws = socket  # attach without opening a real connection


class TestSubscribe:
    def test_subscribe_payload_shape(self) -> None:
        socket = FakeSocket()
        ws = SpotWebSocket()
        _wire(ws, socket)
        rid = ws.subscribe("ticker", ["BTC/USD"])
        sent = json.loads(socket.sent[0])
        assert sent["method"] == "subscribe"
        assert sent["req_id"] == rid
        assert sent["params"]["channel"] == "ticker"
        assert sent["params"]["symbol"] == ["BTC/USD"]
        assert sent["params"]["snapshot"] is True

    def test_subscribe_single_symbol_normalized(self) -> None:
        socket = FakeSocket()
        ws = SpotWebSocket()
        _wire(ws, socket)
        ws.subscribe("book", "BTC/USD", depth=25, snapshot=False, req_id=7)
        sent = json.loads(socket.sent[0])
        assert sent["req_id"] == 7
        assert sent["params"]["symbol"] == ["BTC/USD"]
        assert sent["params"]["depth"] == 25
        assert "snapshot" not in sent["params"]

    def test_subscribe_unknown_channel_rejected(self) -> None:
        ws = SpotWebSocket()
        _wire(ws, FakeSocket())
        with pytest.raises(ValueError):
            ws.subscribe("nope", "BTC/USD")

    def test_private_subscribe_includes_token(self) -> None:
        socket = FakeSocket()
        ws = SpotWebSocket(token="TOKXYZ")
        _wire(ws, socket)
        ws.subscribe("balances")
        sent = json.loads(socket.sent[0])
        assert sent["params"]["token"] == "TOKXYZ"


class TestAck:
    def test_await_ack_success(self) -> None:
        # Real v2 shape: the subscribe request echoed with success=true.
        ack = {
            "method": "subscribe", "req_id": 5, "success": True,
            "result": {"channel": "ticker", "symbol": "BTC/USD"},
            "time_in": "...", "time_out": "...",
        }
        socket = FakeSocket([json.dumps(ack)])
        ws = SpotWebSocket()
        _wire(ws, socket)
        message = ws.await_ack(5, timeout=1)
        assert message["success"] is True

    def test_await_ack_accepts_legacy_subscribe_status(self) -> None:
        ack = {"channel": "ticker", "type": "subscribeStatus", "success": True, "req_id": 5}
        socket = FakeSocket([json.dumps(ack)])
        ws = SpotWebSocket()
        _wire(ws, socket)
        assert ws.await_ack(5, timeout=1)["req_id"] == 5

    def test_await_ack_skips_status_and_rejects(self) -> None:
        status = {"channel": "status", "type": "update", "data": [{"system": "online"}]}
        bad = {
            "method": "subscribe", "req_id": 5, "success": False,
            "result": {"error_message": "Invalid symbol format"},
        }
        socket = FakeSocket([json.dumps(status), json.dumps(bad)])
        ws = SpotWebSocket()
        _wire(ws, socket)
        with pytest.raises(WebSocketError) as exc:
            ws.await_ack(5, timeout=1)
        assert "Invalid symbol format" in str(exc.value)

    def test_next_message_timeout_returns_none(self) -> None:
        socket = FakeSocket()  # recv always raises -> treated as closed
        ws = SpotWebSocket()
        _wire(ws, socket)
        with pytest.raises(WebSocketError):
            ws.next_message(timeout=0.1)


class TestIterMessages:
    def test_skips_heartbeats_and_pongs(self) -> None:
        messages = [
            json.dumps({"method": "pong", "req_id": 1}),           # inbound ack
            json.dumps({"event": "heartbeat", "time_in": "..."}),
            json.dumps({"channel": "ticker", "type": "update", "data": [{"symbol": "BTC/USD"}]}),
        ]
        socket = FakeSocket(messages)
        ws = SpotWebSocket()
        _wire(ws, socket)
        got = list(ws.iter_messages())
        assert len(got) == 1
        assert got[0]["channel"] == "ticker"

    def test_filter_channel(self) -> None:
        messages = [
            json.dumps({"channel": "ticker", "type": "update", "data": []}),
            json.dumps({"channel": "trade", "type": "update", "data": []}),
        ]
        socket = FakeSocket(messages)
        ws = SpotWebSocket()
        _wire(ws, socket)
        got = list(ws.iter_messages(filter_channel="trade"))
        assert len(got) == 1
        assert got[0]["channel"] == "trade"

    def test_auto_ping_on_stall(self) -> None:
        # A stalled-but-open socket (recv times out) should get a ping and
        # the stream continues waiting; a hard close ends the stream.
        import unittest.mock as mock

        socket = FakeSocket()
        ws = SpotWebSocket(ping_interval=0.05)
        _wire(ws, socket)
        # recv always times out: next_message returns None -> auto-ping fires.
        with mock.patch.object(socket, "recv", side_effect=TimeoutError("stall")):
            got = list(ws.iter_messages(timeout=0.2))
        assert got == []
        pings = [json.loads(s) for s in socket.sent if json.loads(s).get("method") == "ping"]
        assert len(pings) >= 1

    def test_hard_error_ends_stream_after_ping_opportunity(self) -> None:
        socket = FakeSocket()  # recv raises ConnectionError -> hard error
        ws = SpotWebSocket(ping_interval=0.05)
        _wire(ws, socket)
        got = list(ws.iter_messages(timeout=0.2))
        assert got == []  # ends quietly, no exception to the caller


def test_publish_ticker_update_flattens() -> None:
    flat = publish_ticker_update(
        {"symbol": "BTC/USD", "bid": "26999.0", "bid_qty": "1.0", "last": "27000.1"}
    )
    assert flat["symbol"] == "BTC/USD"
    assert flat["last"] == "27000.1"
    assert flat["change_pct"] == ""