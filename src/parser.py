"""
parser.py

Catalog loader and CSV usage parser with strict validation and Decimal arithmetic.
"""

import re
from decimal import Decimal
from pathlib import Path

import msgspec

from src.exceptions import (
    ConfigurationError,
    DataFileNotFoundError,
    SubscriberNotFoundError,
)
from src.models import (
    Addon,
    AddonId,
    Rates,
    SubscriberId,
    SubscriberProfile,
    Tariff,
    TariffId,
    TariffsConfig,
)

BILLING_PERIOD_MONTHS: int = 3
_ZERO_DECIMAL: Decimal = Decimal(0)
_MONTH_PATTERN: re.Pattern[str] = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class _AddonsEnvelope(msgspec.Struct, frozen=True):
    """Wrapper schema when addons.json has a root object with an 'addons' key."""

    addons: tuple[Addon, ...]


class _SubscribersEnvelope(msgspec.Struct, frozen=True):
    """Wrapper schema when subscribers.json has a root object with a 'subscribers' key."""

    subscribers: tuple[SubscriberProfile, ...]


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


def _validate_non_negative_decimal(value: Decimal, config_file: str, field_name: str) -> None:
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

        _validate_non_negative_decimal(tariff.fee, config_file, f"tariff '{tariff.id}'.fee")
        _validate_non_negative_decimal(tariff.gb, config_file, f"tariff '{tariff.id}'.gb")

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

        _validate_non_negative_decimal(addon.fee, config_file, f"addon '{addon.id}'.fee")
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