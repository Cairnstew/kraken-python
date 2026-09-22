"""Exception hierarchy for the Kraken API wrapper."""

from __future__ import annotations


class KrakenError(Exception):
    """Base class for all errors raised by this package."""


class ConfigurationError(KrakenError):
    """Raised when required environment/config values are missing."""


class AuthenticationError(KrakenError):
    """Raised when API credentials are missing, invalid, or a signature fails."""


class APIError(KrakenError):
    """The Kraken API answered with one or more error strings in its envelope.

    ``errors`` holds the raw ``["EGeneral:...", ...]`` list from the response.
    """

    def __init__(self, errors: list[str], endpoint: str = "") -> None:
        self.errors = list(errors or [])
        self.endpoint = endpoint
        prefix = f"{endpoint}: " if endpoint else ""
        super().__init__(f"{prefix}Kraken API error: {'; '.join(self.errors)}")


class RateLimitError(APIError):
    """Kraken rejected the request for exceeding the rate limit."""


class InvalidPairError(KrakenError):
    """A symbol/pair string could not be resolved to a known Kraken pair."""


class OrderError(KrakenError):
    """An order-related operation failed (validation, rejection, not found)."""


class WebSocketError(KrakenError):
    """A WebSocket connection, subscribe, or message parse failed."""