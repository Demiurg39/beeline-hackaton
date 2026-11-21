"""
recommender.py
Created by Amir (demiurg39) Chiryagov

Decision engine for evaluating tariff and add-on combinations and selecting
the optimal recommendation for a subscriber.
"""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from itertools import combinations_with_replacement
from typing import NamedTuple

from src.calculator import (
    BILLING_PERIOD_MONTHS,
    MAX_ADDONS_PER_OPTION,
    build_consumption_details,
    calculate_option_costs,
)
from src.exceptions import BillingDomainError, ConfigurationError
from src.models import (
    ActionType,
    Addon,
    AddonId,
    MonthlyUsage,
    RecommendationResult,
    SubscriberProfile,
    Tariff,
    TariffComparisonItem,
    TariffId,
    TariffsConfig,
)

SWITCH_FEE_KGS: Decimal = Decimal(50)

_MONEY_QUANTUM: Decimal = Decimal("0.01")
_ZERO_DECIMAL: Decimal = Decimal(0)


__all__ = [
    "evaluate_recommendations",
    "recommend_tariff",
]


class _EvaluatedCandidate(NamedTuple):
    """Exact evaluation metrics for a single (tariff, add-ons) combination."""

    avg_monthly_cost: Decimal
    is_not_stay: int
    addon_count: int
    tariff_id: TariffId
    addon_ids: list[AddonId]
    tariff: Tariff
    monthly_costs: dict[str, Decimal]
    raw_monthly_savings: Decimal
    switch_fee: Decimal
    raw_net_savings_3m: Decimal

    def rank_key(self) -> tuple[Decimal, int, int, TariffId, list[AddonId]]:
        """Global ranking key enforcing billing tie-breaking rules."""
        return (
            self.avg_monthly_cost,
            self.is_not_stay,
            self.addon_count,
            self.tariff_id,
            self.addon_ids,
        )

    def per_tariff_key(self) -> tuple[Decimal, int, list[AddonId]]:
        """Ranking key for selecting the best add-on set within a single tariff."""
        return (self.avg_monthly_cost, self.addon_count, self.addon_ids)


