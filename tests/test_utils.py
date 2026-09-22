"""Unit tests for helpers — no network required."""

from __future__ import annotations

from decimal import Decimal

import pytest

from kraken_api.utils import (
    as_decimal,
    chunk,
    iso_to_unix,
    now_ms,
    pair_hint,
    to_decimal_str,
    unix_to_iso,
)


class TestChunk:
    def test_empty(self) -> None:
        assert list(chunk([])) == []

    def test_under_size(self) -> None:
        assert list(chunk(["a", "b"], size=50)) == [["a", "b"]]

    def test_exact_multiple(self) -> None:
        assert list(chunk(["a", "b", "c", "d"], size=2)) == [["a", "b"], ["c", "d"]]

    def test_remainder(self) -> None:
        assert list(chunk(["a", "b", "c"], size=2)) == [["a", "b"], ["c"]]

    def test_default_size_is_50(self) -> None:
        # Query endpoints cap ~50 ids per call.
        assert len(list(chunk(list(range(101))))[-1]) == 1


class TestDecimal:
    def test_as_decimal_from_string(self) -> None:
        assert as_decimal("0.000001") == Decimal("0.000001")

    def test_as_decimal_from_int(self) -> None:
        assert as_decimal(37500) == Decimal("37500")

    def test_to_decimal_str_keeps_strings(self) -> None:
        assert to_decimal_str("0.001") == "0.001"

    def test_to_decimal_str_preserves_float_exactly(self) -> None:
        # Floats are converted via their exact repr, never rounded silently.
        assert to_decimal_str(0.1 + 0.2) == "0.30000000000000004"

    def test_to_decimal_str_accepts_decimal(self) -> None:
        assert to_decimal_str(Decimal("0.300")) == "0.300"

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            as_decimal("not-a-number")


class TestTimestamps:
    def test_unix_to_iso(self) -> None:
        assert unix_to_iso(0) == "1970-01-01T00:00:00+00:00"

    def test_unix_to_iso_ms(self) -> None:
        assert unix_to_iso(1000, ms=True) == "1970-01-01T00:00:01+00:00"

    def test_iso_to_unix_roundtrip(self) -> None:
        assert iso_to_unix("1970-01-01T00:00:01+00:00") == 1

    def test_now_ms_is_positive(self) -> None:
        assert now_ms() > 1_500_000_000_000


class TestPairHint:
    def test_ws(self) -> None:
        assert pair_hint("BTC/USD") == "ws"

    def test_alt(self) -> None:
        assert pair_hint("XBTUSD") == "alt"

    def test_pub(self) -> None:
        assert pair_hint("XXBTZUSD") == "pub"

    def test_unknown(self) -> None:
        assert pair_hint("??") == "unknown"