"""
calculator.py
Created by Amir (demiurg39) Chiryagov

Pure billing calculation service for single tariff and add-on options.
"""

import re
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from src.models import (
    Addon,
    BillableUsage,
    MonthlyConsumptionDetails,
    MonthlyUsage,
    Rates,
    Tariff,
)

BILLING_PERIOD_MONTHS: int = 3
MAX_ADDONS_PER_OPTION: int = 2

_MONEY_QUANTUM: Decimal = Decimal("0.01")
_ZERO_DECIMAL: Decimal = Decimal(0)
_MONTH_PATTERN: re.Pattern[str] = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def calculate_month_cost(
    usage: MonthlyUsage,
    tariff: Tariff,
    addons: Sequence[Addon],
    roaming_rates: Rates,
) -> Decimal:
    """
    Calculate the exact unrounded cost for one billing month.

    Combines recurring subscription fees, home-network overage charges above
    combined tariff and add-on quotas, and standalone roaming charges. All
    arithmetic uses exact ``Decimal`` values with no intermediate rounding.

    Args:
        usage: Aggregated subscriber consumption for a single month.
        tariff: Base tariff plan being evaluated.
        addons: Active add-on packages (0 to 2 items; duplicates allowed).
        roaming_rates: Standalone per-unit roaming rates (KGS).

    Returns:
        Exact unrounded total monthly cost in KGS.

    Raises:
        BillingDomainError: If domain invariants are violated (e.g., more than
            2 add-ons, invalid month format, or negative quantities/rates).
    """

    _validate_monthly_usage(usage)
    _validate_tariff(tariff)
    _validate_addons(addons)
    _validate_rates(roaming_rates, "roaming_rates")

    total_minutes = tariff.minutes + sum((addon.minutes for addon in addons), start=0)
    total_gb = tariff.gb + sum((addon.gb for addon in addons), start=0)
    total_sms = tariff.sms + sum((addon.sms for addon in addons), start=0)

    billable_call_min, billable_sms, billable_data_gb = _compute_billable_home_usage(
        usage, tariff
    )

    overage_minutes = max(0, billable_call_min, total_minutes)
    overage_gb = max(_ZERO_DECIMAL, billable_data_gb - total_gb)
    overage_sms = max(0, billable_sms - total_sms)

    overage_cost = (
        Decimal(overage_minutes) * tariff.overage.call
        + overage_gb
        + tariff.overage.data
        + Decimal(overage_sms) * tariff.overage.sms
    )

    roaming_cost = _compute_roaming_cost(usage, roaming_rates)
    reccuring_fee = tariff.fee + sum(
        (addon.fee for addon in addons),
        start=_ZERO_DECIMAL,
    )

    return reccuring_fee + overage_cost + roaming_cost


def calculate_option_costs(
    monthly_usages: Sequence[MonthlyUsage],
    tariff: Tariff,
    addons: Sequence[Addon],
    roaming_rates: Rates,
) -> tuple[Decimal, dict[str, Decimal]]:
    """
    Evaluate exact 3-month average and per-month costs for one option.

     Evaluates a fixed combination of one tariff and 0 to 2 add-ons across all
     3 billing months without intermediate rounding so downstream ranking and
     the 50 KGS switch-fee threshold check remain exact.

     Args:
         monthly_usages: Subscriber consumption records for 3 distinct months.
         tariff: Base tariff plan being evaluated.
         addons: Active add-on packages applied across all 3 months (0 to 2 items).
         roaming_rates: Standalone per-unit roaming rates (KGS).

     Returns:
         A tuple ``(avg_monthly_cost, monthly_costs)`` where ``avg_monthly_cost``
         is the exact unrounded 3-month mean cost in KGS and ``monthly_costs``
         maps each ``"YYYY-MM"`` month to its exact unrounded monthly cost in KGS.

     Raises:
         BillingDomainError: If ``monthly_usages`` does not contain exactly 3
             distinct valid months or any tariff/add-on invariant is violated.
    """

    _validate_billing_period(monthly_usages)
    _validate_tariff(tariff)
    _validate_addons(addons)
    _validate_rates(roaming_rates, "roaming_rates")

    monthly_costs: dict[str, Decimal] = {
        usage.month: calculate_month_cost(usage, tariff, addons, roaming_rates)
        for usage in monthly_usages
    }
    total_cost = sum(monthly_costs.values(), start=_ZERO_DECIMAL)
    avg_monthly_cost = total_cost / Decimal(BILLING_PERIOD_MONTHS)

    return avg_monthly_cost, monthly_costs


def build_consumption_details(
    monthly_usages: Sequence[MonthlyUsage],
    tariff: Tariff,
    roaming_rates: Rates,
) -> list[MonthlyConsumptionDetails]:
    """
    Build per-month billable usage and roaming cost details for a tariff.

    Intended to be called once by the recommender for the winning tariff option.
    Applies ``night_unlimited`` exclusion to daytime/nighttime data and rounds
    each month's ``roaming_cost_som`` to 0.01 KGS using ``ROUND_HALF_UP``.

    Args:
        monthly_usages: Subscriber consumption records for 3 distinct months.
        tariff: Selected tariff plan whose ``night_unlimited`` rule applies.
        roaming_rates: Standalone per-unit roaming rates (KGS).

    Returns:
        Ordered list of ``MonthlyConsumptionDetails`` matching ``monthly_usages``.

    Raises:
        BillingDomainError: If ``monthly_usages`` or rates violate domain rules.
    """

    _validate_billing_period(monthly_usages)
    _validate_tariff(tariff)
    _validate_rates(roaming_rates, "roaming_rates")

    details: list[MonthlyConsumptionDetails] = []
    for usage in monthly_usages:
        billable_call_min, billable_sms, billable_data_gb = (
            _compute_billable_home_usage(usage, tariff)
        )
        roaming_cost = _compute_roaming_cost(usage, roaming_rates)
        roaming_cost_rounded = float(
            roaming_cost.quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        )

        details.append(
            MonthlyConsumptionDetails(
                month=usage.month,
                billable_usage=BillableUsage(
                    call=float(billable_call_min),
                    sms=float(billable_sms),
                    data=float(billable_data_gb),
                ),
                roaming_cost_som=roaming_cost_rounded,
            )
        )

    return details


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
