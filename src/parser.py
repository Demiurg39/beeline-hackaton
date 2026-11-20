"""
parser.py

Catalog loader and CSV usage parser with strict validation and Decimal arithmetic.
"""

import csv
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path

import msgspec

from src.exceptions import (
    BillingDomainError,
    ConfigurationError,
    CsvFormatError,
    CsvRowValidationError,
    DataFileNotFoundError,
    SubscriberNotFoundError,
)
from src.models import (
    Addon,
    AddonId,
    MonthlyUsage,
    Rates,
    SubscriberId,
    SubscriberProfile,
    TariffId,
    TariffsConfig,
)

BILLING_PERIOD_MONTHS: int = 3
SECONDS_PER_MINUTE: Decimal = Decimal(60)
_ZERO_DECIMAL: Decimal = Decimal(0)
_MONTH_PATTERN: re.Pattern[str] = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_REQUIRED_CSV_COLUMNS: set[str] = {"date", "type", "amount"}
_ALLOWED_USAGE_TYPES: set[str] = {"call", "sms", "data"}
_NIGHT_START_TIME: time = time(1, 0, 0)
_NIGHT_END_TIME: time = time(7, 0, 0)


class _AddonsEnvelope(msgspec.Struct, frozen=True):
    """Wrapper schema when addons.json has a root object with an 'addons' key."""

    addons: tuple[Addon, ...]


class _SubscribersEnvelope(msgspec.Struct, frozen=True):
    """Wrapper schema when subscribers.json has a root object with a 'subscribers' key."""

    subscribers: tuple[SubscriberProfile, ...]


@dataclass
class _MonthlyAccumulator:
    """Mutable per-month counters used while streaming CSV rows."""

    home_call_min: int = 0
    home_sms: int = 0
    home_day_gb: Decimal = _ZERO_DECIMAL
    home_night_gb: Decimal = _ZERO_DECIMAL
    roaming_call_min: int = 0
    roaming_sms: int = 0
    roaming_gb: Decimal = _ZERO_DECIMAL

    def to_monthly_usage(self, month: str) -> MonthlyUsage:
        """Convert accumulated counters into an immutable MonthlyUsage struct."""
        return MonthlyUsage(
            month=month,
            home_call_min=self.home_call_min,
            home_sms=self.home_sms,
            home_day_gb=self.home_day_gb,
            home_night_gb=self.home_night_gb,
            roaming_call_min=self.roaming_call_min,
            roaming_sms=self.roaming_sms,
            roaming_gb=self.roaming_gb,
        )


def _read_file_bytes(file_path: Path) -> bytes:
    """Read raw bytes from a file or raise DataFileNotFoundError."""
    if not file_path.is_file():
        raise DataFileNotFoundError(
            f"Required file not found: {file_path}",
            file_path=str(file_path),
        )
    try:
        return file_path.read_bytes()
    except OSError as exc:
        raise DataFileNotFoundError(
            f"Unable to read file '{file_path}': {exc}",
            file_path=str(file_path),
        ) from exc


def _validate_non_negative_decimal(
    value: Decimal, config_file: str, field_name: str
) -> None:
    """Ensure a Decimal value in JSON configuration is finite and >= 0."""
    if not value.is_finite() or value < _ZERO_DECIMAL:
        reason = f"Field '{field_name}' must be finite and >= 0, got {value}"
        raise ConfigurationError(
            f"Invalid configuration in '{config_file}': {reason}",
            config_file=config_file,
            reason=reason,
        )


def _validate_rates(rates: Rates, config_file: str, label: str) -> None:
    """Ensure all per-unit rates are finite and >= 0."""
    _validate_non_negative_decimal(rates.call, config_file, f"{label}.call")
    _validate_non_negative_decimal(rates.data, config_file, f"{label}.data")
    _validate_non_negative_decimal(rates.sms, config_file, f"{label}.sms")


