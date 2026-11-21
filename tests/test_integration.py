"""
test_integration.py
Created by Amir (demiurg39) Chiryagov

Integration tests for the full pipeline, CLI entry point, and 10 open subscribers.
"""

import json
import sys
from pathlib import Path

import pytest

import main as cli_main
from recommend import recommend as root_recommend
from src import evaluate_usage_file, recommend
from src.exceptions import BillingDomainError, DataFileNotFoundError
from tests.conftest import (
    DATA_DIR,
    assert_recommendation_matches,
    load_expected_map,
)

EXPECTED_PATH: Path = DATA_DIR / "expected.json"


def _discover_open_subscriber_ids() -> list[str]:
    """Return sorted subscriber IDs from data/expected.json when available."""
    if not EXPECTED_PATH.is_file():
        return []
    return sorted(load_expected_map(EXPECTED_PATH).keys())


@pytest.mark.skipif(
    not EXPECTED_PATH.is_file(),
    reason="Open benchmark dataset data/expected.json is not present.",
)
def test_open_dataset_contains_at_least_ten_subscribers() -> None:
    """Verify the open benchmark dataset contains all 10 reference subscribers."""
    expected_by_id = load_expected_map(EXPECTED_PATH)
    assert len(expected_by_id) >= 10


@pytest.mark.parametrize("sub_id", _discover_open_subscriber_ids())
def test_open_subscriber_matches_expected_json(sub_id: str) -> None:
    """Each open subscriber matches data/expected.json across all fields."""
    expected = load_expected_map(EXPECTED_PATH)[sub_id]
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


def test_missing_usage_file_raises_data_file_not_found(tmp_path: Path) -> None:
    """Non-existent CSV path fails fast with DataFileNotFoundError instead of SubscriberNotFoundError."""
    with pytest.raises(DataFileNotFoundError):
        recommend(tmp_path / "missing_usage.csv")


def test_root_proxy_and_cli_auto_discover_adjacent_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both root recommend.py and main.py auto-discover catalog files next to a CSV."""
    (tmp_path / "tariffs.json").write_text(
        json.dumps(
            {
                "currency": "KGS",
                "tariffs": [
                    {
                        "id": "T01",
                        "name": "Solo",
                        "fee": "200",
                        "minutes": 100,
                        "gb": "10",
                        "sms": 50,
                        "overage": {"call": "2", "data": "50", "sms": "1"},
                        "night_unlimited": False,
                    }
                ],
                "roaming_rates": {"call": "30", "data": "200", "sms": "10"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "addons.json").write_text("[]", encoding="utf-8")
    (tmp_path / "subscribers.json").write_text(
        json.dumps(
            [
                {
                    "id": "X01",
                    "current_tariff_id": "T01",
                    "months": ["2026-08", "2026-09", "2026-10"],
                    "usage_file": "usage_X01.csv",
                }
            ]
        ),
        encoding="utf-8",
    )
    usage_csv = tmp_path / "usage_X01.csv"
    usage_csv.write_text("date,type,amount\n", encoding="utf-8")

    # Root proxy recommend(usage_file)
    proxy_result = root_recommend(usage_csv)
    assert proxy_result["id"] == "X01"
    assert proxy_result["action"] == "stay"
    assert proxy_result["current_monthly_cost"] == 200.0

    # CLI without --data-dir must auto-discover tmp_path catalog
    monkeypatch.setattr(sys, "argv", ["main.py", str(usage_csv)])
    exit_code = cli_main.main()
    captured = capsys.readouterr()
    assert exit_code == 0
    cli_payload = json.loads(captured.out)
    assert cli_payload == proxy_result

    # CLI on missing file returns exit code 2 with JSON error envelope on stderr
    monkeypatch.setattr(sys, "argv", ["main.py", str(tmp_path / "absent.csv")])
    err_code = cli_main.main()
    err_captured = capsys.readouterr()
    assert err_code == 2
    assert json.loads(err_captured.err)["error"] == "FILE_NOT_FOUND"
