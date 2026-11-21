"""
test_exceptions.py
Created by Aman (Montaggg) Ismailov

Unit tests for custom exceptions in src.exceptions.
"""

from src.exceptions import (
    BillingDomainError,
    ConfigurationError,
    CsvFormatError,
    CsvRowValidationError,
    DataFileNotFoundError,
    SubscriberNotFoundError,
    TariffCalculatorError,
)


def test_base_exception_attributes_and_serialization() -> None:
    """Base TariffCalculatorError stores fields and serializes via to_dict()."""
    exc = TariffCalculatorError(
        "Something went wrong",
        error_code="CUSTOM_ERROR",
        status_code=418,
        details={"foo": "bar"},
    )

    assert isinstance(exc, Exception)
    assert str(exc) == "Something went wrong"
    assert exc.message == "Something went wrong"
    assert exc.error_code == "CUSTOM_ERROR"
    assert exc.status_code == 418
    assert exc.details == {"foo": "bar"}
    assert exc.to_dict() == {
        "error": "CUSTOM_ERROR",
        "message": "Something went wrong",
        "details": {"foo": "bar"},
    }


def test_base_exception_defaults_and_details_isolation() -> None:
    """Base exception uses default code/status and copies caller details dict."""
    base_exc = TariffCalculatorError("Default error")
    assert base_exc.error_code == "TARIFF_CALCULATOR_ERROR"
    assert base_exc.status_code == 400
    assert base_exc.details == {}

    original_details = {"extra": "value"}
    merged_exc = BillingDomainError(
        "Domain error",
        reason="Broken invariant",
        details=original_details,
    )
    original_details["extra"] = "mutated"
    assert merged_exc.details == {"reason": "Broken invariant", "extra": "value"}


def test_data_file_not_found_error() -> None:
    """DataFileNotFoundError inherits from base and exposes file_path in details."""
    exc = DataFileNotFoundError("File missing", file_path="data/usage_P01.csv")

    assert isinstance(exc, TariffCalculatorError)
    assert str(exc) == "File missing"
    assert exc.status_code == 404
    assert exc.error_code == "FILE_NOT_FOUND"
    assert exc.to_dict() == {
        "error": "FILE_NOT_FOUND",
        "message": "File missing",
        "details": {"file_path": "data/usage_P01.csv"},
    }


def test_configuration_error() -> None:
    """ConfigurationError sets status 500 and includes config_file and reason."""
    exc = ConfigurationError(
        "Invalid tariffs schema",
        config_file="tariffs.json",
        reason="Negative price",
    )

    assert isinstance(exc, TariffCalculatorError)
    assert str(exc) == "Invalid tariffs schema"
    assert exc.status_code == 500
    assert exc.error_code == "INVALID_CONFIGURATION"
    assert exc.to_dict() == {
        "error": "INVALID_CONFIGURATION",
        "message": "Invalid tariffs schema",
        "details": {"config_file": "tariffs.json", "reason": "Negative price"},
    }


def test_subscriber_not_found_error() -> None:
    """SubscriberNotFoundError sets status 404 and stores subscriber_id and usage_file."""
    exc = SubscriberNotFoundError(
        "Subscriber not found",
        subscriber_id="P99",
        usage_file="usage_P99.csv",
    )

    assert isinstance(exc, TariffCalculatorError)
    assert str(exc) == "Subscriber not found"
    assert exc.status_code == 404
    assert exc.error_code == "SUBSCRIBER_NOT_FOUND"
    assert exc.to_dict() == {
        "error": "SUBSCRIBER_NOT_FOUND",
        "message": "Subscriber not found",
        "details": {"subscriber_id": "P99", "usage_file": "usage_P99.csv"},
    }


def test_csv_format_error() -> None:
    """CsvFormatError sets status 400 and includes file_path, missing_columns, and reason."""
    exc = CsvFormatError(
        "Missing required columns",
        file_path="usage_P01.csv",
        missing_columns=["amount"],
        reason="Header check failed",
    )

    assert isinstance(exc, TariffCalculatorError)
    assert str(exc) == "Missing required columns"
    assert exc.status_code == 400
    assert exc.error_code == "INVALID_CSV_FORMAT"
    assert exc.to_dict() == {
        "error": "INVALID_CSV_FORMAT",
        "message": "Missing required columns",
        "details": {
            "file_path": "usage_P01.csv",
            "missing_columns": ["amount"],
            "reason": "Header check failed",
        },
    }


def test_csv_row_validation_error() -> None:
    """CsvRowValidationError sets status 422 and includes row_number, column, value, and reason."""
    exc = CsvRowValidationError(
        "Invalid call amount",
        row_number=2,
        column="amount",
        value=-5,
        reason="Must be non-negative",
    )

    assert isinstance(exc, TariffCalculatorError)
    assert str(exc) == "Invalid call amount"
    assert exc.status_code == 422
    assert exc.error_code == "CSV_ROW_VALIDATION_ERROR"
    assert exc.to_dict() == {
        "error": "CSV_ROW_VALIDATION_ERROR",
        "message": "Invalid call amount",
        "details": {
            "row_number": 2,
            "column": "amount",
            "value": -5,
            "reason": "Must be non-negative",
        },
    }


def test_billing_domain_error() -> None:
    """BillingDomainError sets status 400 and includes reason in details."""
    exc = BillingDomainError("Invalid months count", reason="Expected exactly 3 months")

    assert isinstance(exc, TariffCalculatorError)
    assert str(exc) == "Invalid months count"
    assert exc.status_code == 400
    assert exc.error_code == "BILLING_DOMAIN_ERROR"
    assert exc.to_dict() == {
        "error": "BILLING_DOMAIN_ERROR",
        "message": "Invalid months count",
        "details": {"reason": "Expected exactly 3 months"},
    }
