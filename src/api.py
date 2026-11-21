"""
api.py
Created by Aman (Montaggg) Ismailov

JSON-first FastAPI REST service exposing the Beeline tariff recommendation engine.
"""

import csv
import io
import logging
import re
import tempfile
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import msgspec
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi_msgspec.responses import MsgSpecJSONResponse

from src import evaluate_usage_file
from src.exceptions import (
    BillingDomainError,
    CsvFormatError,
    SubscriberNotFoundError,
    TariffCalculatorError,
)
from src.models import (
    Addon,
    ApiRecommendationResponse,
    MonthlyUsage,
    Rates,
    RecommendationResult,
    SubscriberId,
    SubscriberProfile,
    Tariff,
    TariffComparisonItem,
    TariffId,
    TariffsConfig,
    UsageType,
)
from src.parser import load_catalog
from src.recommender import evaluate_recommendations

logger = logging.getLogger(__name__)

MsgspecJSONResponse = MsgSpecJSONResponse

DEFAULT_DATA_DIR: Path = Path(__file__).resolve().parent.parent / "data"
DEFAULT_DEMO_MONTHS: tuple[str, str, str] = ("2026-08", "2026-09", "2026-10")
_MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class UsageEventInput(msgspec.Struct, frozen=True):
    """Single usage record passed in JSON requests."""

    date: str
    type: UsageType
    amount: Decimal
    duration_sec: int = 0
    roaming: int = 0


class UsageSliders(msgspec.Struct, frozen=True):
    """Typical single-month consumption values for interactive UI sliders."""

    home_call_min: int = 0
    home_sms: int = 0
    home_day_gb: Decimal = Decimal(0)
    home_night_gb: Decimal = Decimal(0)
    roaming_call_min: int = 0
    roaming_sms: int = 0
    roaming_gb: Decimal = Decimal(0)


class RecommendRequest(msgspec.Struct, frozen=True):
    """
    Unified JSON request body for ``POST /api/recommend``.

    Provide exactly one usage source:
    - ``subscriber_id``: evaluate a registered subscriber from ``subscribers.json``;
    - ``csv_content``: raw CSV text (requires ``current_tariff_id`` and 3 ``months``);
    - ``events``: structured usage records (requires ``current_tariff_id`` and 3 ``months``);
    - ``monthly_usage``: 3 pre-aggregated monthly totals (requires ``current_tariff_id``);
    - ``average_usage``: typical monthly slider values projected across 3 months
      (requires ``current_tariff_id``).
    """

    subscriber_id: str | None = None
    current_tariff_id: str | None = None
    months: list[str] | None = None
    csv_content: str | None = None
    events: list[UsageEventInput] | None = None
    monthly_usage: list[MonthlyUsage] | None = None
    average_usage: UsageSliders | None = None
    include_explanation: bool = True


class CatalogResponse(msgspec.Struct, frozen=True):
    """Combined catalog payload for single-request frontend initialization."""

    currency: str
    tariffs: tuple[Tariff, ...]
    roaming_rates: Rates
    addons: list[Addon]
    subscribers: list[SubscriberProfile]


_OPENAPI_MODELS: tuple[type[msgspec.Struct], ...] = (
    RecommendRequest,
    UsageEventInput,
    UsageSliders,
    MonthlyUsage,
    ApiRecommendationResponse,
    CatalogResponse,
    TariffsConfig,
    Addon,
    SubscriberProfile,
)

_ROUTE_SCHEMAS: dict[tuple[str, str], dict[str, Any]] = {
    ("/api/health", "get"): {
        "response": {
            "type": "object",
            "properties": {"status": {"type": "string", "example": "ok"}},
            "required": ["status"],
        },
    },
    ("/api/catalog", "get"): {
        "response": {"$ref": "#/components/schemas/CatalogResponse"},
    },
    ("/api/tariffs", "get"): {
        "response": {"$ref": "#/components/schemas/TariffsConfig"},
    },
    ("/api/addons", "get"): {
        "response": {
            "type": "array",
            "items": {"$ref": "#/components/schemas/Addon"},
        },
    },
    ("/api/subscribers", "get"): {
        "response": {
            "type": "array",
            "items": {"$ref": "#/components/schemas/SubscriberProfile"},
        },
    },
    ("/api/recommend", "post"): {
        "request": {"$ref": "#/components/schemas/RecommendRequest"},
        "response": {"$ref": "#/components/schemas/ApiRecommendationResponse"},
    },
    ("/api/recommend/upload", "post"): {
        "response": {"$ref": "#/components/schemas/ApiRecommendationResponse"},
    },
}

