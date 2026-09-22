"""Asset and pair naming resolution.

Kraken spells one market three different ways depending on the surface you
are talking to:

    REST public endpoints      ``XXBTZUSD``   (the "pub" form, X/Z prefixed)
    private / order endpoints  ``XBTUSD``     (the "altname")
    WebSocket v2               ``BTC/USD``    (the "wsname")

On top of that, assets themselves bounce between codes (``XXBT`` on coin
balances, ``XBT`` as the pair base, ``BTC`` on the public site).  This module
owns all of that mapping.  :class:`PairCatalog` loads the real tables from
``/0/public/AssetPairs`` and ``/0/public/Assets`` once (cached), and resolves
any accepted spelling to any other — with an offline fallback table for the
common pairs so the package keeps working without network for scripts, tests,
and documentation examples.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .errors import InvalidPairError
from .logging_config import log_event

if TYPE_CHECKING:
    from .transport import KrakenTransport

_CATALOG_LOG = logging.getLogger("kraken_api.api")


@dataclass(slots=True)
class Pair:
    """One tradable market, canonicalised across all of Kraken's spellings.

    ``pub`` is what REST market-data endpoints want, ``alt`` is what private
    order endpoints want, and ``ws`` is the WebSocket v2 symbol.  ``base`` /
    ``quote`` are the asset *altnames* (``XBT``, ``USD``).
    """

    pub: str          # XXBTZUSD
    alt: str          # XBTUSD
    ws: str           # BTC/USD
    base: str         # XBT
    quote: str        # USD
    pair_decimals: int = 5
    lot_decimals: int = 8

    @classmethod
    def from_asset_pair(cls, pub: str, info: dict[str, Any]) -> "Pair":
        return cls(
            pub=pub,
            alt=info.get("altname", pub),
            ws=info.get("wsname", pub),
            base=info.get("base", ""),
            quote=info.get("quote", ""),
            pair_decimals=int(info.get("pair_decimals", 5) or 5),
            lot_decimals=int(info.get("lot_decimals", 8) or 8),
        )

    @property
    def symbols(self) -> tuple[str, str]:
        return (self.ws, self.alt, self.pub)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pub": self.pub,
            "alt": self.alt,
            "ws": self.ws,
            "base": self.base,
            "quote": self.quote,
            "pair_decimals": self.pair_decimals,
            "lot_decimals": self.lot_decimals,
        }

    def __str__(self) -> str:
        return self.ws


# Offline fallback for the most common pairs; superseded by the live catalog
# as soon as /0/public/AssetPairs is fetched.
_FALLBACK_PAIRS: list[dict[str, Any]] = [
    {"pub": "XXBTZUSD", "alt": "XBTUSD", "ws": "BTC/USD", "base": "XBT", "quote": "USD"},
    {"pub": "XXBTZEUR", "alt": "XBTEUR", "ws": "BTC/EUR", "base": "XBT", "quote": "EUR"},
    {"pub": "XXBTXETH", "alt": "XBTETH", "ws": "BTC/ETH", "base": "XBT", "quote": "XETH"},
    {"pub": "XETHZUSD", "alt": "ETHUSD", "ws": "ETH/USD", "base": "XETH", "quote": "USD"},
    {"pub": "XETHZEUR", "alt": "ETHEUR", "ws": "ETH/EUR", "base": "XETH", "quote": "EUR"},
    {"pub": "XLTCZUSD", "alt": "LTCUSD", "ws": "LTC/USD", "base": "XLTC", "quote": "USD"},
    {"pub": "XXRPZUSD", "alt": "XRPUSD", "ws": "XRP/USD", "base": "XXRP", "quote": "USD"},
    {"pub": "XDGUSD", "alt": "DOGEUSD", "ws": "DOGE/USD", "base": "XDG", "quote": "USD"},
    {"pub": "SOLUSD", "alt": "SOLUSD", "ws": "SOL/USD", "base": "SOL", "quote": "USD"},
    {"pub": "ADAUSD", "alt": "ADAUSD", "ws": "ADA/USD", "base": "ADA", "quote": "USD"},
    {"pub": "DOTUSD", "alt": "DOTUSD", "ws": "DOT/USD", "base": "DOT", "quote": "USD"},
]


class PairCatalog:
    """Loads and resolves Kraken asset/pair names.

    Parameters
    ----------
    transport:
        A transport (or client) used to fetch the live tables.  Pass ``None``
        to stay on the offline fallback table only.
    lazy:
        If True (default), the catalog fetches on first use rather than in
        ``__init__``.
    """

    def __init__(
        self,
        transport: "KrakenTransport | None" = None,
        lazy: bool = True,
    ) -> None:
        # Accept a transport or something exposing .transport (KrakenClient).
        self.transport: "KrakenTransport | None"
        if transport is not None and hasattr(transport, "transport"):
            self.transport = transport.transport  # type: ignore[union-attr]
        else:
            self.transport = transport  # type: ignore[assignment]

        self._by_pub: dict[str, Pair] = {}
        self._by_alt: dict[str, Pair] = {}
        self._by_ws: dict[str, Pair] = {}
        self._loaded_at: datetime | None = None
        self._load_error: str | None = None

        for row in _FALLBACK_PAIRS:
            self._register(Pair(**row))

        if not lazy:
            self.load()

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #

    def _register(self, pair: Pair) -> None:
        self._by_pub[pair.pub] = pair
        self._by_alt[pair.alt] = pair
        self._by_ws[pair.ws] = pair

    def load(self) -> "PairCatalog":
        """Fetch ``/0/public/AssetPairs`` and merge it with the fallbacks.

        If the fetch fails (offline, rate-limited) the fallback table is kept
        and ``_load_error`` records why.
        """
        if self.transport is None:
            self._load_error = "no transport available; using offline fallback pairs"
            return self

        try:
            result = self.transport.public("AssetPairs", params=None)
        except Exception as exc:  # noqa: BLE001 - catalog must never hard-fail
            self._load_error = f"AssetPairs fetch failed: {exc}"
            log_event(
                _CATALOG_LOG,
                "catalog.load_failed",
                reason=str(exc),
            )
            return self

        for pub, info in (result or {}).items():
            if not isinstance(info, dict):
                continue
            try:
                self._register(Pair.from_asset_pair(pub, info))
            except (TypeError, ValueError):
                continue

        self._loaded_at = datetime.now(tz=timezone.utc)
        log_event(
            _CATALOG_LOG,
            "catalog.loaded",
            pair_count=len(self._by_pub),
            live=True,
        )
        return self

    def _ensure_loaded(self) -> None:
        if self.transport is not None and self._loaded_at is None and self._load_error is None:
            self.load()

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #

    @property
    def is_loaded(self) -> bool:
        return self._loaded_at is not None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def pair(self, ref: str) -> Pair:
        """Resolve any pair spelling to a :class:`Pair` record.

        Raises :class:`InvalidPairError` when nothing matches.  Matches the
        ws name (case-insensitive), the altname, and the pub name.
        """
        self._ensure_loaded()
        needle = ref.strip()
        upper = needle.upper()
        slashed = "/" in needle
        for table in (self._by_ws, self._by_alt, self._by_pub):
            hit = table.get(needle) or table.get(upper)
            if hit is not None:
                return hit
        if slashed:
            # "btc/usd" -> by_ws via upper handles it above; try alt translation
            for pair in self._by_ws.values():
                if pair.ws.upper() == upper:
                    return pair
        raise InvalidPairError(
            f"Unknown Kraken pair: {ref!r}. Use a ws name (BTC/USD), altname "
            "(XBTUSD), or public name (XXBTZUSD)."
        )

    def resolve(self, ref: str, style: str = "pub") -> str:
        """Resolve a pair spelling to the requested canonical style.

        ``style`` is ``"pub"`` (XXBTZUSD), ``"alt"`` (XBTUSD), or ``"ws"``
        (BTC/USD).
        """
        pair = self.pair(ref)
        if style == "alt":
            return pair.alt
        if style == "ws":
            return pair.ws
        return pair.pub

    def known_pairs(self) -> list[Pair]:
        """Return all known pairs, sorted by ws name."""
        self._ensure_loaded()
        # Dedupe by pub name (Pair is a slotted, unhashable dataclass).
        by_pub = {p.pub: p for p in self._by_pub.values()}
        return sorted(by_pub.values(), key=lambda p: p.ws)

    def has_asset(self, asset: str) -> bool:
        """True when any known pair references the given asset code."""
        needle = asset.upper()
        return any(
            p.base == needle or p.quote == needle
            for p in self._by_pub.values()
        )