def evaluate_recommendations(
    subscriber: SubscriberProfile,
    monthly_usages: Sequence[MonthlyUsage],
    tariffs_config: TariffsConfig,
    addons: Sequence[Addon],
) -> tuple[RecommendationResult, list[TariffComparisonItem]]:
    """
    Evaluate all valid tariff and add-on combinations and select the best option.

    Generates combinations of each available tariff with 0 to 2 add-ons (with
    replacement), evaluates exact 3-month unrounded costs via ``calculator``,
    enforces the 50 KGS switch-fee payback threshold, resolves ties according to
    billing rules, and builds both the core recommendation and per-tariff
    comparison items for frontend charts.

    Args:
        subscriber: Subscriber profile specifying ``id``, ``current_tariff_id``,
            and the 3 billing months.
        monthly_usages: Aggregated consumption records for the subscriber's
            3 billing months.
        tariffs_config: Catalog containing available tariffs and roaming rates.
        addons: Catalog of available monthly add-on packages.

    Returns:
        A tuple ``(recommendation, comparison)`` where:
        - ``recommendation`` is a ``RecommendationResult`` matching the
          ``expected.json`` output contract.
        - ``comparison`` is a list of ``TariffComparisonItem`` preserving the
          tariff order from ``tariffs_config.tariffs``.

    Raises:
        ConfigurationError: If ``tariffs_config.tariffs`` is empty, contains
            duplicate tariff IDs, or lacks ``subscriber.current_tariff_id``.
        BillingDomainError: If ``monthly_usages`` does not match the 3-month
            billing period or any tariff/add-on invariant is violated.
    """

    _validate_catalog_and_usages(subscriber, monthly_usages, tariffs_config)

    tariffs_by_id: dict[TariffId, Tariff] = {
        tariff.id: tariff for tariff in tariffs_config.tariffs
    }
    current_tariff = tariffs_by_id[subscriber.current_tariff_id]

    baseline_avg_monthly, baseline_monthly_costs = calculate_option_costs(
        monthly_usages=monthly_usages,
        tariff=current_tariff,
        addons=(),
        roaming_rates=tariffs_config.roaming_rates,
    )
    baseline_total_3m = sum(baseline_monthly_costs.values(), start=_ZERO_DECIMAL)

    sorted_addons = tuple(sorted(addons, key=lambda addon: addon.id))
    addon_combos: list[tuple[Addon, ...]] = [
        combo
        for count in range(MAX_ADDONS_PER_OPTION + 1)
        for combo in combinations_with_replacement(sorted_addons, count)
    ]

    eligible_candidates: list[_EvaluatedCandidate] = []
    comparison_items: list[TariffComparisonItem] = []

    for tariff in tariffs_config.tariffs:
        is_same_tariff = tariff.id == subscriber.current_tariff_id
        switch_fee = _ZERO_DECIMAL if is_same_tariff else SWITCH_FEE_KGS

        best_for_tariff: _EvaluatedCandidate | None = None

        for combo in addon_combos:
            addon_ids: list[AddonId] = [addon.id for addon in combo]
            is_stay_option = is_same_tariff and len(addon_ids) == 0

            if is_stay_option:
                avg_monthly_cost = baseline_avg_monthly
                monthly_costs = baseline_monthly_costs
            else:
                avg_monthly_cost, monthly_costs = calculate_option_costs(
                    monthly_usages=monthly_usages,
                    tariff=tariff,
                    addons=combo,
                    roaming_rates=tariffs_config.roaming_rates,
                )

            option_total_3m = sum(monthly_costs.values(), start=_ZERO_DECIMAL)
            raw_monthly_savings = baseline_avg_monthly - avg_monthly_cost
            raw_net_savings_3m = (baseline_total_3m - option_total_3m) - switch_fee

            candidate = _EvaluatedCandidate(
                avg_monthly_cost=avg_monthly_cost,
                is_not_stay=0 if is_stay_option else 1,
                addon_count=len(addon_ids),
                tariff_id=tariff.id,
                addon_ids=addon_ids,
                tariff=tariff,
                monthly_costs=monthly_costs,
                raw_monthly_savings=raw_monthly_savings,
                switch_fee=switch_fee,
                raw_net_savings_3m=raw_net_savings_3m,
            )

            if (
                best_for_tariff is None
                or candidate.per_tariff_key() < best_for_tariff.per_tariff_key()
            ):
                best_for_tariff = candidate

            if _is_eligible_option(
                is_same_tariff=is_same_tariff,
                addon_count=len(addon_ids),
                raw_monthly_savings=raw_monthly_savings,
                raw_net_savings_3m=raw_net_savings_3m,
            ):
                eligible_candidates.append(candidate)

        if best_for_tariff is not None:
            comparison_items.append(
                TariffComparisonItem(
                    tariff_id=tariff.id,
                    tariff_name=tariff.name,
                    addon_ids=best_for_tariff.addon_ids,
                    avg_monthly_cost=_round_money(best_for_tariff.avg_monthly_cost),
                    net_savings_3m=_round_money(best_for_tariff.raw_net_savings_3m),
                    is_profitable=best_for_tariff.raw_net_savings_3m > _ZERO_DECIMAL,
                )
            )

    winner = min(eligible_candidates, key=lambda item: item.rank_key())

    action = _resolve_action(
        current_tariff_id=subscriber.current_tariff_id,
        recommended_tariff_id=winner.tariff_id,
        addon_count=winner.addon_count,
    )

    consumption_details = build_consumption_details(
        monthly_usages=monthly_usages,
        tariff=winner.tariff,
        roaming_rates=tariffs_config.roaming_rates,
    )

    rounded_monthly_costs: dict[str, float] = {
        usage.month: _round_money(winner.monthly_costs[usage.month])
        for usage in monthly_usages
    }

    recommendation = RecommendationResult(
        id=subscriber.id,
        action=action,
        tariff_id=winner.tariff_id,
        addon_ids=winner.addon_ids,
        current_monthly_cost=_round_money(baseline_avg_monthly),
        new_monthly_cost=_round_money(winner.avg_monthly_cost),
        monthly_savings=_round_money(winner.raw_monthly_savings),
        switch_fee=_round_money(winner.switch_fee),
        net_savings_3m=_round_money(winner.raw_net_savings_3m),
        monthly_costs=rounded_monthly_costs,
        consumption_on_recommended_tariff=consumption_details,
    )

    return recommendation, comparison_items


def recommend_tariff(
    subscriber: SubscriberProfile,
    monthly_usages: Sequence[MonthlyUsage],
    tariffs_config: TariffsConfig,
    addons: Sequence[Addon],
) -> RecommendationResult:
    """
    Return only the core ``RecommendationResult`` for benchmark evaluation.

    Convenience wrapper around ``evaluate_recommendations`` used by the public
    ``recommend(usage_file)`` entry point.

    Args:
        subscriber: Subscriber profile specifying ``id``, ``current_tariff_id``,
            and the 3 billing months.
        monthly_usages: Aggregated consumption records for the subscriber's
            3 billing months.
        tariffs_config: Catalog containing available tariffs and roaming rates.
        addons: Catalog of available monthly add-on packages.

    Returns:
        ``RecommendationResult`` matching the ``expected.json`` schema.
    """

    recommendation, _ = evaluate_recommendations(
        subscriber=subscriber,
        monthly_usages=monthly_usages,
        tariffs_config=tariffs_config,
        addons=addons,
    )
    return recommendation


def _round_money(value: Decimal) -> float:
    """Round an exact ``Decimal`` amount to 0.01 KGS using ``ROUND_HALF_UP``."""
    quantized = value.quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    return 0.0 if quantized == _ZERO_DECIMAL else float(quantized)


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
