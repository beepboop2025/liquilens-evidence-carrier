from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from liquilens_trading_copilot import riptide

NOW = datetime(2026, 9, 6, 10, tzinfo=UTC)


def encoded(value: dict[str, Any]) -> bytes:
    return json.dumps(value, allow_nan=False, separators=(",", ":")).encode()


def risk_payload() -> dict[str, Any]:
    return {
        "schema": "riptide.public.v1",
        "product": "riptide",
        "kind": "risk_evidence",
        "available": True,
        "execution_mode": "paper_only",
        "real_orders": False,
        "disclaimer": (
            "Paper-only research record; no real orders or brokerage connection."
        ),
        "generated_at": (NOW - timedelta(seconds=1)).isoformat(),
        "generated_by_run": "daily-20260905T211000-756510ce99",
        "observed_at": (NOW - timedelta(hours=12)).isoformat(),
        "state": "live",
        "feed_written": True,
        "access_mode": "public_login_free",
        "action": "display_and_alert_only",
        "breach_stress": 0.0,
        "channels_requested": 3,
        "channels_reachable": 3,
        "posts_scanned": 41,
        "own_property_hits": 0,
        "scam_hits": 0,
    }


def events_payload() -> dict[str, Any]:
    policy_rows = [
        {
            "provider": "kalshi",
            "source_state": "restricted",
            "mode": "blocked",
            "eligible": False,
            "reason_code": "jurisdiction_unverified",
            "reviewed_at": "2026-08-23",
        },
        {
            "provider": "polymarket",
            "source_state": "unknown",
            "mode": "blocked",
            "eligible": False,
            "reason_code": "rights_and_jurisdiction_review_required",
            "reviewed_at": "2026-08-23",
        },
    ]
    return {
        "schema": "riptide.public.v1",
        "product": "riptide",
        "kind": "event_markets",
        "event_schema": "riptide.event-intelligence.v1",
        "available": False,
        "status": "quarantined",
        "execution_mode": "read_only",
        "real_orders": False,
        "research_only": True,
        "labs": True,
        "disclaimer": "LABS research only.",
        "generated_at": (NOW - timedelta(seconds=1)).isoformat(),
        "collected_at": (NOW - timedelta(hours=7)).isoformat(),
        "snapshot_sha256": "b" * 64,
        "freshness": {
            "basis": "snapshot_collection_time",
            "quote_freshness": "per_event",
            "max_age_hours": 36.0,
            "stale": False,
        },
        "events": [],
        "coverage": {
            "event_count": 0,
            "event_limit": 24,
            "providers_expected": ["kalshi", "polymarket"],
            "providers_with_events": [],
        },
        "source_policy": {
            "schema": "riptide.event-source-policy.v1",
            "version": "2026-08-23.1",
            "surface": "rest",
            "jurisdiction": "unknown",
            "default_new_provider_state": "unknown",
            "providers": policy_rows,
        },
        "providers": [
            {
                "provider": row["provider"],
                "source_state": row["source_state"],
                "publication_mode": "blocked",
                "event_count": 0,
                "status": "quarantined",
            }
            for row in policy_rows
        ],
    }


