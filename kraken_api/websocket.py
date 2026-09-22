"""Real-time market data over Kraken's Spot WebSocket v2.

The REST API is request/response; the WebSocket v2 API
(``wss://ws.kraken.com/v2``) pushes ticker, book, and trade updates as soon as
they happen — no polling, no rate limits.  Private channels (balances,
executions, ...) live on ``wss://ws-auth.kraken.com/v2`` and authenticate with
a token fetched from the ``GetWebSocketsToken`` REST endpoint.

:class:`SpotWebSocket` is a small synchronous wrapper around
``websocket-client``: connect, subscribe with a request id, iterate messages.
It is deliberately unopinionated — you get raw parsed messages and choose what
to do with them (:mod:`kraken_api.watch` contains ready-made generators).

    from kraken_api import KrakenManager
    from kraken_api.websocket import SpotWebSocket

    mgr = KrakenManager.from_env()
    ws = SpotWebSocket.connect()
    ws.subscribe("ticker", ["BTC/USD"])
    for message in ws.iter_messages():
        if message.get("channel") == "ticker":
            print(message["data"])
"""

from __future__ import annotations

import itertools
import json
import logging
import threading
import time
from typing import Any, Iterator

from .errors import WebSocketError
from .logging_config import log_event

_WS_LOG = logging.getLogger("kraken_api.ws")

DEFAULT_PUBLIC_URL = "wss://ws.kraken.com/v2"
DEFAULT_PRIVATE_URL = "wss://ws-auth.kraken.com/v2"

_CHANNELS = ("ticker", "book", "trade", "ohlc", "executions", "balances", "level3")


def _is_timeout(exc: Exception) -> bool:
    """True when *exc* is a socket/websocket timeout rather than a hard error."""
    if isinstance(exc, TimeoutError):
        return True
    name = exc.__class__.__name__.lower()
    return "timeout" in name or name in ("socket.timeout",)


