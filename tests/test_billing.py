"""
test_billing.py
Created by Amir (demiurg39) Chiryagov

Unit tests for single-option billing math and recommendation decision logic.
"""

import math
from decimal import Decimal

import pytest

from src.calculator import (
    build_consumption_details,
    calculate_month_cost,
    calculate_option_costs,
)
from src.exceptions import BillingDomainError, ConfigurationError
from src.models import Addon, AddonId, MonthlyUsage, Rates, TariffId, TariffsConfig
from src.recommender import evaluate_recommendations, recommend_tariff
from tests.conftest import (
    make_addon,
    make_subscriber,
    make_tariff,
    make_uniform_usages,
)


def test_calculate_month_cost_exact_limit_and_overage(
    sample_catalog: tuple[TariffsConfig, tuple[Addon, ...]],
) -> None:
    """Consumption at exact quota has zero overage; exceeding adds per-unit overage."""
    tariffs_config, _ = sample_catalog
    t01 = tariffs_config.tariffs[0]
    roaming = tariffs_config.roaming_rates

    at_limit = MonthlyUsage(
        month="2026-08",
        home_call_min=100,
        home_sms=50,
        home_day_gb=Decimal("6.5"),
        home_night_gb=Decimal("3.5"),
    )
    assert calculate_month_cost(at_limit, t01, (), roaming) == Decimal(250)

    over_limit = MonthlyUsage(
        month="2026-08",
        home_call_min=101,
        home_sms=51,
        home_day_gb=Decimal("7.0"),
        home_night_gb=Decimal("3.5"),
    )
    # 250 + (1 * 2) + (0.5 * 50) + (1 * 1) = 278
    assert calculate_month_cost(over_limit, t01, (), roaming) == Decimal(278)


def test_roaming_billed_separately_even_with_unused_home_quotas(
    sample_catalog: tuple[TariffsConfig, tuple[Addon, ...]],
) -> None:
    """Roaming charges never consume home allowances or night_unlimited."""
    tariffs_config, _ = sample_catalog
    t02 = tariffs_config.tariffs[1]  # fee=400, night_unlimited=True
    roaming = tariffs_config.roaming_rates  # call=30, data=200, sms=10

    usage = MonthlyUsage(
        month="2026-08",
        roaming_call_min=2,
        roaming_sms=3,
        roaming_gb=Decimal("0.5"),
    )
    # Roaming: 2*30 + 3*10 + 0.5*200 = 190; Total: 400 + 190 = 590
    assert calculate_month_cost(usage, t02, (), roaming) == Decimal(590)

    details = build_consumption_details(
        [usage, MonthlyUsage(month="2026-09"), MonthlyUsage(month="2026-10")],
        t02,
        roaming,
    )
    assert details[0].billable_usage.call == 0.0
    assert details[0].billable_usage.sms == 0.0
    assert details[0].billable_usage.data == 0.0
    assert details[0].roaming_cost_som == 190.0


def test_night_unlimited_excludes_night_data_from_quota_and_details(
    sample_catalog: tuple[TariffsConfig, tuple[Addon, ...]],
) -> None:
    """night_unlimited excludes home_night_gb from home data overage and billable_usage."""
    tariffs_config, _ = sample_catalog
    t01, t02, _ = tariffs_config.tariffs
    roaming = tariffs_config.roaming_rates

    usage = MonthlyUsage(
        month="2026-08",
        home_day_gb=Decimal(15),
        home_night_gb=Decimal(20),
    )

    assert calculate_month_cost(usage, t01, (), roaming) == Decimal(1500)
    assert calculate_month_cost(usage, t02, (), roaming) == Decimal(400)

    details = build_consumption_details(
        [
            usage,
            MonthlyUsage(month="2026-09"),
            MonthlyUsage(month="2026-10"),
        ],
        t02,
        roaming,
    )
    assert details[0].billable_usage.data == 15.0


def test_duplicate_addons_stack_quotas_up_to_two(
    sample_catalog: tuple[TariffsConfig, tuple[Addon, ...]],
) -> None:
    """Two identical add-ons stack quotas and select action='add_packages' with 0 switch fee."""
    tariffs_config, addons = sample_catalog
    subscriber = make_subscriber(current_tariff_id="T01")
    usages = make_uniform_usages(home_day_gb="19")

    result = recommend_tariff(subscriber, usages, tariffs_config, addons)

    assert result.action == "add_packages"
    assert result.tariff_id == TariffId("T01")
    assert result.addon_ids == [AddonId("A01"), AddonId("A01")]
    assert result.current_monthly_cost == 700.0
    assert result.new_monthly_cost == 450.0
    assert result.monthly_savings == 250.0
    assert result.switch_fee == 0.0
    assert result.net_savings_3m == 750.0


