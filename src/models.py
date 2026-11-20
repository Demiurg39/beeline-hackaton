"""
models.py
Created by Amir (demiurg39) Chiryagov

Domain models and DTOs for the tariff recommendation engine.
"""

from decimal import Decimal
from typing import Literal, NewType

import msgspec

UsageType = Literal["call", "sms", "data"]
ActionType = Literal["stay", "change", "add_packages"]

TariffId = NewType("TariffId", str)
AddonId = NewType("AddonId", str)
SubscriberId = NewType("SubscriberId", str)


class Rates(msgspec.Struct, frozen=True):
    """
    Per-unit billing rates for overage or roaming usage.

    Attributes:
        call: Cost per outgoing call minute (KGS).
        data: Cost per gigabyte of mobile data (KGS).
        sms: Cost per outgoing SMS message (KGS).
    """

    call: Decimal
    data: Decimal
    sms: Decimal


class Tariff(msgspec.Struct, frozen=True):
    """
    Mobile operator tariff plan definition (entry in tariffs.json).

    Attributes:
        id: Unique tariff code (e.g., "T01").
        name: Human-readable tariff display name.
        fee: Monthly subscription fee (KGS), charged even with zero usage.
        minutes: Monthly home-network call allowance in minutes.
        gb: Monthly home-network mobile data allowance in GB.
        sms: Monthly home-network SMS allowance.
        overage: Unit rates applied when usage exceeds included allowances.
        night_unlimited: Whether home data in [01:00:00, 07:00:00) is free.
    """

    id: TariffId
    name: str
    fee: Decimal
    minutes: int
    gb: Decimal
    sms: int
    overage: Rates
    night_unlimited: bool = False


class TariffsConfig(msgspec.Struct, frozen=True):
    """
    Root tariff catalog configuration loaded from tariffs.json.

    Attributes:
        currency: Billing currency code (default "KGS").
        tariffs: Tuple of all available tariff plans.
        roaming_rates: Flat roaming unit rates excluded from bundles and night unlimited.
    """

    currency: str
    tariffs: tuple[Tariff, ...]
    roaming_rates: Rates


class Addon(msgspec.Struct, frozen=True):
    """Add-on service package definition (entry in addons.json).

    Valid for one calendar month. Between 0 and 2 add-ons (including duplicates)
    may be attached; the chosen combination applies uniformly across all 3 months.

    Attributes:
        id: Unique add-on code (e.g., "A01").
        name: Human-readable add-on display name.
        fee: Monthly recurring add-on fee (KGS).
        minutes: Extra home-network call minutes per month.
        gb: Extra home-network mobile data per month in GB.
        sms: Extra home-network SMS per month.
    """

    id: AddonId
    name: str
    fee: Decimal
    minutes: int
    gb: Decimal
    sms: int


class SubscriberProfile(msgspec.Struct, frozen=True):
    """Input subscriber profile loaded from subscribers.json.

    Attributes:
        id: Unique subscriber identifier (e.g., "P01").
        current_tariff_id: Identifier of the subscriber's active tariff plan.
        months: Exactly 3 billing months in "YYYY-MM" format (including empty months).
        usage_file: Filename of the CSV containing raw usage records.
    """

    id: SubscriberId
    current_tariff_id: TariffId
    months: tuple[str, ...]
    usage_file: str


class NormalizedEvent(msgspec.Struct, frozen=True):
    """Single usage record parsed from CSV with normalized billing units.

    Attributes:
        month: Billing month in "YYYY-MM" format.
        type: Service category ("call", "sms", or "data").
        roaming: Whether the event occurred in roaming (billed separately).
        call_minutes: Call duration rounded up to whole minutes via ceil(sec / 60).
        sms_count: Number of outgoing SMS messages.
        day_data_gb: Data volume outside the [01:00:00, 07:00:00) night window (GB).
        night_data_gb: Data volume overlapping the [01:00:00, 07:00:00) night window (GB).
    """

    month: str
    type: UsageType
    roaming: bool
    call_minutes: int = 0
    sms_count: int = 0
    day_data_gb: Decimal = Decimal(0)
    night_data_gb: Decimal = Decimal(0)


