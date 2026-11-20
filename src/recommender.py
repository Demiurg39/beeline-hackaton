"""
recommender.py
Created by Amir (demiurg39) Chiryagov

Decision engine for evaluating tariff and add-on combinations and selecting
the optimal recommendation for a subscriber.
"""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from src.calculator import BILLING_PERIOD_MONTHS
from src.exceptions import BillingDomainError, ConfigurationError
from src.models import (
    ActionType,
    MonthlyUsage,
    SubscriberProfile,
    TariffId,
    TariffsConfig,
)


_MONEY_QUANTUM: Decimal = Decimal("0.01")
_ZERO_DECIMAL: Decimal = Decimal(0)
_PERIOD_DECIMAL: Decimal = Decimal(BILLING_PERIOD_MONTHS)


def _round_money(value: Decimal) -> float:
    """Round an exact ``Decimal`` amount to 0.01 KGS using ``ROUND_HALF_UP``."""
    return float(value.quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP))


def _resolve_action(
    *,
    current_tariff_id: TariffId,
    recommended_tariff_id: TariffId,
    addon_count: int,
) -> ActionType:
    """Map the winning tariff and add-on count to an ``ActionType`` literal."""
    if recommended_tariff_id != current_tariff_id:
        return "change"
    if addon_count > 0:
        return "add_packages"
    return "stay"


def _validate_catalog_and_usages(
    subscriber: SubscriberProfile,
    monthly_usages: Sequence[MonthlyUsage],
    tariffs_config: TariffsConfig,
) -> None:
    """Validate tariff catalog consistency and subscriber billing period."""

    if not tariffs_config.tariffs:
        reason = "Tariff catalog cannot be empty."
        raise ConfigurationError(
            reason=reason,
            message=reason,
            config_file="tariffs.json",
        )

    seen_tariff_ids: set[TariffId] = set()
    for tariff in tariffs_config.tariffs:
        if tariff.id in seen_tariff_ids:
            reason = f"Duplicate tariff id '{tariff.id}' in tariffs catalog."
            raise ConfigurationError(
                reason=reason,
                message=reason,
                config_file="tariffs.json",
            )
        seen_tariff_ids.add(tariff.id)

    if subscriber.current_tariff_id not in seen_tariff_ids:
        reason = (
            f"Subscriber '{subscriber.id}' references unknown current_tariff_id "
            f"'{subscriber.current_tariff_id}'."
        )
        raise ConfigurationError(
            reason=reason,
            message=reason,
            config_file="subscribers.json",
        )

    if len(monthly_usages) != BILLING_PERIOD_MONTHS:
        reason = (
            f"Expected exactly {BILLING_PERIOD_MONTHS} monthly usage records, "
            f"got {len(monthly_usages)}."
        )
        raise BillingDomainError(reason=reason, message=reason)

    expected_months = tuple(subscriber.months)
    actual_months = tuple(usage.month for usage in monthly_usages)
    if actual_months != expected_months:
        reason = (
            f"Monthly usage sequence {actual_months} does not match subscriber "
            f"billing months {expected_months}."
        )
        raise BillingDomainError(reason=reason, message=reason)


def _is_eligible_option(
    *,
    is_same_tariff: bool,
    addon_count: int,
    raw_monthly_savings: Decimal,
    raw_net_savings_3m: Decimal,
) -> bool:
    """
    Determine whether an evaluated option passes payback eligibility rules.

    - Baseline ("stay") is always eligible as the fallback option.
    - Adding packages on the current tariff requires strictly positive savings
      (``raw_monthly_savings > 0``) since ``switch_fee`` is 0 KGS.
    - Switching to a different tariff requires 3-month savings strictly above
      the 50 KGS switch fee (``raw_net_savings_3m > 0``).
    """

    if is_same_tariff and addon_count == 0:
        return True
    if is_same_tariff:
        return raw_monthly_savings > _ZERO_DECIMAL
    return raw_net_savings_3m > _ZERO_DECIMAL
