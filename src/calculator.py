"""
calculator.py
Created by Amir (demiurg39) Chiryagov

Pure billing calculation service for single tariff and add-on options.
"""

import re
from collections.abc import Sequence
from decimal import Decimal

from src.exceptions import BillingDomainError
from src.models import Addon, MonthlyUsage, Rates, Tariff

BILLING_PERIOD_MONTHS: int = 3
MAX_ADDONS_PER_OPTION: int = 2

_ZERO_DECIMAL: Decimal = Decimal(0)
_MONTH_PATTERN: re.Pattern[str] = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _compute_roaming_cost(usage: MonthlyUsage, roaming_rates: Rates) -> Decimal:
    """Return exact unrounded roaming charges (KGS) for a single month."""
    return (
        Decimal(usage.roaming_call_min) * roaming_rates.call
        + usage.roaming_gb * roaming_rates.data
        + Decimal(usage.roaming_sms) * roaming_rates.sms
    )


def _compute_billable_home_usage(
    usage: MonthlyUsage, tariff: Tariff
) -> tuple[int, int, Decimal]:
    """Return home-network billable ``(call_min, sms, data_gb)`` for a tariff."""
    billable_data_gb = (
        usage.home_day_gb
        if tariff.night_unlimited == True
        else usage.home_day_gb + usage.home_night_gb
    )

    return usage.home_call_min, usage.home_sms, billable_data_gb


def _validate_decimal_non_negative(value: Decimal, field_name: str) -> None:
    """Ensure a ``Decimal`` value is finite and greater than or equal to zero."""

    if not value.is_finite() or value < _ZERO_DECIMAL:
        reason = (
            f"Field '{field_name}' must be a finite non-negative Decimal, got {value}"
        )
        raise BillingDomainError(reason, reason=reason)


def _validate_rates(rates: Rates, label: str) -> None:
    """Ensure all per-unit rates are finite and non-negative."""

    _validate_decimal_non_negative(rates.call, f"{label}.call")
    _validate_decimal_non_negative(rates.data, f"{label}.data")
    _validate_decimal_non_negative(rates.sms, f"{label}.sms")


def _validate_addons(addons: Sequence[Addon]) -> None:
    """Ensure at most 2 add-ons are provided and all fees/quotas are valid."""

    if len(addons) > MAX_ADDONS_PER_OPTION:
        reason = (
            f"At most {MAX_ADDONS_PER_OPTION} add-ons are allowed per option, "
            f"got {len(addons)}"
        )
        raise BillingDomainError(reason, reason=reason)


def _validate_tariff(tariff: Tariff) -> None:
    """Ensure tariff fees, quotas, and overage rates are finite and non-negative."""

    _validate_decimal_non_negative(tariff.fee, f"tariff '{tariff.id}' fee")
    _validate_decimal_non_negative(tariff.gb, f"tariff '{tariff.id}' gb")

    if tariff.minutes < 0 or tariff.sms < 0:
        reason = f"Tariff '{tariff.id}' minutes and sms quotas must be non-negative."
        raise BillingDomainError(reason, reason=reason)

    _validate_rates(tariff.overage, f"tariff '{tariff.id}' overage")


def _validate_monthly_usage(usage: MonthlyUsage) -> None:
    """Ensure month identifier and all consumption counters are valid."""

    if _MONTH_PATTERN.match(usage.month) is None:
        reason = f"Invalid billing month format '{usage.month}'; expected 'YYYY-MM'."
        raise BillingDomainError(reason, reason=reason)

    if usage.home_call_min < 0 or usage.home_sms < 0:
        reason = f"Home call/SMS usage cannot be negative for month '{usage.month}'."
        raise BillingDomainError(reason, reason=reason)

    if usage.roaming_call_min < 0 or usage.roaming_sms < 0:
        reason = f"Roaming call/SMS usage cannot be negative for month '{usage.month}'."
        raise BillingDomainError(reason, reason=reason)

    _validate_decimal_non_negative(usage.home_day_gb, f"{usage.month}.home_day_gb")
    _validate_decimal_non_negative(usage.home_night_gb, f"{usage.month}.home_night_gb")
    _validate_decimal_non_negative(usage.roaming_gb, f"{usage.month}.roaming_gb")


def _validate_billing_period(monthly_usages: Sequence[MonthlyUsage]) -> None:
    """Ensure ``monthly_usages`` spans exactly 3 distinct valid billing months."""

    if len(monthly_usages) != BILLING_PERIOD_MONTHS:
        reason = (
            f"Expected exactly {BILLING_PERIOD_MONTHS} billing months, "
            f"got {len(monthly_usages)}"
        )
        raise BillingDomainError(reason, reason=reason)

    seen_months: set[str] = set()
    for usage in monthly_usages:
        _validate_monthly_usage(usage)
        if usage.month in seen_months:
            reason = f"Duplicate billing month '{usage.month}' is usage periond."
            raise BillingDomainError(reason, reason=reason)
        seen_months.add(usage.month)