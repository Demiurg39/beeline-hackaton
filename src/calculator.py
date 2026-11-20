"""
calculator.py
Created by Amir (demiurg39) Chiryagov

Pure billing calculation service for single tariff and add-on options.
"""

from collections.abc import Sequence
from decimal import Decimal

from src.models import Addon, Rates

MAX_ADDONS_PER_OPTION: int = 2

_ZERO_DECIMAL: Decimal = Decimal(0)


def _validate_decimal_non_negative(value: Decimal, field_name: str) -> None:
    """Ensure a ``Decimal`` value is finite and greater than or equal to zero."""

    if not value.is_finite() or value < _ZERO_DECIMAL:
        reason = (
            f"Field '{field_name}' must be a finite non-negative Decimal, got {value}"
        )
        # TODO: substitude with BillingDomainError
        raise Exception(reason)


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
        # TODO: substitude with BillingDomainError
        raise Exception(reason)
