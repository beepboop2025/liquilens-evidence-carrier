"""Operator-local receipt issuance over the unchanged public evidence contracts.

No broker client is imported here. A strategy cannot supply its own receipt or
evidence: native context is fetched and parsed by TradeSafetyGateway before the
operator issues a separately authenticated paper receipt.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from liquilens_evidence import TradeSafetyExecutionBinding
from liquilens_evidence.trade_safety import (
    TradeSafetyError,
    issue_trade_safety_receipt,
    trade_safety_policy_hash,
    validate_trade_safety_policy,
    validate_trade_safety_request,
    verify_trade_safety_receipt,
)
from trade_safety_gateway.app import (
    ISSUER_ENDPOINT,
    LIQUILENS_BASE_URL,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    MAX_UPSTREAM_BYTES,
    RECEIPT_TTL_SECONDS,
    SERVICE_NAME,
    SERVICE_VERSION,
    UPSTREAM_TOTAL_TIMEOUT_SECONDS,
    RawUpstreamResponse,
    TradeSafetyGateway,
    UpstreamTransport,
    UpstreamUnavailable,
    _is_allowed_upstream,
    _liquilens_section,
    _strict_json_object,
    _unavailable_section,
)
from trade_safety_gateway.policy_guard import (
    PolicyAdmissionConfig,
    PolicyAdmissionGuard,
)

MAX_ASSESSMENT_SECONDS = 20.0


class OperatorEvidenceError(TradeSafetyError):
    """The operator assessment could not preserve its configured boundary."""


def _json(value: Any, *, limit: int) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(encoded.encode("utf-8")) > limit:
        raise OperatorEvidenceError("operator evidence exceeds its byte budget")
    return encoded


def _instant(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise OperatorEvidenceError("operator clock must be timezone aware")
    return value.astimezone(UTC)


def _timestamp(value: str) -> datetime:
    return _instant(datetime.fromisoformat(value.replace("Z", "+00:00")))


@dataclass(frozen=True, slots=True)
class LiquiLensStrategyContext:
    """Explicit institution research context, never an identifier for BTC."""

    institution_slug: str
    required: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.institution_slug, str)
            or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", self.institution_slug) is None
            or len(self.institution_slug) > 128
        ):
            raise OperatorEvidenceError("invalid LiquiLens strategy-context slug")
        if not isinstance(self.required, bool):
            raise OperatorEvidenceError("strategy-context required must be boolean")


@dataclass(frozen=True, slots=True)
class LiquiLensContextObservation:
    state: str
    required: bool
    institution_slug: str
    source_sha256: str | None
    retrieved_at: str
    period_end: str | None
    limitation: str | None
    context_sha256: str


@dataclass(frozen=True, slots=True)
class OperatorAssessment:
    """Immutable encoded receipts; properties return independent copies."""

    receipt_json: str
    source_receipt_json: str
    strategy_context: LiquiLensContextObservation | None

    @property
    def receipt(self) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(self.receipt_json)
        return result

    @property
    def source_receipt(self) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(self.source_receipt_json)
        return result

    @property
    def outcome(self) -> str:
        return str(self.receipt["decision"]["outcome"])

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(self.receipt["decision"]["reason_codes"])


class _BoundedTransport:
    """Preserve fixed destinations and bounds for injected transports too."""

    def __init__(self, transport: UpstreamTransport) -> None:
        self._transport = transport
        self._calls = 0

    async def request(
        self,
        method: str,
        url: str,
        *,
        json_body: Mapping[str, Any] | None = None,
    ) -> RawUpstreamResponse:
        if not _is_allowed_upstream(method, url) or self._calls >= 3:
            raise UpstreamUnavailable("operator upstream destination/call limit")
        self._calls += 1
        if json_body is not None:
            json_body = json.loads(_json(dict(json_body), limit=MAX_REQUEST_BYTES))
        try:
            async with asyncio.timeout(UPSTREAM_TOTAL_TIMEOUT_SECONDS):
                raw = await self._transport.request(method, url, json_body=json_body)
        except TimeoutError as error:
            raise UpstreamUnavailable("operator upstream timed out") from error
        if (
            not isinstance(raw, RawUpstreamResponse)
            or not isinstance(raw.body, bytes)
            or len(raw.body) > MAX_UPSTREAM_BYTES
        ):
            raise UpstreamUnavailable("operator upstream response exceeds byte budget")
        return raw

    async def aclose(self) -> None:
        # The service owns the shared transport; an assessment cannot close it.
        return None


class OperatorEvidenceService:
    """Fetch validated native context and issue an exact, private paper receipt."""

    def __init__(
        self,
        transport: UpstreamTransport,
        *,
        policy: Mapping[str, Any],
        binding: TradeSafetyExecutionBinding,
        hmac_key: bytes,
        clock: Callable[[], datetime],
        policy_guard: PolicyAdmissionGuard | None = None,
        liquilens_context: LiquiLensStrategyContext | None = None,
        total_timeout_seconds: float = MAX_ASSESSMENT_SECONDS,
    ) -> None:
        normalized = validate_trade_safety_policy(
            json.loads(_json(dict(policy), limit=MAX_REQUEST_BYTES))
        )
        digest = trade_safety_policy_hash(normalized)
        if (
            digest != binding.policy_hash
            or normalized["policy_id"] != binding.policy_id
            or normalized["version"] != binding.policy_version
        ):
            raise OperatorEvidenceError(
                "operator policy does not match execution binding"
            )
        if not isinstance(hmac_key, bytes) or not hmac_key or not binding.hmac_key_id:
            raise OperatorEvidenceError(
                "operator paper issuer requires HMAC key and id"
            )
        if (
            isinstance(total_timeout_seconds, bool)
            or not isinstance(total_timeout_seconds, (int, float))
            or not math.isfinite(total_timeout_seconds)
            or not 0 < total_timeout_seconds <= MAX_ASSESSMENT_SECONDS
        ):
            raise OperatorEvidenceError("invalid operator assessment deadline")
        guard = policy_guard or PolicyAdmissionGuard(
            PolicyAdmissionConfig(allowed_policy_sha256={digest})
        )
        guard.admit(normalized)
        if liquilens_context is not None and liquilens_context.required != (
            "liquilens" in normalized["required_products"]
        ):
            raise OperatorEvidenceError(
                "LiquiLens context requirement differs from policy"
            )
        self._transport = transport
        self._policy_json = _json(normalized, limit=MAX_REQUEST_BYTES)
        self._binding = binding
        self._hmac_key = hmac_key
        self._clock = clock
        self._policy_guard = guard
        self._context = liquilens_context
        self._timeout = total_timeout_seconds

    def _request(self, value: Mapping[str, Any]) -> dict[str, Any]:
        request = validate_trade_safety_request(
            json.loads(_json(dict(value), limit=MAX_REQUEST_BYTES))
        )
        if (
            request["mode"] != "paper"
            or "orders:live" in request["agent"]["authorization_scope"]
        ):
            raise OperatorEvidenceError("operator evidence service is paper-only")
        for name in (
            "account_id",
            "tenant_id",
            "operator_id",
            "agent_id",
            "runtime",
            "strategy_id",
        ):
            if request["agent"][name] != getattr(self._binding, name):
                raise OperatorEvidenceError(f"request operator binding differs: {name}")
        if self._context is not None and request["order"]["instrument"]["identifiers"]:
            raise OperatorEvidenceError(
                "strategy context must be separate from identifiers"
            )
        return request

    async def _context_section(
        self,
        transport: _BoundedTransport,
        request_hash: str,
        request_expires_at: datetime,
    ) -> tuple[dict[str, Any], LiquiLensContextObservation]:
        context = self._context
        assert context is not None
        url = LIQUILENS_BASE_URL + quote(context.institution_slug, safe="")
        raw: RawUpstreamResponse | None = None
        limitation: str | None = None
        try:
            raw = await transport.request("GET", url)
            retrieved_at = _instant(self._clock())
            native = _strict_json_object(raw.body, "LiquiLens strategy context")
            if native.get("slug") != context.institution_slug:
                limitation = "liquilens_strategy_context_identity_mismatch"
                raise OperatorEvidenceError(limitation)
            section = _liquilens_section(
                raw=raw,
                source_url=url,
                request_hash=request_hash,
                retrieved_at=retrieved_at,
                request_expires_at=request_expires_at,
            )
        except (ValueError, TradeSafetyError, UpstreamUnavailable, OSError, KeyError):
            retrieved_at = _instant(self._clock())
            limitation = (
                limitation or "liquilens_strategy_context_unavailable_or_invalid"
            )
            section = _unavailable_section(
                product="liquilens",
                request_hash=request_hash,
                source_url=url,
                retrieved_at=retrieved_at,
                limitation=limitation,
                raw=raw,
            )
        metadata = {
            "schema": "liquilens.operator-strategy-context.v1",
            "role": "institution_research_context",
            "institution_slug": context.institution_slug,
            "required": context.required,
            "strategy_id": self._binding.strategy_id,
            "state": section["state"],
            "source_sha256": section["source_sha256"],
            "retrieved_at": section["retrieved_at"],
            "period_end": section["facts"].get("period_end"),
            "limitation": limitation,
        }
        context_hash = hashlib.sha256(
            _json(metadata, limit=MAX_REQUEST_BYTES).encode("utf-8")
        ).hexdigest()
        section["facts"]["operator_strategy_context"] = {
            **metadata,
            "context_sha256": context_hash,
        }
        observation = LiquiLensContextObservation(
            state=section["state"],
            required=context.required,
            institution_slug=context.institution_slug,
            source_sha256=section["source_sha256"],
            retrieved_at=section["retrieved_at"],
            period_end=metadata["period_end"],
            limitation=limitation,
            context_sha256=context_hash,
        )
        return section, observation

    async def assess(self, request: Mapping[str, Any]) -> OperatorAssessment:
        """No caller-supplied assessment or evidence is accepted for signing."""
        normalized = self._request(request)
        policy = json.loads(self._policy_json)
        transport = _BoundedTransport(self._transport)
        gateway = TradeSafetyGateway(
            transport, clock=self._clock, policy_guard=self._policy_guard
        )
        request_hash, _ = gateway.preflight(normalized, policy)
        try:
            async with asyncio.timeout(self._timeout):
                if self._context is not None:
                    # One explicit research source replaces only the unrequested
                    # LiquiLens placeholder, never an unavailable native result.
                    source_task = asyncio.create_task(
                        gateway.assess(normalized, policy)
                    )
                    context_task = asyncio.create_task(
                        self._context_section(
                            transport,
                            request_hash,
                            _timestamp(normalized["expires_at"]),
                        )
                    )
                    try:
                        source, extra = await asyncio.gather(source_task, context_task)
                    except BaseException:
                        # A failed/cancelled assessment must not leave sibling
                        # upstream work running after the caller receives it.
                        source_task.cancel()
                        context_task.cancel()
                        await asyncio.gather(
                            source_task, context_task, return_exceptions=True
                        )
                        raise
                else:
                    source = await gateway.assess(normalized, policy)
                    extra = None
        except TimeoutError as error:
            raise OperatorEvidenceError(
                "operator assessment deadline exceeded"
            ) from error
        source_json = _json(source, limit=MAX_RESPONSE_BYTES)
        now = _instant(self._clock())
        verified = verify_trade_safety_receipt(
            json.loads(source_json), evaluated_at=now, hmac_key=None
        )
        source = verified.receipt
        if (
            source["request"] != normalized
            or source["policy"] != policy
            or source["issuer"]
            != {
                "name": SERVICE_NAME,
                "version": SERVICE_VERSION,
                "endpoint": ISSUER_ENDPOINT,
            }
        ):
            raise OperatorEvidenceError(
                "source assessment does not match local request/policy"
            )
        evidence = source["evidence"]
        observation = None
        if extra is not None:
            if evidence["liquilens"]["state"] != "not_applicable":
                raise OperatorEvidenceError("cannot replace native LiquiLens evidence")
            evidence["liquilens"], observation = extra
        # Never renew a public/source receipt's lifetime by re-signing it.
        remaining = math.floor((_timestamp(source["expires_at"]) - now).total_seconds())
        if remaining < 1:
            raise OperatorEvidenceError("source assessment has no remaining validity")
        receipt = issue_trade_safety_receipt(
            request=normalized,
            evidence=evidence,
            policy=policy,
            broker_preview=source["broker_preview"],
            evaluated_at=now,
            issuer={
                "name": self._binding.issuer_name,
                "version": self._binding.issuer_version,
                "endpoint": self._binding.issuer_endpoint,
            },
            ttl_seconds=min(RECEIPT_TTL_SECONDS, remaining),
            hmac_key=self._hmac_key,
            hmac_key_id=self._binding.hmac_key_id,
        )
        receipt_json = _json(receipt, limit=MAX_RESPONSE_BYTES)
        verify_trade_safety_receipt(receipt, evaluated_at=now, hmac_key=self._hmac_key)
        return OperatorAssessment(receipt_json, source_json, observation)

    async def aclose(self) -> None:
        await self._transport.aclose()
