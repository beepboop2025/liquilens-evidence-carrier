"""Bounded Riptide research reads; never a trading signal or order gate.

The defensive index describes configured public Telegram previews, not BTC or
global risk. Event quarantine is missing coverage, never a zero-risk reading.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from trade_safety_gateway.http_safety import cookie_free_jar

RISK_URL = "https://api.seiche.info/riptide/api/v1/risk"
EVENTS_URL = "https://api.seiche.info/riptide/api/v1/events"
SCHEMA = "liquilens.riptide-research-context.v1"
MAX_RESPONSE_BYTES = 1_048_576
TOTAL_TIMEOUT_SECONDS = 10.0
RISK_MAX_AGE_SECONDS = 86_400
EVENTS_MAX_AGE_SECONDS = 129_600
_PROVIDERS = {"kalshi", "polymarket"}
_RIGHTS = {"licensed", "allowed", "metadata_only", "restricted", "unknown", "blocked"}


class _Invalid(ValueError):
    pass


def _check(condition: bool, reason: str) -> None:
    if not condition:
        raise _Invalid(reason)


def _object(value: Any, reason: str) -> dict[str, Any]:
    _check(isinstance(value, dict), reason)
    return value


def _member(value: Any, choices: set[str]) -> bool:
    return isinstance(value, str) and value in choices


def _label(value: Any, reason: str, maximum: int = 128) -> str:
    _check(
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and re.fullmatch(r"[A-Za-z0-9_.:-]+", value) is not None,
        reason,
    )
    return value


def _count(value: Any, reason: str) -> int:
    _check(type(value) is int and 0 <= value <= 1_000_000_000, reason)
    return value


def _number(value: Any, reason: str) -> float:
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        valid = False
    _check(valid, reason)
    return float(value)


def _now(value: datetime) -> datetime:
    _check(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None,
        "retrieval_clock_invalid",
    )
    return value.astimezone(UTC)


def _clock(value: Any, reason: str) -> datetime:
    _check(isinstance(value, str) and len(value) <= 40 and "T" in value, reason)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _now(parsed)
    except ValueError as error:
        raise _Invalid(reason) from error


def _text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _load(raw: bytes) -> dict[str, Any]:
    _check(
        isinstance(raw, bytes) and 0 < len(raw) <= MAX_RESPONSE_BYTES,
        "response_byte_budget",
    )

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            _check(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    def finite(value: str) -> float:
        number = float(value)
        _check(math.isfinite(number), "nonfinite_json_number")
        return number

    def constant(value: str) -> None:
        raise _Invalid("nonfinite_json_number")

    try:
        result = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_float=finite,
            parse_constant=constant,
        )
    except _Invalid:
        raise
    except (ValueError, UnicodeDecodeError, RecursionError) as error:
        raise _Invalid("invalid_json") from error
    return _object(result, "response_object_required")


def _base(kind: str, raw: bytes | None, retrieved_at: datetime) -> dict[str, Any]:
    return {
        "state": "unavailable",
        "source_url": RISK_URL if kind == "risk" else EVENTS_URL,
        "source_schema": "riptide.public.v1",
        "source_sha256": (
            hashlib.sha256(raw).hexdigest()
            if isinstance(raw, bytes) and len(raw) <= MAX_RESPONSE_BYTES
            else None
        ),
        "retrieved_at": _text(_now(retrieved_at)),
        "reason_codes": [],
        "financial_authority": "none",
        "influences_order_decision": False,
        "journal_cryptographically_verified_by_consumer": False,
    }


def _envelope(payload: dict[str, Any], kind: str, now: datetime) -> datetime:
    _check(
        payload.get("schema") == "riptide.public.v1"
        and payload.get("product") == "riptide"
        and payload.get("kind") == kind,
        "native_schema_mismatch",
    )
    _check(
        payload.get("real_orders") is False
        and type(payload.get("available")) is bool
        and isinstance(payload.get("disclaimer"), str)
        and 0 < len(payload["disclaimer"]) <= 2048,
        "native_authority_mismatch",
    )
    generated = _clock(payload.get("generated_at"), "response_clock_invalid")
    _check(generated <= now, "response_clock_future")
    return generated


def parse_riptide_risk(raw: bytes, retrieved_at: datetime) -> dict[str, Any]:
    """Project a complete <=24-hour defensive scan, or typed unavailable."""
    result = _base("risk", raw, retrieved_at)
    try:
        payload = _load(raw)
        now = _now(retrieved_at)
        generated = _envelope(payload, "risk_evidence", now)
        _check(
            payload.get("execution_mode") == "paper_only"
            and payload.get("action") == "display_and_alert_only"
            and payload.get("access_mode") == "public_login_free",
            "defensive_authority_mismatch",
        )
        _check(payload["available"] is True, "native_risk_unavailable")
        observed = _clock(payload.get("observed_at"), "observation_clock_invalid")
        age = (now - observed).total_seconds()
        result.update(
            observed_at=payload["observed_at"],
            response_generated_at=payload["generated_at"],
            observation_age_seconds=age,
            max_age_seconds=RISK_MAX_AGE_SECONDS,
        )
        _check(observed <= generated <= now, "observation_clock_future")
        _check(age <= RISK_MAX_AGE_SECONDS, "defensive_observation_stale")
        _check(
            payload.get("state") == "live" and payload.get("feed_written") is True,
            "defensive_feed_unavailable",
        )
        requested = _count(payload.get("channels_requested"), "channel_count_invalid")
        reachable = _count(payload.get("channels_reachable"), "channel_count_invalid")
        posts = _count(payload.get("posts_scanned"), "post_count_invalid")
        own = _count(payload.get("own_property_hits"), "hit_count_invalid")
        scam = _count(payload.get("scam_hits"), "hit_count_invalid")
        _check(reachable <= requested, "channel_count_inconsistent")
        _check(posts >= reachable, "post_count_inconsistent")
        _check(
            own <= min(posts, 20) and scam <= min(posts, 20), "hit_count_inconsistent"
        )
        result["coverage"] = {
            "channels_requested": requested,
            "channels_reachable": reachable,
            "posts_scanned": posts,
            "complete": requested > 0 and reachable == requested and posts > 0,
        }
        _check(result["coverage"]["complete"], "defensive_coverage_incomplete")
        score = _number(payload.get("breach_stress"), "defensive_index_invalid")
        _check(0 <= score <= 100, "defensive_index_out_of_range")
        band = (
            "ACUTE"
            if score >= 70
            else "STRAINED"
            if score >= 45
            else "EROSION"
            if score >= 25
            else "CALM"
        )
        result.update(
            state="available",
            generated_by_run=_label(
                payload.get("generated_by_run"), "run_identity_invalid"
            ),
            breach_stress=score,
            index_units="0_to_100_weighted_keyword_density_index",
            native_band=band,
            native_band_thresholds={
                "erosion_min": 25,
                "strained_min": 45,
                "acute_min": 70,
            },
            own_property_hits=own,
            scam_hits=scam,
            action="display_and_alert_only",
            scope=(
                "configured public Telegram previews; "
                "defensive operator-property/sector research"
            ),
            limitations=[
                "not_a_BTC_forecast_or_global_risk_measure",
                "freshness_is_scan_time_not_each_underlying_post_timestamp",
                "channel_identities_watch_terms_and_detector_availability_not_public",
                "own_property_and_scam_hit_counts_are_capped_at_20",
                "server_journal_validation_is_not_public_cryptographic_proof",
            ],
        )
    except _Invalid as error:
        result["reason_codes"] = [str(error)]
    return result


def parse_riptide_events(raw: bytes, retrieved_at: datetime) -> dict[str, Any]:
    """Retain coverage/policy diagnostics only; never emit quotes or bet links."""
    result = _base("events", raw, retrieved_at)
    try:
        payload = _load(raw)
        now = _now(retrieved_at)
        generated = _envelope(payload, "event_markets", now)
        _check(
            payload.get("event_schema") == "riptide.event-intelligence.v1"
            and payload.get("execution_mode") == "read_only"
            and payload.get("research_only") is True
            and payload.get("labs") is True,
            "event_authority_mismatch",
        )
        collected = _clock(payload.get("collected_at"), "collection_clock_invalid")
        _check(collected <= generated <= now, "collection_clock_future")
        policy = _object(payload.get("source_policy"), "event_policy_missing")
        _check(
            policy.get("schema") == "riptide.event-source-policy.v1"
            and policy.get("surface") == "rest"
            and policy.get("jurisdiction") == "unknown"
            and policy.get("default_new_provider_state") == "unknown",
            "event_policy_boundary_mismatch",
        )
        policy_rows = policy.get("providers")
        _check(
            isinstance(policy_rows, list) and len(policy_rows) == 2,
            "provider_policy_count_invalid",
        )
        providers: dict[str, Any] = {}
        for row in policy_rows:
            row = _object(row, "provider_policy_invalid")
            name = row.get("provider")
            _check(
                _member(name, _PROVIDERS) and name not in providers,
                "provider_policy_identity_invalid",
            )
            mode, rights = row.get("mode"), row.get("source_state")
            _check(
                _member(mode, {"blocked", "metadata_only", "full"})
                and _member(rights, _RIGHTS)
                and type(row.get("eligible")) is bool,
                "provider_policy_invalid",
            )
            _check(
                row["eligible"] == (mode != "blocked"),
                "provider_eligibility_inconsistent",
            )
            _check(
                rights not in {"unknown", "blocked", "restricted"} or mode == "blocked",
                "provider_rights_inconsistent",
            )
            providers[name] = {
                "source_state": rights,
                "publication_mode": mode,
                "eligible": row["eligible"],
                "reason_code": _label(
                    row.get("reason_code"), "provider_reason_invalid"
                ),
                "reviewed_at": _label(
                    row.get("reviewed_at"), "provider_review_missing", 40
                ),
            }
        rows = payload.get("events")
        _check(isinstance(rows, list) and len(rows) <= 24, "event_count_invalid")
        counts = {name: 0 for name in _PROVIDERS}
        for row in rows:
            row = _object(row, "event_row_invalid")
            provider = row.get("provider")
            _check(_member(provider, _PROVIDERS), "event_provider_invalid")
            _check(providers[provider]["eligible"], "blocked_provider_event_present")
            counts[provider] += 1
        coverage = _object(payload.get("coverage"), "event_coverage_missing")
        _check(
            coverage.get("providers_expected") == sorted(_PROVIDERS)
            and coverage.get("providers_with_events")
            == sorted(name for name, count in counts.items() if count)
            and type(coverage.get("event_limit")) is int
            and coverage["event_limit"] == 24
            and _count(coverage.get("event_count"), "event_count_invalid") == len(rows),
            "event_coverage_inconsistent",
        )
        blocks = payload.get("providers")
        _check(isinstance(blocks, list) and len(blocks) == 2, "provider_blocks_invalid")
        seen = set()
        for block in blocks:
            block = _object(block, "provider_block_invalid")
            name = block.get("provider")
            _check(
                _member(name, _PROVIDERS) and name not in seen,
                "provider_block_identity_invalid",
            )
            seen.add(name)
            _check(
                block.get("source_state") == providers[name]["source_state"]
                and block.get("publication_mode") == providers[name]["publication_mode"]
                and _count(block.get("event_count"), "provider_event_count_invalid")
                == counts[name],
                "provider_block_inconsistent",
            )
        status = payload.get("status")
        _check(
            _member(status, {"available", "partial", "unavailable", "quarantined"}),
            "event_status_invalid",
        )
        _check(payload["available"] == bool(rows), "event_availability_inconsistent")
        _check(
            (status in {"available", "partial"}) == bool(rows),
            "event_status_inconsistent",
        )
        if status == "quarantined":
            _check(
                all(not row["eligible"] for row in providers.values()),
                "event_quarantine_inconsistent",
            )
        freshness = _object(payload.get("freshness"), "event_freshness_missing")
        _check(
            freshness.get("basis") == "snapshot_collection_time"
            and freshness.get("quote_freshness") == "per_event"
            and type(freshness.get("stale")) is bool
            and _number(freshness.get("max_age_hours"), "event_age_limit_invalid")
            == 36,
            "event_freshness_invalid",
        )
        digest = payload.get("snapshot_sha256")
        _check(
            isinstance(digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
            "event_snapshot_digest_missing",
        )
        age = (now - collected).total_seconds()
        result.update(
            native_status=status,
            native_available=payload["available"],
            event_schema=payload["event_schema"],
            collected_at=payload["collected_at"],
            response_generated_at=payload["generated_at"],
            collection_age_seconds=age,
            max_age_seconds=EVENTS_MAX_AGE_SECONDS,
            reported_snapshot_sha256=digest,
            snapshot_cryptographically_verified_by_consumer=False,
            event_count=len(rows),
            event_limit=24,
            coverage_complete=False,
            providers=providers,
            source_policy={
                key: policy[key] for key in ("schema", "surface", "jurisdiction")
            },
            limitations=[
                "event_quotes_and_betting_links_not_projected",
                "quarantine_or_empty_list_does_not_mean_no_event_risk",
                "published_events_are_a_bounded_sample_not_complete_market_coverage",
                "crowd_quotes_are_not_Riptide_forecasts",
            ],
        )
        result["source_policy"]["version"] = _label(
            policy.get("version"), "event_policy_version_missing"
        )
        _check(
            age <= EVENTS_MAX_AGE_SECONDS and freshness["stale"] is False,
            "event_snapshot_stale",
        )
        if not rows:
            result["reason_codes"] = [
                "event_source_policy_quarantine"
                if status == "quarantined"
                else "event_coverage_unavailable"
            ]
        else:
            result["state"] = "diagnostics_available"
    except _Invalid as error:
        result["state"] = "unavailable"
        result["reason_codes"] = [str(error)]
    return result


async def collect_riptide_context(
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Exactly two fixed GET attempts; no credentials, quotes, broker or tools."""
    now = clock or (lambda: datetime.now(UTC))

    async def read(client: httpx.AsyncClient, url: str) -> bytes:
        async with client.stream("GET", url) as response:
            _check(response.status_code == 200, "source_http_unavailable")
            _check(
                response.headers.get("content-type", "").split(";")[0].lower()
                == "application/json",
                "source_content_type_invalid",
            )
            _check(
                response.headers.get("content-encoding", "identity").lower()
                == "identity",
                "source_encoding_invalid",
            )
            body = bytearray()
            if response.is_stream_consumed:
                body.extend(response.content)
            else:
                async for chunk in response.aiter_raw():
                    body.extend(chunk)
                    _check(len(body) <= MAX_RESPONSE_BYTES, "response_byte_budget")
            _check(len(body) <= MAX_RESPONSE_BYTES, "response_byte_budget")
            return bytes(body)

    results: list[Any]
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                transport=transport,
                trust_env=False,
                follow_redirects=False,
                cookies=cookie_free_jar(),
                timeout=httpx.Timeout(7, connect=2),
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "User-Agent": "liquilens-paper-copilot-operator-research/0.1.0",
                    "X-LiquiLens-Synthetic": "true",
                },
            ) as client:
                results = list(
                    await asyncio.gather(
                        read(client, RISK_URL),
                        read(client, EVENTS_URL),
                        return_exceptions=True,
                    )
                )
    except TimeoutError:
        results = [_Invalid("source_deadline_exceeded")] * 2
    captured = _now(now())
    sources = {}
    for name, parser, raw in zip(
        ("risk", "events"),
        (parse_riptide_risk, parse_riptide_events),
        results,
        strict=True,
    ):
        if isinstance(raw, bytes):
            sources[name] = parser(raw, captured)
        else:
            sources[name] = _base(name, None, captured)
            sources[name]["reason_codes"] = [
                str(raw) if isinstance(raw, _Invalid) else "source_unreachable"
            ]
    return {
        "schema": SCHEMA,
        "observed_at": _text(captured),
        "purpose": "operator_read_only_defensive_research_context",
        "mode": "display_and_alert_only",
        "financial_authority": "none",
        "influences_order_decision": False,
        "broker_calls_performed": False,
        "assessment_performed": False,
        "order_calls_performed": False,
        "sources": sources,
    }
