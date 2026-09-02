"""FastAPI surface for read-only, hash-bound trade-safety assessments.

The service deliberately has no broker adapter, credential input, order route, or
user-selectable upstream.  It translates three fixed public evidence surfaces into
the strict ``liquilens_evidence.trade_safety`` API and issues SHA-256-only receipts.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from urllib.parse import quote, urlparse

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from liquilens_evidence.trade_safety import (
    BROKER_PREVIEW_REFERENCE_SCHEMA,
    TRADE_SAFETY_POLICY_SCHEMA,
    TRADE_SAFETY_REQUEST_SCHEMA,
    TradeSafetyError,
    issue_trade_safety_receipt,
    trade_safety_request_hash,
    validate_trade_safety_policy,
    validate_trade_safety_request,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SERVICE_NAME = "liquilens-trade-safety-gateway"
SERVICE_VERSION = "0.1.1"
GATEWAY_MODE = "sandbox"
MCP_PROTOCOL_VERSION = "2026-07-28"
MCP_LEGACY_PROTOCOL_VERSION = "2025-11-25"

SERVICE_REVISION = os.environ.get("TRADE_SAFETY_SOURCE_REVISION", "source-checkout")
if (
    SERVICE_REVISION != "source-checkout"
    and re.fullmatch(r"[0-9a-f]{40,64}", SERVICE_REVISION) is None
):
    raise RuntimeError("TRADE_SAFETY_SOURCE_REVISION must be a source SHA")
BUILD_CREATED = os.environ.get("TRADE_SAFETY_BUILD_CREATED", "unknown")

SEICHE_URL = "https://api.seiche.info/mcp"
UNDERTOW_URL = "https://api.seiche.info/undertow/mcp"
LIQUILENS_BASE_URL = "https://api.liquilens.in/api/failure-radar/institution/"
ISSUER_ENDPOINT = os.environ.get(
    "TRADE_SAFETY_ISSUER_ENDPOINT", "https://liquilens.in/trade-safety-gateway"
)
_issuer_url = urlparse(ISSUER_ENDPOINT)
if (
    _issuer_url.scheme != "https"
    or not _issuer_url.netloc
    or _issuer_url.username is not None
):
    raise RuntimeError("TRADE_SAFETY_ISSUER_ENDPOINT must be an HTTPS URL")

MAX_REQUEST_BYTES = 64 * 1024
MAX_UPSTREAM_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 512 * 1024
UPSTREAM_TOTAL_TIMEOUT_SECONDS = 5.0
UPSTREAM_CONNECT_TIMEOUT_SECONDS = 2.0
EVIDENCE_TTL_SECONDS = 60
RECEIPT_TTL_SECONDS = 60

PUBLISHED_RUNG_USD = frozenset({1_000.0, 10_000.0, 100_000.0, 1_000_000.0})
BTC_ALIASES = frozenset(
    {
        "BTC",
        "XBT",
        "BITCOIN",
        "BTCUSD",
        "BTC-USD",
        "BTC/USD",
        "XBTUSD",
        "XBT-USD",
        "XBT/USD",
    }
)
REGIMES = frozenset({"CALM", "EROSION", "STRAIN", "STRESS"})
INSTITUTION_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")

SANDBOX_HEADERS = (
    (b"cache-control", b"no-store"),
    (b"x-trade-safety-mode", b"sandbox"),
    (b"x-trade-safety-authority", b"read-only"),
    (b"x-trade-safety-execution", b"disabled"),
    (b"x-trade-safety-revision", SERVICE_REVISION.encode("ascii")),
)


class CheckEnvelope(BaseModel):
    """The only accepted assessment body: exact request plus exact policy."""

    model_config = ConfigDict(extra="forbid", strict=True)

    request: dict[str, Any]
    policy: dict[str, Any]


class MCPEnvelope(BaseModel):
    """Small JSON-RPC envelope for the gateway's read-only MCP surface."""

    model_config = ConfigDict(extra="forbid", strict=True)

    jsonrpc: str
    id: str | int | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _strict_json_object(raw: bytes, field_name: str) -> dict[str, Any]:
    """Decode finite UTF-8 JSON while rejecting duplicate keys and non-objects."""

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
    ) as exc:
        raise ValueError(f"{field_name} must be one strict JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be one strict JSON object")
    return value