def test_switch_fee_50_kgs_exact_threshold_stays(
    default_roaming_rates: Rates,
) -> None:
    """Switch requires strictly > 50 KGS 3-month savings; exact 50 KGS stays."""
    config = TariffsConfig(
        currency="KGS",
        tariffs=(
            make_tariff("T01", "Current", fee="300", minutes=100),
            make_tariff("T02", "CheaperBy50In3M", fee="300", minutes=150),
        ),
        roaming_rates=default_roaming_rates,
    )
    subscriber = make_subscriber(current_tariff_id="T01")
    # Month 1: 125 min -> T01 costs 350, T02 costs 300 (50 KGS diff); months 2-3: 300 each.
    usages = [
        MonthlyUsage(month="2026-08", home_call_min=125),
        MonthlyUsage(month="2026-09"),
        MonthlyUsage(month="2026-10"),
    ]

    result, comparison = evaluate_recommendations(subscriber, usages, config, ())

    assert result.action == "stay"
    assert result.tariff_id == TariffId("T01")
    assert result.switch_fee == 0.0
    assert result.net_savings_3m == 0.0

    t02_item = next(item for item in comparison if item.tariff_id == TariffId("T02"))
    assert t02_item.is_profitable is False
    assert t02_item.net_savings_3m == 0.0


def test_unprofitable_switch_falls_back_to_profitable_addon(
    default_roaming_rates: Rates,
) -> None:
    """When best tariff switch saves <= 50 KGS in 3m, recommender picks next eligible option."""
    config = TariffsConfig(
        currency="KGS",
        tariffs=(
            make_tariff("T01", "Current", fee="300", gb="10"),
            make_tariff("T02", "AltTariff", fee="385", gb="15"),
        ),
        roaming_rates=default_roaming_rates,
    )
    addons = (make_addon("A01", "+5 GB", fee="90", gb="5"),)
    subscriber = make_subscriber(current_tariff_id="T01")
    # 12 GB/mo: T01=400, T02=385 (saves 45 in 3m <= 50 -> ineligible), T01+A01=390 (saves 30 > 0).
    usages = make_uniform_usages(home_day_gb="12")

    result = recommend_tariff(subscriber, usages, config, addons)

    assert result.action == "add_packages"
    assert result.tariff_id == TariffId("T01")
    assert result.addon_ids == [AddonId("A01")]
    assert result.new_monthly_cost == 390.0
    assert result.switch_fee == 0.0
    assert result.net_savings_3m == 30.0


def test_tie_breaking_stay_fewer_addons_and_lexicographical_ids(
    default_roaming_rates: Rates,
) -> None:
    """Tie-breaker prefers stay on 0 savings, then fewer add-ons, then min tariff_id/addon_ids."""
    # 1. Zero savings -> stay beats switching to equal-cost tariff
    equal_config = TariffsConfig(
        currency="KGS",
        tariffs=(
            make_tariff("T02", "Cur", fee="250"),
            make_tariff("T01", "Alt", fee="250"),
        ),
        roaming_rates=default_roaming_rates,
    )
    res_stay = recommend_tariff(
        make_subscriber(current_tariff_id="T02"),
        make_uniform_usages(),
        equal_config,
        (),
    )
    assert res_stay.action == "stay"
    assert res_stay.tariff_id == TariffId("T02")

    # 2. Equal savings -> 0 add-ons beats 1 add-on, and T01 beats T02
    tie_config = TariffsConfig(
        currency="KGS",
        tariffs=(
            make_tariff("T09", "Expensive", fee="500", gb="10"),
            make_tariff("T02", "Second", fee="300", gb="10"),
            make_tariff("T01", "First", fee="300", gb="10"),
            make_tariff("T00", "WithAddon", fee="250", gb="5"),
        ),
        roaming_rates=default_roaming_rates,
    )
    addons = (
        make_addon("A02", "Pack B", fee="50", gb="5"),
        make_addon("A01", "Pack A", fee="50", gb="5"),
    )
    usages = make_uniform_usages(home_day_gb="10")
    res_tie = recommend_tariff(
        make_subscriber(current_tariff_id="T09"),
        usages,
        tie_config,
        addons,
    )
    assert res_tie.action == "change"
    assert res_tie.tariff_id == TariffId("T01")
    assert res_tie.addon_ids == []

    # 3. Equal cost with 1 add-on -> lexicographically smaller addon_id wins
    addon_tie_config = TariffsConfig(
        currency="KGS",
        tariffs=(
            make_tariff("T09", "Expensive", fee="500", gb="10"),
            make_tariff("T01", "Base", fee="250", gb="5"),
        ),
        roaming_rates=default_roaming_rates,
    )
    res_addon_tie = recommend_tariff(
        make_subscriber(current_tariff_id="T09"),
        usages,
        addon_tie_config,
        addons,
    )
    assert res_addon_tie.tariff_id == TariffId("T01")
    assert res_addon_tie.addon_ids == [AddonId("A01")]