_ERROR_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "error": {"type": "string"},
        "message": {"type": "string"},
        "details": {"type": "object", "additionalProperties": True},
    },
    "required": ["error", "message", "details"],
}


def _normalize_optional_str(value: str | None) -> str | None:
    """Convert blank strings to ``None``."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _sanitize_upload_filename(raw_filename: str | None) -> str:
    """Return a safe basename for saving an uploaded CSV inside a temporary directory."""
    safe_name = Path(raw_filename or "uploaded_usage.csv").name
    if safe_name in {"", ".", ".."}:
        return "uploaded_usage.csv"
    return safe_name


def _validate_known_tariff_id(
    tariff_id: str | None,
    tariffs_config: TariffsConfig,
) -> None:
    """Ensure a client-supplied current_tariff_id exists in the loaded tariff catalog."""
    if tariff_id is None:
        return
    known_ids = {tariff.id for tariff in tariffs_config.tariffs}
    if TariffId(tariff_id) not in known_ids:
        reason = f"Unknown tariff id '{tariff_id}'."
        raise BillingDomainError(reason, reason=reason)


def _parse_form_months(raw_months: list[str] | None) -> tuple[str, ...] | None:
    """Normalize ``months`` submitted via multipart/form-data."""
    if not raw_months:
        return None

    parsed: list[str] = []
    for entry in raw_months:
        stripped = entry.strip()
        if not stripped:
            continue

        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                decoded = msgspec.json.decode(stripped)
            except msgspec.DecodeError:
                decoded = None
            if isinstance(decoded, list):
                for item in decoded:
                    text = str(item).strip()
                    if text:
                        parsed.append(text)
                continue

        if "," in stripped:
            for part in stripped.split(","):
                text = part.strip()
                if text:
                    parsed.append(text)
        else:
            parsed.append(stripped)

    return tuple(parsed) if parsed else None


def _validate_three_months(
    months: list[str] | tuple[str, ...] | None,
) -> tuple[str, str, str]:
    """Validate that exactly 3 unique ``YYYY-MM`` billing months are provided."""
    if not months or len(months) != 3:
        reason = "Exactly 3 billing months in 'YYYY-MM' format are required."
        raise BillingDomainError(reason, reason=reason)

    cleaned = tuple(m.strip() for m in months)
    for month in cleaned:
        if not _MONTH_PATTERN.match(month):
            reason = f"Invalid billing month format '{month}'; expected 'YYYY-MM'."
            raise BillingDomainError(reason, reason=reason)

    if len(set(cleaned)) != 3:
        reason = "Billing months must not contain duplicates."
        raise BillingDomainError(reason, reason=reason)

    return (cleaned[0], cleaned[1], cleaned[2])


def _is_valid_non_negative_decimal(value: Decimal) -> bool:
    """Return True if value is a finite non-negative Decimal."""
    return value.is_finite() and value >= Decimal(0)


def _validate_monthly_usage_items(items: list[MonthlyUsage]) -> list[MonthlyUsage]:
    """Ensure pre-aggregated monthly usage items have valid months and finite non-negative values."""
    if len(items) != 3:
        reason = "Exactly 3 MonthlyUsage entries are required."
        raise BillingDomainError(reason, reason=reason)

    _validate_three_months([item.month for item in items])

    validated: list[MonthlyUsage] = []
    for item in items:
        if (
            item.home_call_min < 0
            or item.home_sms < 0
            or item.roaming_call_min < 0
            or item.roaming_sms < 0
            or not _is_valid_non_negative_decimal(item.home_day_gb)
            or not _is_valid_non_negative_decimal(item.home_night_gb)
            or not _is_valid_non_negative_decimal(item.roaming_gb)
        ):
            reason = f"Usage values for month '{item.month}' must be finite and non-negative."
            raise BillingDomainError(reason, reason=reason)
        validated.append(
            MonthlyUsage(
                month=item.month.strip(),
                home_call_min=item.home_call_min,
                home_sms=item.home_sms,
                home_day_gb=item.home_day_gb,
                home_night_gb=item.home_night_gb,
                roaming_call_min=item.roaming_call_min,
                roaming_sms=item.roaming_sms,
                roaming_gb=item.roaming_gb,
            )
        )
    return validated


def _events_to_csv_text(events: list[UsageEventInput]) -> str:
    """Serialize structured JSON events into canonical CSV for ``parse_usage_csv``."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["date", "type", "amount", "duration_sec", "roaming"])
    for event in events:
        writer.writerow(
            [
                event.date,
                event.type,
                str(event.amount),
                event.duration_sec,
                event.roaming,
            ]
        )
    return buffer.getvalue()


