"""
test_edge_cases.py
Created by Amir (demiurg39) Chiryagov

Parametrized end-to-end tests against the 12 official edge-case datasets.
"""

from pathlib import Path

import pytest

from src import recommend
from tests.conftest import (
    EDGE_CASES_DIR,
    assert_recommendation_matches,
    load_expected_map,
)


def _discover_edge_case_dirs() -> list[Path]:
    """Return sorted edge-case directories under data/edge_cases/."""
    if not EDGE_CASES_DIR.is_dir():
        return []
    return sorted(path for path in EDGE_CASES_DIR.iterdir() if path.is_dir())


@pytest.mark.skipif(
    not EDGE_CASES_DIR.is_dir(),
    reason="Official edge-case dataset directory data/edge_cases/ is not present.",
)
def test_all_twelve_official_edge_case_dirs_exist() -> None:
    """Verify all 12 benchmark edge-case directories from the task dataset are present."""
    case_dirs = _discover_edge_case_dirs()
    assert len(case_dirs) >= 12


@pytest.mark.parametrize("case_dir", _discover_edge_case_dirs(), ids=lambda p: p.name)
def test_official_edge_case_matches_expected(case_dir: Path) -> None:
    """Each official edge-case dataset produces exact expected recommendation and breakdown."""
    expected_path = case_dir / "expected.json"
    assert expected_path.is_file(), f"Missing expected.json in {case_dir}"

    expected_by_id = load_expected_map(expected_path)
    usage_files = sorted(case_dir.glob("usage_*.csv"))
    assert usage_files, f"No usage_*.csv files found in {case_dir}"

    for usage_file in usage_files:
        actual = recommend(usage_file)
        sub_id = actual["id"]
        assert sub_id in expected_by_id, f"{case_dir.name}: unexpected subscriber id '{sub_id}'"
        assert_recommendation_matches(actual, expected_by_id[sub_id])
