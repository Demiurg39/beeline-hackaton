"""
test_api.py
Created by Aman (Montaggg) Ismailov

Integration and OpenAPI contract tests for the JSON-first REST API in src/api.py.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from src.api import DEFAULT_DATA_DIR, create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Yield an isolated FastAPI TestClient per test."""
    with TestClient(create_app()) as test_client:
        yield test_client


def test_healthcheck(client: TestClient) -> None:
    """GET /api/health returns 200 OK."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_catalog_and_individual_catalog_endpoints(client: TestClient) -> None:
    """GET /api/catalog, /api/tariffs, /api/addons, and /api/subscribers are consistent."""
    catalog_resp = client.get("/api/catalog")
    assert catalog_resp.status_code == 200
    catalog = catalog_resp.json()
    assert catalog["currency"] == "KGS"
    assert len(catalog["tariffs"]) > 0
    assert len(catalog["addons"]) > 0
    assert len(catalog["subscribers"]) >= 10
    assert "roaming_rates" in catalog

    tariffs_resp = client.get("/api/tariffs")
    assert tariffs_resp.status_code == 200
    assert tariffs_resp.json()["tariffs"] == catalog["tariffs"]
    assert tariffs_resp.json()["roaming_rates"] == catalog["roaming_rates"]

    addons_resp = client.get("/api/addons")
    assert addons_resp.status_code == 200
    assert addons_resp.json() == catalog["addons"]

    subs_resp = client.get("/api/subscribers")
    assert subs_resp.status_code == 200
    assert subs_resp.json() == catalog["subscribers"]


def test_recommend_json_by_subscriber_id(client: TestClient) -> None:
    """POST /api/recommend works with a simple JSON subscriber_id lookup."""
    response = client.post("/api/recommend", json={"subscriber_id": "P01"})
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"recommendation", "comparison", "llm_explanation"}
    assert body["recommendation"]["id"] == "P01"
    assert isinstance(body["llm_explanation"], str) and body["llm_explanation"]
    assert len(body["comparison"]) == len(client.get("/api/tariffs").json()["tariffs"])


def test_recommend_json_with_sliders(client: TestClient) -> None:
    """POST /api/recommend evaluates interactive UI sliders uniformly across 3 months."""
    response = client.post(
        "/api/recommend",
        json={
            "current_tariff_id": "T01",
            "average_usage": {
                "home_call_min": 200,
                "home_sms": 15,
                "home_day_gb": 10.5,
                "home_night_gb": 3.0,
            },
            "include_explanation": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    rec = body["recommendation"]
    assert body["llm_explanation"] is None
    assert rec["action"] in {"stay", "change", "add_packages"}
    assert len(rec["monthly_costs"]) == 3
    monthly_values = list(rec["monthly_costs"].values())
    assert monthly_values[0] == monthly_values[1] == monthly_values[2]
    assert rec["new_monthly_cost"] == monthly_values[0]


def test_recommend_json_with_monthly_usage(client: TestClient) -> None:
    """POST /api/recommend evaluates pre-aggregated 3-month MonthlyUsage payloads."""
    response = client.post(
        "/api/recommend",
        json={
            "current_tariff_id": "T01",
            "subscriber_id": "MONTHLY_USER",
            "monthly_usage": [
                {"month": "2026-08", "home_call_min": 50, "home_day_gb": "5"},
                {"month": "2026-09", "home_call_min": 50, "home_day_gb": "5"},
                {"month": "2026-10", "home_call_min": 50, "home_day_gb": "5"},
            ],
            "include_explanation": False,
        },
    )
    assert response.status_code == 200
    rec = response.json()["recommendation"]
    assert rec["id"] == "MONTHLY_USER"
    assert list(rec["monthly_costs"].keys()) == ["2026-08", "2026-09", "2026-10"]
    assert rec["consumption_on_recommended_tariff"][0]["billable_usage"]["call"] == 50.0


def test_recommend_json_with_inline_csv_content(client: TestClient) -> None:
    """POST /api/recommend with inline CSV matches preset subscriber_id evaluation."""
    baseline = client.post("/api/recommend", json={"subscriber_id": "P01"}).json()[
        "recommendation"
    ]
    p01_profile = next(
        sub for sub in client.get("/api/subscribers").json() if sub["id"] == "P01"
    )
    csv_text = (DEFAULT_DATA_DIR / p01_profile["usage_file"]).read_text(
        encoding="utf-8"
    )
    response = client.post(
        "/api/recommend",
        json={
            "current_tariff_id": p01_profile["current_tariff_id"],
            "months": p01_profile["months"],
            "csv_content": csv_text,
            "subscriber_id": "WEB_P01",
        },
    )
    assert response.status_code == 200
    rec = response.json()["recommendation"]
    assert rec["id"] == "WEB_P01"
    assert rec["action"] == baseline["action"]
    assert rec["tariff_id"] == baseline["tariff_id"]
    assert rec["addon_ids"] == baseline["addon_ids"]
    assert rec["new_monthly_cost"] == baseline["new_monthly_cost"]
    assert rec["net_savings_3m"] == baseline["net_savings_3m"]


def test_recommend_json_with_events_array(client: TestClient) -> None:
    """POST /api/recommend accepts structured JSON usage events and rounds call minutes up."""
    response = client.post(
        "/api/recommend",
        json={
            "current_tariff_id": "T01",
            "months": ["2026-08", "2026-09", "2026-10"],
            "events": [
                {
                    "date": "2026-08-10T02:30:00",
                    "type": "data",
                    "amount": 5.0,
                    "duration_sec": 3600,
                    "roaming": 0,
                },
                {
                    "date": "2026-09-12T15:00:00",
                    "type": "call",
                    "amount": 125,
                    "duration_sec": 0,
                    "roaming": 0,
                },
            ],
        },
    )
    assert response.status_code == 200
    rec = response.json()["recommendation"]
    assert rec["tariff_id"].startswith("T")
    # 125 seconds rounds up to ceil(125 / 60) = 3 minutes in 2026-09
    details_by_month = {
        item["month"]: item for item in rec["consumption_on_recommended_tariff"]
    }
    assert details_by_month["2026-09"]["billable_usage"]["call"] == 3.0


def test_recommend_upload_csv_variants_and_errors(client: TestClient) -> None:
    """POST /api/recommend/upload handles valid files, month formats, empty files, and unsafe names."""
    p01_bytes = (DEFAULT_DATA_DIR / "usage_P01.csv").read_bytes()

    # 1. Registered filename lookup without explicit tariff/months
    registered_resp = client.post(
        "/api/recommend/upload",
        files={"file": ("usage_P01.csv", p01_bytes, "text/csv")},
        data={"include_explanation": "false"},
    )
    assert registered_resp.status_code == 200
    assert registered_resp.json()["recommendation"]["id"] == "P01"
    assert registered_resp.json()["llm_explanation"] is None

    # 2. Custom upload with JSON-encoded months list
    custom_csv = b"date,type,amount\n2026-08-05T12:00:00,call,61\n"
    custom_resp = client.post(
        "/api/recommend/upload",
        files={"file": ("my_export.csv", custom_csv, "text/csv")},
        data={
            "current_tariff_id": "T01",
            "months": '["2026-08", "2026-09", "2026-10"]',
            "subscriber_id": "UPLOAD_1",
        },
    )
    assert custom_resp.status_code == 200
    assert custom_resp.json()["recommendation"]["id"] == "UPLOAD_1"

    # 3. Path traversal filename '..' is sanitized safely (no 500)
    unsafe_name_resp = client.post(
        "/api/recommend/upload",
        files={"file": ("..", custom_csv, "text/csv")},
        data={
            "current_tariff_id": "T01",
            "months": "2026-08,2026-09,2026-10",
        },
    )
    assert unsafe_name_resp.status_code == 200

    # 4. Empty upload returns 400 INVALID_CSV_FORMAT
    empty_resp = client.post(
        "/api/recommend/upload",
        files={"file": ("empty.csv", b"", "text/csv")},
    )
    assert empty_resp.status_code == 400
    assert empty_resp.json()["error"] == "INVALID_CSV_FORMAT"

    # 5. Unknown current_tariff_id returns 400 BILLING_DOMAIN_ERROR (not 500)
    bad_tariff_resp = client.post(
        "/api/recommend/upload",
        files={"file": ("custom.csv", custom_csv, "text/csv")},
        data={
            "current_tariff_id": "T999",
            "months": "2026-08,2026-09,2026-10",
        },
    )
    assert bad_tariff_resp.status_code == 400
    assert bad_tariff_resp.json()["error"] == "BILLING_DOMAIN_ERROR"


def test_recommend_json_validation_and_domain_errors(client: TestClient) -> None:
    """Invalid JSON payloads, conflicting sources, unknown tariffs, and NaN return 4xx envelopes."""
    empty_body_resp = client.post(
        "/api/recommend",
        content=b"   ",
        headers={"Content-Type": "application/json"},
    )
    assert empty_body_resp.status_code == 400
    assert empty_body_resp.json()["error"] == "BILLING_DOMAIN_ERROR"

    empty_resp = client.post("/api/recommend", json={})
    assert empty_resp.status_code == 400
    assert empty_resp.json()["error"] == "BILLING_DOMAIN_ERROR"

    conflict_resp = client.post(
        "/api/recommend",
        json={
            "current_tariff_id": "T01",
            "months": ["2026-08", "2026-09", "2026-10"],
            "csv_content": "date,type,amount\n",
            "events": [],
        },
    )
    assert conflict_resp.status_code == 400
    assert conflict_resp.json()["error"] == "BILLING_DOMAIN_ERROR"

    unknown_tariff_resp = client.post(
        "/api/recommend",
        json={
            "current_tariff_id": "T999",
            "average_usage": {"home_call_min": 10},
        },
    )
    assert unknown_tariff_resp.status_code == 400
    assert unknown_tariff_resp.json()["error"] == "BILLING_DOMAIN_ERROR"

    nan_resp = client.post(
        "/api/recommend",
        json={
            "current_tariff_id": "T01",
            "average_usage": {"home_day_gb": "NaN"},
        },
    )
    assert nan_resp.status_code in {400, 422}
    assert "error" in nan_resp.json()

    not_found_resp = client.post("/api/recommend", json={"subscriber_id": "P999"})
    assert not_found_resp.status_code == 404
    assert not_found_resp.json()["error"] == "SUBSCRIBER_NOT_FOUND"

    bad_type_resp = client.post(
        "/api/recommend",
        content=b'{"current_tariff_id": 12345, "events": "not-a-list"}',
        headers={"Content-Type": "application/json"},
    )
    assert bad_type_resp.status_code == 422
    assert bad_type_resp.json()["error"] == "REQUEST_VALIDATION_ERROR"


def test_openapi_includes_request_and_response_schemas(client: TestClient) -> None:
    """GET /openapi.json exposes RecommendRequest, CatalogResponse, and ApiRecommendationResponse."""
    schema = client.get("/openapi.json").json()
    schemas = schema["components"]["schemas"]
    assert {
        "RecommendRequest",
        "UsageEventInput",
        "UsageSliders",
        "CatalogResponse",
        "ApiRecommendationResponse",
        "TariffsConfig",
        "Addon",
        "SubscriberProfile",
        "ErrorResponse",
    } <= set(schemas.keys())

    post_op = schema["paths"]["/api/recommend"]["post"]
    assert post_op["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RecommendRequest"
    }
    assert post_op["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ApiRecommendationResponse"
    }
