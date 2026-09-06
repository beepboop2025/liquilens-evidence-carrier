"""Pure, private paper projections; the public gateway/composite are unchanged.

``liquilens.paper-funding-exit.v1`` uses at most eight days of actual daily
funding / weekly commercial-paper observations. This is a separately versioned
operator cadence rule, not a change to the public LiquiLens one-day policy.
The funding bands are experimental operator rules, not calibrated forecasts:
pressure = max(SOFR-IORB, abs(EFFR-IORB)); <=5 CALM, >5 EROSION, >15 STRAIN,
>25 STRESS (basis points). No trade authority is issued by these projectors.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

POLICY_ID = "liquilens.paper-funding-exit.v1"
FUNDING_PROFILE_ID = "liquilens.private-paper.usd-funding.v1"
CORPORATE_PROFILE_ID = "liquilens.private-paper.corporate-research.v1"
FUNDING_URL = "https://api.seiche.info/api/money-markets"
CORPORATE_URL = "https://api.liquilens.in/api/public-signals/corporate-transmission"
MAX_BYTES = 1_048_576
MAX_AGE_SECONDS = 8 * 24 * 60 * 60
LOCAL_TTL_SECONDS = 60
FUNDING_BANDS_BP = (5, 15, 25)
# Native cards round each raw rate to 3 decimals of percent and the spread to
# 3 decimals of bp. The maximum combined displayed rounding error is .1005 bp.
# Classification uses the more conservative of displayed and recomputed values.
ALGEBRA_TOLERANCE_BP = Decimal("0.1005")
_STATES = {"CALM", "WATCH", "ALARM"}
_CHANNELS = {
    "cp_market",
    "credit_lines",
    "real_economy",
    "balance_sheet",
    "trade_credit",
    "primary_issuance",
}


class FundingScopeError(ValueError):
    """Native bytes cannot establish the stated private paper scope."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise FundingScopeError(reason)


def _object(value: Any, label: str) -> dict[str, Any]:
    _require(isinstance(value, dict), f"{label}: object required")
    return value


def _text(value: Any, label: str, *, limit: int = 512) -> str:
    _require(
        isinstance(value, str) and 0 < len(value) <= limit,
        f"{label}: bounded text required",
    )
    return value


def _number(value: Any, label: str) -> float:
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    _require(
        finite,
        f"{label}: finite number required",
    )
    return float(value)


def _positive_int(value: Any, label: str) -> int:
    _require(type(value) is int and value > 0, f"{label}: positive count required")
    return value


def _member(value: Any, choices: set[str]) -> bool:
    return isinstance(value, str) and value in choices


def _utc(value: datetime, label: str) -> datetime:
    _require(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None,
        f"{label}: aware clock required",
    )
    return value.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _date(value: Any, label: str) -> datetime:
    _require(
        isinstance(value, str)
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is not None,
        f"{label}: observation date required",
    )
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as error:
        raise FundingScopeError(f"{label}: invalid date") from error


def _timestamp(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(_text(value, label).replace("Z", "+00:00"))
    except ValueError as error:
        raise FundingScopeError(f"{label}: invalid timestamp") from error
    return _utc(parsed, label)


def _load(raw: bytes) -> dict[str, Any]:
    _require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_BYTES, "body byte budget")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            _require(key not in result, "duplicate JSON key")
            result[key] = value
        return result

    def finite(value: str) -> float:
        result = float(value)
        _require(math.isfinite(result), "nonfinite JSON number")
        return result

    def constant(value: str) -> None:
        raise FundingScopeError("nonfinite JSON constant")

    try:
        return _object(
            json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=pairs,
                parse_float=finite,
                parse_constant=constant,
            ),
            "native response",
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise FundingScopeError("invalid strict JSON response") from error


def _bindings(
    request_hash: str,
    retrieved_at: datetime,
    request_expires_at: datetime,
    max_age_seconds: int,
) -> tuple[datetime, datetime]:
    _require(
        isinstance(request_hash, str)
        and re.fullmatch(r"[0-9a-f]{64}", request_hash) is not None,
        "canonical request hash required",
    )
    now = _utc(retrieved_at, "retrieval")
    expiry = _utc(request_expires_at, "request expiry")
    _require(expiry > now, "request already expired")
    _require(
        type(max_age_seconds) is int and 0 < max_age_seconds <= MAX_AGE_SECONDS,
        "private cadence must be positive and at most eight days",
    )
    return now, expiry


