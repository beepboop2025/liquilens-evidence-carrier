"""FastAPI surface for read-only, hash-bound trade-safety assessments.

The service deliberately has no broker adapter, credential input, order route, or
user-selectable upstream, custody, or settlement surface.  It translates three fixed
public evidence surfaces into the strict ``liquilens_evidence.trade_safety`` API and
issues SHA-256-only receipts.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import os
import re
import time as monotonic_time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
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
    trade_safety_policy_hash,
    trade_safety_request_hash,
    validate_trade_safety_policy,
    validate_trade_safety_request,
    verify_trade_safety_receipt,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .http_safety import cookie_free_jar
from .policy_guard import PolicyAdmissionError, PolicyAdmissionGuard
from .telemetry import (
    ASSESSMENT_ACCEPTED,
    ASSESSMENT_OUTCOME,
    ASSESSMENT_REJECTED,
    MCP_ACTIVATION,
    X402_OFFERED,
    X402_RELEASE_FAILED,
    X402_RELEASED,
    X402_SETTLE_FAILED,
    X402_SETTLED,
    X402_VERIFY_FAILED,
    TelemetryEmitter,
    telemetry_from_env,
)
from .upstream_contracts import (
    NativeContractError,
    ProjectedContext,
    parse_seiche_context,
    parse_undertow_context,
)
from .x402_access import (
    PAYMENT_RESPONSE_HEADER,
    CompletedAccess,
    PaymentSettlementFailed,
    PreparedAccess,
    X402AccessError,
    X402AccessGate,
    extract_payment_signature,
)
from .x402_runtime import X402Runtime, x402_runtime_from_env

SERVICE_NAME = "liquilens-trade-safety-gateway"
SERVICE_VERSION = "0.2.1"
GATEWAY_MODE = "sandbox"
MCP_PROTOCOL_VERSION = "2026-07-28"
MCP_LEGACY_PROTOCOL_VERSION = "2025-11-25"
MCP_SUPPORTED_PROTOCOL_VERSIONS = (
    MCP_PROTOCOL_VERSION,
    MCP_LEGACY_PROTOCOL_VERSION,
)
MCP_HEADER_VALUE_MAX_BYTES = 512

SERVICE_REVISION = os.environ.get("TRADE_SAFETY_SOURCE_REVISION", "source-checkout")
if (
    SERVICE_REVISION != "source-checkout"
    and re.fullmatch(r"[0-9a-f]{40,64}", SERVICE_REVISION) is None
):
    raise RuntimeError("TRADE_SAFETY_SOURCE_REVISION must be a source SHA")
BUILD_CREATED = os.environ.get("TRADE_SAFETY_BUILD_CREATED", "unknown")

SEICHE_URL = "https://api.seiche.info/api/trade-safety/risk-context"
UNDERTOW_URL = "https://api.seiche.info/undertow/mcp"
LIQUILENS_BASE_URL = "https://api.liquilens.in/api/failure-radar/institution/"
ISSUER_ENDPOINT = os.environ.get(
    "TRADE_SAFETY_ISSUER_ENDPOINT", "https://liquilens.in/trade-safety-gateway"
)
_issuer_url = urlparse(ISSUER_ENDPOINT)
try:
    _issuer_port = _issuer_url.port
except ValueError as exc:
    raise RuntimeError("TRADE_SAFETY_ISSUER_ENDPOINT must be an HTTPS URL") from exc
if (
    _issuer_url.scheme != "https"
    or not _issuer_url.netloc
    or not _issuer_url.hostname
    or _issuer_url.username is not None
    or _issuer_url.password is not None
):
    raise RuntimeError("TRADE_SAFETY_ISSUER_ENDPOINT must be an HTTPS URL")
_MCP_ALLOWED_ORIGIN = (
    _issuer_url.scheme,
    _issuer_url.hostname.lower(),
    _issuer_port or 443,
)

MAX_REQUEST_BYTES = 64 * 1024
MAX_UPSTREAM_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 512 * 1024
UPSTREAM_TOTAL_TIMEOUT_SECONDS = 5.0
UPSTREAM_CONNECT_TIMEOUT_SECONDS = 2.0
EVIDENCE_TTL_SECONDS = 60
RECEIPT_TTL_SECONDS = 60

PUBLISHED_RUNG_USD = frozenset({1_000.0, 10_000.0, 100_000.0, 1_000_000.0})
UNDERTOW_REQUIRED_VENUES = frozenset(
    {"binance", "bitfinex", "coinbase", "gemini", "kraken", "okx"}
)
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
INSTITUTION_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")

SANDBOX_HEADERS = (
    (b"cache-control", b"no-store"),
    (b"x-trade-safety-mode", b"sandbox"),
    (b"x-trade-safety-authority", b"read-only"),
    (b"x-trade-safety-execution", b"disabled"),
    (b"x-trade-safety-revision", SERVICE_REVISION.encode("ascii")),
)
SANDBOX_RESPONSE_HEADERS = {
    name.decode("ascii"): value.decode("ascii") for name, value in SANDBOX_HEADERS
}


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


def _mcp_origin_allowed(raw_headers: list[tuple[bytes, bytes]]) -> bool:
    values = [value for name, value in raw_headers if name.lower() == b"origin"]
    if not values:
        # Non-browser MCP clients normally omit Origin.
        return True
    if len(values) != 1 or len(values[0]) > MCP_HEADER_VALUE_MAX_BYTES:
        return False
    try:
        origin = values[0].decode("ascii", errors="strict")
        parsed = urlparse(origin)
        port = parsed.port
    except (UnicodeDecodeError, ValueError):
        return False
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        return False
    return (
        parsed.scheme,
        parsed.hostname.lower(),
        port or 443,
    ) == _MCP_ALLOWED_ORIGIN


class SafetyEnvelopeMiddleware:
    """Bound request bodies, require strict JSON, and stamp sandbox authority."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        telemetry: TelemetryEmitter,
        x402_enabled: bool,
    ) -> None:
        self.app = app
        self.telemetry = telemetry
        self.x402_enabled = x402_enabled

    def _record_rejection(self, scope: Scope) -> None:
        path = scope.get("path")
        if path == "/v1/check":
            self.telemetry.emit(
                ASSESSMENT_REJECTED,
                transport="rest",
                reason="invalid_request",
            )
        elif path == "/v1/x402/check" and self.x402_enabled:
            self.telemetry.emit(
                ASSESSMENT_REJECTED,
                transport="x402",
                reason="invalid_request",
            )
        elif path == "/mcp":
            self.telemetry.emit(
                MCP_ACTIVATION,
                transport="mcp",
                operation="transport",
                outcome="error",
            )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        with self.telemetry.request_context(scope.get("headers", [])):
            await self._call(scope, receive, send)

    async def _call(self, scope: Scope, receive: Receive, send: Send) -> None:
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

        raw_headers = scope.get("headers", [])
        if scope.get("path") == "/mcp" and not _mcp_origin_allowed(raw_headers):
            self._record_rejection(scope)
            await _send_json(
                sandbox_send,
                status=403,
                payload={
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "Invalid Origin"},
                },
            )
            return
        if scope["method"].upper() != "POST":
            await self.app(scope, receive, sandbox_send)
            return

        content_lengths = [
            value for name, value in raw_headers if name.lower() == b"content-length"
        ]
        if content_lengths:
            try:
                declared = int(content_lengths[-1])
            except ValueError:
                self._record_rejection(scope)
                await _send_json(
                    sandbox_send,
                    status=400,
                    payload={"detail": "invalid Content-Length"},
                )
                return
            if declared < 0 or declared > MAX_REQUEST_BYTES:
                self._record_rejection(scope)
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
            self._record_rejection(scope)
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
                self._record_rejection(scope)
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
            self._record_rejection(scope)
            await _send_json(
                sandbox_send,
                status=400,
                payload={"detail": str(exc)},
            )
            return
        scope.setdefault("state", {})["strict_json"] = decoded
        scope["state"]["raw_body"] = bytes(body)

        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, sandbox_send)


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _bounded_response(
    payload: Mapping[str, Any],
    status_code: int = 200,
    *,
    headers: Mapping[str, str] | None = None,
) -> Response:
    body = _canonical_json_bytes(payload)
    if len(body) > MAX_RESPONSE_BYTES:
        body = (
            b'{"detail":"gateway response exceeded its fixed byte budget",'
            b'"state":"unavailable"}'
        )
        status_code = 502
        headers = None
    response_headers = dict(SANDBOX_RESPONSE_HEADERS)
    response_headers.update(headers or {})
    return Response(
        body,
        status_code=status_code,
        media_type="application/json",
        headers=response_headers,
    )


