"""
test_api.py
Created by Aman (Montaggg) Ismailov

Integration and OpenAPI contract tests for the JSON-first REST API in src/api.py.
"""

from fastapi.testclient import TestClient

from src.api import DEFAULT_DATA_DIR, create_app

client = TestClient(create_app())


def test_healthcheck() -> None:
    """GET /api/health returns 200 OK."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_combined_catalog() -> None:
    """GET /api/catalog returns tariffs, roaming_rates, addons, and demo subscribers."""
    response = client.get("/api/catalog")
    assert response.status_code == 200
    body = response.json()
    assert body["currency"] == "KGS"
    assert len(body["tariffs"]) > 0
    assert len(body["addons"]) > 0
    assert len(body["subscribers"]) >= 10
    assert "roaming_rates" in body


def test_recommend_json_by_subscriber_id() -> None:
    """POST /api/recommend works with a simple JSON subscriber_id lookup."""
    response = client.post("/api/recommend", json={"subscriber_id": "P01"})
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"recommendation", "comparison", "llm_explanation"}
    assert body["recommendation"]["id"] == "P01"
    assert len(body["comparison"]) == len(client.get("/api/tariffs").json()["tariffs"])


def test_recommend_json_with_sliders() -> None:
    """POST /api/recommend evaluates interactive UI sliders via average_usage."""
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
    assert body["llm_explanation"] is None
    assert body["recommendation"]["action"] in {"stay", "change", "add_packages"}


def test_recommend_json_with_inline_csv_content() -> None:
    """POST /api/recommend accepts raw CSV text inside JSON."""
    csv_text = (DEFAULT_DATA_DIR / "usage_P01.csv").read_text(encoding="utf-8")
    response = client.post(
        "/api/recommend",
        json={
            "current_tariff_id": "T01",
            "months": ["2026-08", "2026-09", "2026-10"],
            "csv_content": csv_text,
            "subscriber_id": "WEB_P01",
        },
    )
    assert response.status_code == 200
    assert response.json()["recommendation"]["id"] == "WEB_P01"


def test_recommend_json_with_events_array() -> None:
    """POST /api/recommend accepts structured JSON usage events."""
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


def test_recommend_json_validation_and_domain_errors() -> None:
    """Invalid JSON payloads and missing required fields return clean error envelopes."""
    empty_resp = client.post("/api/recommend", json={})
    assert empty_resp.status_code == 400
    assert empty_resp.json()["error"] == "BILLING_DOMAIN_ERROR"

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


def test_openapi_includes_request_and_response_schemas() -> None:
    """GET /openapi.json exposes both RecommendRequest and ApiRecommendationResponse."""
    schema = client.get("/openapi.json").json()
    schemas = schema["components"]["schemas"]
    assert {
        "RecommendRequest",
        "UsageEventInput",
        "UsageSliders",
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