def _validate_tariffs_config(config: TariffsConfig, config_file: str) -> set[TariffId]:
    """Validate tariffs.json schema semantics and return known tariff IDs."""
    _validate_rates(config.roaming_rates, config_file, "roaming_rates")

    seen_tariff_ids: set[TariffId] = set()
    for tariff in config.tariffs:
        if tariff.id in seen_tariff_ids:
            reason = f"Duplicate tariff id '{tariff.id}'"
            raise ConfigurationError(
                f"Invalid configuration in '{config_file}': {reason}",
                config_file=config_file,
                reason=reason,
            )
        seen_tariff_ids.add(tariff.id)

        _validate_non_negative_decimal(
            tariff.fee, config_file, f"tariff '{tariff.id}'.fee"
        )
        _validate_non_negative_decimal(
            tariff.gb, config_file, f"tariff '{tariff.id}'.gb"
        )

        if tariff.minutes < 0 or tariff.sms < 0:
            reason = f"Tariff '{tariff.id}' minutes and sms must be >= 0"
            raise ConfigurationError(
                f"Invalid configuration in '{config_file}': {reason}",
                config_file=config_file,
                reason=reason,
            )

        _validate_rates(tariff.overage, config_file, f"tariff '{tariff.id}'.overage")

    return seen_tariff_ids


def _validate_addons(addons: tuple[Addon, ...], config_file: str) -> None:
    """Validate addons.json uniqueness and non-negative fees/allowances."""
    seen_addon_ids: set[AddonId] = set()
    for addon in addons:
        if addon.id in seen_addon_ids:
            reason = f"Duplicate addon id '{addon.id}'"
            raise ConfigurationError(
                f"Invalid configuration in '{config_file}': {reason}",
                config_file=config_file,
                reason=reason,
            )
        seen_addon_ids.add(addon.id)

        _validate_non_negative_decimal(
            addon.fee, config_file, f"addon '{addon.id}'.fee"
        )
        _validate_non_negative_decimal(addon.gb, config_file, f"addon '{addon.id}'.gb")

        if addon.minutes < 0 or addon.sms < 0:
            reason = f"Addon '{addon.id}' minutes and sms must be >= 0"
            raise ConfigurationError(
                f"Invalid configuration in '{config_file}': {reason}",
                config_file=config_file,
                reason=reason,
            )


def _validate_subscribers(
    profiles: tuple[SubscriberProfile, ...],
    known_tariff_ids: set[TariffId],
    config_file: str,
) -> dict[SubscriberId, SubscriberProfile]:
    """Validate subscribers.json entries and return a mapping keyed by SubscriberId."""
    subscribers_map: dict[SubscriberId, SubscriberProfile] = {}
    for profile in profiles:
        if profile.id in subscribers_map:
            reason = f"Duplicate subscriber id '{profile.id}'"
            raise ConfigurationError(
                f"Invalid configuration in '{config_file}': {reason}",
                config_file=config_file,
                reason=reason,
            )

        if profile.current_tariff_id not in known_tariff_ids:
            reason = (
                f"Subscriber '{profile.id}' references unknown tariff "
                f"'{profile.current_tariff_id}'"
            )
            raise ConfigurationError(
                f"Invalid configuration in '{config_file}': {reason}",
                config_file=config_file,
                reason=reason,
            )

        if len(profile.months) != BILLING_PERIOD_MONTHS:
            reason = (
                f"Subscriber '{profile.id}' must define exactly {BILLING_PERIOD_MONTHS} "
                f"billing months, got {len(profile.months)}"
            )
            raise ConfigurationError(
                f"Invalid configuration in '{config_file}': {reason}",
                config_file=config_file,
                reason=reason,
            )

        for month in profile.months:
            if _MONTH_PATTERN.match(month) is None:
                reason = (
                    f"Subscriber '{profile.id}' has invalid month '{month}'; "
                    "expected 'YYYY-MM'"
                )
                raise ConfigurationError(
                    f"Invalid configuration in '{config_file}': {reason}",
                    config_file=config_file,
                    reason=reason,
                )

        subscribers_map[profile.id] = profile

    return subscribers_map