def _build_fallback_explanation(
    recommendation: RecommendationResult,
    comparison: list[TariffComparisonItem],
) -> str:
    """Generate a deterministic summary when the LLM explainer module is unavailable."""
    addons_part = (
        f" with add-ons {', '.join(recommendation.addon_ids)}"
        if recommendation.addon_ids
        else " without add-ons"
    )
    if recommendation.action == "stay":
        return (
            f"Stay on your current tariff ({recommendation.tariff_id}) without add-ons. "
            f"Average monthly cost is {recommendation.current_monthly_cost:.2f} KGS "
            f"({len(comparison)} tariffs evaluated)."
        )
    if recommendation.action == "add_packages":
        return (
            f"Keep tariff {recommendation.tariff_id}{addons_part}. "
            f"Your average monthly cost drops from {recommendation.current_monthly_cost:.2f} "
            f"to {recommendation.new_monthly_cost:.2f} KGS, saving "
            f"{recommendation.net_savings_3m:.2f} KGS net over 3 months."
        )
    return (
        f"Switch to tariff {recommendation.tariff_id}{addons_part}. "
        f"Even after the {recommendation.switch_fee:.2f} KGS switch fee, "
        f"you save {recommendation.net_savings_3m:.2f} KGS net over 3 months "
        f"({recommendation.new_monthly_cost:.2f} vs {recommendation.current_monthly_cost:.2f} KGS/month)."
    )


def _generate_explanation(
    recommendation: RecommendationResult,
    comparison: list[TariffComparisonItem],
) -> str:
    """Invoke ``src.llm_explainer.explain_recommendation`` with a safe fallback."""
    try:
        from src.llm_explainer import explain_recommendation  # type: ignore[import-not-found]
    except ImportError:
        return _build_fallback_explanation(recommendation, comparison)

    try:
        return explain_recommendation(recommendation, comparison)
    except Exception:
        logger.warning(
            "LLM explanation generation failed; using deterministic fallback.",
            exc_info=True,
        )
        return _build_fallback_explanation(recommendation, comparison)


