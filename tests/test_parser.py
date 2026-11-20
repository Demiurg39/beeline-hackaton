"""Unit tests for catalog loading and CSV parsing in src.parser."""

import json
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


# ---------------------------------------------------------------------------
# Helpers for building catalog payloads
# ---------------------------------------------------------------------------


def _base_tariff(tariff_id: str = "T01") -> dict:
    return {
        "id": tariff_id,
        "name": "Test",
        "fee": "250",
        "minutes": 100,
        "gb": "10",
        "sms": 50,
        "overage": {"call": "2", "data": "50", "sms": "1"},
        "night_unlimited": False,
    }


def _base_subscriber(profile_id: str = "P01", tariff_id: str = "T01") -> dict:
    return {
        "id": profile_id,
        "current_tariff_id": tariff_id,
        "months": ["2026-08", "2026-09", "2026-10"],
        "usage_file": f"usage_{profile_id}.csv",
    }


def _base_addon(addon_id: str = "A01") -> dict:
    return {
        "id": addon_id,
        "name": "+5 GB",
        "fee": "100",
        "minutes": 0,
        "gb": "5",
        "sms": 0,
    }


def _base_tariffs_config(tariffs: list[dict]) -> dict:
    return {
        "currency": "KGS",
        "tariffs": tariffs,
        "roaming_rates": {"call": "30", "data": "200", "sms": "10"},
    }


def _write_catalog(
    tmp_path: Path,
    *,
    tariffs: object,
    addons: object,
    subscribers: object,
) -> None:
    """Write catalog JSON files. Nested structures are json-serialized."""
    (tmp_path / "tariffs.json").write_text(json.dumps(tariffs), encoding="utf-8")
    (tmp_path / "addons.json").write_text(json.dumps(addons), encoding="utf-8")
    (tmp_path / "subscribers.json").write_text(
        json.dumps(subscribers), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Required test suite (from the specification)
# ---------------------------------------------------------------------------


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
    day_1, night_1 = split_day_night_gb(
        datetime(2026, 8, 1, 0, 40, 0), 3000, Decimal("10")
    )
    assert day_1 == Decimal("4")
    assert night_1 == Decimal("6")
    assert day_1 + night_1 == Decimal("10")

    # Exit night: 06:30:00 + 3600s (60m) -> 30m night (50%), 30m day (50%)
    day_2, night_2 = split_day_night_gb(
        datetime(2026, 8, 1, 6, 30, 0), 3600, Decimal("8")
    )
    assert day_2 == Decimal("4")
    assert night_2 == Decimal("4")

    # Cross midnight: 23:30:00 + 7200s (120m -> 01:30:00) -> 75% day, 25% night
    day_3, night_3 = split_day_night_gb(
        datetime(2026, 8, 1, 23, 30, 0), 7200, Decimal("12")
    )
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
        '{"currency": "KGS", "tariffs": [], '
        '"roaming_rates": {"call": "1", "data": "1", "sms": "1"}}',
        encoding="utf-8",
    )
    (tmp_path / "addons.json").write_text("[]", encoding="utf-8")
    (tmp_path / "subscribers.json").write_text(
        '[{"id": "P01", "current_tariff_id": "T99", '
        '"months": ["2026-08", "2026-09", "2026-10"], '
        '"usage_file": "usage_P01.csv"}]',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError):
        load_catalog(tmp_path)


# ---------------------------------------------------------------------------
# Additional catalog coverage (previously missing)
# ---------------------------------------------------------------------------


def test_load_catalog_root_array_forms(tmp_path: Path) -> None:
    """addons.json and subscribers.json may be bare root arrays (no envelope)."""
    _write_catalog(
        tmp_path,
        tariffs=_base_tariffs_config([_base_tariff()]),
        addons=[_base_addon()],
        subscribers=[_base_subscriber()],
    )

    _, addons, subscribers = load_catalog(tmp_path)
    assert len(addons) == 1
    assert addons[0].id == "A01"
    assert SubscriberId("P01") in subscribers


def test_load_catalog_duplicate_tariff_id(tmp_path: Path) -> None:
    """Duplicate tariff.id raises ConfigurationError."""
    _write_catalog(
        tmp_path,
        tariffs=_base_tariffs_config([_base_tariff("T01"), _base_tariff("T01")]),
        addons=[],
        subscribers=[],
    )

    with pytest.raises(ConfigurationError):
        load_catalog(tmp_path)


def test_load_catalog_duplicate_addon_id(tmp_path: Path) -> None:
    """Duplicate addon.id raises ConfigurationError."""
    _write_catalog(
        tmp_path,
        tariffs=_base_tariffs_config([_base_tariff()]),
        addons=[_base_addon("A01"), _base_addon("A01")],
        subscribers=[],
    )

    with pytest.raises(ConfigurationError):
        load_catalog(tmp_path)


