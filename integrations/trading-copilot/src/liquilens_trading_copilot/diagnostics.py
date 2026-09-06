"""Bounded, read-only diagnostics; never an assessment or order authorization.

Seiche v1 dates the entire public provenance inventory, including historical
IOER/TED and quarterly GDP. A fresh snapshot cannot refresh those observations.
The remedy requires a versioned dependency contract, not ignoring old rows here.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from datetime import UTC, datetime
from typing import Any

import httpx

ENDPOINTS = {
    "gateway_health": "https://trade-safety.liquilens.in/healthz",
    "gateway_capabilities": "https://trade-safety.liquilens.in/v1/capabilities",
    "seiche_context": "https://api.seiche.info/api/trade-safety/risk-context",
    "seiche_health": "https://api.seiche.info/api/health",
}
MAX_RESPONSE_BYTES = 256 * 1024
REQUEST_TIMEOUT_SECONDS = 8.0
SEICHE_MAX_AGE_SECONDS = 691200
_PUBLISHED_RUNGS = {1000, 10000, 100000, 1000000}


class _InvalidResponse(ValueError):
    pass


def _object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clock(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if len(value) == 10:
            parsed = parsed.replace(tzinfo=UTC)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def _time_text(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _label(value: object) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:+ /-]{1,80}", value):
        return value
    return None


def _integer(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _strict_json(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise _InvalidResponse("duplicate_json_key")
            result[key] = value
        return result

    def number(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise _InvalidResponse("nonfinite_json")
        return parsed

    def constant(value: str) -> None:
        raise _InvalidResponse("nonfinite_json")

    result = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=pairs,
        parse_float=number,
        parse_constant=constant,
    )
    if not isinstance(result, dict):
        raise _InvalidResponse("json_object_required")
    return result


async def _read(
    client: httpx.AsyncClient,
    url: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Do not inherit caller credentials/cookies, redirects or request URLs."""
    request = httpx.Request(
        "GET",
        url,
        headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        extensions={"timeout": httpx.Timeout(REQUEST_TIMEOUT_SECONDS).as_dict()},
    )
    status: int | None = None
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
            response = await client.send(
                request,
                stream=True,
                auth=None,
                follow_redirects=False,
            )
            try:
                status = response.status_code
                if status != 200:
                    raise _InvalidResponse("http_status_not_200")
                content_type = response.headers.get("content-type", "")
                media_type = content_type.split(";", 1)[0].strip().lower()
                if media_type != "application/json":
                    raise _InvalidResponse("json_content_type_required")
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise _InvalidResponse("compressed_response_rejected")
                length = response.headers.get("content-length")
                if length is not None and (
                    not length.isdecimal() or int(length) > MAX_RESPONSE_BYTES
                ):
                    raise _InvalidResponse("response_size_limit")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise _InvalidResponse("response_size_limit")
                    raw.extend(chunk)
                payload = _strict_json(bytes(raw))
                return payload, {"state": "read", "http_status": status}
            finally:
                await response.aclose()
    except _InvalidResponse as exc:
        return None, {"state": "unavailable", "http_status": status, "reason": str(exc)}
    except (ValueError, UnicodeError, RecursionError):
        return None, {
            "state": "unavailable",
            "http_status": status,
            "reason": "invalid_json",
        }
    except (httpx.HTTPError, TimeoutError):
        return None, {
            "state": "unavailable",
            "http_status": status,
            "reason": "source_unreachable_or_timeout",
        }


def _provenance_summary(health: dict[str, Any]) -> dict[str, Any]:
    inventory = health.get("provenance")
    rows = list(inventory.values()) if isinstance(inventory, dict) else inventory
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return {"state": "unavailable", "row_count": None}
    dated = [(row, _clock(row.get("asof"))) for row in rows]
    known = sorted((pair for pair in dated if pair[1] is not None), key=lambda p: p[1])
    return {
        "state": "read",
        "row_count": len(rows),
        "unknown_observation_clock_count": sum(clock is None for _, clock in dated),
        "oldest_rows": [
            {
                "source": _label(row.get("source")),
                "mnemonic": _label(row.get("mnemonic")),
                "as_of": _time_text(clock),
                "frequency": _label(row.get("freq")),
                "role": (
                    "historical_splice_leg"
                    if row.get("mnemonic") == "IOER"
                    and row.get("source") == "fred"
                    and row.get("remote_id") == "IOER"
                    else "historical_prediction_training"
                    if row.get("mnemonic") == "TED"
                    and row.get("source") == "fred"
                    and row.get("remote_id") == "TEDRATE"
                    else "slow_current_composite_input"
                    if row.get("mnemonic") == "GDP"
                    and row.get("source") == "fred"
                    and row.get("remote_id") == "GDP"
                    else "not_classified_by_this_diagnostic"
                ),
            }
            for row, clock in known[:5]
        ],
        "inventory_is_not_a_current_input_dependency_manifest": True,
    }