def _evaluate_json_request(
    payload: RecommendRequest,
    data_dir: Path,
) -> tuple[RecommendationResult, list[TariffComparisonItem]]:
    """Dispatch a validated ``RecommendRequest`` to the billing engine."""
    subscriber_id = _normalize_optional_str(payload.subscriber_id)
    current_tariff_id = _normalize_optional_str(payload.current_tariff_id)
    csv_content = _normalize_optional_str(payload.csv_content)

    explicit_sources = sum(
        source is not None
        for source in (
            csv_content,
            payload.events,
            payload.monthly_usage,
            payload.average_usage,
        )
    )
    if explicit_sources > 1:
        reason = (
            "Provide only one usage source among 'csv_content', 'events', "
            "'monthly_usage', or 'average_usage'."
        )
        raise BillingDomainError(reason, reason=reason)

    tariffs_config, addons, subscribers = load_catalog(data_dir)
    _validate_known_tariff_id(current_tariff_id, tariffs_config)

    # Mode 1: Preset subscriber lookup from subscribers.json
    if explicit_sources == 0:
        if subscriber_id is None:
            reason = (
                "Provide either 'subscriber_id' or one of 'csv_content', 'events', "
                "'monthly_usage', or 'average_usage'."
            )
            raise BillingDomainError(reason, reason=reason)

        profile = subscribers.get(SubscriberId(subscriber_id))
        if profile is None:
            raise SubscriberNotFoundError(
                f"Subscriber '{subscriber_id}' not found in subscribers.json.",
                subscriber_id=subscriber_id,
            )

        usage_path = data_dir / profile.usage_file
        return evaluate_usage_file(
            usage_file=usage_path,
            data_dir=data_dir,
            current_tariff_id=current_tariff_id,
            months=payload.months,
            subscriber_id=subscriber_id,
        )

    # All custom usage modes require current_tariff_id
    if current_tariff_id is None:
        reason = "'current_tariff_id' is required when submitting custom usage data."
        raise BillingDomainError(reason, reason=reason)

    resolved_sub_id = SubscriberId(subscriber_id or "WEB_SUBSCRIBER")

    # Mode 2: Raw CSV string in JSON
    if csv_content is not None:
        months = _validate_three_months(payload.months)
        with tempfile.TemporaryDirectory() as tmp_dir:
            target_path = Path(tmp_dir) / "inline_usage.csv"
            target_path.write_text(csv_content, encoding="utf-8")
            return evaluate_usage_file(
                usage_file=target_path,
                data_dir=data_dir,
                current_tariff_id=current_tariff_id,
                months=months,
                subscriber_id=resolved_sub_id,
            )

    # Mode 3: Structured usage events list
    if payload.events is not None:
        months = _validate_three_months(payload.months)
        generated_csv = _events_to_csv_text(payload.events)
        with tempfile.TemporaryDirectory() as tmp_dir:
            target_path = Path(tmp_dir) / "events_usage.csv"
            target_path.write_text(generated_csv, encoding="utf-8")
            return evaluate_usage_file(
                usage_file=target_path,
                data_dir=data_dir,
                current_tariff_id=current_tariff_id,
                months=months,
                subscriber_id=resolved_sub_id,
            )

    # Mode 4: Pre-aggregated 3-month usage list
    if payload.monthly_usage is not None:
        validated_usages = _validate_monthly_usage_items(payload.monthly_usage)
        months = (
            validated_usages[0].month,
            validated_usages[1].month,
            validated_usages[2].month,
        )
        subscriber = SubscriberProfile(
            id=resolved_sub_id,
            current_tariff_id=TariffId(current_tariff_id),
            months=months,
            usage_file="inline_monthly_usage.json",
        )
        return evaluate_recommendations(
            subscriber=subscriber,
            monthly_usages=validated_usages,
            tariffs_config=tariffs_config,
            addons=addons,
        )

    # Mode 5: Interactive single-month UI sliders projected across 3 months
    sliders = payload.average_usage
    if sliders is None:
        reason = "No usage source was provided."
        raise BillingDomainError(reason, reason=reason)

    months = (
        _validate_three_months(payload.months)
        if payload.months is not None
        else DEFAULT_DEMO_MONTHS
    )
    projected_usages = _validate_monthly_usage_items(
        [
            MonthlyUsage(
                month=month,
                home_call_min=sliders.home_call_min,
                home_sms=sliders.home_sms,
                home_day_gb=sliders.home_day_gb,
                home_night_gb=sliders.home_night_gb,
                roaming_call_min=sliders.roaming_call_min,
                roaming_sms=sliders.roaming_sms,
                roaming_gb=sliders.roaming_gb,
            )
            for month in months
        ]
    )
    subscriber = SubscriberProfile(
        id=resolved_sub_id,
        current_tariff_id=TariffId(current_tariff_id),
        months=months,
        usage_file="inline_sliders.json",
    )
    return evaluate_recommendations(
        subscriber=subscriber,
        monthly_usages=projected_usages,
        tariffs_config=tariffs_config,
        addons=addons,
    )


