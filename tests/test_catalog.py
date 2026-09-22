"""Unit tests for pair/asset resolution — no network required."""

from __future__ import annotations

import pytest

from kraken_api.catalog import Pair, PairCatalog
from kraken_api.errors import InvalidPairError


class TestOfflineFallback:
    def setup_method(self) -> None:
        # No transport -> the built-in fallback table is the whole catalog.
        self.catalog = PairCatalog(transport=None)

    def test_ws_to_pub(self) -> None:
        assert self.catalog.resolve("BTC/USD", "pub") == "XXBTZUSD"

    def test_ws_to_alt(self) -> None:
        assert self.catalog.resolve("BTC/USD", "alt") == "XBTUSD"

    def test_alt_to_pub(self) -> None:
        assert self.catalog.resolve("XBTUSD", "pub") == "XXBTZUSD"

    def test_pub_to_alt(self) -> None:
        assert self.catalog.resolve("XXBTZUSD", "alt") == "XBTUSD"

    def test_pub_to_ws(self) -> None:
        assert self.catalog.resolve("XXBTZUSD", "ws") == "BTC/USD"

    def test_case_insensitive_ws(self) -> None:
        assert self.catalog.resolve("btc/usd", "pub") == "XXBTZUSD"

    def test_pair_object(self) -> None:
        pair = self.catalog.pair("BTC/USD")
        assert isinstance(pair, Pair)
        assert pair.base == "XBT"
        assert pair.quote == "USD"
        assert pair.symbols == ("BTC/USD", "XBTUSD", "XXBTZUSD")

    def test_unknown_pair_raises(self) -> None:
        with pytest.raises(InvalidPairError):
            self.catalog.resolve("FAKE/USD")

    def test_load_error_recorded_without_transport(self) -> None:
        assert self.catalog.load_error is None  # nothing attempted yet
        self.catalog.load()
        assert self.catalog.load_error is not None
        assert "offline fallback" in self.catalog.load_error

    def test_known_pairs_sorted(self) -> None:
        ws_names = [p.ws for p in self.catalog.known_pairs()]
        assert ws_names == sorted(ws_names)
        assert "BTC/USD" in ws_names


class FakeTransport:
    """Minimal stand-in serving a tiny live AssetPairs table."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def public(self, path: str, params=None):
        self.calls.append(path)
        assert path == "AssetPairs"
        return {
            "XXBTZUSD": {
                "altname": "XBTUSD",
                "wsname": "BTC/USD",
                "base": "XXBT",
                "quote": "ZUSD",
                "pair_decimals": 5,
                "lot_decimals": 8,
            },
            "PEPEZUSD": {
                "altname": "PEPEUSD",
                "wsname": "PEPE/USD",
                "base": "PEPE",
                "quote": "ZUSD",
                "pair_decimals": 8,
                "lot_decimals": 8,
            },
        }


class TestLiveCatalog:
    def test_merges_live_pairs_with_fallbacks(self) -> None:
        catalog = PairCatalog(transport=FakeTransport())
        assert catalog.load() is catalog
        assert catalog.is_loaded
        # From the live table:
        assert catalog.resolve("PEPE/USD", "pub") == "PEPEZUSD"
        assert catalog.resolve("PEPEUSD", "alt") == "PEPEUSD"
        # Fallbacks still present since live AssetPairs includes BTC too:
        assert catalog.resolve("BTC/USD", "pub") == "XXBTZUSD"

    def test_lazy_defers_the_fetch(self) -> None:
        transport = FakeTransport()
        catalog = PairCatalog(transport=transport, lazy=True)
        assert transport.calls == []
        catalog.resolve("BTC/USD", "pub")  # triggers load on first use
        assert transport.calls == ["AssetPairs"]

    def test_asset_detection(self) -> None:
        catalog = PairCatalog(transport=FakeTransport(), lazy=False)
        assert catalog.has_asset("ZUSD")
        assert catalog.has_asset("PEPE")
        assert not catalog.has_asset("DOGE")