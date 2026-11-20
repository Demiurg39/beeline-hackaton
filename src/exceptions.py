"""
exceptions.py
Created by Aman (Montaggg) Ismailov

Custom exceptions hierarchy for the tariff calculator.
"""

from typing import Any

__all__ = [
    "BillingDomainError",
    "ConfigurationError",
    "CsvFormatError",
    "CsvRowValidationError",
    "DataFileNotFoundError",
    "SubscriberNotFoundError",
    "TariffCalculatorError",
]


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
        self.error_code: str = (
            error_code if error_code is not None else self.default_error_code
        )
        self.status_code: int = (
            status_code if status_code is not None else self.default_status_code
        )
        self.details: dict[str, Any] = dict(details) if details is not None else {}

    def to_dict(self) -> dict[str, Any]:
        """Return a serializable dictionary representation of the error."""
        return {
            "error": self.error_code,
            "message": self.message,
            "details": self.details,
        }


class DataFileNotFoundError(TariffCalculatorError):
    """Raised when a required usage CSV or JSON configuration file is missing or unreadable."""

    default_error_code: str = "FILE_NOT_FOUND"
    default_status_code: int = 404

    def __init__(
        self,
        message: str,
        *,
        file_path: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize DataFileNotFoundError with the missing file path."""
        merged_details: dict[str, Any] = {"file_path": file_path}
        if details:
            merged_details.update(details)
        super().__init__(message, details=merged_details)


class ConfigurationError(TariffCalculatorError):
    """Raised when JSON configuration files have syntax errors or violate schema invariants."""

    default_error_code: str = "INVALID_CONFIGURATION"
    default_status_code: int = 500

    def __init__(
        self,
        message: str,
        *,
        config_file: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize ConfigurationError with config file name and failure reason."""
        merged_details: dict[str, Any] = {
            "config_file": config_file,
            "reason": reason,
        }
        if details:
            merged_details.update(details)
        super().__init__(message, details=merged_details)


class SubscriberNotFoundError(TariffCalculatorError):
    """Raised when a subscriber cannot be resolved in subscribers.json."""

    default_error_code: str = "SUBSCRIBER_NOT_FOUND"
    default_status_code: int = 404

    def __init__(
        self,
        message: str,
        *,
        subscriber_id: str | None = None,
        usage_file: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize SubscriberNotFoundError with subscriber ID and/or usage file path."""
        merged_details: dict[str, Any] = {
            "subscriber_id": subscriber_id,
            "usage_file": usage_file,
        }
        if details:
            merged_details.update(details)
        super().__init__(message, details=merged_details)


class CsvFormatError(TariffCalculatorError):
    """Raised when a usage CSV file is unreadable, empty, or missing required headers."""

    default_error_code: str = "INVALID_CSV_FORMAT"
    default_status_code: int = 400

    def __init__(
        self,
        message: str,
        *,
        file_path: str,
        reason: str,
        missing_columns: list[str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize CsvFormatError with file path, missing columns, and reason."""
        merged_details: dict[str, Any] = {
            "file_path": file_path,
            "missing_columns": missing_columns,
            "reason": reason,
        }
        if details:
            merged_details.update(details)
        super().__init__(message, details=merged_details)


class CsvRowValidationError(TariffCalculatorError):
    """Raised when a specific row in a usage CSV file contains invalid field values."""

    default_error_code: str = "CSV_ROW_VALIDATION_ERROR"
    default_status_code: int = 422

    def __init__(
        self,
        message: str,
        *,
        row_number: int,
        column: str,
        value: Any,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize CsvRowValidationError with 1-based row number (starting at 2), column, value, and reason."""
        merged_details: dict[str, Any] = {
            "row_number": row_number,
            "column": column,
            "value": value,
            "reason": reason,
        }
        if details:
            merged_details.update(details)
        super().__init__(message, details=merged_details)


class BillingDomainError(TariffCalculatorError):
    """Raised when billing calculation invariants (such as 3-month window or YYYY-MM format) are violated."""

    default_error_code: str = "BILLING_DOMAIN_ERROR"
    default_status_code: int = 400

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize BillingDomainError with the domain invariant violation reason."""
        merged_details: dict[str, Any] = {"reason": reason}
        if details:
            merged_details.update(details)
        super().__init__(message, details=merged_details)
