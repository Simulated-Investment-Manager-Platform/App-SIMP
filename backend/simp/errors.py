"""Typed engine errors. A run that raises one of these is marked FAILED with the class name,
message and failing session. Nothing is caught-and-printed (enforced by ruff BLE/S110)."""

from __future__ import annotations

from datetime import date


class SimpError(Exception):
    """Base class for every expected, named failure."""

    session: date | None = None

    def __init__(self, message: str, *, session: date | None = None) -> None:
        super().__init__(message)
        self.session = session


class ConfigError(SimpError):
    pass


class DataWindowError(SimpError):
    pass


class DataMissingError(SimpError):
    """A required lake partition or manifest does not exist."""


class MissingPriceError(SimpError):
    pass


class LookAheadError(SimpError):
    pass


class CorporateActionError(SimpError):
    """Unreviewed / unsupported corporate action, fractional split share, or same-day split+dividend."""


class NavIdentityError(SimpError):
    pass


class OrderInvariantError(SimpError):
    pass


class MappingError(SimpError):
    pass


class ExecutionModelUnavailable(SimpError):
    pass


class SourceUnavailableError(SimpError):
    """A live data source (WRDS / BMLL) cannot be reached or is not configured."""


class SchemaMismatchError(SimpError):
    """A live source's columns differ from the documented schema."""


class CashShortfallError(SimpError):
    """Net liquid cash went negative: the fund would be borrowing."""
