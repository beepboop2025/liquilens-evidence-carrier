"""Strict consumers for the two native Trade Safety context contracts.

These validators intentionally live at the gateway boundary.  The producer's
unkeyed canonical digest detects accidental transformation, while the gateway's
receipt binds the validated digest to one canonical LiquiLens request hash.
Neither digest is an authentication credential or execution authorization.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

SEICHE_SCHEMA = "seiche.risk-context.v1"
SEICHE_CANONICALIZATION = "python-json-sort-keys-utf8-no-nan-server-internal-v1"
UNDERTOW_SCHEMA = "undertow.trade-safety-exit-context.v1"
UNDERTOW_SCHEMA_URL = (
    "https://liquilens-undertow.com/"
    "undertow-trade-safety-exit-context-v1.schema.json"
)
UNDERTOW_PACK_SCHEMA = "undertow.crypto_desk.v2"
UNDERTOW_RIGHTS_SCHEMA = "undertow.trade-safety-exit-rights.v1"
UNDERTOW_RIGHTS_MANIFEST = "trade_safety_exit_rights.json"
UNDERTOW_LEDGER = "data/_pit/board.jsonl"
UNDERTOW_MAX_OBSERVATION_AGE = timedelta(hours=2)
UNDERTOW_MAX_OBSERVATION_SKEW_SECONDS = 300
UNDERTOW_DEPEG_THRESHOLD = 0.005

VENUES = ("binance", "bitfinex", "coinbase", "gemini", "kraken", "okx")
VENUE_SET = frozenset(VENUES)
VENUE_QUOTES = {
    "binance": "USDT",
    "bitfinex": "USD",
    "coinbase": "USD",
    "gemini": "USD",
    "kraken": "USD",
    "okx": "USDT",
}
RUNGS_USD = frozenset({1_000.0, 10_000.0, 100_000.0, 1_000_000.0})
REGIMES = frozenset({"CALM", "EROSION", "STRAIN", "STRESS"})

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_RIGHTS_VERSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.review\.[1-9]\d*$")

_SEICHE_ROOT_KEYS = frozenset(
    {
        "ok",
        "schema",
        "status",
        "reason",
        "state",
        "evidence_class",
        "rights_status",
        "context_only",
        "executable",
        "executable_quote",
        "real_money_eligible",
        "can_authorize_order",
        "projection_mode",
        "request_time_collection",
        "request_time_model_fitting",
        "request_time_network",
        "request_time_notary",
        "request_time_broker",
        "attestation_state",
        "source_url",
        "source_snapshot_version",
        "regime",
        "stress_index",
        "coverage_pct",
        "fault_count",
        "staleness",
        "clocks",
        "attestation",
        "limitations",
        "disclaimer",
        "canonicalization",
        "projection_sha256",
    }
)
_UNDERTOW_ROOT_KEYS = frozenset(
    {
        "schema",
        "schema_url",
        "status",
        "reason",
        "request_hash",
        "request",
        "evidence_class",
        "measurement",
        "coverage",
        "peg",
        "source",
        "pit",
        "clocks",
        "rights",
        "authority",
        "limitations",
        "context_sha256",
    }
)


class NativeContractError(ValueError):
    """A producer response cannot participate in a Trade Safety receipt."""


@dataclass(frozen=True, slots=True)
class ProjectedContext:
    """Validated fields ready for the core evidence-section envelope."""

    source_schema: str
    as_of: datetime
    knowledge_time: datetime
    native_expires_at: datetime | None
    rights_status: str
    limitations: tuple[str, ...]
    facts: dict[str, Any]


def _exact_object(value: Any, label: str, keys: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NativeContractError(f"{label}_not_object")
    if set(value) != keys:
        raise NativeContractError(f"{label}_shape_mismatch")
    return value


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NativeContractError(f"{label}_not_object")
    return value


def _finite(
    value: Any,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NativeContractError(f"{label}_not_finite_number")
    number = float(value)
    if not math.isfinite(number):
        raise NativeContractError(f"{label}_not_finite_number")
    if minimum is not None and number < minimum:
        raise NativeContractError(f"{label}_out_of_range")
    if maximum is not None and number > maximum:
        raise NativeContractError(f"{label}_out_of_range")
    return number


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise NativeContractError(f"{label}_not_integer")
    return int(value)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NativeContractError(f"{label}_not_text")
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise NativeContractError(f"{label}_not_sha256")
    return value


def _git_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or _GIT_SHA_RE.fullmatch(value) is None:
        raise NativeContractError(f"{label}_not_git_sha")
    return value


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise NativeContractError(f"{label}_not_utc_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError) as exc:
        raise NativeContractError(f"{label}_not_utc_timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise NativeContractError(f"{label}_not_utc_timestamp")
    return parsed.astimezone(UTC)


def _strings(value: Any, label: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item.strip() for item in value)
        or len(set(value)) != len(value)
    ):
        raise NativeContractError(f"{label}_not_unique_text_list")
    return tuple(value)


def _canonical_sha(value: dict[str, Any], excluded: str) -> str:
    unsigned = {key: item for key, item in value.items() if key != excluded}
    try:
        encoded = json.dumps(
            unsigned,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise NativeContractError("native_context_not_canonical_json") from exc
    return hashlib.sha256(encoded).hexdigest()


def _binding(
    *, request_hash: str, source_schema: str, native_context_sha256: str
) -> dict[str, str]:
    unsigned = {
        "canonicalization": "json-sort-keys-utf8-no-nan-v1",
        "native_context_sha256": native_context_sha256,
        "request_hash": request_hash,
        "source_schema": source_schema,
    }
    encoded = json.dumps(
        unsigned,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {**unsigned, "binding_sha256": hashlib.sha256(encoded).hexdigest()}


def _fresh(as_of: datetime, retrieved_at: datetime, max_age_seconds: int) -> None:
    if as_of > retrieved_at:
        raise NativeContractError("native_evidence_clock_in_future")
    if (retrieved_at - as_of).total_seconds() > max_age_seconds:
        raise NativeContractError("native_evidence_stale")


def parse_seiche_context(
    payload: dict[str, Any],
    *,
    request_hash: str,
    retrieved_at: datetime,
    max_age_seconds: int,
    source_url: str,
) -> ProjectedContext:
    """Validate and bind one ``seiche.risk-context.v1`` response."""

    context = _exact_object(payload, "seiche_context", _SEICHE_ROOT_KEYS)
    projection_sha = _sha256(context["projection_sha256"], "seiche_projection")
    if projection_sha != _canonical_sha(context, "projection_sha256"):
        raise NativeContractError("seiche_projection_digest_mismatch")
    if (
        context["schema"] != SEICHE_SCHEMA
        or context["source_url"] != source_url
        or context["canonicalization"] != SEICHE_CANONICALIZATION
    ):
        raise NativeContractError("seiche_identity_mismatch")
    if context["status"] != "available" or context["ok"] is not True:
        raise NativeContractError("seiche_reported_unavailable")
    if (
        context["reason"] is not None
        or context["state"] != "context_only"
        or context["evidence_class"] != "derived"
        or context["rights_status"] != "metadata_only"
        or context["context_only"] is not True
        or context["projection_mode"] != "cache_only"
    ):
        raise NativeContractError("seiche_state_mismatch")
    for field in (
        "executable",
        "executable_quote",
        "real_money_eligible",
        "can_authorize_order",
        "request_time_collection",
        "request_time_model_fitting",
        "request_time_network",
        "request_time_notary",
        "request_time_broker",
    ):
        if context[field] is not False:
            raise NativeContractError("seiche_authority_mismatch")

    version = _text(context["source_snapshot_version"], "seiche_snapshot_version")
    regime = context["regime"]
    if regime not in REGIMES:
        raise NativeContractError("seiche_regime_invalid")
    stress_index = _finite(
        context["stress_index"], "seiche_stress_index", minimum=0, maximum=100
    )
    coverage = _finite(
        context["coverage_pct"], "seiche_coverage", minimum=0, maximum=100
    )
    fault_count = _integer(context["fault_count"], "seiche_fault_count")

    staleness = _exact_object(
        context["staleness"],
        "seiche_staleness",
        frozenset({"fresh", "aging", "stale", "dead", "unknown", "total"}),
    )
    counts = {
        state: _integer(staleness[state], f"seiche_staleness_{state}")
        for state in ("fresh", "aging", "stale", "dead", "unknown")
    }
    total = _integer(staleness["total"], "seiche_staleness_total")
    if total <= 0 or total != sum(counts.values()):
        raise NativeContractError("seiche_staleness_count_mismatch")

    clocks = _exact_object(
        context["clocks"],
        "seiche_clocks",
        frozenset(
            {
                "snapshot_generated_at",
                "evidence_as_of",
                "evaluated_at",
                "snapshot_age_seconds",
                "evidence_age_seconds",
                "basis",
            }
        ),
    )
    snapshot_at = _timestamp(
        clocks["snapshot_generated_at"], "seiche_snapshot_generated_at"
    )
    evidence_at = _timestamp(clocks["evidence_as_of"], "seiche_evidence_as_of")
    evaluated_at = _timestamp(clocks["evaluated_at"], "seiche_evaluated_at")
    if not evidence_at <= snapshot_at <= evaluated_at <= retrieved_at:
        raise NativeContractError("seiche_clock_order_mismatch")
    snapshot_age = _integer(
        clocks["snapshot_age_seconds"], "seiche_snapshot_age_seconds"
    )
    evidence_age = _integer(
        clocks["evidence_age_seconds"], "seiche_evidence_age_seconds"
    )
    if snapshot_age != int((evaluated_at - snapshot_at).total_seconds()):
        raise NativeContractError("seiche_snapshot_age_mismatch")
    if evidence_age != int((evaluated_at - evidence_at).total_seconds()):
        raise NativeContractError("seiche_evidence_age_mismatch")
    _text(clocks["basis"], "seiche_clock_basis")
    _fresh(evidence_at, retrieved_at, max_age_seconds)

    attestation = _exact_object(
        context["attestation"],
        "seiche_attestation",
        frozenset(
            {
                "status",
                "ed25519_status",
                "ots_status",
                "bitcoin_anchor_claimed",
                "ledger_read",
                "reason",
                "disclosure",
            }
        ),
    )
    if (
        context["attestation_state"] != "not_evaluated"
        or attestation["status"] != "not_evaluated"
        or attestation["ed25519_status"] != "not_evaluated"
        or attestation["ots_status"] != "not_evaluated"
        or attestation["bitcoin_anchor_claimed"] is not False
        or attestation["ledger_read"] is not False
    ):
        raise NativeContractError("seiche_attestation_boundary_mismatch")
    _text(attestation["reason"], "seiche_attestation_reason")
    _text(attestation["disclosure"], "seiche_attestation_disclosure")
    limitations = _strings(context["limitations"], "seiche_limitations")
    if not {
        "not_order_bound_and_cannot_authorize_or_route_an_order",
        "stream_attestation_is_not_per_order_execution_authority",
    } <= set(limitations):
        raise NativeContractError("seiche_safety_limitation_missing")
    _text(context["disclaimer"], "seiche_disclaimer")

    authority = {
        field: context[field]
        for field in (
            "context_only",
            "executable",
            "executable_quote",
            "real_money_eligible",
            "can_authorize_order",
            "request_time_collection",
            "request_time_model_fitting",
            "request_time_network",
            "request_time_notary",
            "request_time_broker",
        )
    }
    return ProjectedContext(
        source_schema=SEICHE_SCHEMA,
        as_of=evidence_at,
        knowledge_time=snapshot_at,
        native_expires_at=None,
        rights_status="metadata_only",
        limitations=limitations,
        facts={
            "regime": regime,
            "stress_index": stress_index,
            "coverage_pct": coverage,
            "fault_count": fault_count,
            "source_snapshot_version": version,
            "staleness": staleness,
            "clocks": clocks,
            "attestation": attestation,
            "authority": authority,
            "projection_sha256": projection_sha,
            "gateway_binding": _binding(
                request_hash=request_hash,
                source_schema=SEICHE_SCHEMA,
                native_context_sha256=projection_sha,
            ),
        },
    )


def _venue_number_map(value: Any, label: str) -> dict[str, float]:
    mapping = _exact_object(value, label, VENUE_SET)
    return {
        venue: _finite(mapping[venue], f"{label}_{venue}", minimum=0)
        for venue in VENUES
    }


def _venue_timestamp_map(value: Any, label: str) -> dict[str, datetime]:
    mapping = _exact_object(value, label, VENUE_SET)
    return {venue: _timestamp(mapping[venue], f"{label}_{venue}") for venue in VENUES}


def _venue_sha_map(value: Any, label: str) -> None:
    mapping = _exact_object(value, label, VENUE_SET)
    for venue in VENUES:
        _sha256(mapping[venue], f"{label}_{venue}")


def _empty_venue_list(value: Any, label: str) -> None:
    if value != []:
        raise NativeContractError(f"{label}_not_empty")


def _validate_undertow_authority(value: Any, mode: str) -> dict[str, Any]:
    keys = frozenset(
        {
            "state",
            "mode",
            "paper_only",
            "execution_authority",
            "can_authorize_order",
            "can_route_order",
            "can_place_order",
            "can_modify_order",
            "can_cancel_order",
            "can_clear_other_controls",
            "can_increase_risk",
            "executable_quote",
            "real_money_eligible",
        }
    )
    authority = _exact_object(value, "undertow_authority", keys)
    if authority["state"] != "context_only" or authority["mode"] != mode:
        raise NativeContractError("undertow_authority_identity_mismatch")
    if authority["paper_only"] is not True:
        raise NativeContractError("undertow_paper_only_boundary_missing")
    for field in keys - {"state", "mode", "paper_only"}:
        if authority[field] is not False:
            raise NativeContractError("undertow_authority_mismatch")
    return authority


def _validate_undertow_rights(
    value: Any, *, knowledge_at: datetime, retrieved_at: datetime
) -> dict[str, Any]:
    keys = frozenset(
        {
            "status",
            "manifest",
            "manifest_schema",
            "manifest_version",
            "reviewed_by",
            "reviewed_at",
            "valid_from",
            "valid_until",
            "raw_sha256",
            "canonical_sha256",
            "pit_input_sha256",
            "scope",
            "raw_order_books_included",
            "redistribution",
            "venue_states",
            "venue_reviewed_at_by_venue",
            "venue_proof_sha256_by_venue",
        }
    )
    rights = _exact_object(value, "undertow_rights", keys)
    if (
        rights["status"] != "approved"
        or rights["manifest"] != UNDERTOW_RIGHTS_MANIFEST
        or rights["manifest_schema"] != UNDERTOW_RIGHTS_SCHEMA
        or not isinstance(rights["manifest_version"], str)
        or _RIGHTS_VERSION_RE.fullmatch(rights["manifest_version"]) is None
        or rights["scope"] != "derived_metadata_only"
        or rights["raw_order_books_included"] is not False
        or rights["redistribution"] != "derived_metrics_only"
    ):
        raise NativeContractError("undertow_rights_state_mismatch")
    _text(rights["reviewed_by"], "undertow_rights_reviewer")
    for field in ("raw_sha256", "canonical_sha256", "pit_input_sha256"):
        _sha256(rights[field], f"undertow_rights_{field}")
    reviewed_at = _timestamp(rights["reviewed_at"], "undertow_rights_reviewed_at")
    valid_from = _timestamp(rights["valid_from"], "undertow_rights_valid_from")
    valid_until = _timestamp(rights["valid_until"], "undertow_rights_valid_until")
    if (
        reviewed_at > knowledge_at
        or valid_from > knowledge_at
        or valid_from >= valid_until
        or retrieved_at >= valid_until
    ):
        raise NativeContractError("undertow_rights_clock_mismatch")
    states = _exact_object(rights["venue_states"], "undertow_rights_states", VENUE_SET)
    if any(states[venue] != "approved" for venue in VENUES):
        raise NativeContractError("undertow_venue_rights_not_approved")
    venue_reviewed = _venue_timestamp_map(
        rights["venue_reviewed_at_by_venue"], "undertow_venue_rights_reviewed_at"
    )
    if (
        max(venue_reviewed.values()) != reviewed_at
        or any(
            clock > knowledge_at or clock > reviewed_at
            for clock in venue_reviewed.values()
        )
    ):
        raise NativeContractError("undertow_venue_rights_clock_mismatch")
    _venue_sha_map(
        rights["venue_proof_sha256_by_venue"], "undertow_venue_rights_proof"
    )
    return rights


def _validate_undertow_coverage(value: Any) -> dict[str, Any]:
    keys = frozenset(
        {
            "state",
            "expected_venues",
            "priced_venues",
            "unreachable_venues",
            "unable_at_observed_depth",
            "uncovered_at_required_band",
            "conversion_unavailable_venues",
            "missing_venues",
            "source_lower_bound_note",
            "depth_coverage_by_venue",
        }
    )
    coverage = _exact_object(value, "undertow_coverage", keys)
    if (
        coverage["state"] != "complete"
        or coverage["expected_venues"] != list(VENUES)
        or coverage["priced_venues"] != list(VENUES)
        or coverage["source_lower_bound_note"] is not None
    ):
        raise NativeContractError("undertow_coverage_incomplete")
    for field in (
        "unreachable_venues",
        "unable_at_observed_depth",
        "uncovered_at_required_band",
        "conversion_unavailable_venues",
        "missing_venues",
    ):
        _empty_venue_list(coverage[field], f"undertow_{field}")
    depth = _exact_object(
        coverage["depth_coverage_by_venue"], "undertow_depth_coverage", VENUE_SET
    )
    depth_keys = frozenset(
        {
            "side",
            "required_band",
            "covers_required_band",
            "covers_1pct_bid",
            "covers_2pct_bid",
            "span_below",
            "depth_1pct_bid_quote",
            "depth_2pct_bid_quote",
            "within_observed_depth",
        }
    )
    for venue in VENUES:
        item = _exact_object(depth[venue], f"undertow_depth_{venue}", depth_keys)
        depth_1 = _finite(item["depth_1pct_bid_quote"], "undertow_depth_1", minimum=0)
        depth_2 = _finite(item["depth_2pct_bid_quote"], "undertow_depth_2", minimum=0)
        span = _finite(item["span_below"], "undertow_depth_span", minimum=0)
        if (
            depth_1 <= 0
            or depth_2 <= 0
            or item["side"] != "bid"
            or item["required_band"] not in {"1pct", "2pct"}
            or item["covers_required_band"] is not True
            or item["within_observed_depth"] is not True
            or item["covers_1pct_bid"] is not (span >= 0.01)
            or item["covers_2pct_bid"] is not (span >= 0.02)
            or depth_2 < depth_1
        ):
            raise NativeContractError("undertow_depth_coverage_mismatch")
    return coverage


def _validate_undertow_peg(value: Any) -> tuple[dict[str, Any], datetime, float]:
    keys = frozenset(
        {
            "state",
            "pair",
            "source",
            "price",
            "deviation",
            "warn_threshold",
            "depeg_flag",
            "observation_at",
        }
    )
    peg = _exact_object(value, "undertow_peg", keys)
    price = _finite(peg["price"], "undertow_peg_price", minimum=0)
    deviation = _finite(peg["deviation"], "undertow_peg_deviation", minimum=0)
    if (
        price <= 0
        or peg["state"] != "within_threshold"
        or peg["pair"] != "USDT/USD"
        or peg["source"] != "coinbase USDT-USD ticker"
        or peg["warn_threshold"] != UNDERTOW_DEPEG_THRESHOLD
        or peg["depeg_flag"] is not False
        or not math.isclose(deviation, abs(1.0 - price), rel_tol=0, abs_tol=1e-12)
        or deviation > UNDERTOW_DEPEG_THRESHOLD
    ):
        raise NativeContractError("undertow_peg_mismatch")
    return peg, _timestamp(peg["observation_at"], "undertow_peg_observation"), price


def _validate_undertow_measurement(
    value: Any,
    *,
    expected_request: dict[str, Any],
    peg_price: float,
    coverage: dict[str, Any],
) -> dict[str, Any]:
    keys = frozenset(
        {
            "instrument",
            "asset",
            "side",
            "venue",
            "requested_size_usd",
            "published_rung_used_usd",
            "estimator",
            "quote_conversion_by_venue",
            "sell_cost_bps_by_venue",
            "sell_cost_usd_by_venue",
            "best",
            "worst",
            "venue_spread_bps",
            "venue_spread_usd",
        }
    )
    measurement = _exact_object(value, "undertow_measurement", keys)
    size = _finite(
        measurement["requested_size_usd"], "undertow_requested_size", minimum=0
    )
    rung = _finite(
        measurement["published_rung_used_usd"], "undertow_published_rung", minimum=0
    )
    if (
        measurement["instrument"] != "BTC/USD"
        or measurement["asset"] != "BTC"
        or measurement["side"] != "sell"
        or measurement["venue"] is not None
        or measurement["estimator"] != "band_interpolation_v1"
        or size <= 0
        or rung <= 0
        or size != float(expected_request["requested_size_usd"])
        or rung != size
        or rung not in RUNGS_USD
    ):
        raise NativeContractError("undertow_measurement_identity_mismatch")

    costs_bps = _venue_number_map(
        measurement["sell_cost_bps_by_venue"], "undertow_sell_cost_bps"
    )
    costs_usd = _venue_number_map(
        measurement["sell_cost_usd_by_venue"], "undertow_sell_cost_usd"
    )
    conversions = _exact_object(
        measurement["quote_conversion_by_venue"],
        "undertow_quote_conversion",
        VENUE_SET,
    )
    conversion_keys = frozenset(
        {"quote_currency", "state", "usd_per_quote", "requested_notional_quote"}
    )
    for venue in VENUES:
        conversion = _exact_object(
            conversions[venue], f"undertow_conversion_{venue}", conversion_keys
        )
        quote = VENUE_QUOTES[venue]
        expected_price = peg_price if quote == "USDT" else 1.0
        expected_state = "bound_usdt_usd" if quote == "USDT" else "identity"
        usd_per_quote = _finite(
            conversion["usd_per_quote"], "undertow_usd_per_quote", minimum=0
        )
        quote_notional = _finite(
            conversion["requested_notional_quote"],
            "undertow_quote_notional",
            minimum=0,
        )
        depth = coverage["depth_coverage_by_venue"][venue]
        depth_1 = float(depth["depth_1pct_bid_quote"])
        depth_2 = float(depth["depth_2pct_bid_quote"])
        expected_band = "1pct" if quote_notional <= depth_1 else "2pct"
        if (
            usd_per_quote <= 0
            or quote_notional <= 0
            or conversion["quote_currency"] != quote
            or conversion["state"] != expected_state
            or not math.isclose(usd_per_quote, expected_price, rel_tol=0, abs_tol=1e-12)
            or not math.isclose(
                quote_notional,
                size / expected_price,
                rel_tol=1e-12,
                abs_tol=1e-6,
            )
            or depth["required_band"] != expected_band
            or depth["within_observed_depth"] is not (quote_notional <= depth_2)
        ):
            raise NativeContractError("undertow_quote_conversion_mismatch")
        expected_usd = round(size * costs_bps[venue] / 10_000, 2)
        if not math.isclose(costs_usd[venue], expected_usd, rel_tol=0, abs_tol=1e-9):
            raise NativeContractError("undertow_usd_cost_mismatch")

    best_venue = min(VENUES, key=lambda venue: (costs_bps[venue], venue))
    worst_venue = max(VENUES, key=lambda venue: (costs_bps[venue], venue))
    summary_keys = frozenset({"venue", "sell_cost_bps", "sell_cost_usd"})
    best = _exact_object(measurement["best"], "undertow_best", summary_keys)
    worst = _exact_object(measurement["worst"], "undertow_worst", summary_keys)
    if best != {
        "venue": best_venue,
        "sell_cost_bps": costs_bps[best_venue],
        "sell_cost_usd": costs_usd[best_venue],
    } or worst != {
        "venue": worst_venue,
        "sell_cost_bps": costs_bps[worst_venue],
        "sell_cost_usd": costs_usd[worst_venue],
    }:
        raise NativeContractError("undertow_cost_summary_mismatch")
    spread_bps = costs_bps[worst_venue] - costs_bps[best_venue]
    spread_usd = round(size * spread_bps / 10_000, 2)
    if not math.isclose(
        _finite(measurement["venue_spread_bps"], "undertow_spread_bps", minimum=0),
        spread_bps,
        rel_tol=0,
        abs_tol=1e-12,
    ) or not math.isclose(
        _finite(measurement["venue_spread_usd"], "undertow_spread_usd", minimum=0),
        spread_usd,
        rel_tol=0,
        abs_tol=1e-9,
    ):
        raise NativeContractError("undertow_venue_spread_mismatch")
    return measurement


def parse_undertow_context(
    payload: dict[str, Any],
    *,
    expected_request: dict[str, Any],
    request_hash: str,
    retrieved_at: datetime,
    max_age_seconds: int,
    source_url: str,
) -> ProjectedContext:
    """Validate and retain one request-bound Undertow hosted-MCP response."""

    context = _exact_object(payload, "undertow_context", _UNDERTOW_ROOT_KEYS)
    context_sha = _sha256(context["context_sha256"], "undertow_context")
    if context_sha != _canonical_sha(context, "context_sha256"):
        raise NativeContractError("undertow_context_digest_mismatch")
    if (
        context["schema"] != UNDERTOW_SCHEMA
        or context["schema_url"] != UNDERTOW_SCHEMA_URL
    ):
        raise NativeContractError("undertow_identity_mismatch")
    if context["status"] != "available":
        raise NativeContractError("undertow_reported_unavailable")
    if context["reason"] is not None or context["evidence_class"] != "derived":
        raise NativeContractError("undertow_state_mismatch")
    if (
        context["request_hash"] != request_hash
        or context["request"] != expected_request
    ):
        raise NativeContractError("undertow_request_binding_mismatch")

    authority = _validate_undertow_authority(
        context["authority"], expected_request["mode"]
    )
    source = _exact_object(
        context["source"],
        "undertow_source",
        frozenset(
            {
                "url",
                "pack",
                "source_schema",
                "raw_sha256",
                "canonical_sha256",
                "pit_input_sha256",
                "deployed_sha",
            }
        ),
    )
    if (
        source["url"] != source_url
        or source["pack"] != "crypto_desk.json"
        or source["source_schema"] != UNDERTOW_PACK_SCHEMA
    ):
        raise NativeContractError("undertow_source_identity_mismatch")
    for field in ("raw_sha256", "canonical_sha256", "pit_input_sha256"):
        _sha256(source[field], f"undertow_source_{field}")
    _git_sha(source["deployed_sha"], "undertow_source_deployed_sha")

    pit = _exact_object(
        context["pit"],
        "undertow_pit",
        frozenset(
            {
                "state",
                "board_content_sha256",
                "ledger",
                "key",
                "revision",
                "record_hash",
                "chain_verified",
                "head_verified",
            }
        ),
    )
    if (
        pit["state"] != "verified"
        or pit["ledger"] != UNDERTOW_LEDGER
        or pit["chain_verified"] is not True
        or pit["head_verified"] is not True
    ):
        raise NativeContractError("undertow_pit_not_verified")
    _sha256(pit["board_content_sha256"], "undertow_board_content")
    _text(pit["key"], "undertow_pit_key")
    _integer(pit["revision"], "undertow_pit_revision", minimum=1)
    _sha256(pit["record_hash"], "undertow_pit_record")

    clocks = _exact_object(
        context["clocks"],
        "undertow_clocks",
        frozenset(
            {
                "observation_at",
                "oldest_observation_at",
                "venue_observation_at_by_venue",
                "max_observation_skew_seconds",
                "max_observation_skew_allowed_seconds",
                "knowledge_at",
                "retrieved_at",
                "expires_at",
            }
        ),
    )
    observation_at = _timestamp(clocks["observation_at"], "undertow_observation")
    oldest_at = _timestamp(
        clocks["oldest_observation_at"], "undertow_oldest_observation"
    )
    venue_clocks = _venue_timestamp_map(
        clocks["venue_observation_at_by_venue"], "undertow_venue_observation"
    )
    knowledge_at = _timestamp(clocks["knowledge_at"], "undertow_knowledge")
    upstream_retrieved = _timestamp(clocks["retrieved_at"], "undertow_retrieved")
    expires_at = _timestamp(clocks["expires_at"], "undertow_expires")
    peg, peg_clock, peg_price = _validate_undertow_peg(context["peg"])
    all_observations = [*venue_clocks.values(), peg_clock]
    expected_oldest = min(all_observations)
    expected_newest = max(all_observations)
    skew = (expected_newest - expected_oldest).total_seconds()
    if (
        oldest_at != expected_oldest
        or observation_at != expected_newest
        or not (
            oldest_at
            <= observation_at
            <= knowledge_at
            <= upstream_retrieved
            <= retrieved_at
        )
        or expires_at != oldest_at + UNDERTOW_MAX_OBSERVATION_AGE
        or retrieved_at >= expires_at
        or clocks["max_observation_skew_allowed_seconds"]
        != UNDERTOW_MAX_OBSERVATION_SKEW_SECONDS
        or not math.isclose(
            _finite(
                clocks["max_observation_skew_seconds"],
                "undertow_observation_skew",
                minimum=0,
            ),
            skew,
            rel_tol=0,
            abs_tol=1e-9,
        )
        or skew > UNDERTOW_MAX_OBSERVATION_SKEW_SECONDS
    ):
        raise NativeContractError("undertow_clock_mismatch_or_stale")
    _fresh(oldest_at, retrieved_at, max_age_seconds)

    rights = _validate_undertow_rights(
        context["rights"], knowledge_at=knowledge_at, retrieved_at=retrieved_at
    )
    coverage = _validate_undertow_coverage(context["coverage"])
    measurement = _validate_undertow_measurement(
        context["measurement"],
        expected_request=expected_request,
        peg_price=peg_price,
        coverage=coverage,
    )
    limitations = _strings(context["limitations"], "undertow_limitations")
    if not {
        "not_an_executable_quote_or_broker_preview",
        "context_cannot_clear_another_trade_safety_control",
    } <= set(limitations):
        raise NativeContractError("undertow_safety_limitation_missing")

    return ProjectedContext(
        source_schema=UNDERTOW_SCHEMA,
        as_of=oldest_at,
        knowledge_time=knowledge_at,
        native_expires_at=expires_at,
        rights_status="metadata_only",
        limitations=limitations,
        facts={
            "requested_size_usd": measurement["requested_size_usd"],
            "published_rung_used_usd": measurement["published_rung_used_usd"],
            "worst_sell_cost_bps": measurement["worst"]["sell_cost_bps"],
            "venue_spread_bps": measurement["venue_spread_bps"],
            "native_request": context["request"],
            "measurement": measurement,
            "coverage": coverage,
            "peg": peg,
            "source": source,
            "pit": pit,
            "clocks": clocks,
            "rights": rights,
            "authority": authority,
            "context_sha256": context_sha,
            "gateway_binding": _binding(
                request_hash=request_hash,
                source_schema=UNDERTOW_SCHEMA,
                native_context_sha256=context_sha,
            ),
        },
    )


__all__ = [
    "REGIMES",
    "RUNGS_USD",
    "SEICHE_SCHEMA",
    "UNDERTOW_SCHEMA",
    "VENUES",
    "NativeContractError",
    "ProjectedContext",
    "parse_seiche_context",
    "parse_undertow_context",
]
