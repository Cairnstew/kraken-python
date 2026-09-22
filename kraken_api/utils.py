"""Small helpers shared across the Kraken API wrapper.

Kraken deals in decimal quantities and prices that are *strings* on the wire
(to avoid float rounding on the exchange).  This module centralises the
conversions (``Decimal`` <-> string), the timestamp helpers, and a generic
batching helper for the private Query endpoints (which cap batch sizes).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

# ---------------------------------------------------------------------------
# Decimal / string helpers
# ---------------------------------------------------------------------------


def as_decimal(value: object) -> Decimal:
    """Parse a Kraken numeric value (string, int, float, Decimal) as Decimal.

    Raises :class:`ValueError` if the value cannot be parsed.
    """
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{value!r} is not a valid decimal number") from exc


def to_decimal_str(value: object) -> str:
    """Render *value* as the canonical decimal string an API param wants.

    Strings and Decimals pass through unchanged.  Floats are converted via
    their exact ``repr`` (never rounded — a price must not be silently
    altered); pass strings or Decimals for precise quantities.
    """
    return format(as_decimal(value), "f")


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------


def unix_to_iso(unix: object, ms: bool = False) -> str:
    """Convert a UTC Unix timestamp (or epoch-millis when ``ms``) to ISO 8601."""
    secs = float(unix)
    if ms:
        secs = secs / 1000.0
    return datetime.fromtimestamp(secs, tz=timezone.utc).isoformat()


def iso_to_unix(iso: str) -> int:
    """Convert an ISO 8601 time (with or without timezone) to Unix seconds."""
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def now_ms() -> int:
    """Current time as a UTC epoch-millisecond int (used for Kraken nonces)."""
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


# ---------------------------------------------------------------------------
# Batching
# ---------------------------------------------------------------------------


def chunk(items: Iterable[str], size: int = 50) -> Iterator[list[str]]:
    """Yield an iterable in fixed-size chunks.

    ``size`` defaults to 50 — the Kraken private Query endpoints (QueryOrders,
    QueryTrades, QueryLedgers) reject more than ~50 ids per call.
    """
    batch: list[str] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


# ---------------------------------------------------------------------------
# Pair symbol hints
# ---------------------------------------------------------------------------


def pair_hint(ref: str) -> str:
    """Return a cheap, catalog-free hint about how ``ref`` spells a pair.

    Kraken spells pairs three ways.  This returns one of ``"ws"``
    (``BTC/USD``), ``"alt"`` (``XBTUSD``), ``"pub"`` (``XXBTZUSD``), or
    ``"unknown"``.  Resolution to canonical forms is
    :class:`kraken_api.catalog.PairCatalog`'s job; this just helps humans
    and error messages.
    """
    ref = ref.strip()
    if "/" in ref:
        return "ws"
    upper = ref.upper()
    if len(upper) == 8 and upper.startswith(("X", "Z")):
        return "pub"
    if len(upper) == 6:
        return "alt"
    return "unknown"


def pair_repr(ref: str) -> str:
    """Human-friendly display form: the wsname with a slash, best effort."""
    ref = ref.strip()
    if "/" in ref:
        return ref.upper()
    upper = ref.upper()
    if upper == "XXBTZUSD":
        return "BTC/USD"
    if upper == "XETHZUSD":
        return "ETH/USD"
    return upper