class MonthlyUsage(msgspec.Struct, frozen=True):
    """
    Aggregated subscriber usage totals for a single calendar month.

    Used by the billing engine to evaluate any tariff and add-on combination
    without re-scanning raw CSV events.

    Attributes:
        month: Billing month in "YYYY-MM" format.
        home_call_min: Total rounded home-network call minutes.
        home_sms: Total home-network SMS count.
        home_day_gb: Total home-network daytime data volume (GB).
        home_night_gb: Total home-network nighttime [01:00, 07:00) data volume (GB).
        roaming_call_min: Total rounded roaming call minutes.
        roaming_sms: Total roaming SMS count.
        roaming_gb: Total roaming data volume (GB).
    """

    month: str
    home_call_min: int = 0
    home_sms: int = 0
    home_day_gb: Decimal = Decimal(0)
    home_night_gb: Decimal = Decimal(0)
    roaming_call_min: int = 0
    roaming_sms: int = 0
    roaming_gb: Decimal = Decimal(0)


class BillableUsage(msgspec.Struct, frozen=True):
    """
    Home-network billable usage on the recommended tariff plan.

    Nighttime traffic is excluded from data when the recommended tariff
    includes night_unlimited.

    Attributes:
        call: Billable home call minutes for the month.
        sms: Billable home SMS count for the month.
        data: Billable home data volume for the month (GB).
    """

    call: float
    sms: float
    data: float


class MonthlyConsumptionDetails(msgspec.Struct, frozen=True):
    """
    Per-month billable consumption and roaming cost breakdown.

    Attributes:
        month: Billing month in "YYYY-MM" format.
        billable_usage: Home-network usage counted against bundles and overage.
        roaming_cost_som: Total roaming charges for the month (KGS).
    """

    month: str
    billable_usage: BillableUsage
    roaming_cost_som: float


class RecommendationResult(msgspec.Struct, frozen=True):
    """
    Core tariff recommendation output matching the expected.json contract.

    Attributes:
        id: Subscriber identifier.
        action: Recommended action ("stay", "change", or "add_packages").
        tariff_id: Recommended tariff identifier.
        addon_ids: Sorted list of recommended add-on identifiers (0 to 2 items).
        current_monthly_cost: Average monthly cost on current tariff without add-ons (KGS).
        new_monthly_cost: Average monthly cost on the recommended option (KGS).
        monthly_savings: Average monthly savings before one-time switch fee (KGS).
        switch_fee: One-time tariff switch fee (50.0 KGS if tariff changes, else 0.0).
        net_savings_3m: Net 3-month savings after deducting switch_fee (KGS).
        monthly_costs: Per-month total cost mapping {"YYYY-MM": cost_kgs}.
        consumption_on_recommended_tariff: Per-month billable usage details.
    """

    id: SubscriberId
    action: ActionType
    tariff_id: TariffId
    addon_ids: list[AddonId]
    current_monthly_cost: float
    new_monthly_cost: float
    monthly_savings: float
    switch_fee: float
    net_savings_3m: float
    monthly_costs: dict[str, float]
    consumption_on_recommended_tariff: list[MonthlyConsumptionDetails]


class TariffComparisonItem(msgspec.Struct, frozen=True):
    """
    Best evaluated configuration per tariff for frontend comparison charts.

    Attributes:
        tariff_id: Evaluated tariff identifier.
        tariff_name: Human-readable tariff name for chart labels.
        addon_ids: Optimal add-on combination (0 to 2 items) for this tariff.
        avg_monthly_cost: Average monthly cost including add-ons and overage (KGS).
        net_savings_3m: Net 3-month savings relative to current tariff (KGS).
        is_profitable: Whether switching is profitable after accounting for switch_fee.
    """

    tariff_id: TariffId
    tariff_name: str
    addon_ids: list[AddonId]
    avg_monthly_cost: float
    net_savings_3m: float
    is_profitable: bool


class ApiRecommendationResponse(msgspec.Struct, frozen=True):
    """
    Extended FastAPI response envelope used to generate the frontend SDK.

    Attributes:
        recommendation: Core recommendation result compatible with benchmark schema.
        comparison: Best evaluated configurations across all available tariffs.
        llm_explanation: Optional natural-language explanation generated by an LLM.
    """

    recommendation: RecommendationResult
    comparison: list[TariffComparisonItem]
    llm_explanation: str | None = None