@pytest.mark.parametrize(
    "field_override",
    [
        {"fee": "-1"},
        {"gb": "-1"},
        {"minutes": -1},
        {"sms": -1},
        {"overage": {"call": "-1", "data": "1", "sms": "1"}},
        {"overage": {"call": "1", "data": "-1", "sms": "1"}},
        {"overage": {"call": "1", "data": "1", "sms": "-1"}},
    ],
)
def test_load_catalog_negative_tariff_fields(
    tmp_path: Path, field_override: dict
) -> None:
    """Negative prices or allowances on a tariff raise ConfigurationError."""
    tariff = _base_tariff()
    tariff.update(field_override)
    _write_catalog(
        tmp_path,
        tariffs=_base_tariffs_config([tariff]),
        addons=[],
        subscribers=[],
    )

    with pytest.raises(ConfigurationError):
        load_catalog(tmp_path)


def test_load_catalog_negative_roaming_rates(tmp_path: Path) -> None:
    """Negative roaming rate raises ConfigurationError."""
    cfg = _base_tariffs_config([_base_tariff()])
    cfg["roaming_rates"] = {"call": "-1", "data": "1", "sms": "1"}
    _write_catalog(tmp_path, tariffs=cfg, addons=[], subscribers=[])

    with pytest.raises(ConfigurationError):
        load_catalog(tmp_path)


def test_load_catalog_negative_addon_fields(tmp_path: Path) -> None:
    """Negative addon fee/allowance raises ConfigurationError."""
    addon = _base_addon()
    addon["fee"] = "-1"
    _write_catalog(
        tmp_path,
        tariffs=_base_tariffs_config([_base_tariff()]),
        addons=[addon],
        subscribers=[],
    )

    with pytest.raises(ConfigurationError):
        load_catalog(tmp_path)


def test_load_catalog_subscriber_wrong_months_count(tmp_path: Path) -> None:
    """Subscriber with != 3 months raises ConfigurationError."""
    subscriber = _base_subscriber()
    subscriber["months"] = ["2026-08", "2026-09"]
    _write_catalog(
        tmp_path,
        tariffs=_base_tariffs_config([_base_tariff()]),
        addons=[],
        subscribers=[subscriber],
    )

    with pytest.raises(ConfigurationError):
        load_catalog(tmp_path)


@pytest.mark.parametrize("bad_month", ["2026-13", "26-08", "2026/08", "2026-00"])
def test_load_catalog_subscriber_invalid_month_format(
    tmp_path: Path, bad_month: str
) -> None:
    """Subscriber month not matching ^\\d{4}-(0[1-9]|1[0-2])$ raises ConfigurationError."""
    subscriber = _base_subscriber()
    subscriber["months"] = ["2026-08", "2026-09", bad_month]
    _write_catalog(
        tmp_path,
        tariffs=_base_tariffs_config([_base_tariff()]),
        addons=[],
        subscribers=[subscriber],
    )

    with pytest.raises(ConfigurationError):
        load_catalog(tmp_path)


