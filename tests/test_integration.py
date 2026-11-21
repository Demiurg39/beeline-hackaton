"""
test_integration.py
Created by Amir (demiurg39) Chiryagov

Integration tests for the full pipeline and 10 open benchmark subscribers.
"""

from pathlib import Path

import pytest

from src import evaluate_usage_file, recommend
from src.exceptions import BillingDomainError
from tests.conftest import (
    DATA_DIR,
    assert_recommendation_matches,
    load_expected_map,
)

EXPECTED_PATH: Path = DATA_DIR / "expected.json"


@pytest.mark.skipif(
    not EXPECTED_PATH.is_file(),
    reason="Open benchmark dataset data/expected.json is not present.",
)
def test_open_subscribers_match_expected_json() -> None:
    """All 10 open subscribers match data/expected.json across all fields."""
    expected_by_id = load_expected_map(EXPECTED_PATH)
    assert len(expected_by_id) >= 10

    for sub_id, expected in sorted(expected_by_id.items()):
        usage_file = DATA_DIR / f"usage_{sub_id}.csv"
        assert usage_file.is_file(), f"Missing usage file {usage_file}"

        actual = recommend(usage_file)
        assert_recommendation_matches(actual, expected)


@pytest.mark.skipif(
    not (DATA_DIR / "usage_P01.csv").is_file(),
    reason="Open dataset data/usage_P01.csv is not present.",
)
def test_evaluate_usage_file_comparison_and_explicit_overrides(tmp_path: Path) -> None:
    """evaluate_usage_file preserves tariff order and supports unregistered CSV overrides."""
    usage_file = DATA_DIR / "usage_P01.csv"

    recommendation, comparison = evaluate_usage_file(usage_file)
    assert len(comparison) == 5
    assert [item.tariff_id for item in comparison] == [
        "T01",
        "T02",
        "T03",
        "T04",
        "T05",
    ]

    # Unregistered CSV filename with explicit current_tariff_id and months overrides
    custom_csv = tmp_path / "custom_upload.csv"
    custom_csv.write_bytes(usage_file.read_bytes())

    overridden = recommend(
        custom_csv,
        data_dir=DATA_DIR,
        current_tariff_id="T05",
        months=tuple(recommendation.monthly_costs.keys()),
        subscriber_id="CUSTOM_01",
    )
    assert overridden["id"] == "CUSTOM_01"

    # Providing only one of current_tariff_id / months for unregistered CSV raises BillingDomainError
    with pytest.raises(BillingDomainError):
        recommend(
            custom_csv,
            data_dir=DATA_DIR,
            current_tariff_id="T01",
        )
