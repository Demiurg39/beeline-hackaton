from decimal import Decimal
from typing import Literal, NewType

import msgspec

UsageType = Literal["call", "sms", "data"]
ActionType = Literal["stay", "change", "add_packages"]

TariffId = NewType("TariffId", str)
AddonId = NewType("AddonId", str)
SubscriberId = NewType("SubscriberId", str)

class Rates(msgspec.Struct, frozen=True):
    call: Decimal
    data: Decimal
    sms: Decimal

class Tariff(msgspec.Struct, frozen=True):
    id: TariffId
    name: str
    fee: Decimal
    minutes: int
    gb: Decimal
    sms: int
    overage: Rates
    night_unlimited: bool = False

class TariffsConfig(msgspec.Struct, frozen=True):
    curency: str
    tariffs: tuple[Tariff, ...]
    roaming_rates: Rates

class Addon(msgspec.Struct, frozen=True):
    id: AddonId
    name: str
    fee: Decimal
    minutes: int
    gb: Decimal
    sms: int

class SubscriberProfile(msgspec.Struct, frozen=True):
    id: SubscriberId
    current_tariff_id: str
    months: tuple[str, ...]
    usage_file: str

class NormalizedEvents(msgspec.Struct, frozen=True):
    month: str
    type: UsageType
    roaming: bool
    call_minutes: int = 0
    sms_count: int = 0
    day_data_gb: Decimal = Decimal(0)
    night_data_gb: Decimal = Decimal(0)

class MounthlyUsage(msgspec.Struct, frozen=True):
    month: str
    home_call_min: int = 0
    home_sms: int = 0
    home_day_gb: Decimal = Decimal(0)
    home_night_gb: Decimal = Decimal(0)
    roaming_call_min: int = 0
    roaming_sms: int = 0
    roaming_gb: Decimal = Decimal(0)

class BillableUsage(msgspec.Struct, frozen=True):
    call: float
    sms: float
    data: float

class MonthlyConsumptionDetails(msgspec.Struct, frozen=True):
    month: str
    billable_usage: BillableUsage
    roaming_cost_som: float

class RecommendationResult(msgspec.Struct, frozen=True):
    id: RecommendationId
    action: ActionType
    tariff_id: TariffId
    addon_ids: list[AddonId]
    currently_mounthly_cost: float
    new_monthly_cost: float
    monthly_savings: float
    switch_fee: float
    net_savings_3m: float
    monthly_cost: dict[str, float]
    consumption_on_recommendation_tariff: list[MonthlyConsumptionDetails]

class TariffComparisonItem(msgspec.Struct, frozen=True):
    tariff_id: TariffId
    tariff_name: str
    addon_ids: list[AddonId]
    avg_monthly_cost: float
    net_savings_3m: float
    is_profitable: bool

class ApiRecommendationResponse(msgspec.Struct, frozen=True):
    recommendation: RecommendationResult
    comparison: list[TariffComparisonItem]
    llm_explanation: str | None = None