def test_load_catalog_broken_json_raises_configuration_error(
    tmp_path: Path,
) -> None:
    """Malformed JSON in any catalog file surfaces as ConfigurationError."""
    (tmp_path / "tariffs.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "addons.json").write_text("[]", encoding="utf-8")
    (tmp_path / "subscribers.json").write_text("[]", encoding="utf-8")

    with pytest.raises(ConfigurationError):
        load_catalog(tmp_path)


# ---------------------------------------------------------------------------
# Additional parser coverage (previously missing)
# ---------------------------------------------------------------------------


def test_parse_usage_missing_file(tmp_path: Path) -> None:
    """Missing usage CSV raises DataFileNotFoundError."""
    with pytest.raises(DataFileNotFoundError):
        parse_usage_csv(tmp_path / "does_not_exist.csv", MONTHS)


def test_parse_usage_zero_byte_file(tmp_path: Path) -> None:
    """Zero-byte CSV (no header) raises CsvFormatError with all required columns."""
    csv_file = tmp_path / "empty.csv"
    csv_file.write_bytes(b"")

    with pytest.raises(CsvFormatError) as exc_info:
        parse_usage_csv(csv_file, MONTHS)
    assert exc_info.value.details["missing_columns"] == ["amount", "date", "type"]


def test_parse_usage_bad_utf8(tmp_path: Path) -> None:
    """Non-UTF-8 bytes raise CsvFormatError."""
    csv_file = tmp_path / "bad_utf8.csv"
    csv_file.write_bytes(
        b"date,type,amount\n"
        b"2026-08-01T10:00:00,call,30\n"
        b"\xff\xfe\n"
    )

    with pytest.raises(CsvFormatError):
        parse_usage_csv(csv_file, MONTHS)


def test_months_invalid_format_raises_billing_domain_error(
    tmp_path: Path,
) -> None:
    """Invalid month format in months tuple raises BillingDomainError."""
    csv_file = tmp_path / "u.csv"
    csv_file.write_text("date,type,amount\n", encoding="utf-8")

    with pytest.raises(BillingDomainError):
        parse_usage_csv(csv_file, ("2026-08", "2026-13", "2026-09"))


def test_months_duplicates_raise_billing_domain_error(tmp_path: Path) -> None:
    """Duplicate months in months tuple raise BillingDomainError."""
    csv_file = tmp_path / "u.csv"
    csv_file.write_text("date,type,amount\n", encoding="utf-8")

    with pytest.raises(BillingDomainError):
        parse_usage_csv(csv_file, ("2026-08", "2026-08", "2026-09"))


def test_months_order_preserved_in_output(tmp_path: Path) -> None:
    """Result order follows the input months tuple, not chronological order."""
    csv_file = tmp_path / "u.csv"
    csv_file.write_text("date,type,amount\n", encoding="utf-8")

    result = parse_usage_csv(csv_file, ("2026-10", "2026-08", "2026-09"))
    assert [m.month for m in result] == ["2026-10", "2026-08", "2026-09"]


def test_roaming_data_goes_entirely_to_roaming_gb(tmp_path: Path) -> None:
    """Roaming data volume is never split into day/night home buckets."""
    csv_file = tmp_path / "u.csv"
    csv_file.write_text(
        "date,type,amount,duration_sec,roaming\n"
        # Session fully inside night window, but roaming=1 -> all to roaming_gb.
        "2026-08-01T02:00:00,data,2.5,3600,1\n",
        encoding="utf-8",
    )

    result = parse_usage_csv(csv_file, MONTHS)

    assert result[0].roaming_gb == Decimal("2.5")
    assert result[0].home_day_gb == Decimal("0")
    assert result[0].home_night_gb == Decimal("0")


def test_fractional_call_amount_rounds_up(tmp_path: Path) -> None:
    """Fractional seconds on a call still round up to one minute."""
    csv_file = tmp_path / "u.csv"
    csv_file.write_text(
        "date,type,amount\n"
        "2026-08-01T10:00:00,call,30.5\n",
        encoding="utf-8",
    )

    result = parse_usage_csv(csv_file, MONTHS)
    assert result[0].home_call_min == 1


def test_sms_amount_integral_but_decimal_form(tmp_path: Path) -> None:
    """SMS amount '5.0' is accepted as 5 (integer-valued Decimal)."""
    csv_file = tmp_path / "u.csv"
    csv_file.write_text(
        "date,type,amount\n"
        "2026-08-01T10:00:00,sms,5.0\n",
        encoding="utf-8",
    )

    result = parse_usage_csv(csv_file, MONTHS)
    assert result[0].home_sms == 5


def test_date_only_iso_rejected(tmp_path: Path) -> None:
    """Date-only ISO strings are rejected — the parser requires a time component."""
    csv_file = tmp_path / "u.csv"
    csv_file.write_text(
        "date,type,amount\n"
        "2026-08-01,call,30\n",
        encoding="utf-8",
    )

    with pytest.raises(CsvRowValidationError) as exc_info:
        parse_usage_csv(csv_file, MONTHS)
    assert exc_info.value.details["column"] == "date"


@pytest.mark.parametrize(
    "row_line",
    [
        "2026-08-01T10:00:00,data,1.5,-1,0",   # negative duration_sec
        "2026-08-01T10:00:00,data,1.5,1.5,0",  # fractional duration_sec
    ],
)
def test_invalid_duration_sec_values_rejected(
    tmp_path: Path, row_line: str
) -> None:
    """Negative / fractional duration_sec raise CsvRowValidationError."""
    csv_file = tmp_path / "u.csv"
    csv_file.write_text(
        "date,type,amount,duration_sec,roaming\n"
        f"{row_line}\n",
        encoding="utf-8",
    )

    with pytest.raises(CsvRowValidationError) as exc_info:
        parse_usage_csv(csv_file, MONTHS)
    assert exc_info.value.details["column"] == "duration_sec"


def test_split_fully_inside_day_and_night() -> None:
    """Whole-session splits when the interval lies fully inside day or night."""
    assert split_day_night_gb(
        datetime(2026, 8, 1, 12, 0, 0), 3600, Decimal("5")
    ) == (Decimal("5"), Decimal("0"))
    assert split_day_night_gb(
        datetime(2026, 8, 1, 2, 0, 0), 1800, Decimal("5")
    ) == (Decimal("0"), Decimal("5"))


@pytest.mark.parametrize(
    ("start", "duration_sec", "amount"),
    [
        (datetime(2026, 8, 1, 0, 40, 0), 3000, Decimal("10")),
        (datetime(2026, 8, 1, 6, 30, 0), 3600, Decimal("8")),
        (datetime(2026, 8, 1, 23, 30, 0), 7200, Decimal("12")),
        # Multi-day session crossing two full night windows.
        (datetime(2026, 8, 1, 23, 0, 0), 86400, Decimal("24")),
    ],
)
def test_split_invariant_day_plus_night_equals_amount(
    start: datetime, duration_sec: int, amount: Decimal
) -> None:
    """day_gb + night_gb must equal amount_gb exactly (no rounding drift)."""
    day, night = split_day_night_gb(start, duration_sec, amount)
    assert day + night == amount
    assert day >= Decimal("0")
    assert night >= Decimal("0")