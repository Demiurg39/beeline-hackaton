"""Custom exceptions hierarchy for the tariff calculator."""

from typing import Any


class TariffCalculatorError(Exception):
    """Base exception for all tariff calculator errors."""

    default_error_code: str = "TARIFF_CALCULATOR_ERROR"
    default_status_code: int = 400

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the base exception with message, code, HTTP status, and details."""
        super().__init__(message)
        self.message: str = message
        self.error_code: str = error_code if error_code is not None else self.default_error_code
        self.status_code: int = status_code if status_code is not None else self.default_status_code
        self.details: dict[str, Any] = dict(details) if details is not None else {}

    def to_dict(self) -> dict[str, Any]:
        """Return a serializable dictionary representation of the error."""
        return {
            "error": self.error_code,
            "message": self.message,
            "details": self.details,
        }