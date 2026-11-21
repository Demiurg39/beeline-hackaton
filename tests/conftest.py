"""Shared fixtures, path constants, domain builders, and assertion helpers for pytest."""

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
        overage=overage or Rates(Decimal("2"), Decimal("50"), Decimal("1")),
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

