"""
__init__.py
Created by Amir (demiurg39) Chiryagov

Public package entry points for the Beeline tariff recommendation engine.
"""
from collections.abc import Sequence
from pathlib import Path

from src.exceptions import BillingDomainError, SubscriberNotFoundError
from src.models import SubscriberId, SubscriberProfile, TariffId
from src.parser import resolve_subscriber

_DEFAULT_DATA_DIR: Path = Path(__file__).resolve().parent.parent / "data"
_CATALOG_MARKER_FILES: tuple[str, str, str] = (
    "tariffs.json",
    "addons.json",
    "subscribers.json",
)


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