def load_catalog(
    data_dir: Path,
) -> tuple[TariffsConfig, tuple[Addon, ...], dict[SubscriberId, SubscriberProfile]]:
    """Load and validate tariffs.json, addons.json, and subscribers.json."""
    tariffs_path = data_dir / "tariffs.json"
    addons_path = data_dir / "addons.json"
    subscribers_path = data_dir / "subscribers.json"

    tariffs_bytes = _read_file_bytes(tariffs_path)
    addons_bytes = _read_file_bytes(addons_path)
    subscribers_bytes = _read_file_bytes(subscribers_path)

    try:
        tariffs_config = msgspec.json.decode(tariffs_bytes, type=TariffsConfig)
    except msgspec.MsgspecError as exc:
        raise ConfigurationError(
            f"Failed to decode '{tariffs_path.name}': {exc}",
            config_file=str(tariffs_path),
            reason=str(exc),
        ) from exc

    try:
        decoded_addons = msgspec.json.decode(
            addons_bytes,
            type=tuple[Addon, ...] | _AddonsEnvelope,
        )
    except msgspec.MsgspecError as exc:
        raise ConfigurationError(
            f"Failed to decode '{addons_path.name}': {exc}",
            config_file=str(addons_path),
            reason=str(exc),
        ) from exc
    addons = (
        decoded_addons.addons
        if isinstance(decoded_addons, _AddonsEnvelope)
        else decoded_addons
    )

    try:
        decoded_subscribers = msgspec.json.decode(
            subscribers_bytes,
            type=tuple[SubscriberProfile, ...] | _SubscribersEnvelope,
        )
    except msgspec.MsgspecError as exc:
        raise ConfigurationError(
            f"Failed to decode '{subscribers_path.name}': {exc}",
            config_file=str(subscribers_path),
            reason=str(exc),
        ) from exc
    profiles = (
        decoded_subscribers.subscribers
        if isinstance(decoded_subscribers, _SubscribersEnvelope)
        else decoded_subscribers
    )

    known_tariff_ids = _validate_tariffs_config(tariffs_config, str(tariffs_path))
    _validate_addons(addons, str(addons_path))
    subscribers_map = _validate_subscribers(
        profiles,
        known_tariff_ids,
        str(subscribers_path),
    )

    return tariffs_config, addons, subscribers_map


def resolve_subscriber(
    usage_file: Path,
    subscribers: dict[SubscriberId, SubscriberProfile],
) -> SubscriberProfile:
    """Find a subscriber profile matching the given usage CSV file path."""
    target_filename = usage_file.name
    for profile in subscribers.values():
        if Path(profile.usage_file).name == target_filename:
            return profile

    raise SubscriberNotFoundError(
        f"No subscriber found for usage file '{target_filename}'",
        subscriber_id=None,
        usage_file=str(usage_file),
    )


def calc_call_minutes(duration_sec: Decimal) -> int:
    """Round a single call duration in seconds up to whole minutes (0 -> 0)."""
    if duration_sec == _ZERO_DECIMAL:
        return 0
    return int(
        (duration_sec / SECONDS_PER_MINUTE).to_integral_value(rounding=ROUND_CEILING)
    )


def split_day_night_gb(
    start_dt: datetime,
    duration_sec: int,
    amount_gb: Decimal,
) -> tuple[Decimal, Decimal]:
    """Split home data volume into (day_gb, night_gb) for the [01:00:00, 07:00:00) window."""
    if duration_sec == 0:
        if _NIGHT_START_TIME <= start_dt.time() < _NIGHT_END_TIME:
            return _ZERO_DECIMAL, amount_gb
        return amount_gb, _ZERO_DECIMAL

    end_dt = start_dt + timedelta(seconds=duration_sec)
    night_sec = 0
    current_day = start_dt.date()
    last_day = end_dt.date()

    while current_day <= last_day:
        night_start = datetime.combine(current_day, _NIGHT_START_TIME)
        night_end = datetime.combine(current_day, _NIGHT_END_TIME)
        overlap_start = max(start_dt, night_start)
        overlap_end = min(end_dt, night_end)
        if overlap_end > overlap_start:
            night_sec += int((overlap_end - overlap_start).total_seconds())
        current_day += timedelta(days=1)

    night_gb = amount_gb * Decimal(night_sec) / Decimal(duration_sec)
    day_gb = amount_gb - night_gb
    return day_gb, night_gb


def _validate_months_tuple(months: tuple[str, ...]) -> None:
    """Validate that months contains exactly 3 distinct valid YYYY-MM strings."""
    if len(months) != BILLING_PERIOD_MONTHS:
        reason = f"Expected exactly {BILLING_PERIOD_MONTHS} billing months, got {len(months)}"
        raise BillingDomainError(reason, reason=reason)

    seen: set[str] = set()
    for month in months:
        if _MONTH_PATTERN.match(month) is None:
            reason = f"Invalid billing month format '{month}'; expected 'YYYY-MM'"
            raise BillingDomainError(reason, reason=reason)
        if month in seen:
            reason = f"Duplicate billing month '{month}' in months list"
            raise BillingDomainError(reason, reason=reason)
        seen.add(month)