def test_round_half_up_and_no_intermediate_rounding() -> None:
    """Averages divide exact 3-month sum without intermediate rounding and use ROUND_HALF_UP."""
    config = TariffsConfig(
        currency="KGS",
        tariffs=(
            make_tariff(
                "T01",
                "Exact",
                fee="250",
                minutes=0,
                gb="0",
                sms=0,
                overage=Rates(Decimal("0.001"), Decimal("1"), Decimal("0.001")),
            ),
        ),
        roaming_rates=Rates(Decimal(1), Decimal(1), Decimal(1)),
    )
    # Month 1: 250.004 -> 250.00; Month 2: 250.004 -> 250.00; Month 3: 250.007 -> 250.01
    # Exact 3m sum = 750.015 -> exact avg = 250.005 -> ROUND_HALF_UP = 250.01
    # (Intermediate rounding would give (250.00 + 250.00 + 250.01) / 3 = 250.0033 -> 250.00;
    #  ROUND_HALF_EVEN on 250.005 would also give 250.00.)
    usages = [
        MonthlyUsage(month="2026-08", home_call_min=4),
        MonthlyUsage(month="2026-09", home_call_min=4),
        MonthlyUsage(month="2026-10", home_call_min=7),
    ]

    result = recommend_tariff(make_subscriber(), usages, config, ())

    assert result.monthly_costs["2026-08"] == 250.0
    assert result.monthly_costs["2026-09"] == 250.0
    assert result.monthly_costs["2026-10"] == 250.01
    assert result.current_monthly_cost == 250.01


def test_negative_zero_is_normalized_to_positive_zero(
    default_roaming_rates: Rates,
) -> None:
    """Tiny negative Decimal amounts rounding to zero never serialize as -0.0."""
    config = TariffsConfig(
        currency="KGS",
        tariffs=(
            make_tariff("T01", "Current", fee="300", minutes=100),
            make_tariff(
                "T02",
                "TinyLoss",
                fee="300",
                minutes=124,
                overage=Rates(Decimal("0.001"), Decimal("50"), Decimal("1")),
            ),
        ),
        roaming_rates=default_roaming_rates,
    )
    # T01 3m = 950.00, T02 3m = 900.001, switch_fee = 50 -> raw_net_savings_3m = -0.001 -> 0.0
    usages = [
        MonthlyUsage(month="2026-08", home_call_min=125),
        MonthlyUsage(month="2026-09"),
        MonthlyUsage(month="2026-10"),
    ]

    _, comparison = evaluate_recommendations(make_subscriber(), usages, config, ())
    t02_item = next(item for item in comparison if item.tariff_id == TariffId("T02"))
    assert t02_item.net_savings_3m == 0.0
    assert math.copysign(1.0, t02_item.net_savings_3m) == 1.0


@pytest.mark.parametrize(
    "bad_addon",
    [
        make_addon("A_NEG_FEE", fee="-10"),
        make_addon("A_NEG_GB", gb="-1"),
        make_addon("A_NEG_MIN", minutes=-5),
        make_addon("A_NEG_SMS", sms=-1),
        make_addon("A_NAN_FEE", fee=Decimal("NaN")),
        make_addon("A_INF_GB", gb=Decimal("Infinity")),
    ],
)
def test_invalid_addon_fields_raise_billing_domain_error(
    sample_catalog: tuple[TariffsConfig, tuple[Addon, ...]],
    bad_addon: Addon,
) -> None:
    """Negative or non-finite add-on fees/allowances raise BillingDomainError."""
    tariffs_config, _ = sample_catalog
    t01 = tariffs_config.tariffs[0]
    roaming = tariffs_config.roaming_rates

    with pytest.raises(BillingDomainError):
        calculate_month_cost(MonthlyUsage(month="2026-08"), t01, (bad_addon,), roaming)


def test_domain_invariants_raise_domain_or_configuration_errors(
    sample_catalog: tuple[TariffsConfig, tuple[Addon, ...]],
) -> None:
    """Invalid add-on count, period length, or unknown subscriber tariff raise domain errors."""
    tariffs_config, addons = sample_catalog
    t01 = tariffs_config.tariffs[0]
    roaming = tariffs_config.roaming_rates
    single_month = MonthlyUsage(month="2026-08")

    with pytest.raises(BillingDomainError):
        calculate_month_cost(single_month, t01, addons, roaming)  # 3 add-ons > max 2

    with pytest.raises(BillingDomainError):
        calculate_option_costs([single_month], t01, (), roaming)  # 1 month != 3

    unknown_sub = make_subscriber(current_tariff_id="T99")
    with pytest.raises(ConfigurationError):
        recommend_tariff(unknown_sub, make_uniform_usages(), tariffs_config, addons)