def _evaluate_uploaded_csv(
    *,
    raw_bytes: bytes,
    safe_name: str,
    data_dir: Path,
    current_tariff_id: str | None,
    months: tuple[str, ...] | None,
    subscriber_id: str | None,
) -> tuple[RecommendationResult, list[TariffComparisonItem]]:
    """Validate and evaluate an uploaded CSV file inside a temporary directory."""
    if current_tariff_id is not None:
        tariffs_config, _, _ = load_catalog(data_dir)
        _validate_known_tariff_id(current_tariff_id, tariffs_config)

    with tempfile.TemporaryDirectory() as tmp_dir:
        target_path = Path(tmp_dir) / safe_name
        target_path.write_bytes(raw_bytes)

        return evaluate_usage_file(
            usage_file=target_path,
            data_dir=data_dir,
            current_tariff_id=current_tariff_id,
            months=months,
            subscriber_id=subscriber_id,
        )


def _build_openapi_generator(app: FastAPI) -> Callable[[], dict[str, Any]]:
    """Build a custom OpenAPI schema generator enriched with msgspec struct schemas."""

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema

        openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )

        _, struct_schemas = msgspec.json.schema_components(
            _OPENAPI_MODELS,
            ref_template="#/components/schemas/{name}",
        )
        components = openapi_schema.setdefault("components", {})
        schemas = components.setdefault("schemas", {})
        schemas.update(struct_schemas)
        schemas["ErrorResponse"] = _ERROR_RESPONSE_SCHEMA

        paths = openapi_schema.get("paths", {})
        for (path, method), spec in _ROUTE_SCHEMAS.items():
            operation = paths.get(path, {}).get(method)
            if not isinstance(operation, dict):
                continue

            if "request" in spec:
                operation["requestBody"] = {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": spec["request"],
                            "example": {
                                "subscriber_id": "P01",
                                "include_explanation": True,
                            },
                        },
                    },
                }

            responses = operation.setdefault("responses", {})
            ok_response = responses.setdefault(
                "200",
                {"description": "Successful Response"},
            )
            ok_response["content"] = {
                "application/json": {"schema": spec["response"]},
            }
            for status_code, desc in (
                ("400", "Bad Request / Domain or CSV Format Error"),
                ("404", "Resource or Subscriber Not Found"),
                ("422", "Validation Error"),
                ("500", "Configuration or Internal Error"),
            ):
                responses.setdefault(
                    status_code,
                    {
                        "description": desc,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/ErrorResponse"
                                },
                            },
                        },
                    },
                )

        app.openapi_schema = openapi_schema
        return app.openapi_schema

    return custom_openapi


