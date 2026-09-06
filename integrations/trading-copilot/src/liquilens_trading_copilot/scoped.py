"""Versioned private paper assessment over current, explicitly scoped sources.

The public sandbox's full-composite and sell-only v1 contracts are unchanged.
This operator profile assesses a selected funding slice and a separate,
hypothetical liquidation scenario; neither is a live execution quote.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from liquilens_evidence import TradeSafetyExecutionBinding
from liquilens_evidence.trade_safety import (
    TradeSafetyError,
    issue_trade_safety_receipt,
    trade_safety_policy_hash,
    trade_safety_request_hash,
    validate_trade_safety_request,
    verify_trade_safety_receipt,
)
from trade_safety_gateway.app import (
    BROKER_PREVIEW_REFERENCE_SCHEMA,
    UNDERTOW_URL,
    RawUpstreamResponse,
    _mcp_call,
    _unavailable_section,
    _undertow_contract_request,
    _undertow_section,
)
from trade_safety_gateway.http_safety import cookie_free_jar

from .config import SCOPED_PROFILE, scoped_policy
from .entry_profile import (
    build_liquidation_scenario,
    project_liquidation_section,
    verify_entry_profile_receipt,
)
from .evidence import OperatorEvidenceError, _json
from .funding import parse_corporate_research, parse_funding_scope

FUNDING_URL = "https://api.seiche.info/api/money-markets"
CORPORATE_URL = "https://api.liquilens.in/api/public-signals/corporate-transmission"
FIXED_ROUTES = {("GET", FUNDING_URL), ("GET", CORPORATE_URL), ("POST", UNDERTOW_URL)}


def _text(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


class ScopedUpstreamTransport:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.client = httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            cookies=cookie_free_jar(),
            timeout=httpx.Timeout(5, connect=2),
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": "liquilens-paper-copilot-operator/0.1.0",
                "X-LiquiLens-Synthetic": "true",
            },
        )

    async def request(
        self,
        method: str,
        url: str,
        *,
        json_body: Mapping[str, Any] | None = None,
    ) -> RawUpstreamResponse:
        if (method, url) not in FIXED_ROUTES:
            raise OperatorEvidenceError("scoped_source_not_allowed")
        if method == "POST" and (
            json_body is None
            or json_body.get("method") != "tools/call"
            or json_body.get("params", {}).get("name") != "trade_safety_exit_context"
        ):
            raise OperatorEvidenceError("scoped_tool_not_allowed")
        content = (
            None
            if json_body is None
            else json.dumps(
                json_body,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        if content is not None and len(content) > 65536:
            raise OperatorEvidenceError("scoped_request_too_large")
        async with asyncio.timeout(7):
            async with self.client.stream(
                method,
                url,
                content=content,
                headers={"Content-Type": "application/json"} if content else None,
            ) as response:
                if (
                    response.status_code != 200
                    or response.headers.get("content-type", "").split(";")[0]
                    != "application/json"
                    or response.headers.get("content-encoding", "identity")
                    != "identity"
                ):
                    raise OperatorEvidenceError("scoped_source_http_invalid")
                raw = bytearray()
                if response.is_stream_consumed:
                    raw.extend(response.content)
                else:
                    async for chunk in response.aiter_raw():
                        raw.extend(chunk)
                        if len(raw) > 1048576:
                            raise OperatorEvidenceError("scoped_source_too_large")
                if len(raw) > 1048576:
                    raise OperatorEvidenceError("scoped_source_too_large")
                return RawUpstreamResponse(body=bytes(raw))

    async def aclose(self) -> None:
        await self.client.aclose()


@dataclass(frozen=True, slots=True)
class ScopedAssessment:
    receipt_json: str

    @property
    def receipt(self) -> dict[str, Any]:
        return json.loads(self.receipt_json)

    @property
    def outcome(self) -> str:
        return self.receipt["decision"]["outcome"]

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(self.receipt["decision"]["reason_codes"])


class ScopedPaperEvidenceService:
    def __init__(
        self,
        transport: ScopedUpstreamTransport,
        *,
        binding: TradeSafetyExecutionBinding,
        policy: Mapping[str, Any],
        hmac_key: bytes,
        clock: Callable[[], datetime],
    ) -> None:
        if dict(policy) != scoped_policy() or binding.policy_id != SCOPED_PROFILE:
            raise OperatorEvidenceError("scoped_paper_policy_not_exact")
        if binding.policy_hash != trade_safety_policy_hash(policy):
            raise OperatorEvidenceError("scoped_policy_binding_mismatch")
        if (
            binding.hmac_key_id != "operator-paper-funding-exit-v1"
            or not isinstance(hmac_key, bytes)
            or len(hmac_key) < 32
        ):
            raise OperatorEvidenceError("scoped_operator_authentication_invalid")
        self.transport, self.binding, self.key, self.clock = (
            transport,
            binding,
            hmac_key,
            clock,
        )
        self.policy = json.dumps(dict(policy), allow_nan=False, sort_keys=True)

    async def funding_regime(self) -> str:
        async with asyncio.timeout(10):
            raw = await self.transport.request("GET", FUNDING_URL)
        if (
            not isinstance(raw, RawUpstreamResponse)
            or not isinstance(raw.body, bytes)
            or len(raw.body) > 1048576
        ):
            raise OperatorEvidenceError("scoped_source_bytes_invalid")
        now = self.clock()
        section = parse_funding_scope(
            raw=raw.body,
            request_hash="0" * 64,
            retrieved_at=now,
            request_expires_at=now + timedelta(seconds=90),
            max_age_seconds=691200,
        )
        return section["facts"]["regime"]

    async def assess(self, request: Mapping[str, Any]) -> ScopedAssessment:
        normalized = validate_trade_safety_request(request)
        for field in (
            "account_id",
            "tenant_id",
            "operator_id",
            "agent_id",
            "runtime",
            "strategy_id",
        ):
            if normalized["agent"][field] != getattr(self.binding, field):
                raise OperatorEvidenceError("scoped_request_identity_mismatch")
        if normalized["policy_ref"] != {
            "policy_id": self.binding.policy_id,
            "version": self.binding.policy_version,
        }:
            raise OperatorEvidenceError("scoped_request_policy_mismatch")
        scenario = build_liquidation_scenario(normalized)
        scenario_hash = trade_safety_request_hash(scenario)
        request_hash = trade_safety_request_hash(normalized)
        expected = _undertow_contract_request(scenario, scenario_hash)
        expires = datetime.fromisoformat(
            normalized["expires_at"].replace("Z", "+00:00")
        )
        created = datetime.fromisoformat(
            normalized["created_at"].replace("Z", "+00:00")
        )
        if not created <= self.clock() < expires:
            raise OperatorEvidenceError("scoped_request_not_current")
        async with asyncio.timeout(20):
            results = await asyncio.gather(
                self.transport.request("GET", FUNDING_URL),
                self.transport.request("GET", CORPORATE_URL),
                self.transport.request(
                    "POST",
                    UNDERTOW_URL,
                    json_body=_mcp_call(
                        "trade_safety_exit_context",
                        expected,
                        "trade-safety-undertow-v1",
                    ),
                ),
                return_exceptions=True,
            )
        now = self.clock()
        if now >= expires:
            raise OperatorEvidenceError("scoped_request_expired_during_capture")
        evidence = {}
        for product, url, raw in zip(
            ("seiche", "liquilens", "undertow"),
            (FUNDING_URL, CORPORATE_URL, UNDERTOW_URL),
            results,
            strict=True,
        ):
            try:
                if (
                    not isinstance(raw, RawUpstreamResponse)
                    or not isinstance(raw.body, bytes)
                    or len(raw.body) > 1048576
                ):
                    raise OperatorEvidenceError("source_unreachable")
                if product == "seiche":
                    section = parse_funding_scope(
                        raw=raw.body,
                        request_hash=request_hash,
                        retrieved_at=now,
                        request_expires_at=expires,
                        max_age_seconds=691200,
                    )
                elif product == "liquilens":
                    section = parse_corporate_research(
                        raw=raw.body,
                        request_hash=request_hash,
                        retrieved_at=now,
                        request_expires_at=expires,
                        max_age_seconds=691200,
                    )
                else:
                    native = _undertow_section(
                        raw=raw,
                        request_hash=scenario_hash,
                        expected_request=expected,
                        retrieved_at=now,
                        request_expires_at=expires,
                        max_age_seconds=300,
                    )
                    section = project_liquidation_section(normalized, scenario, native)
            except (ValueError, TradeSafetyError, KeyError, TypeError):
                section = _unavailable_section(
                    product=product,
                    request_hash=request_hash,
                    source_url=url,
                    retrieved_at=now,
                    limitation=f"scoped_{product}_unavailable_or_invalid",
                    raw=(
                        raw
                        if isinstance(raw, RawUpstreamResponse)
                        and isinstance(raw.body, bytes)
                        and len(raw.body) <= 1048576
                        else None
                    ),
                )
            evidence[product] = section
        preview = {
            "schema": BROKER_PREVIEW_REFERENCE_SCHEMA,
            "state": "not_applicable",
            "provider": None,
            "account_id": self.binding.account_id,
            "request_hash": request_hash,
            "preview_id": None,
            "source_url": None,
            "source_sha256": None,
            "retrieved_at": _text(now),
            "expires_at": None,
            "limitations": ["private_paper_profile_has_no_live_broker_preview"],
            "facts": {},
        }
        receipt = issue_trade_safety_receipt(
            request=normalized,
            evidence=evidence,
            policy=json.loads(self.policy),
            broker_preview=preview,
            evaluated_at=now,
            issuer={
                "name": self.binding.issuer_name,
                "version": self.binding.issuer_version,
                "endpoint": self.binding.issuer_endpoint,
            },
            ttl_seconds=30,
            hmac_key=self.key,
            hmac_key_id=self.binding.hmac_key_id,
        )
        verify_trade_safety_receipt(receipt, evaluated_at=now, hmac_key=self.key)
        if receipt["decision"]["outcome"] == "pass":
            self.verify_for_submission(receipt)
        return ScopedAssessment(_json(receipt, limit=524288))

    def verify_for_submission(self, receipt: Mapping[str, Any]) -> None:
        verify_entry_profile_receipt(
            receipt,
            evaluated_at=self.clock(),
            hmac_key=self.key,
            binding=self.binding,
        )


async def scoped_readiness(
    *,
    paper_credentials_present: bool,
    account_id_configured: bool,
) -> dict[str, Any]:
    """Two public GETs only; no order-specific assessment or broker call."""
    transport = ScopedUpstreamTransport()
    try:
        results = await asyncio.gather(
            transport.request("GET", FUNDING_URL),
            transport.request("GET", CORPORATE_URL),
            return_exceptions=True,
        )
    finally:
        await transport.aclose()
    now = datetime.now(UTC)
    summaries, blockers = {}, []
    for name, parser, raw in zip(
        ("seiche_funding_scope", "liquilens_current_cp_scope"),
        (parse_funding_scope, parse_corporate_research),
        results,
        strict=True,
    ):
        try:
            if not isinstance(raw, RawUpstreamResponse):
                raise OperatorEvidenceError("source_unreachable")
            section = parser(
                raw=raw.body,
                request_hash="0" * 64,
                retrieved_at=now,
                request_expires_at=now + timedelta(seconds=90),
                max_age_seconds=691200,
            )
            facts = section["facts"]
            summaries[name] = {
                "state": section["state"],
                "as_of": section["as_of"],
                "source_sha256": section["source_sha256"],
                "scope": section["source_schema"],
                **{
                    key: facts[key]
                    for key in (
                        "regime",
                        "sofr_minus_iorb_bp",
                        "effr_minus_iorb_bp",
                        "cp_spread_bp",
                    )
                    if key in facts
                },
            }
        except (ValueError, TradeSafetyError, KeyError, TypeError):
            summaries[name] = {"state": "unavailable"}
            blockers.append(f"{name}_unavailable")
    if not paper_credentials_present:
        blockers.append("paper_credentials_or_operator_hmac_missing")
    if not account_id_configured:
        blockers.append("paper_account_id_missing")
    return {
        "schema": "liquilens.scoped-paper-readiness.v1",
        "profile": SCOPED_PROFILE,
        "observed_at": _text(now),
        "mode": "paper",
        "status": "blocked" if blockers else "pending_order_specific_checks",
        "blockers": blockers,
        "sources": summaries,
        "broker_calls_performed": False,
        "assessment_performed": False,
        "execution_enabled": False,
        "remaining_order_checks": [
            "exact_undertow_scenario",
            "paper_account_identity",
            "portfolio_limits",
            "authenticated_receipt",
        ],
    }
