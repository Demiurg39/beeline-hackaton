"""
conftest.py
Created by Amir (demiurg39) Chiryagov

Shared fixtures, path constants, domain builders, and assertion helpers for pytest.
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from src.models import (
    Addon,
    AddonId,
    MonthlyUsage,
    Rates,
    SubscriberId,
    SubscriberProfile,
    Tariff,
    TariffId,
    TariffsConfig,
)

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = PROJECT_ROOT / "data"
EDGE_CASES_DIR: Path = DATA_DIR / "edge_cases"
DEFAULT_MONTHS: tuple[str, str, str] = ("2026-08", "2026-09", "2026-10")


@pytest.fixture
def default_overage_rates() -> Rates:
    """Return standard per-unit overage rates in KGS."""
    return Rates(
        call=Decimal(2),
        data=Decimal(50),
        sms=Decimal(1),
    )


@pytest.fixture
def default_roaming_rates() -> Rates:
    """Return standard per-unit roaming rates in KGS."""
    return Rates(
        call=Decimal(30),
        data=Decimal(200),
        sms=Decimal(10),
    )


@pytest.fixture
def sample_catalog(
    default_overage_rates: Rates,
    default_roaming_rates: Rates,
) -> tuple[TariffsConfig, tuple[Addon, ...]]:
    """Return a minimal 3-tariff and 3-addon catalog for unit tests."""
    tariffs_config = TariffsConfig(
        currency="KGS",
        tariffs=(
            make_tariff("T01", "Start", fee="250", minutes=100, gb="10", sms=50),
            make_tariff(
                "T02",
                "Night Pro",
                fee="400",
                minutes=200,
                gb="15",
                sms=100,
                overage=Rates(Decimal(2), Decimal(40), Decimal(1)),
                night_unlimited=True,
            ),
            make_tariff(
                "T03",
                "Maxi",
                fee="600",
                minutes=500,
                gb="30",
                sms=300,
                overage=Rates(Decimal(1), Decimal(30), Decimal(1)),
                night_unlimited=True,
            ),
        ),
        roaming_rates=default_roaming_rates,
    )
    addons = (
        make_addon("A01", "+5 GB", fee="100", gb="5"),
        make_addon("A02", "+100 Min", fee="80", minutes=100),
        make_addon("A03", "+50 SMS", fee="30", sms=50),
    )
    return tariffs_config, addons


def make_tariff(
    tariff_id: str,
    name: str = "Plan",
    *,
    fee: str | Decimal = "250",
    minutes: int = 100,
    gb: str | Decimal = "10",
    sms: int = 50,
    overage: Rates | None = None,
    night_unlimited: bool = False,
) -> Tariff:
    """Construct a Tariff struct with concise defaults for unit tests."""
    return Tariff(
        id=TariffId(tariff_id),
        name=name,
        fee=Decimal(str(fee)),
        minutes=minutes,
        gb=Decimal(str(gb)),
        sms=sms,
        overage=overage or Rates(Decimal(2), Decimal(50), Decimal(1)),
        night_unlimited=night_unlimited,
    )


def make_addon(
    addon_id: str,
    name: str = "Addon",
    *,
    fee: str | Decimal = "100",
    minutes: int = 0,
    gb: str | Decimal = "0",
    sms: int = 0,
) -> Addon:
    """Construct an Addon struct with concise defaults for unit tests."""
    return Addon(
        id=AddonId(addon_id),
        name=name,
        fee=Decimal(str(fee)),
        minutes=minutes,
        gb=Decimal(str(gb)),
        sms=sms,
    )


def make_subscriber(
    sub_id: str = "P01",
    current_tariff_id: str = "T01",
    months: tuple[str, ...] = DEFAULT_MONTHS,
) -> SubscriberProfile:
    """Construct a SubscriberProfile struct for unit tests."""
    return SubscriberProfile(
        id=SubscriberId(sub_id),
        current_tariff_id=TariffId(current_tariff_id),
        months=months,
        usage_file=f"usage_{sub_id}.csv",
    )


def make_uniform_usages(
    *,
    home_call_min: int = 0,
    home_sms: int = 0,
    home_day_gb: str | Decimal = "0",
    home_night_gb: str | Decimal = "0",
    roaming_call_min: int = 0,
    roaming_sms: int = 0,
    roaming_gb: str | Decimal = "0",
    months: tuple[str, ...] = DEFAULT_MONTHS,
) -> list[MonthlyUsage]:
    """Build identical MonthlyUsage records across all billing months."""
    return [
        MonthlyUsage(
            month=month,
            home_call_min=home_call_min,
            home_sms=home_sms,
            home_day_gb=Decimal(str(home_day_gb)),
            home_night_gb=Decimal(str(home_night_gb)),
            roaming_call_min=roaming_call_min,
            roaming_sms=roaming_sms,
            roaming_gb=Decimal(str(roaming_gb)),
        )
        for month in months
    ]


def load_expected_map(expected_path: Path) -> dict[str, dict[str, Any]]:
    """Load an expected.json file and normalize it into {subscriber_id: expected_dict}."""
    raw = json.loads(expected_path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return {item["id"]: item for item in raw}
    if isinstance(raw, dict):
        if "results" in raw and isinstance(raw["results"], list):
            return {item["id"]: item for item in raw["results"]}
        return raw
    raise AssertionError(
        f"Unsupported expected.json structure in {expected_path}: {type(raw)}"
    )


def assert_recommendation_matches(
    actual: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    """Assert that a recommend() output dictionary matches the benchmark contract."""
    sub_id = expected["id"]
    assert actual["id"] == sub_id
    assert actual["action"] == expected["action"], f"{sub_id}: action mismatch"
    assert actual["tariff_id"] == expected["tariff_id"], f"{sub_id}: tariff_id mismatch"
    assert actual["addon_ids"] == expected["addon_ids"], f"{sub_id}: addon_ids mismatch"

    for money_field in (
        "current_monthly_cost",
        "new_monthly_cost",
        "monthly_savings",
        "switch_fee",
        "net_savings_3m",
    ):
        if money_field in expected:
            assert actual[money_field] == pytest.approx(
                expected[money_field], abs=0.01
            ), f"{sub_id}: mismatch in {money_field}"

    if "monthly_costs" in expected:
        assert set(actual["monthly_costs"].keys()) == set(
            expected["monthly_costs"].keys()
        )
        for month, expected_cost in expected["monthly_costs"].items():
            assert actual["monthly_costs"][month] == pytest.approx(
                expected_cost, abs=0.01
            ), f"{sub_id}: monthly_costs[{month}] mismatch"

    if "consumption_on_recommended_tariff" in expected:
        actual_details = actual["consumption_on_recommended_tariff"]
        expected_details = expected["consumption_on_recommended_tariff"]
        assert len(actual_details) == len(expected_details)

        for act_item, exp_item in zip(actual_details, expected_details, strict=True):
            assert act_item["month"] == exp_item["month"]
            assert act_item["roaming_cost_som"] == pytest.approx(
                exp_item["roaming_cost_som"], abs=0.01
            )
            for metric in ("call", "sms", "data"):
                assert act_item["billable_usage"][metric] == pytest.approx(
                    exp_item["billable_usage"][metric], abs=1e-4
                ), f"{sub_id} ({exp_item['month']}): billable_usage.{metric} mismatch"