class CompletedAccessExpired(ValueError):
    """A paid journal entry contains a receipt that is no longer actionable."""


def _completed_access_outcome(
    completed: CompletedAccess,
    *,
    evaluated_at: datetime,
    expected_request_hash: str,
    expected_policy_hash: str,
) -> str:
    """Validate cached protected bytes before release and extract a safe outcome."""

    if (
        completed.status_code != 200
        or completed.content_type != "application/json"
        or len(completed.response_body) > MAX_RESPONSE_BYTES
        or not completed.payment_response_header
    ):
        raise ValueError("completed x402 access metadata is invalid")
    payload = _strict_json_object(completed.response_body, "protected response")
    expiry = _parse_timestamp(payload.get("expires_at"), "receipt.expires_at")
    instant = _utc_instant(evaluated_at, "evaluated_at")
    if instant >= expiry:
        raise CompletedAccessExpired("settled Trade Safety receipt is expired")
    try:
        verified = verify_trade_safety_receipt(
            payload,
            evaluated_at=instant,
            hmac_key=None,
        )
    except TradeSafetyError as exc:
        raise ValueError("settled Trade Safety receipt is invalid") from exc
    verified_receipt = verified.receipt
    if (
        verified_receipt["request_hash"] != expected_request_hash
        or verified_receipt["policy_hash"] != expected_policy_hash
    ):
        raise ValueError("settled Trade Safety receipt has the wrong request binding")
    return verified.outcome.value


def _completed_access_response(
    completed: CompletedAccess,
    *,
    evaluated_at: datetime,
    expected_request_hash: str,
    expected_policy_hash: str,
) -> Response:
    """Release only the exact response bytes bound to successful settlement."""

    _completed_access_outcome(
        completed,
        evaluated_at=evaluated_at,
        expected_request_hash=expected_request_hash,
        expected_policy_hash=expected_policy_hash,
    )
    return Response(
        completed.response_body,
        status_code=completed.status_code,
        media_type=completed.content_type,
        headers={PAYMENT_RESPONSE_HEADER: completed.payment_response_header},
    )


def _settled_access_error_response(
    completed: CompletedAccess,
    *,
    detail: str,
    state: str,
    status_code: int,
) -> Response:
    """Return settlement proof without releasing invalid or expired receipt bytes."""

    return _bounded_response(
        {"detail": detail, "state": state},
        status_code=status_code,
        headers={PAYMENT_RESPONSE_HEADER: completed.payment_response_header},
    )


def _x402_verify_telemetry_reason(error: X402AccessError) -> str:
    if error.code == "payment_signature_required":
        return "payment_missing"
    if error.code in {
        "duplicate_payment_signature",
        "invalid_json_value",
        "invalid_payment_extension",
        "invalid_payment_payload",
        "invalid_payment_payload_shape",
        "invalid_x402_version",
        "malformed_json",
        "malformed_payment_signature",
        "payment_payload_mismatch",
        "payment_signature_too_large",
    }:
        return "payment_malformed"
    if error.code in {
        "accepted_offer_mismatch",
        "binding_extension_mismatch",
        "invalid_network",
        "journal_binding_mismatch",
        "resource_mismatch",
    }:
        return "offer_mismatch"
    if error.code == "facilitator_unavailable":
        return "facilitator_unavailable"
    if error.code == "payment_verification_failed":
        return "payment_rejected"
    if error.code == "payment_processing":
        return "replay_in_progress"
    if error.code in {"journal_full", "journal_terminal_capacity"}:
        return "capacity_exhausted"
    return "internal_error"


def _x402_settle_telemetry_reason(error: X402AccessError) -> str:
    if error.code == "settlement_uncertain":
        return "settlement_uncertain"
    if error.code == "facilitator_unavailable":
        return "facilitator_unavailable"
    if error.code == "payment_verification_failed":
        return "payment_rejected"
    if error.code == "payment_processing":
        return "replay_in_progress"
    return "internal_error"


def _elapsed_ms(started_ns: int) -> float:
    """Return monotonic elapsed milliseconds without exposing wall-clock detail."""

    return max(0, monotonic_time.monotonic_ns() - started_ns) / 1_000_000


def _assessment_rejection_reason(error: Exception) -> str:
    """Reduce detailed validation failures to the closed telemetry vocabulary."""

    if isinstance(error, PolicyAdmissionError):
        return "policy_not_admitted"
    return "invalid_request"