def test_current_risk_retains_scan_clock_limited_coverage_and_no_authority() -> None:
    payload = risk_payload()
    raw = encoded(payload)
    result = riptide.parse_riptide_risk(raw, NOW)
    assert result["state"] == "available"
    assert result["observed_at"] == payload["observed_at"]
    assert result["observation_age_seconds"] == 12 * 3600
    assert result["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["generated_by_run"] == payload["generated_by_run"]
    assert result["coverage"] == {
        "channels_requested": 3,
        "channels_reachable": 3,
        "posts_scanned": 41,
        "complete": True,
    }
    assert result["native_band"] == "CALM"
    assert result["breach_stress"] == 0
    assert result["financial_authority"] == "none"
    assert result["influences_order_decision"] is False
    assert result["journal_cryptographically_verified_by_consumer"] is False


@pytest.mark.parametrize(
    ("score", "band"),
    [
        (24.9, "CALM"),
        (25, "EROSION"),
        (44.9, "EROSION"),
        (45, "STRAINED"),
        (69.9, "STRAINED"),
        (70, "ACUTE"),
        (100, "ACUTE"),
    ],
)
def test_native_bands_are_defensive_alerts_not_order_holds(
    score: float, band: str
) -> None:
    payload = risk_payload()
    payload["breach_stress"] = score
    result = riptide.parse_riptide_risk(encoded(payload), NOW)
    assert result["native_band"] == band
    assert result["state"] == "available"
    assert result["action"] == "display_and_alert_only"
    assert result["influences_order_decision"] is False


@pytest.mark.parametrize(
    ("key", "value", "reason"),
    [
        (
            "observed_at",
            (NOW - timedelta(hours=24, seconds=1)).isoformat(),
            "defensive_observation_stale",
        ),
        (
            "observed_at",
            (NOW + timedelta(seconds=1)).isoformat(),
            "observation_clock_future",
        ),
        ("observed_at", None, "observation_clock_invalid"),
        ("observed_at", "2026-09-06T00:00:00", "observation_clock_invalid"),
        (
            "generated_at",
            (NOW + timedelta(seconds=1)).isoformat(),
            "response_clock_future",
        ),
        ("channels_requested", 0, "channel_count_inconsistent"),
        ("channels_reachable", 2, "defensive_coverage_incomplete"),
        ("channels_reachable", 4, "channel_count_inconsistent"),
        ("channels_reachable", True, "channel_count_invalid"),
        ("posts_scanned", 0, "post_count_inconsistent"),
        ("posts_scanned", 2, "post_count_inconsistent"),
        ("posts_scanned", -1, "post_count_invalid"),
        ("own_property_hits", 21, "hit_count_inconsistent"),
        ("scam_hits", None, "hit_count_invalid"),
        ("breach_stress", -0.1, "defensive_index_out_of_range"),
        ("breach_stress", 100.1, "defensive_index_out_of_range"),
        ("breach_stress", False, "defensive_index_invalid"),
        ("breach_stress", "0", "defensive_index_invalid"),
        ("state", "unreachable", "defensive_feed_unavailable"),
        ("feed_written", False, "defensive_feed_unavailable"),
        ("action", "authorize_orders", "defensive_authority_mismatch"),
        ("access_mode", "private_login", "defensive_authority_mismatch"),
        ("execution_mode", "live", "defensive_authority_mismatch"),
        ("real_orders", True, "native_authority_mismatch"),
        ("kind", "allocation", "native_schema_mismatch"),
        ("available", False, "native_risk_unavailable"),
        ("generated_by_run", None, "run_identity_invalid"),
    ],
)
def test_risk_fault_is_unavailable_without_fabricating_calm(
    key: str, value: Any, reason: str
) -> None:
    payload = risk_payload()
    payload[key] = value
    result = riptide.parse_riptide_risk(encoded(payload), NOW)
    assert result["state"] == "unavailable"
    assert result["reason_codes"] == [reason]
    assert "breach_stress" not in result
    assert "native_band" not in result


def test_quarantine_remains_unavailable_despite_fresh_collection() -> None:
    payload = events_payload()
    raw = encoded(payload)
    result = riptide.parse_riptide_events(raw, NOW)
    assert result["state"] == "unavailable"
    assert result["native_status"] == "quarantined"
    assert result["reason_codes"] == ["event_source_policy_quarantine"]
    assert result["collected_at"] == payload["collected_at"]
    assert result["event_count"] == 0
    assert result["coverage_complete"] is False
    assert result["providers"]["kalshi"]["source_state"] == "restricted"
    assert (
        result["providers"]["polymarket"]["reason_code"]
        == "rights_and_jurisdiction_review_required"
    )
    assert result["source_policy"]["version"] == "2026-08-23.1"
    assert result["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["reported_snapshot_sha256"] == "b" * 64
    assert result["snapshot_cryptographically_verified_by_consumer"] is False
    assert "events" not in result


@pytest.mark.parametrize(
    "fault",
    [
        "stale",
        "future",
        "clock_unknown",
        "false_available",
        "rights_event",
        "policy_missing",
        "authority",
        "coverage",
        "native_stale",
        "hash_missing",
        "wrong_provider",
    ],
)
def test_event_faults_remain_explicitly_unavailable(fault: str) -> None:
    payload = events_payload()
    if fault == "stale":
        payload["collected_at"] = (NOW - timedelta(hours=37)).isoformat()
    elif fault == "future":
        payload["collected_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif fault == "clock_unknown":
        payload["collected_at"] = None
    elif fault == "false_available":
        payload["available"] = True
    elif fault == "rights_event":
        payload["events"] = [{"provider": "kalshi"}]
    elif fault == "policy_missing":
        del payload["source_policy"]
    elif fault == "authority":
        payload["research_only"] = False
    elif fault == "coverage":
        payload["coverage"]["event_count"] = 1
    elif fault == "native_stale":
        payload["freshness"]["stale"] = True
    elif fault == "hash_missing":
        del payload["snapshot_sha256"]
    elif fault == "wrong_provider":
        payload["providers"][0]["provider"] = "unknown"
    result = riptide.parse_riptide_events(encoded(payload), NOW)
    assert result["state"] == "unavailable"
    assert result["reason_codes"] and result["reason_codes"] != [
        "event_source_policy_quarantine"
    ]
    assert result["influences_order_decision"] is False


def test_even_eligible_event_diagnostics_do_not_copy_quotes_or_links() -> None:
    payload = events_payload()
    payload.update(status="partial", available=True)
    row = payload["source_policy"]["providers"][0]
    row.update(
        source_state="allowed", mode="full", eligible=True, reason_code="permitted"
    )
    payload["providers"][0].update(
        source_state="allowed", publication_mode="full", event_count=1
    )
    payload["events"] = [
        {
            "provider": "kalshi",
            "yes_probability": 0.7,
            "title": "BTC trade",
            "url": "https://bet.example.test/secret-market",
        }
    ]
    payload["coverage"].update(event_count=1, providers_with_events=["kalshi"])
    result = riptide.parse_riptide_events(encoded(payload), NOW)
    assert result["state"] == "diagnostics_available"
    assert result["coverage_complete"] is False
    assert "yes_probability" not in json.dumps(result)
    assert "secret-market" not in json.dumps(result)
    assert result["influences_order_decision"] is False


@pytest.mark.parametrize(
    "parser", [riptide.parse_riptide_risk, riptide.parse_riptide_events]
)
@pytest.mark.parametrize(
    "raw",
    [
        b'{"x":1,"x":2}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":1e999}',
        b"[]",
        b"\xff",
        b"{" + b" " * riptide.MAX_RESPONSE_BYTES + b"}",
    ],
)
def test_strict_json_and_budget(parser: Any, raw: bytes) -> None:
    result = parser(raw, NOW)
    assert result["state"] == "unavailable"
    assert result["reason_codes"]
    assert "native_band" not in result


def test_collector_only_two_fixed_public_gets_and_never_inherits_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://untrusted.invalid:1234")
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted.invalid:1234")
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "not-for-riptide")
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert not request.content
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        assert "APCA-API-KEY-ID" not in request.headers
        assert request.headers["X-LiquiLens-Synthetic"] == "true"
        assert request.headers["Accept-Encoding"] == "identity"
        payload = (
            risk_payload() if str(request.url) == riptide.RISK_URL else events_payload()
        )
        return httpx.Response(
            200, json=payload, headers={"set-cookie": "identity=do-not-propagate"}
        )

    result = asyncio.run(
        riptide.collect_riptide_context(
            transport=httpx.MockTransport(handle), clock=lambda: NOW
        )
    )
    assert sorted(str(request.url) for request in requests) == sorted(
        [riptide.RISK_URL, riptide.EVENTS_URL]
    )
    assert result["sources"]["risk"]["state"] == "available"
    assert result["sources"]["events"]["state"] == "unavailable"
    assert result["financial_authority"] == "none"
    assert result["influences_order_decision"] is False
    assert result["broker_calls_performed"] is False
    assert result["assessment_performed"] is False


@pytest.mark.parametrize(
    "fault", ["redirect", "http", "html", "encoding", "oversize", "unreachable"]
)
def test_collector_bounds_fail_one_source_without_losing_other(fault: str) -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) == riptide.EVENTS_URL:
            return httpx.Response(200, json=events_payload())
        if fault == "redirect":
            return httpx.Response(
                302, headers={"location": "https://untrusted.invalid"}
            )
        if fault == "http":
            return httpx.Response(503, text="private upstream error details")
        if fault == "html":
            return httpx.Response(200, text="not json")
        if fault == "encoding":
            return httpx.Response(
                200,
                content=b"",
                headers={"content-type": "application/json", "content-encoding": "br"},
            )
        if fault == "oversize":
            return httpx.Response(
                200,
                content=b" " * (riptide.MAX_RESPONSE_BYTES + 1),
                headers={"content-type": "application/json"},
            )
        raise httpx.ConnectError(
            "secret error URL must not reach report", request=request
        )

    result = asyncio.run(
        riptide.collect_riptide_context(
            transport=httpx.MockTransport(handle), clock=lambda: NOW
        )
    )
    assert len(calls) == 2
    assert result["sources"]["risk"]["state"] == "unavailable"
    assert result["sources"]["events"]["native_status"] == "quarantined"
    assert "private upstream" not in json.dumps(result)
    assert "secret error" not in json.dumps(result)


def test_total_deadline_cancels_incomplete_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(riptide, "TOTAL_TIMEOUT_SECONDS", 0.02)
    cancelled = []

    async def handle(request: httpx.Request) -> httpx.Response:
        try:
            await asyncio.sleep(2)
        finally:
            cancelled.append(str(request.url))
        return httpx.Response(200, json={})

    result = asyncio.run(
        riptide.collect_riptide_context(
            transport=httpx.MockTransport(handle), clock=lambda: NOW
        )
    )
    assert len(cancelled) == 2
    assert all(
        source["reason_codes"] == ["source_deadline_exceeded"]
        for source in result["sources"].values()
    )