def _fresh(as_of: datetime, now: datetime, maximum: int, label: str) -> None:
    _require(as_of <= now, f"{label}: future observation")
    _require((now - as_of).total_seconds() < maximum, f"{label}: stale observation")


def _indexed(value: Any, label: str) -> dict[str, dict[str, Any]]:
    _require(isinstance(value, list) and len(value) <= 256, f"{label}: bounded list")
    result: dict[str, dict[str, Any]] = {}
    for entry in value:
        row = _object(entry, label)
        identity = _text(row.get("id"), label)
        _require(identity not in result, f"{label}: duplicate identity")
        result[identity] = row
    return result


def _section(
    *,
    product: str,
    raw: bytes,
    request_hash: str,
    now: datetime,
    expiry: datetime,
    as_of: datetime,
    maximum: int,
    facts: dict[str, Any],
    limitations: list[str],
) -> dict[str, Any]:
    expires = min(
        expiry,
        now + timedelta(seconds=LOCAL_TTL_SECONDS),
        as_of + timedelta(seconds=maximum),
    )
    _require(expires > now, "scope already expired")
    return {
        "product": product,
        "request_hash": request_hash,
        "state": "context_only",
        "evidence_class": "derived" if product == "seiche" else "research",
        "source_url": FUNDING_URL if product == "seiche" else CORPORATE_URL,
        "source_schema": FUNDING_PROFILE_ID
        if product == "seiche"
        else CORPORATE_PROFILE_ID,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "as_of": _utc_text(as_of),
        "knowledge_time": _utc_text(now),
        "retrieved_at": _utc_text(now),
        "expires_at": _utc_text(expires),
        "rights_status": "metadata_only",
        "real_money_eligible": False,
        "executable_quote": False,
        "limitations": [
            "private_paper_profile_only_no_trade_or_real_money_authority",
            "retrieval_is_local_knowledge_time_not_native_publication_time",
            "date_only_observations_use_UTC_midnight_without_clock_refresh",
            "local_expiry_does_not_extend_source_observation_eligibility",
            *limitations,
        ],
        "facts": {"policy_id": POLICY_ID, "max_age_seconds": maximum, **facts},
    }