def _assessment_rejection_detail(error: Exception) -> str:
    if isinstance(error, TradeSafetyError):
        return str(error)
    return "numeric input is outside the supported range"


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
    if method == "GET" and url == SEICHE_URL:
        return True
    if method == "POST" and url == UNDERTOW_URL:
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

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        timeout = httpx.Timeout(
            UPSTREAM_TOTAL_TIMEOUT_SECONDS,
            connect=UPSTREAM_CONNECT_TIMEOUT_SECONDS,
        )
        self._client = httpx.AsyncClient(
            cookies=cookie_free_jar(),
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
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
                if response.is_stream_consumed:
                    chunks.append(response.content)
                    total = len(response.content)
                    if total > MAX_UPSTREAM_BYTES:
                        raise UpstreamUnavailable("upstream body is too large")
                else:
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


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    return value


def _mcp_structured(body: bytes, expected_id: str) -> dict[str, Any]:
    response = _strict_json_object(body, "upstream response")
    if set(response) != {"jsonrpc", "id", "result"}:
        raise ValueError("upstream JSON-RPC response has unexpected fields")
    if response.get("jsonrpc") != "2.0" or response.get("id") != expected_id:
        raise ValueError("upstream JSON-RPC identity mismatch")
    result = _mapping(response.get("result"), "upstream result")
    if set(result) != {"content", "structuredContent", "isError"}:
        raise ValueError("upstream tool result has unexpected fields")
    if result.get("isError") is not False:
        raise ValueError("upstream tool result is an error")
    if not isinstance(result.get("content"), list):
        raise ValueError("upstream tool content must be an array")
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


def _projected_section(
    *,
    product: str,
    raw: RawUpstreamResponse,
    request_hash: str,
    retrieved_at: datetime,
    request_expires_at: datetime,
    projected: ProjectedContext,
    source_url: str,
    local_expiry_limitation: str,
) -> dict[str, Any]:
    expiry_candidates = [
        request_expires_at,
        retrieved_at + timedelta(seconds=EVIDENCE_TTL_SECONDS),
    ]
    if projected.native_expires_at is not None:
        expiry_candidates.append(projected.native_expires_at)
    expires_at = min(expiry_candidates)
    if expires_at <= retrieved_at:
        raise NativeContractError(f"{product}_context_expired")
    limitations = list(
        dict.fromkeys(
            [
                *projected.limitations,
                "gateway_receipt_binds_native_context_to_canonical_request_hash",
                local_expiry_limitation,
            ]
        )
    )
    return {
        "product": product,
        "request_hash": request_hash,
        "state": "context_only",
        "evidence_class": "derived",
        "source_url": source_url,
        "source_schema": projected.source_schema,
        "source_sha256": _source_sha(raw.body),
        "as_of": _utc_text(projected.as_of),
        "knowledge_time": _utc_text(projected.knowledge_time),
        "retrieved_at": _utc_text(retrieved_at),
        "expires_at": _utc_text(expires_at),
        "rights_status": projected.rights_status,
        "real_money_eligible": False,
        "executable_quote": False,
        "limitations": limitations,
        "facts": projected.facts,
    }


def _seiche_section(
    *,
    raw: RawUpstreamResponse,
    request_hash: str,
    retrieved_at: datetime,
    request_expires_at: datetime,
    max_age_seconds: int,
) -> dict[str, Any]:
    payload = _strict_json_object(raw.body, "Seiche response")
    projected = parse_seiche_context(
        payload,
        request_hash=request_hash,
        retrieved_at=retrieved_at,
        max_age_seconds=max_age_seconds,
        source_url=SEICHE_URL,
    )
    return _projected_section(
        product="seiche",
        raw=raw,
        request_hash=request_hash,
        retrieved_at=retrieved_at,
        request_expires_at=request_expires_at,
        projected=projected,
        source_url=SEICHE_URL,
        local_expiry_limitation="gateway_expiry_is_local_not_an_upstream_expiry",
    )


def _undertow_section(
    *,
    raw: RawUpstreamResponse,
    request_hash: str,
    expected_request: dict[str, Any],
    retrieved_at: datetime,
    request_expires_at: datetime,
    max_age_seconds: int,
) -> dict[str, Any]:
    payload = _mcp_structured(raw.body, "trade-safety-undertow-v1")
    projected = parse_undertow_context(
        payload,
        expected_request=expected_request,
        request_hash=request_hash,
        retrieved_at=retrieved_at,
        max_age_seconds=max_age_seconds,
        source_url=UNDERTOW_URL,
    )
    return _projected_section(
        product="undertow",
        raw=raw,
        request_hash=request_hash,
        retrieved_at=retrieved_at,
        request_expires_at=request_expires_at,
        projected=projected,
        source_url=UNDERTOW_URL,
        local_expiry_limitation="gateway_expiry_is_bounded_by_native_expiry",
    )


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
    if request["mode"] not in {"observe", "paper"}:
        return False, "undertow_trade_safety_context_is_observe_or_paper_only"
    order = request["order"]
    if order["quantity"] is not None:
        # Request v1 carries both notional and an optional quantity but has no
        # broker-normalized economic-order digest or reference-price contract.
        # Until that exists, accepting both would let a tiny notional conceal
        # an arbitrarily large quantity from the notional policy and Undertow
        # size checks.  Preserve the request in the receipt, but make the
        # mandatory Undertow dependency explicitly unavailable.
        return False, "quantity_requires_broker_normalized_economic_order_binding"
    if order["venue"] is not None:
        return False, "undertow_has_no_canonical_order_venue_mapping"
    if order["side"] != "sell":
        return False, "undertow_trade_safety_context_supports_only_sell_orders"
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


def _undertow_contract_request(
    request: Mapping[str, Any], request_hash: str
) -> dict[str, Any]:
    """Project only the exact fields accepted by Undertow's paper-only tool."""

    return {
        "request_hash": request_hash,
        "mode": request["mode"],
        "instrument": "BTC/USD",
        "side": "sell",
        "venue": None,
        "requested_size_usd": float(request["order"]["notional"]["amount"]),
    }


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
        policy_guard: PolicyAdmissionGuard | None = None,
    ) -> None:
        self._upstream = upstream
        self._clock = clock
        self._policy_guard = policy_guard or PolicyAdmissionGuard.from_env()

    def _admit(
        self,
        request: Mapping[str, Any],
        policy: Mapping[str, Any],
        *,
        evaluated_at: datetime,
    ) -> tuple[dict[str, Any], dict[str, Any], datetime]:
        """Validate the local request/policy boundary without network I/O."""

        try:
            normalized_request = validate_trade_safety_request(request)
            normalized_policy = validate_trade_safety_policy(policy)
            # A schema-valid policy is still caller-authored. Admit it against the
            # server's immutable safety envelope before anything leaves this process.
            self._policy_guard.admit(normalized_policy)
        except OverflowError as exc:
            raise TradeSafetyError(
                "numeric input is outside the supported range"
            ) from exc
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
        if evaluated_at < request_created_at:
            raise TradeSafetyError("request is not yet valid")
        if evaluated_at >= request_expires_at:
            raise TradeSafetyError("request is expired")
        # Validate the only request-derived path component during preflight too.
        _institution_slug(normalized_request)
        return normalized_request, normalized_policy, request_expires_at

    def preflight(
        self,
        request: Mapping[str, Any],
        policy: Mapping[str, Any],
    ) -> tuple[str, str]:
        """Fail invalid paid requests locally before payment verification."""

        evaluated_at = _utc_instant(self._clock(), "clock")
        normalized_request, normalized_policy, _expires_at = self._admit(
            request,
            policy,
            evaluated_at=evaluated_at,
        )
        return (
            trade_safety_request_hash(normalized_request),
            trade_safety_policy_hash(normalized_policy),
        )

    async def assess(
        self,
        request: Mapping[str, Any],
        policy: Mapping[str, Any],
    ) -> dict[str, Any]:
        started_at = _utc_instant(self._clock(), "clock")
        normalized_request, normalized_policy, request_expires_at = self._admit(
            request,
            policy,
            evaluated_at=started_at,
        )

        slug = _institution_slug(normalized_request)
        undertow_allowed, undertow_limitation = _undertow_eligibility(
            normalized_request
        )
        request_hash = trade_safety_request_hash(normalized_request)
        undertow_request = (
            _undertow_contract_request(normalized_request, request_hash)
            if undertow_allowed
            else None
        )

        calls: dict[str, Awaitable[RawUpstreamResponse]] = {
            "seiche": self._upstream.request("GET", SEICHE_URL)
        }
        if undertow_allowed:
            assert undertow_request is not None
            calls["undertow"] = self._upstream.request(
                "POST",
                UNDERTOW_URL,
                json_body=_mcp_call(
                    "trade_safety_exit_context",
                    undertow_request,
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
                    max_age_seconds=normalized_policy["max_evidence_age_seconds"][
                        "seiche"
                    ],
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
                    assert undertow_request is not None
                    undertow = _undertow_section(
                        raw=undertow_raw,
                        request_hash=request_hash,
                        expected_request=undertow_request,
                        retrieved_at=retrieved_at,
                        request_expires_at=request_expires_at,
                        max_age_seconds=normalized_policy["max_evidence_age_seconds"][
                            "undertow"
                        ],
                    )
                except (ValueError, TradeSafetyError):
                    undertow = _unavailable_section(
                        product="undertow",
                        request_hash=request_hash,
                        source_url=UNDERTOW_URL,
                        retrieved_at=retrieved_at,
                        limitation=(
                            "undertow_trade_safety_context_unavailable_or_invalid"
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


def capabilities(
    policy_guard: PolicyAdmissionGuard | None = None,
    x402_gate: X402AccessGate | None = None,
    telemetry: TelemetryEmitter | None = None,
) -> dict[str, Any]:
    """Return the static authority and integration contract, without probing."""

    guard = policy_guard or PolicyAdmissionGuard.from_env()
    policy_config = guard.config
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
        "telemetry": (
            telemetry.status
            if telemetry is not None
            else {"state": "disabled", "delivery_failures": 0}
        ),
        "policy_admission": {
            "mode": "server_owned_safety_envelope",
            "required_products": sorted(policy_config.required_products),
            "required_hold_regimes": sorted(policy_config.hold_regimes),
            "max_evidence_age_seconds": dict(policy_config.max_evidence_age_seconds),
            "max_notional_usd": policy_config.max_notional_usd,
            "max_exit_cost_bps": policy_config.max_exit_cost_bps,
            "max_venue_spread_bps": policy_config.max_venue_spread_bps,
            "exact_policy_allowlist": (policy_config.allowed_policy_sha256 is not None),
        },
        "x402_access": {
            "state": "configured" if x402_gate is not None else "disabled",
            "protocol_version": 2,
            "payment_flow": "authorization",
            "protected_path": ("/v1/x402/check" if x402_gate is not None else None),
            "resource_url": (
                x402_gate.config.resource_url if x402_gate is not None else None
            ),
            "network": (x402_gate.config.network if x402_gate is not None else None),
            "amount_atomic": (
                x402_gate.config.amount if x402_gate is not None else None
            ),
            "asset": x402_gate.config.asset if x402_gate is not None else None,
            "pay_to": x402_gate.config.pay_to if x402_gate is not None else None,
            "discovery_extensions": (
                sorted(
                    {
                        *x402_gate.config.required_extensions,
                        "liquilens",
                    }
                )
                if x402_gate is not None
                else []
            ),
            "free_routes": [
                "/healthz",
                "/v1/capabilities",
                "/v1/check",
                "/mcp",
            ],
            "payment_changes_safety_outcome": False,
            "payment_identity_in_safety_receipt": False,
        },
        "authority": {
            "can_execute": False,
            "can_recommend": False,
            "can_allocate_capital": False,
            "can_route_order": False,
            "can_custody": False,
            "can_settle": False,
            "has_broker_credentials": False,
            "has_order_submission": False,
        },
        "upstreams": {
            "seiche": {
                "url": SEICHE_URL,
                "method": "GET",
                "schema": "seiche.risk-context.v1",
                "state": "context_only",
            },
            "undertow": {
                "url": UNDERTOW_URL,
                "tool": "trade_safety_exit_context",
                "schema": "undertow.trade-safety-exit-context.v1",
                "state": "context_only",
                "modes": ["observe", "paper"],
                "side": "sell",
                "asset_aliases": sorted(BTC_ALIASES),
                "currency": "USD",
                "published_rungs_usd": sorted(PUBLISHED_RUNG_USD),
                "required_venues": sorted(UNDERTOW_REQUIRED_VENUES),
            },
            "liquilens": {
                "url_base": LIQUILENS_BASE_URL,
                "trigger": (
                    "request.order.instrument.identifiers.liquilens_institution_slug"
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
            "Read fixed public native Trade Safety context from Seiche and "
            "Undertow, plus conditional LiquiLens context, and issue a short-lived "
            "SHA-256-only sandbox receipt. This tool cannot recommend, preview "
            "with a broker, route, resize, custody, settle, or execute an order."
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


def _mcp_ascii_header(request: Request, name: bytes) -> str | None:
    values = [
        value
        for header_name, value in request.scope.get("headers", [])
        if header_name.lower() == name.lower()
    ]
    if not values:
        return None
    if len(values) != 1 or not 0 < len(values[0]) <= MCP_HEADER_VALUE_MAX_BYTES:
        raise ValueError("MCP header must occur exactly once within its byte budget")
    try:
        decoded = values[0].decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("MCP header must be ASCII") from exc
    if any(ord(character) < 0x21 or ord(character) > 0x7E for character in decoded):
        raise ValueError("MCP header must contain visible ASCII")
    return decoded


def _decode_mcp_name_header(value: str) -> str:
    prefix = "=?base64?"
    suffix = "?="
    if not (value.startswith(prefix) and value.endswith(suffix)):
        return value
    encoded = value[len(prefix) : -len(suffix)]
    if not encoded or len(encoded) % 4:
        raise ValueError("Mcp-Name base64 sentinel is malformed")
    try:
        raw = base64.b64decode(encoded.encode("ascii"), validate=True)
        if base64.b64encode(raw).decode("ascii") != encoded:
            raise ValueError("Mcp-Name base64 sentinel is not canonical")
        decoded = raw.decode("utf-8", errors="strict")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Mcp-Name base64 sentinel is malformed") from exc
    if not decoded or len(raw) > MCP_HEADER_VALUE_MAX_BYTES:
        raise ValueError("Mcp-Name decoded value is outside its byte budget")
    return decoded


def _mcp_header_mismatch(envelope: MCPEnvelope) -> Response:
    return _bounded_response(
        _rpc_error(
            envelope.id,
            -32020,
            "MCP request headers are missing, malformed, or inconsistent",
        ),
        status_code=400,
    )


def _mcp_transport_error(
    request: Request,
    envelope: MCPEnvelope,
    *,
    modern: bool,
) -> Response | None:
    try:
        protocol = _mcp_ascii_header(request, b"mcp-protocol-version")
        method = _mcp_ascii_header(request, b"mcp-method")
        encoded_name = _mcp_ascii_header(request, b"mcp-name")
        name = None if encoded_name is None else _decode_mcp_name_header(encoded_name)
    except ValueError:
        return _mcp_header_mismatch(envelope)

    if protocol is not None and protocol not in MCP_SUPPORTED_PROTOCOL_VERSIONS:
        requested = (
            protocol if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", protocol) else ""
        )
        return _bounded_response(
            _rpc_error(
                envelope.id,
                -32022,
                "Unsupported protocol version",
                {
                    "supported": list(MCP_SUPPORTED_PROTOCOL_VERSIONS),
                    "requested": requested,
                },
            ),
            status_code=400,
        )

    if modern:
        meta = envelope.params.get("_meta")
        body_version = (
            meta.get("io.modelcontextprotocol/protocolVersion")
            if isinstance(meta, dict)
            else None
        )
        if protocol is None or body_version != protocol or method != envelope.method:
            return _mcp_header_mismatch(envelope)
        if body_version != MCP_PROTOCOL_VERSION:
            return _bounded_response(
                _rpc_error(
                    envelope.id,
                    -32022,
                    "Unsupported protocol version",
                    {
                        "supported": [MCP_PROTOCOL_VERSION],
                        "requested": (
                            body_version
                            if isinstance(body_version, str)
                            and re.fullmatch(
                                r"[0-9]{4}-[0-9]{2}-[0-9]{2}", body_version
                            )
                            else ""
                        ),
                    },
                ),
                status_code=400,
            )
        body_name = envelope.params.get("name")
        if envelope.method == "tools/call" and (
            not isinstance(body_name, str) or name != body_name
        ):
            return _mcp_header_mismatch(envelope)
    else:
        if protocol == MCP_PROTOCOL_VERSION:
            return _mcp_header_mismatch(envelope)
        if envelope.method != "initialize" and protocol is None:
            return _mcp_header_mismatch(envelope)
        if method is not None and method != envelope.method:
            return _mcp_header_mismatch(envelope)
        body_name = envelope.params.get("name")
        if name is not None and name != body_name:
            return _mcp_header_mismatch(envelope)
    return None


def create_app(
    *,
    upstream: UpstreamTransport | None = None,
    clock: Callable[[], datetime] = _utc_now,
    policy_guard: PolicyAdmissionGuard | None = None,
    telemetry: TelemetryEmitter | None = None,
    x402_runtime: X402Runtime | None = None,
) -> FastAPI:
    """Create an application; tests can inject a byte-exact fake transport."""

    owns_transport = upstream is None
    active_transport = upstream or HttpxUpstreamTransport()
    active_policy_guard = policy_guard or PolicyAdmissionGuard.from_env()
    active_telemetry = telemetry or telemetry_from_env(
        SERVICE_VERSION,
        SERVICE_REVISION,
    )
    owns_x402_runtime = x402_runtime is None
    active_x402_runtime = x402_runtime or x402_runtime_from_env()
    active_x402_gate = (
        active_x402_runtime.gate if active_x402_runtime is not None else None
    )
    gateway = TradeSafetyGateway(
        active_transport,
        clock=clock,
        policy_guard=active_policy_guard,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if owns_x402_runtime and active_x402_runtime is not None:
                await active_x402_runtime.aclose()
            if owns_transport:
                await active_transport.aclose()

    application = FastAPI(
        title="LiquiLens Trade Safety Gateway",
        version=SERVICE_VERSION,
        description=(
            "Read-only sandbox assessment over fixed public evidence sources. "
            "There is no broker credential, order preview, recommendation, "
            "routing, custody, trade settlement, or execution surface. Optional "
            "x402 settlement buys access only and cannot affect the assessment."
        ),
        lifespan=lifespan,
    )
    application.add_middleware(
        SafetyEnvelopeMiddleware,
        telemetry=active_telemetry,
        x402_enabled=active_x402_gate is not None,
    )
    application.state.gateway = gateway
    application.state.upstream = active_transport
    application.state.telemetry = active_telemetry
    application.state.x402_runtime = active_x402_runtime

    @application.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, _error: RequestValidationError
    ) -> Response:
        # FastAPI's default validation response includes rejected input values.
        # Keep malformed envelopes useful but never reflect accidental secrets.
        if request.url.path == "/v1/check":
            active_telemetry.emit(
                ASSESSMENT_REJECTED,
                transport="rest",
                reason="invalid_request",
            )
        elif request.url.path == "/v1/x402/check":
            active_telemetry.emit(
                ASSESSMENT_REJECTED,
                transport="x402",
                reason="invalid_request",
            )
        elif request.url.path == "/mcp":
            active_telemetry.emit(
                MCP_ACTIVATION,
                transport="mcp",
                operation="transport",
                outcome="error",
            )
        return _bounded_response(
            {"detail": "invalid request envelope", "state": "invalid_request"},
            status_code=422,
        )

    @application.exception_handler(Exception)
    async def unexpected_error(_request: Request, _error: Exception) -> Response:
        # Starlette's outer error middleware can bypass user middleware. This
        # response therefore carries its own sandbox headers and reflects no
        # backend exception text, journal path, payment data, or request data.
        return _bounded_response(
            {"detail": "gateway unavailable", "state": "unavailable"},
            status_code=500,
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
                "telemetry": active_telemetry.status,
            }
        )

    @application.get("/v1/capabilities", response_model=None)
    async def get_capabilities() -> Response:
        return _bounded_response(
            capabilities(
                active_policy_guard,
                active_x402_gate,
                active_telemetry,
            )
        )

    @application.post("/v1/check", response_model=None)
    async def check(envelope: CheckEnvelope) -> Response:
        started_ns = monotonic_time.monotonic_ns()
        try:
            receipt = await gateway.assess(envelope.request, envelope.policy)
        except TradeSafetyError as exc:
            active_telemetry.emit(
                ASSESSMENT_REJECTED,
                transport="rest",
                duration_ms=_elapsed_ms(started_ns),
                reason=_assessment_rejection_reason(exc),
            )
            return _bounded_response(
                {
                    "detail": _assessment_rejection_detail(exc),
                    "state": "invalid_request",
                },
                status_code=422,
            )
        active_telemetry.emit(
            ASSESSMENT_ACCEPTED,
            transport="rest",
            duration_ms=_elapsed_ms(started_ns),
        )
        active_telemetry.emit(
            ASSESSMENT_OUTCOME,
            transport="rest",
            duration_ms=_elapsed_ms(started_ns),
            outcome=receipt["decision"]["outcome"],
        )
        return _bounded_response(receipt)

    if active_x402_gate is not None:

        def x402_challenge_response(
            body: bytes,
            *,
            error: str = "PAYMENT-SIGNATURE header is required",
        ) -> Response:
            challenge = active_x402_gate.challenge(
                body,
                resource=active_x402_gate.config.resource_url,
                error=error,
            )
            return _bounded_response(
                challenge.payment_required,
                status_code=402,
                headers=challenge.response_headers,
            )

        @application.post("/v1/x402/check", response_model=None)
        async def x402_check(
            envelope: CheckEnvelope,
            request: Request,
        ) -> Response:
            started_ns = monotonic_time.monotonic_ns()
            raw_body = request.scope.get("state", {}).get("raw_body")
            if not isinstance(raw_body, bytes):
                active_telemetry.emit(
                    ASSESSMENT_REJECTED,
                    transport="x402",
                    duration_ms=_elapsed_ms(started_ns),
                    reason="internal_error",
                )
                return _bounded_response(
                    {
                        "detail": "paid request snapshot unavailable",
                        "state": "unavailable",
                    },
                    status_code=500,
                )

            # Never solicit or verify payment for an envelope the local safety
            # boundary already knows it cannot assess.
            try:
                expected_request_hash, expected_policy_hash = gateway.preflight(
                    envelope.request,
                    envelope.policy,
                )
            except TradeSafetyError as exc:
                active_telemetry.emit(
                    ASSESSMENT_REJECTED,
                    transport="x402",
                    duration_ms=_elapsed_ms(started_ns),
                    reason=_assessment_rejection_reason(exc),
                )
                return _bounded_response(
                    {
                        "detail": _assessment_rejection_detail(exc),
                        "state": "invalid_request",
                    },
                    status_code=422,
                )

            try:
                payment_signature = extract_payment_signature(
                    request.scope.get("headers", []),
                    max_header_bytes=(active_x402_gate.config.max_payment_header_bytes),
                )
            except X402AccessError as exc:
                reason = _x402_verify_telemetry_reason(exc)
                if exc.http_status == 402:
                    response = x402_challenge_response(
                        raw_body,
                        error="Payment authorization was not accepted",
                    )
                    active_telemetry.emit(
                        X402_OFFERED,
                        transport="x402",
                        duration_ms=_elapsed_ms(started_ns),
                    )
                else:
                    response = _bounded_response(
                        {"detail": "x402 payment is invalid", "state": reason},
                        status_code=exc.http_status,
                    )
                active_telemetry.emit(
                    X402_VERIFY_FAILED,
                    transport="x402",
                    duration_ms=_elapsed_ms(started_ns),
                    reason=reason,
                )
                return response

            if payment_signature is None:
                response = x402_challenge_response(raw_body)
                active_telemetry.emit(
                    X402_OFFERED,
                    transport="x402",
                    duration_ms=_elapsed_ms(started_ns),
                )
                active_telemetry.emit(
                    X402_VERIFY_FAILED,
                    transport="x402",
                    duration_ms=_elapsed_ms(started_ns),
                    reason="payment_missing",
                )
                return response

            try:
                access = await active_x402_gate.authorize(
                    raw_body,
                    resource=active_x402_gate.config.resource_url,
                    payment_signature=payment_signature,
                )
            except PaymentSettlementFailed as exc:
                # ``authorize`` can surface only a journaled terminal result.
                # The original settle attempt already emitted the failure; a
                # replay is not another attempt and must not inflate the funnel.
                return _bounded_response(
                    {},
                    status_code=exc.http_status,
                    headers={
                        PAYMENT_RESPONSE_HEADER: exc.payment_response_header,
                    },
                )
            except X402AccessError as exc:
                if exc.code == "settlement_uncertain":
                    # This is a read of sticky reconciliation state, not a new
                    # settle call. The initial attempt owns the failure event.
                    return _bounded_response(
                        {
                            "detail": "x402 settlement requires reconciliation",
                            "state": "settlement_uncertain",
                        },
                        status_code=exc.http_status,
                    )
                if exc.code == "settled_response_retired":
                    active_telemetry.emit(
                        X402_RELEASE_FAILED,
                        transport="x402",
                        duration_ms=_elapsed_ms(started_ns),
                        delivery="replay",
                        reason="response_retired",
                    )
                    return _bounded_response(
                        {
                            "detail": "settled response retention ended; the "
                            "authorization cannot be charged again",
                            "state": "settled_response_retired",
                        },
                        status_code=exc.http_status,
                    )
                if exc.code == "payment_authorization_retired":
                    active_telemetry.emit(
                        X402_VERIFY_FAILED,
                        transport="x402",
                        duration_ms=_elapsed_ms(started_ns),
                        reason="authorization_retired",
                    )
                    return _bounded_response(
                        {
                            "detail": "payment authorization was retired and "
                            "cannot be reused",
                            "state": "payment_authorization_retired",
                        },
                        status_code=exc.http_status,
                    )
                reason = _x402_verify_telemetry_reason(exc)
                if exc.http_status == 402:
                    response = x402_challenge_response(
                        raw_body,
                        error="Payment authorization was not accepted",
                    )
                    active_telemetry.emit(
                        X402_OFFERED,
                        transport="x402",
                        duration_ms=_elapsed_ms(started_ns),
                    )
                else:
                    response = _bounded_response(
                        {"detail": "x402 access unavailable", "state": reason},
                        status_code=exc.http_status,
                    )
                active_telemetry.emit(
                    X402_VERIFY_FAILED,
                    transport="x402",
                    duration_ms=_elapsed_ms(started_ns),
                    reason=reason,
                )
                return response

            if isinstance(access, CompletedAccess):
                evaluated_at = _utc_instant(clock(), "clock")
                try:
                    outcome = _completed_access_outcome(
                        access,
                        evaluated_at=evaluated_at,
                        expected_request_hash=expected_request_hash,
                        expected_policy_hash=expected_policy_hash,
                    )
                    response = _completed_access_response(
                        access,
                        evaluated_at=evaluated_at,
                        expected_request_hash=expected_request_hash,
                        expected_policy_hash=expected_policy_hash,
                    )
                except CompletedAccessExpired:
                    active_telemetry.emit(
                        X402_RELEASE_FAILED,
                        transport="x402",
                        duration_ms=_elapsed_ms(started_ns),
                        delivery="replay",
                        reason="response_expired",
                    )
                    return _settled_access_error_response(
                        access,
                        detail=(
                            "settled safety receipt is expired; submit a fresh "
                            "request and payment"
                        ),
                        state="settled_response_expired",
                        status_code=409,
                    )
                except ValueError:
                    active_telemetry.emit(
                        X402_RELEASE_FAILED,
                        transport="x402",
                        duration_ms=_elapsed_ms(started_ns),
                        delivery="replay",
                        reason="response_invalid",
                    )
                    return _settled_access_error_response(
                        access,
                        detail="settled safety response is invalid",
                        state="settled_response_invalid",
                        status_code=503,
                    )
                active_telemetry.emit(
                    X402_RELEASED,
                    transport="x402",
                    duration_ms=_elapsed_ms(started_ns),
                    delivery="replay",
                    outcome=outcome,
                )
                return response

            if not isinstance(access, PreparedAccess):
                active_telemetry.emit(
                    X402_VERIFY_FAILED,
                    transport="x402",
                    duration_ms=_elapsed_ms(started_ns),
                    reason="internal_error",
                )
                return _bounded_response(
                    {"detail": "x402 access unavailable", "state": "internal_error"},
                    status_code=500,
                )

            settlement_invoked = False
            try:
                try:
                    receipt = await gateway.assess(envelope.request, envelope.policy)
                except TradeSafetyError as exc:
                    active_telemetry.emit(
                        ASSESSMENT_REJECTED,
                        transport="x402",
                        duration_ms=_elapsed_ms(started_ns),
                        reason=_assessment_rejection_reason(exc),
                    )
                    if not active_x402_gate.abort(access):
                        return _bounded_response(
                            {
                                "detail": "payment claim reconciliation required",
                                "state": "internal_error",
                            },
                            status_code=503,
                        )
                    return _bounded_response(
                        {
                            "detail": _assessment_rejection_detail(exc),
                            "state": "invalid_request",
                        },
                        status_code=422,
                    )
                except Exception:
                    active_telemetry.emit(
                        ASSESSMENT_REJECTED,
                        transport="x402",
                        duration_ms=_elapsed_ms(started_ns),
                        reason="internal_error",
                    )
                    if not active_x402_gate.abort(access):
                        return _bounded_response(
                            {
                                "detail": "payment claim reconciliation required",
                                "state": "internal_error",
                            },
                            status_code=503,
                        )
                    return _bounded_response(
                        {"detail": "assessment unavailable", "state": "unavailable"},
                        status_code=500,
                    )

                response_body = _canonical_json_bytes(receipt)
                receipt_outcome = receipt["decision"]["outcome"]
                active_telemetry.emit(
                    ASSESSMENT_ACCEPTED,
                    transport="x402",
                    duration_ms=_elapsed_ms(started_ns),
                )
                active_telemetry.emit(
                    ASSESSMENT_OUTCOME,
                    transport="x402",
                    duration_ms=_elapsed_ms(started_ns),
                    outcome=receipt_outcome,
                )
                if len(response_body) > MAX_RESPONSE_BYTES:
                    if not active_x402_gate.abort(access):
                        return _bounded_response(
                            {
                                "detail": "payment claim reconciliation required",
                                "state": "internal_error",
                            },
                            status_code=503,
                        )
                    return _bounded_response(
                        {
                            "detail": "assessment response exceeded its byte budget",
                            "state": "unavailable",
                        },
                        status_code=502,
                    )

                if (
                    len(response_body)
                    > active_x402_gate.config.max_cached_response_bytes
                ):
                    active_telemetry.emit(
                        X402_RELEASE_FAILED,
                        transport="x402",
                        duration_ms=_elapsed_ms(started_ns),
                        delivery="initial",
                        reason="response_too_large",
                    )
                    if not active_x402_gate.abort(access):
                        return _bounded_response(
                            {
                                "detail": "payment claim reconciliation required",
                                "state": "internal_error",
                            },
                            status_code=503,
                        )
                    return _bounded_response(
                        {
                            "detail": "assessment response exceeds the paid "
                            "cache byte budget",
                            "state": "response_too_large",
                        },
                        status_code=502,
                    )

                settlement_invoked = True
                try:
                    completed = await active_x402_gate.settle(
                        access,
                        response_body,
                        status_code=200,
                        content_type="application/json",
                    )
                except PaymentSettlementFailed as exc:
                    active_telemetry.emit(
                        X402_SETTLE_FAILED,
                        transport="x402",
                        duration_ms=_elapsed_ms(started_ns),
                        reason="payment_rejected",
                    )
                    return _bounded_response(
                        {},
                        status_code=exc.http_status,
                        headers={
                            PAYMENT_RESPONSE_HEADER: exc.payment_response_header,
                        },
                    )
                except X402AccessError as exc:
                    reason = _x402_settle_telemetry_reason(exc)
                    active_telemetry.emit(
                        X402_SETTLE_FAILED,
                        transport="x402",
                        duration_ms=_elapsed_ms(started_ns),
                        reason=reason,
                    )
                    return _bounded_response(
                        {
                            "detail": "x402 settlement unavailable",
                            "state": reason,
                        },
                        status_code=exc.http_status,
                    )
                except Exception:
                    active_telemetry.emit(
                        X402_SETTLE_FAILED,
                        transport="x402",
                        duration_ms=_elapsed_ms(started_ns),
                        reason="settlement_uncertain",
                    )
                    return _bounded_response(
                        {
                            "detail": "x402 settlement unavailable",
                            "state": "settlement_uncertain",
                        },
                        status_code=503,
                    )

                active_telemetry.emit(
                    X402_SETTLED,
                    transport="x402",
                    duration_ms=_elapsed_ms(started_ns),
                )
                try:
                    evaluated_at = _utc_instant(clock(), "clock")
                    outcome = _completed_access_outcome(
                        completed,
                        evaluated_at=evaluated_at,
                        expected_request_hash=expected_request_hash,
                        expected_policy_hash=expected_policy_hash,
                    )
                except CompletedAccessExpired:
                    active_telemetry.emit(
                        X402_RELEASE_FAILED,
                        transport="x402",
                        duration_ms=_elapsed_ms(started_ns),
                        delivery="initial",
                        reason="response_expired",
                    )
                    return _settled_access_error_response(
                        completed,
                        detail=(
                            "settled safety receipt expired before delivery; "
                            "submit a fresh request and payment"
                        ),
                        state="settled_response_expired",
                        status_code=409,
                    )
                except ValueError:
                    active_telemetry.emit(
                        X402_RELEASE_FAILED,
                        transport="x402",
                        duration_ms=_elapsed_ms(started_ns),
                        delivery="initial",
                        reason="response_invalid",
                    )
                    return _settled_access_error_response(
                        completed,
                        detail="settled safety response failed validation",
                        state="settled_response_invalid",
                        status_code=503,
                    )
                if outcome != receipt_outcome:
                    active_telemetry.emit(
                        X402_RELEASE_FAILED,
                        transport="x402",
                        duration_ms=_elapsed_ms(started_ns),
                        delivery="initial",
                        reason="response_invalid",
                    )
                    return _settled_access_error_response(
                        completed,
                        detail="settled safety response failed validation",
                        state="settled_response_invalid",
                        status_code=503,
                    )
                active_telemetry.emit(
                    X402_RELEASED,
                    transport="x402",
                    duration_ms=_elapsed_ms(started_ns),
                    delivery="initial",
                    outcome=outcome,
                )
                return _completed_access_response(
                    completed,
                    evaluated_at=evaluated_at,
                    expected_request_hash=expected_request_hash,
                    expected_policy_hash=expected_policy_hash,
                )
            except BaseException:
                # Cancellation before settlement must not strand a reusable
                # authorization in processing. Once settle is invoked the gate's
                # sticky state owns reconciliation and must never be auto-aborted.
                if not settlement_invoked:
                    active_x402_gate.abort(access)
                raise

    @application.post("/mcp", response_model=None)
    async def mcp(envelope: MCPEnvelope, _request: Request) -> Response:
        started_ns = monotonic_time.monotonic_ns()
        is_notification = "id" not in envelope.model_fields_set
        modern = (
            envelope.method not in {"initialize", "notifications/initialized"}
            and "_meta" in envelope.params
        )
        transport_error = _mcp_transport_error(
            _request,
            envelope,
            modern=modern,
        )
        if transport_error is not None:
            active_telemetry.emit(
                MCP_ACTIVATION,
                transport="mcp",
                duration_ms=_elapsed_ms(started_ns),
                operation="transport",
                outcome="error",
            )
            return transport_error
        if envelope.jsonrpc != "2.0":
            active_telemetry.emit(
                MCP_ACTIVATION,
                transport="mcp",
                duration_ms=_elapsed_ms(started_ns),
                operation="transport",
                outcome="error",
            )
            return _bounded_response(
                _rpc_error(envelope.id, -32600, "invalid JSON-RPC version"),
                status_code=400,
            )
        if not is_notification and envelope.id is None:
            active_telemetry.emit(
                MCP_ACTIVATION,
                transport="mcp",
                duration_ms=_elapsed_ms(started_ns),
                operation="transport",
                outcome="error",
            )
            return _bounded_response(
                _rpc_error(None, -32600, "JSON-RPC request id must be non-null"),
                status_code=400,
            )
        if is_notification:
            if not envelope.method.startswith("notifications/"):
                active_telemetry.emit(
                    MCP_ACTIVATION,
                    transport="mcp",
                    duration_ms=_elapsed_ms(started_ns),
                    operation="transport",
                    outcome="error",
                )
                return _bounded_response(
                    _rpc_error(None, -32600, "JSON-RPC requests require an id"),
                    status_code=400,
                )
            if (
                envelope.method == "notifications/initialized"
                and envelope.params
                and (
                    set(envelope.params) != {"_meta"}
                    or not isinstance(envelope.params.get("_meta"), dict)
                )
            ):
                active_telemetry.emit(
                    MCP_ACTIVATION,
                    transport="mcp",
                    duration_ms=_elapsed_ms(started_ns),
                    operation="transport",
                    outcome="error",
                )
                return _bounded_response(
                    _rpc_error(None, -32602, "invalid initialized notification"),
                    status_code=400,
                )
            # Notifications never receive a JSON-RPC response. Unknown legacy
            # notifications are deliberately accepted as bounded no-ops.
            return Response(status_code=202)
        if envelope.method == "initialize":
            if (
                not isinstance(envelope.params.get("protocolVersion"), str)
                or not isinstance(envelope.params.get("capabilities"), dict)
                or not isinstance(envelope.params.get("clientInfo"), dict)
            ):
                active_telemetry.emit(
                    MCP_ACTIVATION,
                    transport="mcp",
                    duration_ms=_elapsed_ms(started_ns),
                    operation="initialize",
                    outcome="error",
                )
                return _bounded_response(
                    _rpc_error(envelope.id, -32602, "invalid initialize params")
                )
            active_telemetry.emit(
                MCP_ACTIVATION,
                transport="mcp",
                duration_ms=_elapsed_ms(started_ns),
                operation="initialize",
                outcome="success",
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
                            "route, resize, credential, custody, trade settlement, "
                            "or execution tool. Optional x402 buys access only."
                        ),
                    },
                )
            )
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
            client_capabilities = meta.get("io.modelcontextprotocol/clientCapabilities")
            client_info = meta.get("io.modelcontextprotocol/clientInfo")
            if not isinstance(client_capabilities, dict) or (
                client_info is not None
                and (
                    not isinstance(client_info, dict)
                    or not isinstance(client_info.get("name"), str)
                    or not isinstance(client_info.get("version"), str)
                )
            ):
                return _bounded_response(
                    _rpc_error(envelope.id, -32602, "invalid request metadata")
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
                active_telemetry.emit(
                    MCP_ACTIVATION,
                    transport="mcp",
                    duration_ms=_elapsed_ms(started_ns),
                    operation="tools_list",
                    outcome="error",
                )
                return _bounded_response(
                    _rpc_error(envelope.id, -32602, "tools/list accepts no params")
                )
            active_telemetry.emit(
                MCP_ACTIVATION,
                transport="mcp",
                duration_ms=_elapsed_ms(started_ns),
                operation="tools_list",
                outcome="success",
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
                _rpc_error(envelope.id, -32601, "method not found"),
                status_code=404 if modern else 200,
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
                active_telemetry.emit(
                    MCP_ACTIVATION,
                    transport="mcp",
                    duration_ms=_elapsed_ms(started_ns),
                    operation="trade_safety_capabilities",
                    outcome="error",
                )
                return _bounded_response(
                    _rpc_error(envelope.id, -32602, "capabilities accepts no arguments")
                )
            result = capabilities(
                active_policy_guard,
                active_x402_gate,
                active_telemetry,
            )
            active_telemetry.emit(
                MCP_ACTIVATION,
                transport="mcp",
                duration_ms=_elapsed_ms(started_ns),
                operation="trade_safety_capabilities",
                outcome="success",
            )
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
            message = (
                "invalid input"
                if isinstance(exc, ValidationError)
                else _assessment_rejection_detail(exc)
            )
            active_telemetry.emit(
                ASSESSMENT_REJECTED,
                transport="mcp",
                duration_ms=_elapsed_ms(started_ns),
                reason=_assessment_rejection_reason(exc),
            )
            active_telemetry.emit(
                MCP_ACTIVATION,
                transport="mcp",
                duration_ms=_elapsed_ms(started_ns),
                operation="assess_trade_safety",
                outcome="error",
            )
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
        active_telemetry.emit(
            ASSESSMENT_ACCEPTED,
            transport="mcp",
            duration_ms=_elapsed_ms(started_ns),
        )
        active_telemetry.emit(
            ASSESSMENT_OUTCOME,
            transport="mcp",
            duration_ms=_elapsed_ms(started_ns),
            outcome=receipt["decision"]["outcome"],
        )
        active_telemetry.emit(
            MCP_ACTIVATION,
            transport="mcp",
            duration_ms=_elapsed_ms(started_ns),
            operation="assess_trade_safety",
            outcome="success",
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


__all__ = [
    "BTC_ALIASES",
    "LIQUILENS_BASE_URL",
    "MAX_REQUEST_BYTES",
    "MAX_UPSTREAM_BYTES",
    "PUBLISHED_RUNG_USD",
    "SEICHE_URL",
    "UNDERTOW_REQUIRED_VENUES",
    "UNDERTOW_URL",
    "RawUpstreamResponse",
    "TradeSafetyGateway",
    "UpstreamTransport",
    "capabilities",
    "create_app",
]