def _parse_row_date(
    raw_date: str | None, row_number: int, months_set: set[str]
) -> tuple[datetime, str]:
    """Validate ISO 8601 naive datetime string and return (dt, 'YYYY-MM')."""
    if raw_date is None or not raw_date.strip():
        raise CsvRowValidationError(
            f"Row {row_number}: 'date' cannot be empty",
            row_number=row_number,
            column="date",
            value=raw_date,
            reason="Empty date value",
        )

    cleaned = raw_date.strip()
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise CsvRowValidationError(
            f"Row {row_number}: invalid ISO 8601 date '{raw_date}'",
            row_number=row_number,
            column="date",
            value=raw_date,
            reason=f"Invalid ISO 8601 format: {exc}",
        ) from exc

    if dt.tzinfo is not None:
        raise CsvRowValidationError(
            f"Row {row_number}: timezone offset is not allowed in '{raw_date}'",
            row_number=row_number,
            column="date",
            value=raw_date,
            reason="Timezone-aware datetime is not allowed; expected naive Asia/Bishkek time",
        )

    event_month = f"{dt.year:04d}-{dt.month:02d}"
    if event_month not in months_set:
        raise CsvRowValidationError(
            f"Row {row_number}: event month '{event_month}' is outside target billing months",
            row_number=row_number,
            column="date",
            value=raw_date,
            reason=f"Month '{event_month}' is not in subscriber billing months",
        )

    return dt, event_month


def _parse_row_type(raw_type: str | None, row_number: int) -> str:
    """Validate event type in {'call', 'sms', 'data'}."""
    cleaned = raw_type.strip() if raw_type is not None else ""
    if cleaned not in _ALLOWED_USAGE_TYPES:
        raise CsvRowValidationError(
            f"Row {row_number}: invalid usage type '{raw_type}'",
            row_number=row_number,
            column="type",
            value=raw_type,
            reason="Allowed values are 'call', 'sms', 'data'",
        )
    return cleaned


def _parse_row_amount(
    raw_amount: str | None, usage_type: str, row_number: int
) -> Decimal:
    """Validate finite non-negative Decimal amount (and integer constraint for SMS)."""
    if raw_amount is None or not raw_amount.strip():
        raise CsvRowValidationError(
            f"Row {row_number}: 'amount' cannot be empty",
            row_number=row_number,
            column="amount",
            value=raw_amount,
            reason="Empty amount value",
        )

    cleaned = raw_amount.strip()
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise CsvRowValidationError(
            f"Row {row_number}: invalid numeric amount '{raw_amount}'",
            row_number=row_number,
            column="amount",
            value=raw_amount,
            reason=f"Invalid decimal number: {exc}",
        ) from exc

    if not amount.is_finite() or amount < _ZERO_DECIMAL:
        raise CsvRowValidationError(
            f"Row {row_number}: amount must be finite and >= 0, got '{raw_amount}'",
            row_number=row_number,
            column="amount",
            value=raw_amount,
            reason="Amount must be a finite non-negative number",
        )

    if usage_type == "sms" and amount != amount.to_integral_value():
        raise CsvRowValidationError(
            f"Row {row_number}: SMS amount must be an integer, got '{raw_amount}'",
            row_number=row_number,
            column="amount",
            value=raw_amount,
            reason="SMS count must be a whole integer",
        )

    return amount


def _parse_row_duration_sec(
    raw_duration: str | None, usage_type: str, row_number: int
) -> int:
    """Validate optional duration_sec column (integer >= 0, must be 0 for call and sms)."""
    if raw_duration is None or not raw_duration.strip():
        return 0

    cleaned = raw_duration.strip()
    try:
        duration_dec = Decimal(cleaned)
    except InvalidOperation as exc:
        raise CsvRowValidationError(
            f"Row {row_number}: invalid duration_sec '{raw_duration}'",
            row_number=row_number,
            column="duration_sec",
            value=raw_duration,
            reason=f"Invalid decimal number: {exc}",
        ) from exc

    if (
        not duration_dec.is_finite()
        or duration_dec < _ZERO_DECIMAL
        or duration_dec != duration_dec.to_integral_value()
    ):
        raise CsvRowValidationError(
            f"Row {row_number}: duration_sec must be a non-negative integer, got '{raw_duration}'",
            row_number=row_number,
            column="duration_sec",
            value=raw_duration,
            reason="duration_sec must be a finite non-negative integer",
        )

    duration_int = int(duration_dec)
    if usage_type in {"call", "sms"} and duration_int != 0:
        raise CsvRowValidationError(
            f"Row {row_number}: duration_sec must be 0 for '{usage_type}', got {duration_int}",
            row_number=row_number,
            column="duration_sec",
            value=raw_duration,
            reason=f"duration_sec must be 0 for '{usage_type}' events",
        )

    return duration_int