def parse_funding_scope(
    raw: bytes,
    request_hash: str,
    retrieved_at: datetime,
    request_expires_at: datetime,
    max_age_seconds: int,
) -> dict[str, Any]:
    """Validate five native cards and three sources before projecting funding."""
    now, expiry = _bindings(
        request_hash, retrieved_at, request_expires_at, max_age_seconds
    )
    payload = _load(raw)
    _require(
        payload.get("schema") == "seiche.money-market-desk.v1"
        and payload.get("ok") is True
        and payload.get("context_only") is True,
        "money-market native schema/context boundary",
    )
    section = _indexed(payload.get("sections"), "sections").get("policy_corridor")
    metrics = _indexed(_object(section, "policy corridor").get("metrics"), "metrics")
    sources = _indexed(payload.get("source_metadata"), "source metadata")
    rates: dict[str, dict[str, Any]] = {}
    for name, series in (("SOFR", "SOFR"), ("EFFR", "EFFR"), ("IORB", "IORB/IOER")):
        key = name.lower()
        card = _object(metrics.get(f"policy.{key}"), name)
        source = _object(sources.get(f"fred_{key}"), f"{name} source")
        _require(
            card.get("status") == "available"
            and card.get("unit") == "%"
            and card.get("cadence") == "daily"
            and _member(card.get("freshness"), {"fresh", "aging"}),
            f"{name}: unavailable or incompatible rate",
        )
        as_of = _date(card.get("asof"), name)
        _fresh(as_of, now, max_age_seconds, name)
        _require(
            source.get("series") == series
            and source.get("available") is True
            and source.get("cadence") == "daily"
            and source.get("asof") == card["asof"]
            and _member(source.get("freshness"), {"fresh", "aging"}),
            f"{name}: source clock/identity mismatch",
        )
        _require(_date(source.get("coverage_start"), name) <= as_of, "source coverage")
        rates[name] = {
            "metric_id": card["id"],
            "value": _number(card.get("value"), name),
            "unit": "%",
            "asof": card["asof"],
            "source_id": source["id"],
            "series": series,
            "publisher": _text(source.get("publisher"), "publisher"),
            "source": _text(card.get("source"), "card source"),
            "coverage_start": source["coverage_start"],
            "observations": _positive_int(source.get("observations"), name),
            "native_freshness": card["freshness"],
        }
    _require(
        len({item["asof"] for item in rates.values()}) == 1, "raw rate dates differ"
    )
    observation = _date(rates["SOFR"]["asof"], "funding observation")
    spreads: dict[str, dict[str, Any]] = {}
    for numerator in ("SOFR", "EFFR"):
        identity = f"policy.{numerator.lower()}_minus_iorb"
        card = _object(metrics.get(identity), identity)
        alignment = _object(card.get("alignment"), "spread alignment")
        inputs = {name: rates[name]["asof"] for name in (numerator, "IORB")}
        counts = {name: rates[name]["observations"] for name in inputs}
        _require(
            card.get("status") == "available"
            and card.get("unit") == "bp"
            and card.get("cadence") == "daily"
            and _member(card.get("freshness"), {"fresh", "aging"})
            and card.get("asof") == rates[numerator]["asof"]
            and alignment.get("method") == "exact_date_inner_join"
            and alignment.get("no_forward_fill") is True
            and alignment.get("input_asof") == inputs
            and alignment.get("input_observations") == counts
            and alignment.get("latest_common_asof") == card["asof"],
            f"{identity}: exact join proof mismatch",
        )
        observed_counts = _object(alignment.get("input_observations"), "input counts")
        for count in observed_counts.values():
            _positive_int(count, "input count")
        overlap = _positive_int(alignment.get("overlap_observations"), "overlap")
        _require(overlap <= min(counts.values()), "overlap exceeds input count")
        value = _number(card.get("value"), identity)
        computed = 100 * (
            Decimal(str(rates[numerator]["value"]))
            - Decimal(str(rates["IORB"]["value"]))
        )
        _require(
            abs(Decimal(str(value)) - computed) <= ALGEBRA_TOLERANCE_BP,
            "spread algebra mismatch",
        )
        spreads[numerator] = {
            "metric_id": identity,
            "value_bp": value,
            "recomputed_bp": float(computed),
            "asof": card["asof"],
            "alignment": {
                "method": "exact_date_inner_join",
                "no_forward_fill": True,
                "input_asof": inputs,
                "input_observations": counts,
                "latest_common_asof": card["asof"],
                "overlap_observations": overlap,
            },
        }
    capture: dict[str, str] = {}
    for key in ("snapshot_generated_at", "served_at"):
        if key in payload:
            stamp = _timestamp(payload[key], key)
            _fresh(stamp, now, max_age_seconds, key)
            _require(stamp >= observation, f"{key}: predates inputs")
            capture[key] = payload[key]
    if len(capture) == 2:
        _require(
            _timestamp(capture["snapshot_generated_at"], "snapshot")
            <= _timestamp(capture["served_at"], "served"),
            "snapshot follows serving",
        )
    if "freshness" in payload:
        freshness = _object(payload["freshness"], "native freshness")
        evaluation = _date(freshness.get("evaluation_asof"), "evaluation date")
        _require(
            evaluation.date() == now.date(), "native evaluation date is not current"
        )
        capture["evaluation_asof"] = freshness["evaluation_asof"]
    pressure = max(
        spreads["SOFR"]["value_bp"],
        spreads["SOFR"]["recomputed_bp"],
        abs(spreads["EFFR"]["value_bp"]),
        abs(spreads["EFFR"]["recomputed_bp"]),
    )
    regime = ("CALM", "EROSION", "STRAIN", "STRESS")[
        sum(pressure > band for band in FUNDING_BANDS_BP)
    ]
    return _section(
        product="seiche",
        raw=raw,
        request_hash=request_hash,
        now=now,
        expiry=expiry,
        as_of=observation,
        maximum=max_age_seconds,
        facts={
            "profile_id": FUNDING_PROFILE_ID,
            "native_schema": payload["schema"],
            "scope": (
                "USD policy corridor: SOFR, EFFR, IORB and their exact-date spreads"
            ),
            "regime": regime,
            "regime_basis": (
                "experimental operator max(SOFR-IORB, abs(EFFR-IORB)); "
                "not Seiche composite"
            ),
            "regime_thresholds_bp": {
                "calm_max": 5,
                "erosion_max": 15,
                "strain_max": 25,
            },
            "pressure_bp": pressure,
            "sofr_minus_iorb_bp": spreads["SOFR"]["value_bp"],
            "effr_minus_iorb_bp": spreads["EFFR"]["value_bp"],
            "algebra_rounding_tolerance_bp": float(ALGEBRA_TOLERANCE_BP),
            "inputs": rates,
            "spreads": spreads,
            "capture": capture,
        },
        limitations=[
            "experimental_operator_bands_not_calibrated_or_optimized_return_forecasts",
            "full_Seiche_composite_regime_and_historical_calibration_not_projected",
            "displayed_rate_rounding_bound_checked_classification_uses_conservative_value",
        ],
    )