def create_app(data_dir: Path | None = None) -> FastAPI:
    """Create and configure the FastAPI application instance."""
    resolved_data_dir = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR

    application = FastAPI(
        title="Beeline Tariff Advisor API",
        version="0.1.0",
        description="JSON-first REST API for 3-month subscriber usage analysis and tariff/add-on recommendations.",
        default_response_class=MsgSpecJSONResponse,
    )
    application.state.data_dir = resolved_data_dir

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.exception_handler(TariffCalculatorError)
    async def handle_tariff_calculator_error(
        _request: Request,
        exc: TariffCalculatorError,
    ) -> MsgSpecJSONResponse:
        return MsgSpecJSONResponse(
            status_code=exc.status_code,
            content=exc.to_dict(),
        )

    @application.get("/api/health", response_model=None, tags=["system"])
    def healthcheck() -> MsgSpecJSONResponse:
        """Return service liveness status."""
        return MsgSpecJSONResponse(content={"status": "ok"})

    @application.get("/api/catalog", response_model=None, tags=["catalog"])
    def get_catalog() -> MsgSpecJSONResponse:
        """Return tariffs, roaming rates, add-ons, and demo subscribers in one response."""
        tariffs_config, addons, subscribers = load_catalog(application.state.data_dir)
        catalog = CatalogResponse(
            currency=tariffs_config.currency,
            tariffs=tariffs_config.tariffs,
            roaming_rates=tariffs_config.roaming_rates,
            addons=list(addons),
            subscribers=list(subscribers.values()),
        )
        return MsgSpecJSONResponse(content=catalog)

    @application.get("/api/tariffs", response_model=None, tags=["catalog"])
    def get_tariffs() -> MsgSpecJSONResponse:
        """Return available tariffs, currency, and roaming rates."""
        tariffs_config, _, _ = load_catalog(application.state.data_dir)
        return MsgSpecJSONResponse(content=tariffs_config)

    @application.get("/api/addons", response_model=None, tags=["catalog"])
    def get_addons() -> MsgSpecJSONResponse:
        """Return all available monthly add-on packages."""
        _, addons, _ = load_catalog(application.state.data_dir)
        return MsgSpecJSONResponse(content=list(addons))

    @application.get("/api/subscribers", response_model=None, tags=["catalog"])
    def get_subscribers() -> MsgSpecJSONResponse:
        """Return predefined demo subscriber profiles from subscribers.json."""
        _, _, subscribers = load_catalog(application.state.data_dir)
        return MsgSpecJSONResponse(content=list(subscribers.values()))

    @application.post("/api/recommend", response_model=None, tags=["recommendations"])
    async def recommend_from_json(request: Request) -> MsgSpecJSONResponse:
        """
        Primary JSON endpoint: decode ``RecommendRequest`` and return recommendation.
        """
        raw_body = await request.body()
        if not raw_body.strip():
            reason = "Request body must be a valid JSON object."
            raise BillingDomainError(reason, reason=reason)

        try:
            payload = msgspec.json.decode(
                raw_body,
                type=RecommendRequest,
                strict=False,
            )
        except (msgspec.DecodeError, msgspec.ValidationError) as exc:
            return MsgSpecJSONResponse(
                status_code=422,
                content={
                    "error": "REQUEST_VALIDATION_ERROR",
                    "message": f"Invalid JSON payload: {exc}",
                    "details": {"reason": str(exc)},
                },
            )

        recommendation, comparison = await run_in_threadpool(
            _evaluate_json_request,
            payload=payload,
            data_dir=application.state.data_dir,
        )
        explanation = (
            await run_in_threadpool(_generate_explanation, recommendation, comparison)
            if payload.include_explanation
            else None
        )
        response_payload = ApiRecommendationResponse(
            recommendation=recommendation,
            comparison=comparison,
            llm_explanation=explanation,
        )
        return MsgSpecJSONResponse(content=response_payload)

    @application.post(
        "/api/recommend/upload", response_model=None, tags=["recommendations"]
    )
    async def recommend_from_csv_upload(
        file: UploadFile = File(...),
        current_tariff_id: str | None = Form(default=None),
        months: list[str] | None = Form(default=None),
        subscriber_id: str | None = Form(default=None),
        include_explanation: bool = Form(default=True),
    ) -> MsgSpecJSONResponse:
        """Secondary multipart/form-data endpoint for direct CSV file uploads."""
        raw_bytes = await file.read()
        safe_name = _sanitize_upload_filename(file.filename)

        if len(raw_bytes) == 0:
            raise CsvFormatError(
                f"Uploaded CSV file '{safe_name}' is empty.",
                file_path=safe_name,
                reason="Uploaded file has 0 bytes.",
            )

        parsed_months = _parse_form_months(months)
        normalized_tariff_id = _normalize_optional_str(current_tariff_id)
        normalized_subscriber_id = _normalize_optional_str(subscriber_id)

        recommendation, comparison = await run_in_threadpool(
            _evaluate_uploaded_csv,
            raw_bytes=raw_bytes,
            safe_name=safe_name,
            data_dir=application.state.data_dir,
            current_tariff_id=normalized_tariff_id,
            months=parsed_months,
            subscriber_id=normalized_subscriber_id,
        )

        explanation = (
            await run_in_threadpool(_generate_explanation, recommendation, comparison)
            if include_explanation
            else None
        )
        response_payload = ApiRecommendationResponse(
            recommendation=recommendation,
            comparison=comparison,
            llm_explanation=explanation,
        )
        return MsgSpecJSONResponse(content=response_payload)

    application.openapi = _build_openapi_generator(application)  # type: ignore[method-assign]
    return application


app = create_app()
