from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from liquilens_evidence.trade_safety import _validate_evidence_section

from liquilens_trading_copilot.funding import (
    CORPORATE_PROFILE_ID,
    FUNDING_PROFILE_ID,
    MAX_AGE_SECONDS,
    MAX_BYTES,
    POLICY_ID,
    FundingScopeError,
    parse_corporate_research,
    parse_funding_scope,
)

NOW = datetime(2026, 9, 6, 9, 30, tzinfo=UTC)
REQUEST_HASH = "a" * 64


def encode(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()


def funding_payload(now: datetime = NOW) -> dict[str, Any]:
    """Synthetic native structure matching the public money-market v1 contract."""
    date = (now - timedelta(days=3)).date().isoformat()
    rates = {"SOFR": 3.66, "EFFR": 3.63, "IORB": 3.65}
    counts = {"SOFR": 2104, "EFFR": 2430, "IORB": 3533}
    metrics: list[dict[str, Any]] = []
    sources = []
    for name, value in rates.items():
        metrics.append(
            {
                "id": f"policy.{name.lower()}",
                "value": value,
                "unit": "%",
                "asof": date,
                "cadence": "daily",
                "status": "available",
                "freshness": "fresh",
                "source": f"Federal Reserve via FRED {name}",
            }
        )
        sources.append(
            {
                "id": f"fred_{name.lower()}",
                "series": name if name != "IORB" else "IORB/IOER",
                "asof": date,
                "available": True,
                "observations": counts[name],
                "coverage_start": "2018-04-03",
                "cadence": "daily",
                "freshness": "fresh",
                "publisher": "Federal Reserve via FRED",
            }
        )
    for name, value in (("SOFR", 1.0), ("EFFR", -2.0)):
        metrics.append(
            {
                "id": f"policy.{name.lower()}_minus_iorb",
                "value": value,
                "unit": "bp",
                "asof": date,
                "status": "available",
                "cadence": "daily",
                "freshness": "fresh",
                "alignment": {
                    "method": "exact_date_inner_join",
                    "no_forward_fill": True,
                    "input_asof": {name: date, "IORB": date},
                    "input_observations": {name: counts[name], "IORB": counts["IORB"]},
                    "latest_common_asof": date,
                    "overlap_observations": counts[name],
                },
            }
        )
    return {
        "schema": "seiche.money-market-desk.v1",
        "ok": True,
        "context_only": True,
        "asof": date,
        "regime": "NORMAL",
        "snapshot_generated_at": (now - timedelta(minutes=5)).isoformat(),
        "served_at": (now - timedelta(minutes=1)).isoformat(),
        "freshness": {"evaluation_asof": now.date().isoformat()},
        "sections": [{"id": "policy_corridor", "metrics": metrics}],
        "source_metadata": sources,
        # Old full-composite inputs are not silently relabeled as current funding.
        "unrelated_historical_calibration": {"asof": "2021-07-28"},
    }


def corporate_payload(now: datetime = NOW) -> dict[str, Any]:
    def date(age: int) -> str:
        return (now - timedelta(days=age)).date().isoformat()

    def channel(age: int, legs: dict[str, Any]) -> dict[str, Any]:
        return {
            "available": True,
            "state": "CALM",
            "stale": False,
            "as_of": date(age),
            "legs": legs,
        }

    return {
        "schema_version": 2,
        "method_version": 2,
        "available": True,
        "as_of": date(3),
        "coverage": "4/6 channels readable",
        "regime": "CALM",
        "stale": False,
        "transmission": "QUIET",
        "calibrated": date(35),
        "calibration_review_by": date(-330),
        "historical_evidence": {
            "real_money_eligible": False,
            "validated_backtest_eligible": False,
        },
        "counts": {"CALM": 4, "WATCH": 0, "ALARM": 0},
        "cannot_see": {
            "trade_credit": "No free aggregate public series.",
            "primary_issuance": "Licensed series; declared dark.",
        },
        "channels": {
            "cp_market": channel(
                3,
                {
                    "spread": {
                        "state": "CALM",
                        "as_of": date(3),
                        "spread_bp": 10.0,
                        "percentile": 0.578,
                        "chg_20obs_bp": 1.0,
                        "chg_z": 0.05,
                        "chg_window_calendar_days": 92,
                    },
                    "rollover": {
                        "state": "CALM",
                        "as_of": date(4),
                        "chg_8w_pct": 19.3,
                        "outstanding_bn": 272.5,
                    },
                },
            ),
            "credit_lines": channel(
                11,
                {
                    "revolver_draw": {
                        "state": "CALM",
                        "as_of": date(11),
                        "ci_loans_bn": 2956.9,
                    },
                    "sloos": {
                        "state": "CALM",
                        "as_of": date(67),
                        "net_tightening_pct": 0.0,
                    },
                },
            ),
            "real_economy": channel(
                8,
                {
                    "claims": {
                        "state": "CALM",
                        "as_of": date(8),
                        "continued_claims": 1779000,
                    },
                    "capex": {"state": "CALM", "as_of": date(67), "yoy_pct": 12.6},
                },
            ),
            "balance_sheet": channel(
                158,
                {
                    "coverage": {
                        "state": "CALM",
                        "as_of": date(158),
                        "coverage_x": 14.81,
                    },
                    "liquidity": {
                        "state": "CALM",
                        "as_of": date(248),
                        "liquid_over_debt": 0.589,
                        "optional_metric": None,
                    },
                },
            ),
            "trade_credit": {"available": False, "state": None, "declared_dark": True},
            "primary_issuance": {
                "available": False,
                "state": None,
                "declared_dark": True,
            },
        },
    }


def parse_funding(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return parse_funding_scope(
        encode(payload),
        REQUEST_HASH,
        NOW,
        NOW + timedelta(seconds=120),
        kwargs.get("max_age_seconds", MAX_AGE_SECONDS),
    )


def parse_corporate(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return parse_corporate_research(
        encode(payload),
        REQUEST_HASH,
        NOW,
        NOW + timedelta(seconds=120),
        kwargs.get("max_age_seconds", MAX_AGE_SECONDS),
    )


def metric(payload: dict[str, Any], name: str) -> dict[str, Any]:
    return next(row for row in payload["sections"][0]["metrics"] if row["id"] == name)


def test_funding_preserves_clocks_proof_and_exact_bytes_without_composite_claim() -> (
    None
):
    payload = funding_payload()
    section = parse_funding(payload)
    assert _validate_evidence_section("seiche", section) == section
    assert section["as_of"] == "2026-09-03T00:00:00Z"
    assert section["source_sha256"] == hashlib.sha256(encode(payload)).hexdigest()
    assert section["source_schema"] == FUNDING_PROFILE_ID
    assert section["request_hash"] == REQUEST_HASH
    assert section["facts"]["policy_id"] == POLICY_ID
    assert section["facts"]["regime"] == "CALM"
    assert section["facts"]["pressure_bp"] == 2.0
    assert section["facts"]["inputs"]["SOFR"]["asof"] == "2026-09-03"
    assert section["facts"]["inputs"]["SOFR"]["coverage_start"] == "2018-04-03"
    assert section["facts"]["spreads"]["SOFR"]["alignment"]["no_forward_fill"] is True
    assert section["facts"]["capture"]["snapshot_generated_at"] != section["as_of"]
    assert section["facts"]["native_schema"] == "seiche.money-market-desk.v1"
    assert "unrelated_historical_calibration" not in section["facts"]
    assert section["expires_at"] == (NOW + timedelta(seconds=60)).isoformat().replace(
        "+00:00", "Z"
    )
    assert section["real_money_eligible"] is False
    assert section["executable_quote"] is False


@pytest.mark.parametrize(
    ("bp", "expected"),
    [
        (5, "CALM"),
        (5.1, "EROSION"),
        (15, "EROSION"),
        (15.1, "STRAIN"),
        (25, "STRAIN"),
        (25.1, "STRESS"),
    ],
)
def test_operator_bands_are_explicit_not_native_regime(
    bp: float, expected: str
) -> None:
    payload = funding_payload()
    metric(payload, "policy.sofr")["value"] = round(3.65 + bp / 100, 3)
    metric(payload, "policy.sofr_minus_iorb")["value"] = bp
    assert parse_funding(payload)["facts"]["regime"] == expected


def test_negative_effr_dislocation_is_not_hidden_by_sofr() -> None:
    payload = funding_payload()
    metric(payload, "policy.effr")["value"] = 3.35
    metric(payload, "policy.effr_minus_iorb")["value"] = -30
    assert parse_funding(payload)["facts"]["regime"] == "STRESS"


def test_rounding_bound_does_not_hide_crossed_threshold() -> None:
    payload = funding_payload()
    metric(payload, "policy.sofr")["value"] = 3.701
    metric(payload, "policy.sofr_minus_iorb")["value"] = 5
    section = parse_funding(payload)
    assert section["facts"]["pressure_bp"] == 5.1
    assert section["facts"]["regime"] == "EROSION"


@pytest.mark.parametrize(
    "problem",
    [
        "join",
        "forward_fill",
        "input_clock",
        "missing_clock",
        "input_count",
        "overlap",
        "unit",
        "algebra",
        "raw_clock",
        "source_clock",
        "duplicate_metric",
        "missing_rate",
        "future",
        "stale",
        "capture",
        "capture_order",
        "evaluation",
        "boolean_value",
    ],
)
def test_funding_rejects_semantic_breaks_despite_success_envelope(problem: str) -> None:
    payload = funding_payload()
    card = metric(payload, "policy.sofr_minus_iorb")
    if problem == "join":
        card["alignment"]["method"] = "nearest"
    elif problem == "forward_fill":
        card["alignment"]["no_forward_fill"] = False
    elif problem == "input_clock":
        card["alignment"]["input_asof"]["SOFR"] = "2021-07-28"
    elif problem == "missing_clock":
        del card["alignment"]["input_asof"]["SOFR"]
    elif problem == "input_count":
        card["alignment"]["input_observations"]["SOFR"] = 2
    elif problem == "overlap":
        card["alignment"]["overlap_observations"] = 999999
    elif problem == "unit":
        card["unit"] = "%"
    elif problem == "algebra":
        card["value"] = 0.8
    elif problem == "raw_clock":
        metric(payload, "policy.sofr")["asof"] = "2026-09-02"
    elif problem == "source_clock":
        payload["source_metadata"][0]["asof"] = "2026-09-02"
    elif problem == "duplicate_metric":
        payload["sections"][0]["metrics"].append(copy.deepcopy(card))
    elif problem == "missing_rate":
        payload["sections"][0]["metrics"].pop(0)
    elif problem == "future":
        metric(payload, "policy.sofr")["asof"] = "2026-09-07"
    elif problem == "stale":
        metric(payload, "policy.sofr")["asof"] = "2021-07-28"
    elif problem == "capture":
        payload["snapshot_generated_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif problem == "capture_order":
        payload["snapshot_generated_at"] = NOW.isoformat()
    elif problem == "evaluation":
        payload["freshness"]["evaluation_asof"] = "2026-09-05"
    elif problem == "boolean_value":
        metric(payload, "policy.sofr")["value"] = True
    with pytest.raises(FundingScopeError):
        parse_funding(payload)


@pytest.mark.parametrize("parser", [parse_funding_scope, parse_corporate_research])
@pytest.mark.parametrize(
    "raw",
    [
        b'{"x":1,"x":2}',
        b'{"x":NaN}',
        b'{"x":1e999}',
        b"[]",
        b"\xff",
        b"{" + b" " * MAX_BYTES + b"}",
    ],
)
def test_strict_json_and_budget(parser: Any, raw: bytes) -> None:
    with pytest.raises(FundingScopeError):
        parser(raw, REQUEST_HASH, NOW, NOW + timedelta(seconds=60), MAX_AGE_SECONDS)


@pytest.mark.parametrize("age", [True, 0, -1, MAX_AGE_SECONDS + 1, 86400])
def test_cadence_is_bounded_and_stricter_policy_is_respected(age: int) -> None:
    with pytest.raises(FundingScopeError):
        parse_funding(funding_payload(), max_age_seconds=age)
    with pytest.raises(FundingScopeError):
        parse_corporate(corporate_payload(), max_age_seconds=age)


def test_binding_and_native_freshness_limit_expiry() -> None:
    raw = encode(funding_payload())
    with pytest.raises(FundingScopeError):
        parse_funding_scope(
            raw, "unbound", NOW, NOW + timedelta(seconds=60), MAX_AGE_SECONDS
        )
    with pytest.raises(FundingScopeError):
        parse_funding_scope(
            raw,
            REQUEST_HASH,
            NOW.replace(tzinfo=None),
            NOW + timedelta(seconds=60),
            MAX_AGE_SECONDS,
        )
    with pytest.raises(FundingScopeError):
        parse_funding_scope(raw, REQUEST_HASH, NOW, NOW, MAX_AGE_SECONDS)
    age = int((NOW - datetime(2026, 9, 3, tzinfo=UTC)).total_seconds())
    section = parse_funding_scope(
        raw, REQUEST_HASH, NOW, NOW + timedelta(seconds=60), age + 5
    )
    assert section["expires_at"] == (NOW + timedelta(seconds=5)).isoformat().replace(
        "+00:00", "Z"
    )
    section = parse_funding_scope(
        raw, REQUEST_HASH, NOW, NOW + timedelta(seconds=3), MAX_AGE_SECONDS
    )
    assert section["expires_at"] == (NOW + timedelta(seconds=3)).isoformat().replace(
        "+00:00", "Z"
    )


def test_corporate_current_scope_retains_older_research_and_dark_coverage() -> None:
    payload = corporate_payload()
    section = parse_corporate(payload)
    assert _validate_evidence_section("liquilens", section) == section
    facts = section["facts"]
    assert section["source_schema"] == CORPORATE_PROFILE_ID
    assert section["source_sha256"] == hashlib.sha256(encode(payload)).hexdigest()
    assert section["as_of"] == "2026-09-02T00:00:00Z"
    assert facts["native_aggregate_as_of"] == "2026-09-03"
    assert facts["cp_spread_bp"] == 10.0
    assert facts["cp_rollover_chg_8w_pct"] == 19.3
    assert facts["coverage"] == "4/6 channels readable"
    assert facts["historical_evidence"]["real_money_eligible"] is False
    older = facts["research_channels"]["balance_sheet"]["legs"]["liquidity"]
    assert (
        older["as_of"]
        == payload["channels"]["balance_sheet"]["legs"]["liquidity"]["as_of"]
    )
    assert older["optional_metric"] is None
    assert facts["research_channels"]["trade_credit"]["available"] is False
    assert "trade_credit" in facts["cannot_see"]
    assert "regime" not in facts  # Native aggregate is research, not Seiche regime.


def test_corporate_pressure_is_retained_for_the_separate_operator_rule() -> None:
    payload = corporate_payload()
    payload["channels"]["cp_market"]["legs"]["spread"]["spread_bp"] = 51.0
    assert parse_corporate(payload)["facts"]["cp_spread_bp"] == 51.0


@pytest.mark.parametrize(
    "problem",
    [
        "stale",
        "future",
        "unknown_date",
        "missing_clock",
        "missing_value",
        "withheld",
        "native_stale",
        "no_state",
        "channel_stale",
        "channel_withheld",
        "coverage",
        "count",
        "eligibility",
        "version",
        "old_review",
        "negative_outstanding",
        "aggregate_clock",
    ],
)
def test_corporate_rejects_current_scope_gaps(problem: str) -> None:
    payload = corporate_payload()
    cp = payload["channels"]["cp_market"]
    rollover = cp["legs"]["rollover"]
    if problem == "stale":
        rollover["as_of"] = "2021-07-28"
    elif problem == "future":
        rollover["as_of"] = "2026-09-07"
    elif problem == "unknown_date":
        rollover["as_of"] = None
    elif problem == "missing_clock":
        del rollover["as_of"]
    elif problem == "missing_value":
        rollover["chg_8w_pct"] = None
    elif problem == "withheld":
        rollover["withheld"] = True
    elif problem == "native_stale":
        rollover["stale"] = True
    elif problem == "no_state":
        rollover["state"] = None
    elif problem == "channel_stale":
        cp["stale"] = True
    elif problem == "channel_withheld":
        cp["withheld"] = True
    elif problem == "coverage":
        payload["coverage"] = "6/6 channels readable"
    elif problem == "count":
        payload["counts"]["CALM"] = 6
    elif problem == "eligibility":
        payload["historical_evidence"]["real_money_eligible"] = True
    elif problem == "version":
        payload["method_version"] = 3
    elif problem == "old_review":
        payload["calibration_review_by"] = "2026-09-05"
    elif problem == "negative_outstanding":
        rollover["outstanding_bn"] = -1
    elif problem == "aggregate_clock":
        payload["as_of"] = "2026-09-06"
    with pytest.raises(FundingScopeError):
        parse_corporate(payload)