async def collect_readiness(
    client: httpx.AsyncClient | None = None,
    *,
    paper_credentials_present: bool = False,
    account_id_configured: bool = False,
) -> dict[str, Any]:
    """Read four fixed public endpoints and report gaps without claiming readiness.

    Credential arguments describe presence only. No keys, account identifiers,
    environment values, request proposals or broker clients are accepted.
    """
    if (
        type(paper_credentials_present) is not bool
        or type(account_id_configured) is not bool
    ):
        raise TypeError("credential and account configuration must be booleans")
    if client is None:
        async with httpx.AsyncClient(trust_env=False) as owned:
            return await collect_readiness(
                owned,
                paper_credentials_present=paper_credentials_present,
                account_id_configured=account_id_configured,
            )
    observations = await asyncio.gather(
        *(_read(client, url) for url in ENDPOINTS.values())
    )
    payloads = {
        name: payload or {}
        for name, (payload, _) in zip(ENDPOINTS, observations, strict=True)
    }
    sources = {
        name: status for name, (_, status) in zip(ENDPOINTS, observations, strict=True)
    }
    blockers: list[dict[str, str]] = []

    def block(code: str, detail: str, source: str) -> None:
        blockers.append({"code": code, "detail": detail, "source": source})

    for name, state in sources.items():
        if state["state"] != "read":
            block(
                "source_unavailable",
                "Required diagnostic source could not be read safely.",
                name,
            )
    health = payloads["gateway_health"]
    caps = payloads["gateway_capabilities"]
    context = payloads["seiche_context"]
    seiche_health = payloads["seiche_health"]
    if (
        health.get("status") != "ok"
        or health.get("mode") != "sandbox"
        or health.get("can_execute") is not False
    ):
        block(
            "gateway_health_or_authority_invalid",
            "Expected a healthy, non-executing sandbox.",
            "gateway_health",
        )
    authority = _object(caps.get("authority"))
    authority_fields = (
        "can_execute",
        "can_recommend",
        "can_allocate_capital",
        "can_route_order",
        "can_custody",
        "can_settle",
        "has_broker_credentials",
        "has_order_submission",
    )
    if (
        caps.get("mode") != "sandbox"
        or caps.get("live_outcome") != "unavailable"
        or any(authority.get(key) is not False for key in authority_fields)
    ):
        block(
            "gateway_live_boundary_unexpected",
            "Live trading is unsupported; declared authority must remain false.",
            "gateway_capabilities",
        )
    admission = _object(caps.get("policy_admission"))
    declared_age = _integer(
        _object(admission.get("max_evidence_age_seconds")).get("seiche")
    )
    max_age = (
        min(declared_age, SEICHE_MAX_AGE_SECONDS)
        if declared_age
        else SEICHE_MAX_AGE_SECONDS
    )
    if declared_age is None or not 0 < declared_age <= SEICHE_MAX_AGE_SECONDS:
        block(
            "seiche_freshness_policy_invalid",
            "Expected a positive Seiche age ceiling no greater than eight days.",
            "gateway_capabilities",
        )
    if (
        context.get("schema") != "seiche.risk-context.v1"
        or context.get("status") != "available"
        or context.get("ok") is not True
    ):
        block(
            "seiche_context_unavailable",
            "Seiche did not declare the supported context available.",
            "seiche_context",
        )
    if any(
        context.get(key) is not False
        for key in (
            "executable",
            "executable_quote",
            "real_money_eligible",
            "can_authorize_order",
        )
    ):
        block(
            "seiche_authority_invalid",
            "Public context must not claim execution or real-money authority.",
            "seiche_context",
        )
    clocks = _object(context.get("clocks"))
    evidence_at = _clock(clocks.get("evidence_as_of"))
    snapshot_at = _clock(clocks.get("snapshot_generated_at"))
    evaluated_at = _clock(clocks.get("evaluated_at"))
    now = datetime.now(UTC)
    observed_age = None
    if evidence_at is None or snapshot_at is None or evaluated_at is None:
        block(
            "seiche_observation_clock_unknown",
            "Missing or invalid observation/snapshot/evaluation clock; "
            "fetch time is not a substitute.",
            "seiche_context",
        )
    elif not evidence_at <= snapshot_at <= evaluated_at <= now:
        block(
            "seiche_clock_order_invalid",
            "Source observation clocks must not follow snapshot or evaluation clocks.",
            "seiche_context",
        )
    else:
        observed_age = int((now - evidence_at).total_seconds())
        if _integer(clocks.get("evidence_age_seconds")) != int(
            (evaluated_at - evidence_at).total_seconds()
        ) or _integer(clocks.get("snapshot_age_seconds")) != int(
            (evaluated_at - snapshot_at).total_seconds()
        ):
            block(
                "seiche_reported_age_mismatch",
                "Reported ages do not match source clocks.",
                "seiche_context",
            )
        if observed_age > max_age:
            block(
                "seiche_evidence_too_old",
                "The observation exceeds the gateway age ceiling even if its "
                "containing snapshot was just rebuilt.",
                "seiche_context",
            )
    provenance = _provenance_summary(seiche_health)
    if provenance["state"] != "read":
        block(
            "seiche_provenance_unavailable",
            "No valid bounded public provenance inventory was available.",
            "seiche_health",
        )
    if not paper_credentials_present:
        block(
            "paper_credentials_missing",
            "ALPACA_PAPER_API_KEY and ALPACA_PAPER_SECRET_KEY are required "
            "by the separate operator adapter.",
            "operator_configuration",
        )
    if not account_id_configured:
        block(
            "paper_account_id_missing",
            "config.json.account_id must be configured and independently "
            "matched to the paper account.",
            "operator_configuration",
        )
    block(
        "order_specific_authenticated_receipt_required",
        "Public gateway receipts are hash-only; submission requires an "
        "independently verified exact-order, tenant-authenticated, unexpired, "
        "one-time paper pass receipt and durable journal.",
        "operator_execution",
    )
    upstream = _object(_object(caps.get("upstreams")).get("undertow"))
    rungs = upstream.get("published_rungs_usd")
    valid_rungs = (
        isinstance(rungs, list)
        and len(rungs) == 4
        and all(
            type(rung) in (int, float) and rung in _PUBLISHED_RUNGS for rung in rungs
        )
        and set(rungs) == _PUBLISHED_RUNGS
    )
    if (
        upstream.get("side") != "sell"
        or upstream.get("currency") != "USD"
        or upstream.get("modes") != ["observe", "paper"]
        or not valid_rungs
    ):
        block(
            "gateway_order_scope_unavailable",
            "The supported sell/USD exact-size context scope "
            "was not declared correctly.",
            "gateway_capabilities",
        )
    max_notional = admission.get("max_notional_usd")
    valid_notional = type(max_notional) in (int, float) and 0 < max_notional <= 100000
    if not valid_notional:
        block(
            "gateway_notional_policy_unavailable",
            "Expected the declared notional ceiling to be positive and "
            "no greater than USD 100,000.",
            "gateway_capabilities",
        )
    return {
        "schema": "liquilens.trading-copilot-readiness.v1",
        "checked_at": _time_text(now),
        "mode": "paper",
        "status": "blocked",
        "ready_for_paper_submission": False,
        "execution_enabled": False,
        "assessment_performed": False,
        "broker_calls_performed": False,
        "blockers": blockers,
        "sources": sources,
        "gateway": {
            "version": _label(health.get("version")),
            "source_revision": _label(health.get("source_revision")),
        },
        "paper_configuration": {
            "credentials_present": paper_credentials_present,
            "account_id_configured": account_id_configured,
            "account_identity_verified": False,
        },
        "supported_gateway_scope": {
            "modes": ["observe", "paper"],
            "live_trading_supported": False,
            "side": "sell",
            "buy_supported": False,
            "instrument": "BTC/USD",
            "published_rungs_usd": rungs if valid_rungs else [],
            "maximum_policy_notional_usd": max_notional if valid_notional else None,
            "policy_admissible_rungs_usd": sorted(
                rung for rung in rungs if rung <= max_notional
            )
            if valid_rungs and valid_notional
            else [],
            "arbitrary_size_supported": False,
            "automatic_resize": False,
            "quantity_and_notional_equivalence_verified": False,
            "scope_is_evidence_only_not_broker_asset_support": True,
        },
        "semantic_freshness": {
            "evidence_as_of": _time_text(evidence_at),
            "snapshot_generated_at": _time_text(snapshot_at),
            "observed_evidence_age_seconds": observed_age,
            "gateway_max_evidence_age_seconds": max_age,
            "provenance_snapshot_matches_context": snapshot_at is not None
            and snapshot_at == _clock(seiche_health.get("generated_at")),
            "provenance": provenance,
            "interpretation": (
                "Seiche v1 uses the oldest public provenance observation. "
                "Historical and slow-cadence rows can dominate it; this does "
                "not mean every input is stale. This diagnostic never "
                "excludes them or refreshes their clocks."
            ),
            "order_eligibility": "not_assessed",
        },
        "future_fix_plan": [
            "Preserve the v1 freshness gate and original observation clocks.",
            "Publish an assembler-bound, versioned manifest of actual composite "
            "dependencies, separating current inputs, historical calibration "
            "and slow-cadence inputs with genuine publication/observation clocks.",
            "Expose missing required table clocks as unavailable; validate "
            "the manifest and policy in the gateway together.",
            "A narrower funding context needs its own named scope and policy; "
            "it cannot silently reuse the full composite regime after "
            "dropping dependencies.",
        ],
        "coverage_limits": [
            "No assessment, broker/account lookup, order, payment "
            "or model call was performed.",
            "Configuration presence is not credential validity, "
            "account identity or execution readiness.",
            "Undertow and optional institution evidence have not been "
            "evaluated for an exact order.",
            "Public response declarations are not independent runtime-byte "
            "or source-attestation verification.",
        ],
    }
