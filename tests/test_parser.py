"""Unit tests for catalog loading and CSV parsing in src.parser."""

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.exceptions import (
    BillingDomainError,
    ConfigurationError,
    CsvFormatError,
    CsvRowValidationError,
    DataFileNotFoundError,
    SubscriberNotFoundError,
)
from src.models import MonthlyUsage, SubscriberId
from src.parser import (
    calc_call_minutes,
    load_catalog,
    parse_usage_csv,
    resolve_subscriber,
    split_day_night_gb,
)

MONTHS: tuple[str, ...] = ("2026-08", "2026-09", "2026-10")


def test_empty_csv_with_headers_returns_three_zero_months(tmp_path: Path) -> None:
    """Header-only CSV returns 3 zero-filled MonthlyUsage records without errors."""
    csv_file = tmp_path / "usage_empty.csv"
    csv_file.write_text("date,type,amount\n", encoding="utf-8")

    result = parse_usage_csv(csv_file, MONTHS)

    assert result == [
        MonthlyUsage(month="2026-08"),
        MonthlyUsage(month="2026-09"),
        MonthlyUsage(month="2026-10"),
    ]


def test_empty_middle_month_preserved_in_order(tmp_path: Path) -> None:
    """Middle month with zero events remains in output list with zero counters."""
    csv_file = tmp_path / "usage_middle.csv"
    csv_file.write_text(
        "date,type,amount,duration_sec,roaming\n"
        "2026-08-10T12:00:00,sms,3,0,0\n"
        "2026-10-05T15:30:00,call,61,0,1\n",
        encoding="utf-8",
    )

    result = parse_usage_csv(csv_file, MONTHS)

    assert len(result) == 3
    assert result[0] == MonthlyUsage(month="2026-08", home_sms=3)
    assert result[1] == MonthlyUsage(month="2026-09")
    assert result[2] == MonthlyUsage(month="2026-10", roaming_call_min=2)


def test_call_minutes_rounding_per_record(tmp_path: Path) -> None:
    """Each call is rounded up independently before monthly summation."""
    assert calc_call_minutes(Decimal("0")) == 0
    assert calc_call_minutes(Decimal("1")) == 1
    assert calc_call_minutes(Decimal("60")) == 1
    assert calc_call_minutes(Decimal("61")) == 2

    csv_file = tmp_path / "usage_calls.csv"
    csv_file.write_text(
        "date,type,amount\n"
        "2026-08-01T10:00:00,call,30\n"
        "2026-08-01T10:05:00,call,30\n"
        "2026-08-01T10:10:00,call,0\n",
        encoding="utf-8",
    )

    result = parse_usage_csv(csv_file, MONTHS)
    assert result[0].home_call_min == 2


def test_zero_duration_data_night_window_boundaries() -> None:
    """Zero-duration data sessions use half-open [01:00:00, 07:00:00) at start_dt."""
    amount = Decimal("2.5")

    assert split_day_night_gb(datetime(2026, 8, 1, 0, 59, 59), 0, amount) == (
        Decimal("2.5"),
        Decimal("0"),
    )
    assert split_day_night_gb(datetime(2026, 8, 1, 1, 0, 0), 0, amount) == (
        Decimal("0"),
        Decimal("2.5"),
    )
    assert split_day_night_gb(datetime(2026, 8, 1, 7, 0, 0), 0, amount) == (
        Decimal("2.5"),
        Decimal("0"),
    )


def test_positive_duration_data_night_crossings() -> None:
    """Positive-duration data sessions split volume proportionally across night boundaries."""
    # Enter night: 00:40:00 + 3000s (50m) -> 20m day (40%), 30m night (60%)
    day_1, night_1 = split_day_night_gb(datetime(2026, 8, 1, 0, 40, 0), 3000, Decimal("10"))
    assert day_1 == Decimal("4")
    assert night_1 == Decimal("6")
    assert day_1 + night_1 == Decimal("10")

    # Exit night: 06:30:00 + 3600s (60m) -> 30m night (50%), 30m day (50%)
    day_2, night_2 = split_day_night_gb(datetime(2026, 8, 1, 6, 30, 0), 3600, Decimal("8"))
    assert day_2 == Decimal("4")
    assert night_2 == Decimal("4")

    # Cross midnight: 23:30:00 + 7200s (120m -> 01:30:00) -> 90m day (75%), 30m night (25%)
    day_3, night_3 = split_day_night_gb(datetime(2026, 8, 1, 23, 30, 0), 7200, Decimal("12"))
    assert day_3 == Decimal("9")
    assert night_3 == Decimal("3")


def test_missing_optional_columns_default_values(tmp_path: Path) -> None:
    """CSV without duration_sec and roaming columns defaults them to 0 and False."""
    csv_file = tmp_path / "usage_minimal.csv"
    csv_file.write_text(
        "date,type,amount\n"
        "2026-08-15T02:00:00,data,1.25\n",
        encoding="utf-8",
    )

    result = parse_usage_csv(csv_file, MONTHS)

    assert result[0].home_day_gb == Decimal("0")
    assert result[0].home_night_gb == Decimal("1.25")
    assert result[0].roaming_gb == Decimal("0")