def _research_channels(channels: dict[str, Any], now: datetime) -> dict[str, Any]:
    result = {}
    for name in sorted(_CHANNELS):
        channel = _object(channels[name], name)
        _require(
            type(channel.get("available")) is bool, "channel availability required"
        )
        state = channel.get("state")
        _require(state is None or _member(state, _STATES), "unknown research state")
        record: dict[str, Any] = {"available": channel["available"], "state": state}
        for key in ("stale", "declared_dark", "withheld"):
            if key in channel:
                _require(type(channel[key]) is bool, f"channel {key} must be boolean")
                record[key] = channel[key]
        if "as_of" in channel:
            value = channel["as_of"]
            if value is not None:
                _require(_date(value, name) <= now, "future research channel")
            record["as_of"] = value
        legs = _object(channel.get("legs", {}), "research legs")
        _require(len(legs) <= 16, "research leg budget")
        record["legs"] = {}
        for leg_name, value in legs.items():
            _text(leg_name, "leg name", limit=64)
            leg = _object(value, leg_name)
            # Preserve concise scalars, including null values, native periods and
            # withholding flags. No undocumented nested context or prose is copied.
            _require(len(leg) <= 32, "research scalar budget")
            selected = {}
            for key, scalar in leg.items():
                _text(key, "leg scalar name", limit=64)
                if scalar is None or type(scalar) in (bool, int, float):
                    if type(scalar) in (int, float):
                        _number(scalar, key)
                    selected[key] = scalar
                elif isinstance(scalar, str):
                    selected[key] = _text(scalar, key)
                else:
                    raise FundingScopeError("unexpected nested research leg")
            if leg.get("as_of") is not None:
                _require(_date(leg["as_of"], leg_name) <= now, "future research leg")
            record["legs"][leg_name] = selected
        result[name] = record
    return result