class SpotWebSocket:
    """Synchronous Kraken Spot WebSocket v2 client.

    Parameters
    ----------
    url:
        Public endpoint by default; pass the auth endpoint plus ``token`` for
        private channels.
    token:
        Session token for private channels (from ``GetWebSocketsToken``).
    ping_interval:
        Seconds between keep-alive pings (the server requires one; the client
        also auto-pings inside :meth:`iter_messages`).
    """

    def __init__(
        self,
        url: str = DEFAULT_PUBLIC_URL,
        token: str | None = None,
        ping_interval: float = 30.0,
    ) -> None:
        self.url = url
        self.token = token
        self.ping_interval = ping_interval
        self._ws: Any | None = None
        self._ids = itertools.count(1)

    # ------------------------------------------------------------------ #
    # Connection
    # ------------------------------------------------------------------ #

    def connect(self, timeout: float = 10.0) -> "SpotWebSocket":
        """Open the WebSocket connection (no-op if already open)."""
        if self._ws is not None:
            return self
        try:
            import websocket as _ws  # local import: optional dependency

            self._ws = _ws.create_connection(
                self.url,
                timeout=timeout,
                header=["User-Agent: kraken-python/0.1.0"],
            )
        except Exception as exc:  # noqa: BLE001 - surface as our own error
            self._ws = None
            raise WebSocketError(f"could not connect to {self.url}: {exc}") from exc

        log_event(_WS_LOG, "ws.connected", url=self.url, authenticated=bool(self.token))
        return self

    @classmethod
    def connect_public(cls, timeout: float = 10.0) -> "SpotWebSocket":
        """One-shot helper: connect to the public endpoint."""
        return cls(DEFAULT_PUBLIC_URL).connect(timeout=timeout)

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            finally:
                self._ws = None

    def __enter__(self) -> "SpotWebSocket":
        return self.connect()

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def connected(self) -> bool:
        return self._ws is not None

    # ------------------------------------------------------------------ #
    # Outbound messages
    # ------------------------------------------------------------------ #

    def _send(self, payload: dict[str, Any]) -> None:
        if self._ws is None:
            self.connect()
        try:
            self._ws.send(json.dumps(payload))  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            raise WebSocketError(f"send failed: {exc}") from exc

    def subscribe(
        self,
        channel: str,
        symbols: str | list[str] | None = None,
        *,
        snapshot: bool = True,
        depth: int | None = None,
        req_id: int | None = None,
    ) -> int:
        """Subscribe to a channel, optionally scoped to symbols.

        Returns the request id (useful with :meth:`await_ack`).
        """
        if channel not in _CHANNELS:
            raise ValueError(
                f"unknown channel {channel!r}; expected one of {_CHANNELS}"
            )
        rid = req_id if req_id is not None else next(self._ids)
        params: dict[str, Any] = {"channel": channel}
        if symbols is not None:
            params["symbol"] = [symbols] if isinstance(symbols, str) else list(symbols)
        if snapshot:
            params["snapshot"] = True
        if depth is not None:
            params["depth"] = depth
        if self.token:
            params["token"] = self.token
        self._send({"method": "subscribe", "params": params, "req_id": rid})
        return rid

    def unsubscribe(self, channel: str, symbols: str | list[str] | None = None) -> int:
        rid = next(self._ids)
        params: dict[str, Any] = {"channel": channel}
        if symbols is not None:
            params["symbol"] = [symbols] if isinstance(symbols, str) else list(symbols)
        self._send({"method": "unsubscribe", "params": params, "req_id": rid})
        return rid

    def ping(self) -> None:
        self._send({"method": "ping", "req_id": next(self._ids)})

    # ------------------------------------------------------------------ #
    # Inbound messages
    # ------------------------------------------------------------------ #

    def await_ack(self, req_id: int, timeout: float = 5.0) -> dict[str, Any]:
        """Wait for the subscribe/unsubscribe acknowledgement for ``req_id``.

        The v2 server replies to a subscribe with the same request object:
        ``{"method": "subscribe", "req_id": N, "success": bool, "result": {...}}``
        (older builds used a ``{"type": "subscribeStatus", ...}`` event, which
        is accepted too).  Raises :class:`WebSocketError` on rejection.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self.next_message(timeout=max(0.1, deadline - time.monotonic()))
            if message is None:
                continue
            is_ack = (
                message.get("req_id") == req_id
                and (
                    message.get("method") in ("subscribe", "unsubscribe")
                    or message.get("type") == "subscribeStatus"
                )
            )
            if not is_ack:
                continue
            if not message.get("success", False):
                detail = message.get("error_message") or message.get("result") or "unknown error"
                raise WebSocketError(f"subscribe rejected: {detail}")
            return message
        raise WebSocketError(f"no ack for request {req_id} within {timeout}s")

    def next_message(self, timeout: float = 10.0) -> dict[str, Any] | None:
        """Receive the next message (``None`` if the timeout elapses).

        A *timeout* returns ``None`` (the stream is still alive — used for
        keep-alive pings); a hard socket error raises :class:`WebSocketError`.
        """
        if self._ws is None:
            self.connect()
        try:
            raw = self._ws.recv()  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            if _is_timeout(exc):
                return None
            raise WebSocketError(f"recv failed: {exc}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WebSocketError(f"server sent non-JSON message: {raw[:200]!r}") from exc

    def iter_messages(
        self,
        *,
        timeout: float | None = None,
        filter_channel: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield messages until the connection closes or ``timeout`` passes.

        Auto-pings every ``ping_interval`` seconds.  ``filter_channel`` skips
        heartbeats and any message from other channels.
        """
        started = time.monotonic()
        last_ping = 0.0
        while self._ws is not None:
            if timeout is not None and time.monotonic() - started >= timeout:
                return
            try:
                message = self.next_message(timeout=0.5)
            except WebSocketError:
                return
            if message is None:
                if time.monotonic() - last_ping >= self.ping_interval:
                    self.ping()
                    last_ping = time.monotonic()
                continue
            if message.get("channel") == "heartbeat":
                continue
            if message.get("method") == "pong" or message.get("event") == "heartbeat":
                continue
            if filter_channel and message.get("channel") != filter_channel:
                continue
            yield message

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return self.iter_messages()


def publish_ticker_update(data_item: dict[str, Any]) -> dict[str, Any]:
    """Normalise one WS v2 ticker data item into a flat JSON-able dict.

    The API sends full-string precision fields; this keeps the raw strings
    and adds nothing but the pair.  Not a :class:`~kraken_api.models.Ticker`
    (that is REST-shaped); the Watch helpers favour these dicts.
    """
    return {
        "symbol": data_item.get("symbol", ""),
        "bid": data_item.get("bid", ""),
        "bid_qty": data_item.get("bid_qty", ""),
        "ask": data_item.get("ask", ""),
        "ask_qty": data_item.get("ask_qty", ""),
        "last": data_item.get("last", ""),
        "volume": data_item.get("volume", ""),
        "high": data_item.get("high", ""),
        "low": data_item.get("low", ""),
        "change_pct": data_item.get("change_pct", ""),
    }