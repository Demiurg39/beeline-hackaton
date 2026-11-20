"""
__init__.py
Created by Amir (demiurg39) Chiryagov

Public package entry points for the Beeline tariff recommendation engine.
"""

from pathlib import Path

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
