"""
recommender.py
Created by Amir (demiurg39) Chiryagov

Decision engine for evaluating tariff and add-on combinations and selecting
the optimal recommendation for a subscriber.
"""

from decimal import ROUND_HALF_UP, Decimal

from src.calculator import BILLING_PERIOD_MONTHS
from src.models import ActionType, TariffId

_MONEY_QUANTUM: Decimal = Decimal("0.01")
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