@pytest.mark.parametrize(
    ("row_line", "expected_column"),
    [
        ("2026-08-01T12:00:00,mms,1,0,0", "type"),
        ("2026-08-01T12:00:00,call,-10,0,0", "amount"),
        ("2026-08-01T12:00:00,sms,1.5,0,0", "amount"),
        ("2026-08-01T12:00:00,call,30,0,2", "roaming"),
        ("not-a-date,call,30,0,0", "date"),
        ("2026-08-01T12:00:00+06:00,call,30,0,0", "date"),
        ("2026-11-01T12:00:00,call,30,0,0", "date"),
        ("2026-08-01T12:00:00,call,30,15,0", "duration_sec"),
    ],
)
def test_row_validation_errors_report_exact_line_and_column(
    tmp_path: Path,
    row_line: str,
    expected_column: str,
) -> None:
    """Invalid row values raise CsvRowValidationError with row_number=3 and column name."""
    csv_file = tmp_path / "usage_invalid.csv"
    csv_file.write_text(
        "date,type,amount,duration_sec,roaming\n"
        "2026-08-01T10:00:00,sms,1,0,0\n"
        f"{row_line}\n",
        encoding="utf-8",
    )

    with pytest.raises(CsvRowValidationError) as exc_info:
        parse_usage_csv(csv_file, MONTHS)

    assert exc_info.value.details["row_number"] == 3
    assert exc_info.value.details["column"] == expected_column


def test_csv_format_and_domain_errors(tmp_path: Path) -> None:
    """Missing headers raise CsvFormatError; wrong months count raises BillingDomainError."""
    bad_csv = tmp_path / "bad_header.csv"
    bad_csv.write_text("date,type\n", encoding="utf-8")

    with pytest.raises(CsvFormatError) as fmt_exc:
        parse_usage_csv(bad_csv, MONTHS)
    assert fmt_exc.value.details["missing_columns"] == ["amount"]

    valid_csv = tmp_path / "valid.csv"
    valid_csv.write_text("date,type,amount\n", encoding="utf-8")
    with pytest.raises(BillingDomainError):
        parse_usage_csv(valid_csv, ("2026-08", "2026-09"))


def test_load_catalog_wrapped_and_resolve_subscriber(tmp_path: Path) -> None:
    """load_catalog supports wrapped JSON objects and resolve_subscriber matches by filename."""
    (tmp_path / "tariffs.json").write_text(
        """
        {
          "currency": "KGS",
          "tariffs": [
            {
              "id": "T01",
              "name": "Start",
              "fee": "250",
              "minutes": 100,
              "gb": "10",
              "sms": 50,
              "overage": {"call": "2", "data": "50", "sms": "1"},
              "night_unlimited": false
            }
          ],
          "roaming_rates": {"call": "30", "data": "200", "sms": "10"}
        }
        """,
        encoding="utf-8",
    )
    (tmp_path / "addons.json").write_text(
        """
        {
          "addons": [
            {"id": "A01", "name": "+5 GB", "fee": "100", "minutes": 0, "gb": "5", "sms": 0}
          ]
        }
        """,
        encoding="utf-8",
    )
    (tmp_path / "subscribers.json").write_text(
        """
        {
          "subscribers": [
            {
              "id": "P01",
              "current_tariff_id": "T01",
              "months": ["2026-08", "2026-09", "2026-10"],
              "usage_file": "usage_P01.csv"
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    tariffs_cfg, addons, subscribers = load_catalog(tmp_path)
    assert tariffs_cfg.currency == "KGS"
    assert len(addons) == 1
    assert SubscriberId("P01") in subscribers

    profile = resolve_subscriber(Path("some/nested/dir/usage_P01.csv"), subscribers)
    assert profile.id == SubscriberId("P01")

    with pytest.raises(SubscriberNotFoundError):
        resolve_subscriber(Path("usage_UNKNOWN.csv"), subscribers)


def test_load_catalog_validation_errors(tmp_path: Path) -> None:
    """Missing catalog files raise DataFileNotFoundError; unknown tariff raises ConfigurationError."""
    with pytest.raises(DataFileNotFoundError):
        load_catalog(tmp_path)

    (tmp_path / "tariffs.json").write_text(
        '{"currency": "KGS", "tariffs": [], "roaming_rates": {"call": "1", "data": "1", "sms": "1"}}',
        encoding="utf-8",
    )
    (tmp_path / "addons.json").write_text("[]", encoding="utf-8")
    (tmp_path / "subscribers.json").write_text(
        '[{"id": "P01", "current_tariff_id": "T99", "months": ["2026-08", "2026-09", "2026-10"], "usage_file": "usage_P01.csv"}]',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError):
        load_catalog(tmp_path)