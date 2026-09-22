"""Signing must reproduce the official Kraken documentation example.

The Kraken REST auth guide publishes a worked vector:
    https://docs.kraken.com/exchange/guides/rest/authentication
If this test fails, request signing is broken — every private call would be
rejected with API-Key:Invalid signature.
"""

from __future__ import annotations

from kraken_api.transport import KrakenTransport, get_kraken_signature

DOC_SECRET = "kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg=="
DOC_PATH = "/0/private/AddOrder"
DOC_NONCE = "1616492376594"
DOC_EXPECTED = "4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ=="


def test_signature_matches_doc_vector() -> None:
    payload = {
        "nonce": DOC_NONCE,
        "ordertype": "limit",
        "pair": "XBTUSD",
        "price": 37500,
        "type": "buy",
        "volume": 1.25,
    }
    assert get_kraken_signature(DOC_PATH, payload, DOC_SECRET) == DOC_EXPECTED


def test_signature_changes_with_nonce() -> None:
    a = get_kraken_signature(DOC_PATH, {"nonce": "1", "pair": "XBTUSD"}, DOC_SECRET)
    b = get_kraken_signature(DOC_PATH, {"nonce": "2", "pair": "XBTUSD"}, DOC_SECRET)
    assert a != b


def test_signature_changes_with_path() -> None:
    a = get_kraken_signature("/0/private/Balance", {"nonce": "1"}, DOC_SECRET)
    b = get_kraken_signature("/0/private/TradeBalance", {"nonce": "1"}, DOC_SECRET)
    assert a != b


def test_nonce_is_strictly_increasing() -> None:
    transport = KrakenTransport(api_key="k", api_secret=DOC_SECRET)
    seen = [int(transport._next_nonce()) for _ in range(50)]
    # Even with a frozen clock, the counter must never stay flat or go back.
    assert all(seen[i] < seen[i + 1] for i in range(len(seen) - 1))


def test_nonce_catches_up_after_clock_drift() -> None:
    transport = KrakenTransport(api_key="k", api_secret=DOC_SECRET)
    first = int(transport._next_nonce())
    # Pretend a previous run used a larger nonce (clock was ahead).
    transport._last_nonce = first + 1000
    assert int(transport._next_nonce()) == first + 1001