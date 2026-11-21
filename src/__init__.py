"""
__init__.py
Created by Amir (demiurg39) Chiryagov

Public package entry points for the Beeline tariff recommendation engine.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import msgspec

from src.exceptions import (
    BillingDomainError,
    DataFileNotFoundError,
    SubscriberNotFoundError,
)
from src.models import (
    RecommendationResult,
    SubscriberId,
    SubscriberProfile,
    TariffComparisonItem,
    TariffId,
)
from src.parser import load_catalog, parse_usage_csv, resolve_subscriber
from src.recommender import evaluate_recommendations

_DEFAULT_DATA_DIR: Path = Path(__file__).resolve().parent.parent / "data"
_CATALOG_MARKER_FILES: tuple[str, str, str] = (
    "tariffs.json",
    "addons.json",
    "subscribers.json",
)

__all__ = [
    "evaluate_usage_file",
    "recommend",
]


def evaluate_usage_file(
    usage_file: str | Path,
    *,
    data_dir: str | Path | None = None,
    current_tariff_id: str | TariffId | None = None,
    months: Sequence[str] | None = None,
    subscriber_id: str | SubscriberId | None = None,
) -> tuple[RecommendationResult, list[TariffComparisonItem]]:
    """
    Run the full billing pipeline and return typed recommendation and comparison models.

    Args:
        usage_file: Path to the subscriber's usage CSV file.
        data_dir: Optional directory containing ``tariffs.json``, ``addons.json``,
            and ``subscribers.json``. Defaults to the directory of ``usage_file``
            if catalog files exist there, otherwise the project ``data/`` directory.
        current_tariff_id: Optional override for the subscriber's current tariff ID.
        months: Optional override for the 3 billing months (``"YYYY-MM"``).
        subscriber_id: Optional subscriber identifier override.

    Returns:
        A tuple ``(recommendation, comparison)`` with typed ``msgspec`` structs.
    """

    usage_path = Path(usage_file)
    if not usage_path.is_file():
        raise DataFileNotFoundError(
            f"Usage CSV file not found: {usage_path}",
            file_path=str(usage_path),
        )

    catalog_dir = _resolve_catalog_dir(usage_path, data_dir)
    tariffs_config, addons, subscribers = load_catalog(catalog_dir)

    subscriber = _resolve_target_subscriber(
        usage_path=usage_path,
        subscribers=subscribers,
        current_tariff_id=current_tariff_id,
        months=months,
        subscriber_id=subscriber_id,
    )
    monthly_usages = parse_usage_csv(usage_path, subscriber.months)

    return evaluate_recommendations(
        subscriber=subscriber,
        monthly_usages=monthly_usages,
        tariffs_config=tariffs_config,
        addons=addons,
    )


def recommend(
    usage_file: str | Path,
    *,
    data_dir: str | Path | None = None,
    current_tariff_id: str | TariffId | None = None,
    months: Sequence[str] | None = None,
    subscriber_id: str | SubscriberId | None = None,
) -> dict[str, Any]:
    """
    Evaluate a subscriber's usage file and return a JSON-compatible recommendation dict.

    Serves as the primary entry point for automated benchmark grading
    (``recommend(usage_file)``) as well as programmatic and API callers.

    Args:
        usage_file: Path to the subscriber's usage CSV file.
        data_dir: Optional directory containing ``tariffs.json``, ``addons.json``,
            and ``subscribers.json``.
        current_tariff_id: Optional override for the subscriber's current tariff ID.
        months: Optional override for the 3 billing months (``"YYYY-MM"``).
        subscriber_id: Optional subscriber identifier override.

    Returns:
        Dictionary matching the ``expected.json`` schema with rounded monetary
        values and per-month billable consumption details.
    """
    recommendation, _ = evaluate_usage_file(
        usage_file=usage_file,
        data_dir=data_dir,
        current_tariff_id=current_tariff_id,
        months=months,
        subscriber_id=subscriber_id,
    )
    return msgspec.to_builtins(recommendation)


def _resolve_catalog_dir(usage_path: Path, data_dir: str | Path | None) -> Path:
    """Locate the directory containing tariffs.json, addons.json, and subscribers.json."""
    if data_dir is not None:
        return Path(data_dir)

    sibling_dir = usage_path.resolve().parent
    if all((sibling_dir / filename).is_file() for filename in _CATALOG_MARKER_FILES):
        return sibling_dir

    return _DEFAULT_DATA_DIR


def _resolve_target_subscriber(
    *,
    usage_path: Path,
    subscribers: dict[SubscriberId, SubscriberProfile],
    current_tariff_id: str | TariffId | None,
    months: Sequence[str] | None,
    subscriber_id: str | SubscriberId | None,
) -> SubscriberProfile:
    """Resolve subscriber metadata from catalog or explicit override parameters."""

    if current_tariff_id is not None and months is not None:
        resolved_id = SubscriberId(str(subscriber_id or usage_path.stem))
        return SubscriberProfile(
            id=resolved_id,
            current_tariff_id=TariffId(str(current_tariff_id)),
            months=tuple(months),
            usage_file=usage_path.name,
        )

    try:
        catalog_subscriber = resolve_subscriber(usage_path, subscribers)
    except SubscriberNotFoundError:
        if current_tariff_id is not None or months is not None:
            reason = (
                "Both 'current_tariff_id' and 'months' must be provided when "
                f"'{usage_path.name}' is not registered in subscribers.json."
            )
            raise BillingDomainError(reason=reason, message=reason) from None
        raise

    return SubscriberProfile(
        id=(
            SubscriberId(str(subscriber_id))
            if subscriber_id is not None
            else catalog_subscriber.id
        ),
        current_tariff_id=(
            TariffId(str(current_tariff_id))
            if current_tariff_id is not None
            else catalog_subscriber.current_tariff_id
        ),
        months=tuple(months) if months is not None else catalog_subscriber.months,
        usage_file=catalog_subscriber.usage_file,
    )
