"""
recommender.py
Created by Amir (demiurg39) Chiryagov

Decision engine for evaluating tariff and add-on combinations and selecting
the optimal recommendation for a subscriber.
"""

from decimal import ROUND_HALF_UP, Decimal

from src.calculator import BILLING_PERIOD_MONTHS

_MONEY_QUANTUM: Decimal = Decimal("0.01")
def _round_money(value: Decimal) -> float:
    """Round an exact ``Decimal`` amount to 0.01 KGS using ``ROUND_HALF_UP``."""
    return float(value.quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP))