def _parse_row_roaming(raw_roaming: str | None, row_number: int) -> bool:
    """Validate optional roaming column ('', None, '0' -> False; '1' -> True)."""
    if raw_roaming is None or not raw_roaming.strip():
        return False

    cleaned = raw_roaming.strip()
    if cleaned == "0":
        return False
    if cleaned == "1":
        return True

    raise CsvRowValidationError(
        f"Row {row_number}: invalid roaming flag '{raw_roaming}'",
        row_number=row_number,
        column="roaming",
        value=raw_roaming,
        reason="Allowed values for roaming are '0', '1', or empty",
    )


def parse_usage_csv(
    usage_file: Path,
    months: tuple[str, ...],
) -> list[MonthlyUsage]:
    """Parse usage CSV, normalize units/day-night split, and aggregate by months."""
    _validate_months_tuple(months)

    if not usage_file.is_file():
        raise DataFileNotFoundError(
            f"Usage CSV file not found: {usage_file}",
            file_path=str(usage_file),
        )

    accumulators: dict[str, _MonthlyAccumulator] = {
        month: _MonthlyAccumulator() for month in months
    }
    months_set: set[str] = set(months)

    try:
        with usage_file.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise CsvFormatError(
                    f"CSV file '{usage_file}' is empty and missing headers",
                    file_path=str(usage_file),
                    missing_columns=sorted(_REQUIRED_CSV_COLUMNS),
                    reason="Missing CSV header row",
                )

            present_columns = set(reader.fieldnames)
            missing = sorted(_REQUIRED_CSV_COLUMNS - present_columns)
            if missing:
                raise CsvFormatError(
                    f"CSV file '{usage_file}' is missing required columns: {missing}",
                    file_path=str(usage_file),
                    missing_columns=missing,
                    reason=f"Missing required columns: {', '.join(missing)}",
                )

            for row_number, row in enumerate(reader, start=2):
                start_dt, event_month = _parse_row_date(
                    row.get("date"), row_number, months_set
                )
                usage_type = _parse_row_type(row.get("type"), row_number)
                amount = _parse_row_amount(row.get("amount"), usage_type, row_number)
                duration_sec = _parse_row_duration_sec(
                    row.get("duration_sec"),
                    usage_type,
                    row_number,
                )
                is_roaming = _parse_row_roaming(row.get("roaming"), row_number)

                acc = accumulators[event_month]
                if usage_type == "call":
                    call_mins = calc_call_minutes(amount)
                    if is_roaming:
                        acc.roaming_call_min += call_mins
                    else:
                        acc.home_call_min += call_mins
                elif usage_type == "sms":
                    sms_count = int(amount)
                    if is_roaming:
                        acc.roaming_sms += sms_count
                    else:
                        acc.home_sms += sms_count
                else:
                    if is_roaming:
                        acc.roaming_gb += amount
                    else:
                        day_gb, night_gb = split_day_night_gb(
                            start_dt, duration_sec, amount
                        )
                        acc.home_day_gb += day_gb
                        acc.home_night_gb += night_gb

    except UnicodeDecodeError as exc:
        raise CsvFormatError(
            f"CSV file '{usage_file}' is not valid UTF-8: {exc}",
            file_path=str(usage_file),
            missing_columns=None,
            reason=f"UTF-8 decoding failed: {exc}",
        ) from exc
    except OSError as exc:
        raise DataFileNotFoundError(
            f"Unable to read usage CSV file '{usage_file}': {exc}",
            file_path=str(usage_file),
        ) from exc

    return [accumulators[month].to_monthly_usage(month) for month in months]