def parse_corporate_research(
    raw: bytes,
    request_hash: str,
    retrieved_at: datetime,
    request_expires_at: datetime,
    max_age_seconds: int,
) -> dict[str, Any]:
    """Require current CP legs; retain other channel periods as research only."""
    now, expiry = _bindings(
        request_hash, retrieved_at, request_expires_at, max_age_seconds
    )
    payload = _load(raw)
    _require(
        type(payload.get("schema_version")) is int
        and payload["schema_version"] == 2
        and type(payload.get("method_version")) is int
        and payload["method_version"] == 2
        and payload.get("available") is True,
        "corporate native schema/availability mismatch",
    )
    historical = _object(payload.get("historical_evidence"), "historical boundary")
    _require(
        historical.get("real_money_eligible") is False
        and historical.get("validated_backtest_eligible") is False,
        "corporate historical boundary mismatch",
    )
    channels = _object(payload.get("channels"), "corporate channels")
    _require(set(channels) == _CHANNELS, "corporate channel schema changed")
    research = _research_channels(channels, now)
    cp = channels["cp_market"]
    _require(
        cp.get("available") is True
        and cp.get("stale") is False
        and cp.get("withheld", False) is False
        and _member(cp.get("state"), _STATES),
        "CP channel unavailable/stale",
    )
    legs = _object(cp.get("legs"), "CP legs")
    clocks = []
    for name in ("spread", "rollover"):
        leg = _object(legs.get(name), f"CP {name}")
        _require(
            _member(leg.get("state"), _STATES)
            and leg.get("stale", False) is False
            and leg.get("withheld", False) is False,
            f"CP {name}: unavailable/stale/withheld",
        )
        stamp = _date(leg.get("as_of"), f"CP {name}")
        _fresh(stamp, now, max_age_seconds, f"CP {name}")
        clocks.append(stamp)
    _require(
        _date(cp.get("as_of"), "CP as_of") == max(clocks), "CP aggregate clock mismatch"
    )
    spread = _number(legs["spread"].get("spread_bp"), "CP spread bp")
    rollover = _number(legs["rollover"].get("chg_8w_pct"), "CP eight-week change")
    outstanding = _number(legs["rollover"].get("outstanding_bn"), "CP outstanding")
    _require(outstanding > 0 and rollover >= -100, "CP rollover value inconsistent")
    available = sum(channel["available"] for channel in research.values())
    coverage = f"{available}/6 channels readable"
    _require(payload.get("coverage") == coverage, "corporate coverage mismatch")
    counts = _object(payload.get("counts"), "corporate counts")
    expected = {
        state: sum(
            row["available"] and row["state"] == state for row in research.values()
        )
        for state in _STATES
    }
    _require(
        set(counts) == _STATES
        and all(type(value) is int for value in counts.values())
        and counts == expected,
        "corporate state counts mismatch",
    )
    cannot_see = _object(payload.get("cannot_see"), "dark channels")
    _require(set(cannot_see) <= _CHANNELS, "unknown dark channel")
    dark = {
        name: _text(reason, "coverage limitation", limit=1024)
        for name, reason in cannot_see.items()
    }
    for name, row in research.items():
        if row.get("declared_dark") is True:
            _require(
                not row["available"] and name in dark, "dark channel coverage mismatch"
            )
    calibrated = _date(payload.get("calibrated"), "calibration date")
    review = _date(payload.get("calibration_review_by"), "calibration review")
    _require(calibrated <= now < review, "corporate method calibration review due")
    _require(_member(payload.get("regime"), _STATES), "corporate regime schema")
    aggregate = _date(payload.get("as_of"), "corporate aggregate as_of")
    channel_dates = [
        _date(row["as_of"], "channel as_of")
        for row in research.values()
        if row.get("as_of") is not None
    ]
    _require(
        channel_dates and aggregate == max(channel_dates),
        "corporate aggregate clock mismatch",
    )
    return _section(
        product="liquilens",
        raw=raw,
        request_hash=request_hash,
        now=now,
        expiry=expiry,
        as_of=min(clocks),
        maximum=max_age_seconds,
        facts={
            "profile_id": CORPORATE_PROFILE_ID,
            "native_schema_version": 2,
            "native_method_version": 2,
            "freshness_scope": (
                "current CP spread and rollover only; "
                "other legs are dated background research"
            ),
            "cp_spread_bp": spread,
            "cp_rollover_chg_8w_pct": rollover,
            "cp_outstanding_bn": outstanding,
            "cp_market_state": cp["state"],
            "cp_spread_as_of": legs["spread"]["as_of"],
            "cp_rollover_as_of": legs["rollover"]["as_of"],
            "coverage": coverage,
            "available_channels": available,
            "total_channels": 6,
            "native_counts": counts,
            "native_aggregate_as_of": payload["as_of"],
            "native_aggregate_regime_research_only": payload["regime"],
            "research_channels": research,
            "cannot_see": dark,
            "historical_evidence": {
                "real_money_eligible": False,
                "validated_backtest_eligible": False,
            },
            "calibrated": payload["calibrated"],
            "calibration_review_by": payload["calibration_review_by"],
        },
        limitations=[
            "aggregate_context_is_not_an_institution_or_instrument_rating",
            "older_quarterly_research_legs_do_not_share_the_current_CP_freshness_claim",
            "native_aggregate_as_of_is_latest_channel_not_all_inputs",
            "mechanism_diagnostics_are_not_validated_backtests",
            "private_daily_weekly_CP_eight_day_ceiling_does_not_change_public_policy",
        ],
    )