async def _send_json(
    send: Send,
    *,
    status: int,
    payload: Mapping[str, Any],
) -> None:
    body = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class SafetyEnvelopeMiddleware:
    """Bound request bodies, require strict JSON, and stamp sandbox authority."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def sandbox_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                names = {name for name, _value in SANDBOX_HEADERS}
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() not in names
                ]
                headers.extend(SANDBOX_HEADERS)
                message["headers"] = headers
            await send(message)

        if scope["method"].upper() != "POST":
            await self.app(scope, receive, sandbox_send)
            return

        raw_headers = scope.get("headers", [])
        content_lengths = [
            value for name, value in raw_headers if name.lower() == b"content-length"
        ]
        if content_lengths:
            try:
                declared = int(content_lengths[-1])
            except ValueError:
                await _send_json(
                    sandbox_send,
                    status=400,
                    payload={"detail": "invalid Content-Length"},
                )
                return
            if declared < 0 or declared > MAX_REQUEST_BYTES:
                await _send_json(
                    sandbox_send,
                    status=413,
                    payload={"detail": "request body exceeds 65536 bytes"},
                )
                return

        content_types = [
            value for name, value in raw_headers if name.lower() == b"content-type"
        ]
        media_type = (
            content_types[-1].decode("latin-1").split(";", 1)[0].strip().lower()
            if content_types
            else ""
        )
        if media_type != "application/json":
            await _send_json(
                sandbox_send,
                status=415,
                payload={"detail": "Content-Type must be application/json"},
            )
            return

        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > MAX_REQUEST_BYTES:
                await _send_json(
                    sandbox_send,
                    status=413,
                    payload={"detail": "request body exceeds 65536 bytes"},
                )
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        try:
            decoded = _strict_json_object(body, "request body")
        except ValueError as exc:
            await _send_json(
                sandbox_send,
                status=400,
                payload={"detail": str(exc)},
            )
            return
        scope.setdefault("state", {})["strict_json"] = decoded

        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, sandbox_send)


def _bounded_response(payload: Mapping[str, Any], status_code: int = 200) -> Response:
    body = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(body) > MAX_RESPONSE_BYTES:
        body = (
            b'{"detail":"gateway response exceeded its fixed byte budget",'
            b'"state":"unavailable"}'
        )
        status_code = 502
    return Response(body, status_code=status_code, media_type="application/json")


class UpstreamUnavailable(RuntimeError):
    """Fixed upstream failed transport, byte-budget, or HTTP checks."""


@dataclass(frozen=True, slots=True)
class RawUpstreamResponse:
    """Exact uncompressed response entity bytes from a fixed upstream."""

    body: bytes


class UpstreamTransport(Protocol):
    """Narrow injection point used by production HTTP and deterministic tests."""

    async def request(
        self,
        method: str,
        url: str,
        *,
        json_body: Mapping[str, Any] | None = None,
    ) -> RawUpstreamResponse: ...

    async def aclose(self) -> None: ...


def _is_allowed_upstream(method: str, url: str) -> bool:
    if method == "POST" and url in {SEICHE_URL, UNDERTOW_URL}:
        return True
    if method != "GET" or not url.startswith(LIQUILENS_BASE_URL):
        return False
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "api.liquilens.in"
        and parsed.port is None
        and parsed.query == ""
        and parsed.fragment == ""
        and parsed.path.startswith("/api/failure-radar/institution/")
    )


class HttpxUpstreamTransport:
    """HTTP transport with fixed destinations, no redirects, proxies, or cookies."""

    def __init__(self) -> None:
        timeout = httpx.Timeout(
            UPSTREAM_TOTAL_TIMEOUT_SECONDS,
            connect=UPSTREAM_CONNECT_TIMEOUT_SECONDS,
        )
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": f"{SERVICE_NAME}/{SERVICE_VERSION}",
            },
        )

    async def request(
        self,
        method: str,
        url: str,
        *,
        json_body: Mapping[str, Any] | None = None,
    ) -> RawUpstreamResponse:
        normalized_method = method.upper()
        if not _is_allowed_upstream(normalized_method, url):
            raise UpstreamUnavailable("destination is not on the fixed allowlist")
        content: bytes | None = None
        headers: dict[str, str] = {}
        if json_body is not None:
            content = json.dumps(
                json_body,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = self._client.build_request(
            normalized_method,
            url,
            content=content,
            headers=headers,
        )
        response: httpx.Response | None = None
        try:
            async with asyncio.timeout(UPSTREAM_TOTAL_TIMEOUT_SECONDS):
                response = await self._client.send(
                    request,
                    stream=True,
                    follow_redirects=False,
                )
                if response.status_code != 200:
                    raise UpstreamUnavailable("upstream did not return HTTP 200")
                media_type = (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                if media_type != "application/json":
                    raise UpstreamUnavailable("upstream did not return JSON")
                encoding = response.headers.get("content-encoding", "identity").lower()
                if encoding not in {"", "identity"}:
                    raise UpstreamUnavailable(
                        "compressed upstream bodies are forbidden"
                    )
                declared = response.headers.get("content-length")
                if declared is not None:
                    try:
                        declared_size = int(declared)
                        if declared_size < 0 or declared_size > MAX_UPSTREAM_BYTES:
                            raise UpstreamUnavailable("upstream body is too large")
                    except ValueError as exc:
                        raise UpstreamUnavailable(
                            "upstream Content-Length is invalid"
                        ) from exc
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_raw():
                    total += len(chunk)
                    if total > MAX_UPSTREAM_BYTES:
                        raise UpstreamUnavailable("upstream body is too large")
                    chunks.append(chunk)
                return RawUpstreamResponse(body=b"".join(chunks))
        except (TimeoutError, httpx.HTTPError, OSError) as exc:
            raise UpstreamUnavailable("fixed upstream is unreachable") from exc
        finally:
            if response is not None:
                await response.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_instant(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise TypeError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _parse_date(value: Any, field_name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date") from exc


def _finite_number(
    value: Any,
    field_name: str,
    *,
    minimum: float = 0.0,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or number < minimum:
        raise ValueError(f"{field_name} is outside its expected range")
    if maximum is not None and number > maximum:
        raise ValueError(f"{field_name} is outside its expected range")
    return number


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    return value


def _mcp_structured(body: bytes, expected_id: str) -> dict[str, Any]:
    response = _strict_json_object(body, "upstream response")
    if response.get("jsonrpc") != "2.0" or response.get("id") != expected_id:
        raise ValueError("upstream JSON-RPC identity mismatch")
    if "error" in response:
        raise ValueError("upstream JSON-RPC error")
    result = _mapping(response.get("result"), "upstream result")
    if result.get("isError") is True:
        raise ValueError("upstream tool result is an error")
    return _mapping(result.get("structuredContent"), "upstream structuredContent")


def _source_sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _evidence_expiry(retrieved_at: datetime, request_expires_at: datetime) -> str:
    expires = min(
        request_expires_at,
        retrieved_at + timedelta(seconds=EVIDENCE_TTL_SECONDS),
    )
    if expires <= retrieved_at:
        raise TradeSafetyError("request expired while retrieving evidence")
    return _utc_text(expires)


def _unavailable_section(
    *,
    product: str,
    request_hash: str,
    source_url: str,
    retrieved_at: datetime,
    limitation: str,
    raw: RawUpstreamResponse | None = None,
) -> dict[str, Any]:
    return {
        "product": product,
        "request_hash": request_hash,
        "state": "unavailable",
        "evidence_class": "unavailable",
        "source_url": source_url,
        "source_schema": None,
        "source_sha256": _source_sha(raw.body) if raw is not None else None,
        "as_of": None,
        "knowledge_time": None,
        "retrieved_at": _utc_text(retrieved_at),
        "expires_at": None,
        "rights_status": "metadata_only",
        "real_money_eligible": False,
        "executable_quote": False,
        "limitations": [limitation],
        "facts": {},
    }


def _liquilens_not_applicable(
    *, request_hash: str, retrieved_at: datetime
) -> dict[str, Any]:
    return {
        "product": "liquilens",
        "request_hash": request_hash,
        "state": "not_applicable",
        "evidence_class": "unavailable",
        "source_url": LIQUILENS_BASE_URL,
        "source_schema": "liquilens.failure-radar.institution.v1",
        "source_sha256": None,
        "as_of": None,
        "knowledge_time": None,
        "retrieved_at": _utc_text(retrieved_at),
        "expires_at": None,
        "rights_status": "metadata_only",
        "real_money_eligible": False,
        "executable_quote": False,
        "limitations": ["institution_slug_not_requested"],
        "facts": {},
    }


def _seiche_section(
    *,
    raw: RawUpstreamResponse,
    request_hash: str,
    retrieved_at: datetime,
    request_expires_at: datetime,
) -> dict[str, Any]:
    payload = _mcp_structured(raw.body, "trade-safety-seiche-v1")
    if payload.get("schema") != "seiche.public.v2":
        raise ValueError("Seiche schema mismatch")
    generated_at = _parse_timestamp(payload.get("generated_at"), "generated_at")
    if generated_at > retrieved_at:
        raise ValueError("Seiche generated_at follows retrieval")
    conclusion = _mapping(payload.get("conclusion"), "conclusion")
    regime = conclusion.get("regime")
    if regime not in REGIMES:
        raise ValueError("Seiche regime is invalid")
    stress_index = _finite_number(
        conclusion.get("value"), "conclusion.value", maximum=100.0
    )
    coverage = _finite_number(
        conclusion.get("coverage_pct"),
        "conclusion.coverage_pct",
        maximum=100.0,
    )
    if coverage < 100.0:
        raise ValueError("Seiche conclusion coverage is incomplete")
    data_quality = _mapping(payload.get("data_quality"), "data_quality")
    if data_quality.get("schema") != "seiche.data_quality.v1":
        raise ValueError("Seiche data-quality schema mismatch")
    quality_generated_at = _parse_timestamp(
        data_quality.get("generated_at"), "data_quality.generated_at"
    )
    if quality_generated_at > generated_at:
        raise ValueError("Seiche data-quality clock follows publication")
    headline_ages = data_quality.get("headline_ages")
    if not isinstance(headline_ages, list) or not headline_ages:
        raise ValueError("Seiche headline observation clocks are unavailable")
    headline_dates: list[date] = []
    for index, item in enumerate(headline_ages):
        headline = _mapping(item, f"data_quality.headline_ages[{index}]")
        series = headline.get("series")
        if not isinstance(series, str) or not series.strip():
            raise ValueError("Seiche headline series is invalid")
        headline_date = _parse_date(
            headline.get("asof"), f"data_quality.headline_ages[{index}].asof"
        )
        if headline_date > generated_at.date():
            raise ValueError("Seiche headline observation follows publication")
        age_days = headline.get("age_days")
        if (
            isinstance(age_days, bool)
            or not isinstance(age_days, int)
            or age_days < 0
            or age_days != (generated_at.date() - headline_date).days
        ):
            raise ValueError("Seiche headline age does not match its as-of date")
        headline_dates.append(headline_date)
    semantic_as_of = datetime.combine(min(headline_dates), time.min, tzinfo=UTC)
    return {
        "product": "seiche",
        "request_hash": request_hash,
        "state": "context_only",
        "evidence_class": "derived",
        "source_url": SEICHE_URL,
        "source_schema": "seiche.public.v2",
        "source_sha256": _source_sha(raw.body),
        "as_of": _utc_text(semantic_as_of),
        "knowledge_time": _utc_text(generated_at),
        "retrieved_at": _utc_text(retrieved_at),
        "expires_at": _evidence_expiry(retrieved_at, request_expires_at),
        "rights_status": "metadata_only",
        "real_money_eligible": False,
        "executable_quote": False,
        "limitations": [
            "public_metadata_context_only_not_licensed_for_real_money_execution",
            "semantic_as_of_is_oldest_reported_headline_observation",
            "gateway_expiry_is_local_not_an_upstream_expiry",
        ],
        "facts": {
            "regime": regime,
            "stress_index": stress_index,
            "coverage_pct": coverage,
        },
    }


def _undertow_section(
    *,
    raw: RawUpstreamResponse,
    request_hash: str,
    requested_size: float,
    retrieved_at: datetime,
    request_expires_at: datetime,
) -> dict[str, Any]:
    payload = _mcp_structured(raw.body, "trade-safety-undertow-v1")
    as_of_date = _parse_date(payload.get("asof"), "asof")
    generated_at = _parse_timestamp(payload.get("generated_at"), "generated_at")
    as_of = datetime.combine(as_of_date, time.min, tzinfo=UTC)
    if as_of > generated_at or generated_at > retrieved_at:
        raise ValueError("Undertow clocks are inconsistent")
    if payload.get("asset") != "BTC":
        raise ValueError("Undertow asset is not BTC")
    echoed_size = _finite_number(
        payload.get("requested_size_usd"), "requested_size_usd"
    )
    published_rung = _finite_number(
        payload.get("published_rung_used_usd"), "published_rung_used_usd"
    )
    if echoed_size != requested_size or published_rung != requested_size:
        raise ValueError("Undertow did not return the exact requested published rung")
    if published_rung not in PUBLISHED_RUNG_USD:
        raise ValueError("Undertow returned an unpublished rung")
    venue_costs = _mapping(
        payload.get("sell_cost_bp_by_venue"), "sell_cost_bp_by_venue"
    )
    if not venue_costs:
        raise ValueError("Undertow venue costs are empty")
    normalized_costs: list[float] = []
    for venue, cost in venue_costs.items():
        if not isinstance(venue, str) or not venue.strip():
            raise ValueError("Undertow venue name is invalid")
        normalized_costs.append(_finite_number(cost, f"venue.{venue}"))
    worst = _mapping(payload.get("worst"), "worst")
    worst_sell = _finite_number(worst.get("sell_bp"), "worst.sell_bp")
    if abs(worst_sell - max(normalized_costs)) > 1e-9:
        raise ValueError("Undertow worst cost does not match venue costs")
    venue_spread = _finite_number(payload.get("venue_spread_bp"), "venue_spread_bp")
    unable = payload.get("unable_at_observed_depth")
    if not isinstance(unable, list) or not all(
        isinstance(item, str) for item in unable
    ):
        raise ValueError("Undertow unable-at-depth field is invalid")
    return {
        "product": "undertow",
        "request_hash": request_hash,
        "state": "context_only",
        "evidence_class": "derived",
        "source_url": UNDERTOW_URL,
        "source_schema": None,
        "source_sha256": _source_sha(raw.body),
        "as_of": _utc_text(as_of),
        "knowledge_time": _utc_text(generated_at),
        "retrieved_at": _utc_text(retrieved_at),
        "expires_at": _evidence_expiry(retrieved_at, request_expires_at),
        "rights_status": "metadata_only",
        "real_money_eligible": False,
        "executable_quote": False,
        "limitations": [
            "public_metadata_context_only_not_licensed_for_real_money_execution",
            "estimated_depth_cost_not_a_book_walk_or_executable_quote",
            "exact_published_rung_required_nearest_rung_substitution_forbidden",
            "upstream_has_no_response_schema_id_shape_validated_by_gateway",
            "gateway_expiry_is_local_not_an_upstream_expiry",
        ],
        "facts": {
            "requested_size_usd": echoed_size,
            "published_rung_used_usd": published_rung,
            "worst_sell_cost_bps": worst_sell,
            "venue_spread_bps": venue_spread,
        },
    }


def _liquilens_section(
    *,
    raw: RawUpstreamResponse,
    source_url: str,
    request_hash: str,
    retrieved_at: datetime,
    request_expires_at: datetime,
) -> dict[str, Any]:
    payload = _strict_json_object(raw.body, "LiquiLens response")
    historical = _mapping(payload.get("historical_evidence"), "historical_evidence")
    status = historical.get("status")
    validated_eligible = historical.get("validated_backtest_eligible")
    real_money_eligible = historical.get("real_money_eligible")
    if not isinstance(status, str) or not status.strip():
        raise ValueError("LiquiLens historical evidence status is missing")
    if not isinstance(validated_eligible, bool) or not isinstance(
        real_money_eligible, bool
    ):
        raise ValueError("LiquiLens historical eligibility flags are invalid")
    trajectory = payload.get("trajectory")
    if not isinstance(trajectory, list) or not trajectory:
        raise ValueError("LiquiLens trajectory is empty")
    period_ends = [
        _parse_date(_mapping(item, "trajectory item").get("period_end"), "period_end")
        for item in trajectory
    ]
    latest_period_end = max(period_ends)
    as_of = datetime.combine(latest_period_end, time.min, tzinfo=UTC)
    if as_of > retrieved_at:
        raise ValueError("LiquiLens period end follows retrieval")
    return {
        "product": "liquilens",
        "request_hash": request_hash,
        "state": "context_only",
        "evidence_class": "research",
        "source_url": source_url,
        "source_schema": "liquilens.failure-radar.institution.v1",
        "source_sha256": _source_sha(raw.body),
        "as_of": _utc_text(as_of),
        "knowledge_time": _utc_text(retrieved_at),
        "retrieved_at": _utc_text(retrieved_at),
        "expires_at": _evidence_expiry(retrieved_at, request_expires_at),
        "rights_status": "metadata_only",
        "real_money_eligible": False,
        "executable_quote": False,
        "limitations": [
            "historical_status_metadata_only_not_an_institution_rating",
            "knowledge_time_is_gateway_retrieval_not_filing_publication_time",
            "gateway_expiry_is_local_not_an_upstream_expiry",
        ],
        "facts": {
            "period_end": latest_period_end.isoformat(),
            "historical_evidence_status": status,
            "validated_backtest_eligible": validated_eligible,
            "historical_real_money_eligible": real_money_eligible,
        },
    }


def _undertow_eligibility(request: Mapping[str, Any]) -> tuple[bool, str]:
    order = request["order"]
    instrument = order["instrument"]
    symbol = str(instrument["symbol"]).strip().upper()
    if instrument["asset_class"] != "crypto" or symbol not in BTC_ALIASES:
        return False, "undertow_supports_only_explicit_btc_crypto_aliases"
    notional = order["notional"]
    if notional["currency"] != "USD":
        return False, "undertow_supports_only_usd_notional"
    amount = float(notional["amount"])
    if amount not in PUBLISHED_RUNG_USD:
        return False, "undertow_requires_an_exact_published_usd_rung"
    return True, ""


def _institution_slug(request: Mapping[str, Any]) -> str | None:
    identifiers = request["order"]["instrument"]["identifiers"]
    if "liquilens_institution_slug" not in identifiers:
        return None
    slug = identifiers["liquilens_institution_slug"]
    if not isinstance(slug, str) or INSTITUTION_SLUG_RE.fullmatch(slug) is None:
        raise TradeSafetyError(
            "request.order.instrument.identifiers.liquilens_institution_slug "
            "must be a lowercase slug"
        )
    return slug


def _mcp_call(
    tool: str, arguments: Mapping[str, Any], request_id: str
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": dict(arguments)},
    }


class TradeSafetyGateway:
    """Compose fixed public context into a deterministic non-executable receipt."""

    def __init__(
        self,
        upstream: UpstreamTransport,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._upstream = upstream
        self._clock = clock

    async def assess(
        self,
        request: Mapping[str, Any],
        policy: Mapping[str, Any],
    ) -> dict[str, Any]:
        started_at = _utc_instant(self._clock(), "clock")
        normalized_request = validate_trade_safety_request(request)
        normalized_policy = validate_trade_safety_policy(policy)
        if normalized_request["policy_ref"] != {
            "policy_id": normalized_policy["policy_id"],
            "version": normalized_policy["version"],
        }:
            raise TradeSafetyError("request.policy_ref does not match policy identity")
        request_created_at = _parse_timestamp(
            normalized_request["created_at"], "request.created_at"
        )
        request_expires_at = _parse_timestamp(
            normalized_request["expires_at"], "request.expires_at"
        )
        if started_at < request_created_at:
            raise TradeSafetyError("request is not yet valid")
        if started_at >= request_expires_at:
            raise TradeSafetyError("request is expired")

        slug = _institution_slug(normalized_request)
        undertow_allowed, undertow_limitation = _undertow_eligibility(
            normalized_request
        )
        request_hash = trade_safety_request_hash(normalized_request)

        calls: dict[str, Awaitable[RawUpstreamResponse]] = {
            "seiche": self._upstream.request(
                "POST",
                SEICHE_URL,
                json_body=_mcp_call("funding_stress_now", {}, "trade-safety-seiche-v1"),
            )
        }
        if undertow_allowed:
            calls["undertow"] = self._upstream.request(
                "POST",
                UNDERTOW_URL,
                json_body=_mcp_call(
                    "exit_cost",
                    {"size_usd": normalized_request["order"]["notional"]["amount"]},
                    "trade-safety-undertow-v1",
                ),
            )
        liquilens_url: str | None = None
        if slug is not None:
            liquilens_url = LIQUILENS_BASE_URL + quote(slug, safe="")
            calls["liquilens"] = self._upstream.request("GET", liquilens_url)

        names = list(calls)
        results = await asyncio.gather(
            *(calls[name] for name in names), return_exceptions=True
        )
        fetched = dict(zip(names, results, strict=True))
        retrieved_at = _utc_instant(self._clock(), "clock")
        if retrieved_at >= request_expires_at:
            raise TradeSafetyError("request expired while retrieving evidence")

        seiche_raw = fetched["seiche"]
        if isinstance(seiche_raw, RawUpstreamResponse):
            try:
                seiche = _seiche_section(
                    raw=seiche_raw,
                    request_hash=request_hash,
                    retrieved_at=retrieved_at,
                    request_expires_at=request_expires_at,
                )
            except (ValueError, TradeSafetyError):
                seiche = _unavailable_section(
                    product="seiche",
                    request_hash=request_hash,
                    source_url=SEICHE_URL,
                    retrieved_at=retrieved_at,
                    limitation="seiche_upstream_contract_unavailable_or_invalid",
                    raw=seiche_raw,
                )
        else:
            seiche = _unavailable_section(
                product="seiche",
                request_hash=request_hash,
                source_url=SEICHE_URL,
                retrieved_at=retrieved_at,
                limitation="seiche_upstream_unreachable",
            )

        if not undertow_allowed:
            undertow = _unavailable_section(
                product="undertow",
                request_hash=request_hash,
                source_url=UNDERTOW_URL,
                retrieved_at=retrieved_at,
                limitation=undertow_limitation,
            )
        else:
            undertow_raw = fetched["undertow"]
            if isinstance(undertow_raw, RawUpstreamResponse):
                try:
                    undertow = _undertow_section(
                        raw=undertow_raw,
                        request_hash=request_hash,
                        requested_size=float(
                            normalized_request["order"]["notional"]["amount"]
                        ),
                        retrieved_at=retrieved_at,
                        request_expires_at=request_expires_at,
                    )
                except (ValueError, TradeSafetyError):
                    undertow = _unavailable_section(
                        product="undertow",
                        request_hash=request_hash,
                        source_url=UNDERTOW_URL,
                        retrieved_at=retrieved_at,
                        limitation=(
                            "undertow_exact_rung_contract_unavailable_or_invalid"
                        ),
                        raw=undertow_raw,
                    )
            else:
                undertow = _unavailable_section(
                    product="undertow",
                    request_hash=request_hash,
                    source_url=UNDERTOW_URL,
                    retrieved_at=retrieved_at,
                    limitation="undertow_upstream_unreachable",
                )

        if slug is None:
            liquilens = _liquilens_not_applicable(
                request_hash=request_hash, retrieved_at=retrieved_at
            )
        else:
            liquilens_raw = fetched["liquilens"]
            assert liquilens_url is not None
            if isinstance(liquilens_raw, RawUpstreamResponse):
                try:
                    liquilens = _liquilens_section(
                        raw=liquilens_raw,
                        source_url=liquilens_url,
                        request_hash=request_hash,
                        retrieved_at=retrieved_at,
                        request_expires_at=request_expires_at,
                    )
                except (ValueError, TradeSafetyError):
                    liquilens = _unavailable_section(
                        product="liquilens",
                        request_hash=request_hash,
                        source_url=liquilens_url,
                        retrieved_at=retrieved_at,
                        limitation=(
                            "liquilens_upstream_contract_unavailable_or_invalid"
                        ),
                        raw=liquilens_raw,
                    )
            else:
                liquilens = _unavailable_section(
                    product="liquilens",
                    request_hash=request_hash,
                    source_url=liquilens_url,
                    retrieved_at=retrieved_at,
                    limitation="liquilens_upstream_unreachable",
                )

        broker_preview = {
            "schema": BROKER_PREVIEW_REFERENCE_SCHEMA,
            "state": "not_applicable",
            "provider": None,
            "account_id": normalized_request["agent"]["account_id"],
            "request_hash": request_hash,
            "preview_id": None,
            "source_url": None,
            "source_sha256": None,
            "retrieved_at": _utc_text(retrieved_at),
            "expires_at": None,
            "limitations": [
                "sandbox_has_no_broker_credentials_preview_or_order_submission"
            ],
            "facts": {},
        }
        return issue_trade_safety_receipt(
            request=normalized_request,
            evidence={
                "seiche": seiche,
                "undertow": undertow,
                "liquilens": liquilens,
            },
            policy=normalized_policy,
            broker_preview=broker_preview,
            evaluated_at=retrieved_at,
            issuer={
                "name": SERVICE_NAME,
                "version": SERVICE_VERSION,
                "endpoint": ISSUER_ENDPOINT,
            },
            ttl_seconds=RECEIPT_TTL_SECONDS,
            hmac_key=None,
            hmac_key_id=None,
        )


def capabilities() -> dict[str, Any]:
    """Return the static authority and integration contract, without probing."""

    return {
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "source_revision": SERVICE_REVISION,
        "build_created": BUILD_CREATED,
        "issuer_endpoint": ISSUER_ENDPOINT,
        "mode": GATEWAY_MODE,
        "state": "read_only_sandbox",
        "request_schema": TRADE_SAFETY_REQUEST_SCHEMA,
        "policy_schema": TRADE_SAFETY_POLICY_SCHEMA,
        "receipt_integrity": "sha256",
        "live_outcome": "unavailable",
        "authority": {
            "can_execute": False,
            "can_recommend": False,
            "can_allocate_capital": False,
            "has_broker_credentials": False,
            "has_order_submission": False,
        },
        "upstreams": {
            "seiche": {
                "url": SEICHE_URL,
                "tool": "funding_stress_now",
                "state": "context_only",
            },
            "undertow": {
                "url": UNDERTOW_URL,
                "tool": "exit_cost",
                "state": "context_only",
                "asset_aliases": sorted(BTC_ALIASES),
                "currency": "USD",
                "published_rungs_usd": sorted(PUBLISHED_RUNG_USD),
            },
            "liquilens": {
                "url_base": LIQUILENS_BASE_URL,
                "trigger": (
                    "request.order.instrument.identifiers."
                    "liquilens_institution_slug"
                ),
                "state": "conditional_context_only",
            },
        },
        "limits": {
            "request_bytes": MAX_REQUEST_BYTES,
            "upstream_response_bytes": MAX_UPSTREAM_BYTES,
            "response_bytes": MAX_RESPONSE_BYTES,
            "upstream_total_timeout_seconds": UPSTREAM_TOTAL_TIMEOUT_SECONDS,
            "upstream_connect_timeout_seconds": UPSTREAM_CONNECT_TIMEOUT_SECONDS,
        },
        "mcp_protocol_versions": [
            MCP_PROTOCOL_VERSION,
            MCP_LEGACY_PROTOCOL_VERSION,
        ],
        "mcp_tools": ["assess_trade_safety", "trade_safety_capabilities"],
        "execution_tools": [],
    }


MCP_TOOLS = [
    {
        "name": "assess_trade_safety",
        "title": "Assess exact-order trade safety",
        "description": (
            "Read fixed public Seiche, Undertow, and conditional LiquiLens context "
            "and issue a short-lived SHA-256-only sandbox receipt. This tool cannot "
            "recommend, preview with a broker, route, resize, or execute an order."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["request", "policy"],
            "properties": {
                "request": {"type": "object"},
                "policy": {"type": "object"},
            },
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    },
    {
        "name": "trade_safety_capabilities",
        "title": "Trade-safety gateway capabilities",
        "description": (
            "Describe the static sandbox authority, fixed sources, and limits."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
]


def _rpc_result(
    rpc_id: str | int | None,
    result: Mapping[str, Any],
    *,
    modern: bool = False,
) -> dict[str, Any]:
    payload = dict(result)
    if modern:
        payload.setdefault("resultType", "complete")
        payload["_meta"] = {
            "io.modelcontextprotocol/serverInfo": {
                "name": SERVICE_NAME,
                "title": "LiquiLens Trade Safety Gateway",
                "version": SERVICE_VERSION,
                "websiteUrl": ISSUER_ENDPOINT,
            }
        }
    else:
        payload = {
            key: value
            for key, value in payload.items()
            if key not in {"resultType", "cacheScope", "ttlMs", "_meta"}
        }
    return {"jsonrpc": "2.0", "id": rpc_id, "result": payload}


def _rpc_error(
    rpc_id: str | int | None,
    code: int,
    message: str,
    data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = dict(data)
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": error,
    }


def _exact_keys(value: Mapping[str, Any], expected: set[str]) -> bool:
    return set(value) == expected


def create_app(
    *,
    upstream: UpstreamTransport | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> FastAPI:
    """Create an application; tests can inject a byte-exact fake transport."""

    owns_transport = upstream is None
    active_transport = upstream or HttpxUpstreamTransport()
    gateway = TradeSafetyGateway(active_transport, clock=clock)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        if owns_transport:
            await active_transport.aclose()

    application = FastAPI(
        title="LiquiLens Trade Safety Gateway",
        version=SERVICE_VERSION,
        description=(
            "Read-only sandbox assessment over fixed public evidence sources. "
            "There is no broker credential, order preview, recommendation, or "
            "execution surface."
        ),
        lifespan=lifespan,
    )
    application.add_middleware(SafetyEnvelopeMiddleware)
    application.state.gateway = gateway
    application.state.upstream = active_transport

    @application.exception_handler(RequestValidationError)
    async def validation_error(
        _request: Request, _error: RequestValidationError
    ) -> Response:
        # FastAPI's default validation response includes rejected input values.
        # Keep malformed envelopes useful but never reflect accidental secrets.
        return _bounded_response(
            {"detail": "invalid request envelope", "state": "invalid_request"},
            status_code=422,
        )

    @application.get("/healthz", response_model=None)
    async def healthz() -> Response:
        return _bounded_response(
            {
                "status": "ok",
                "service": SERVICE_NAME,
                "version": SERVICE_VERSION,
                "source_revision": SERVICE_REVISION,
                "build_created": BUILD_CREATED,
                "issuer_endpoint": ISSUER_ENDPOINT,
                "mode": GATEWAY_MODE,
                "state": "read_only_sandbox",
                "can_execute": False,
            }
        )

    @application.get("/v1/capabilities", response_model=None)
    async def get_capabilities() -> Response:
        return _bounded_response(capabilities())

    @application.post("/v1/check", response_model=None)
    async def check(envelope: CheckEnvelope) -> Response:
        try:
            receipt = await gateway.assess(envelope.request, envelope.policy)
        except TradeSafetyError as exc:
            return _bounded_response(
                {"detail": str(exc), "state": "invalid_request"}, status_code=422
            )
        return _bounded_response(receipt)

    @application.post("/mcp", response_model=None)
    async def mcp(envelope: MCPEnvelope, _request: Request) -> Response:
        if envelope.jsonrpc != "2.0":
            return _bounded_response(
                _rpc_error(envelope.id, -32600, "invalid JSON-RPC version")
            )
        if envelope.method == "initialize":
            if (
                not isinstance(envelope.params.get("protocolVersion"), str)
                or not isinstance(envelope.params.get("capabilities"), dict)
                or not isinstance(envelope.params.get("clientInfo"), dict)
            ):
                return _bounded_response(
                    _rpc_error(envelope.id, -32602, "invalid initialize params")
                )
            return _bounded_response(
                _rpc_result(
                    envelope.id,
                    {
                        "protocolVersion": MCP_LEGACY_PROTOCOL_VERSION,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {
                            "name": SERVICE_NAME,
                            "title": "LiquiLens Trade Safety Gateway",
                            "version": SERVICE_VERSION,
                        },
                        "instructions": (
                            "Read-only public sandbox. Live results fail closed; "
                            "there is no broker preview, recommendation, order "
                            "route, resize, credential, or execution tool."
                        ),
                    },
                )
            )
        modern = "_meta" in envelope.params
        operation_params = {
            key: value for key, value in envelope.params.items() if key != "_meta"
        }
        if modern:
            meta = envelope.params.get("_meta")
            if not isinstance(meta, dict):
                return _bounded_response(
                    _rpc_error(envelope.id, -32602, "params._meta must be an object")
                )
            requested_version = meta.get("io.modelcontextprotocol/protocolVersion")
            if requested_version != MCP_PROTOCOL_VERSION:
                return _bounded_response(
                    _rpc_error(
                        envelope.id,
                        -32022,
                        "Unsupported protocol version",
                        {
                            "supported": [MCP_PROTOCOL_VERSION],
                            "requested": requested_version or "",
                        },
                    )
                )
        if envelope.method == "server/discover":
            return _bounded_response(
                _rpc_result(
                    envelope.id,
                    {
                        "supportedVersions": [MCP_PROTOCOL_VERSION],
                        "capabilities": {"tools": {"listChanged": False}},
                        "instructions": (
                            "Read-only public sandbox; live and execution authority "
                            "are unavailable."
                        ),
                        "ttlMs": 3_600_000,
                        "cacheScope": "public",
                    },
                    modern=True,
                )
            )
        if envelope.method == "ping":
            return _bounded_response(_rpc_result(envelope.id, {}, modern=modern))
        if envelope.method == "tools/list":
            if operation_params:
                return _bounded_response(
                    _rpc_error(envelope.id, -32602, "tools/list accepts no params")
                )
            return _bounded_response(
                _rpc_result(
                    envelope.id,
                    {
                        "tools": MCP_TOOLS,
                        "ttlMs": 3_600_000,
                        "cacheScope": "public",
                    },
                    modern=modern,
                )
            )
        if envelope.method != "tools/call":
            return _bounded_response(
                _rpc_error(envelope.id, -32601, "method not found")
            )
        if not _exact_keys(operation_params, {"name", "arguments"}):
            return _bounded_response(
                _rpc_error(envelope.id, -32602, "invalid tools/call params")
            )
        name = operation_params["name"]
        arguments = operation_params["arguments"]
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return _bounded_response(
                _rpc_error(envelope.id, -32602, "invalid tools/call params")
            )
        if name == "trade_safety_capabilities":
            if arguments:
                return _bounded_response(
                    _rpc_error(envelope.id, -32602, "capabilities accepts no arguments")
                )
            result = capabilities()
            return _bounded_response(
                _rpc_result(
                    envelope.id,
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": "Read-only sandbox; execution is disabled.",
                            }
                        ],
                        "structuredContent": result,
                        "isError": False,
                    },
                    modern=modern,
                )
            )
        if name != "assess_trade_safety":
            return _bounded_response(_rpc_error(envelope.id, -32602, "unknown tool"))
        try:
            tool_input = CheckEnvelope.model_validate(arguments)
            receipt = await gateway.assess(tool_input.request, tool_input.policy)
        except (TradeSafetyError, ValidationError) as exc:
            message = str(exc) if isinstance(exc, TradeSafetyError) else "invalid input"
            return _bounded_response(
                _rpc_result(
                    envelope.id,
                    {
                        "content": [{"type": "text", "text": message}],
                        "structuredContent": {
                            "error": {
                                "code": "trade_safety_request_rejected",
                                "message": message,
                            }
                        },
                        "isError": True,
                    },
                    modern=modern,
                )
            )
        return _bounded_response(
            _rpc_result(
                envelope.id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"{receipt['receipt_id']}: "
                                f"{receipt['decision']['outcome']} "
                                "(sandbox, no execution)"
                            ),
                        }
                    ],
                    "structuredContent": receipt,
                    "isError": False,
                },
                modern=modern,
            )
        )

    return application


app = create_app()


__all__ = [
    "BTC_ALIASES",
    "LIQUILENS_BASE_URL",
    "MAX_REQUEST_BYTES",
    "MAX_UPSTREAM_BYTES",
    "PUBLISHED_RUNG_USD",
    "SEICHE_URL",
    "UNDERTOW_URL",
    "RawUpstreamResponse",
    "TradeSafetyGateway",
    "UpstreamTransport",
    "app",
    "capabilities",
    "create_app",
]
