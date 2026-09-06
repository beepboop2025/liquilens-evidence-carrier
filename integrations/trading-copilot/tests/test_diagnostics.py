"""Synthetic endpoint contracts; these tests make no public/product requests."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from liquilens_trading_copilot import diagnostics


def _payloads(*, evidence_age_days: int = 2) -> dict:
    now = datetime.now(UTC).replace(microsecond=0)
    snapshot = now - timedelta(seconds=60)
    evidence = now - timedelta(days=evidence_age_days)
    authority = {
        field: False
        for field in (
            "can_execute",
            "can_recommend",
            "can_allocate_capital",
            "can_route_order",
            "can_custody",
            "can_settle",
            "has_broker_credentials",
            "has_order_submission",
        )
    }
    return {
        "gateway_health": {
            "status": "ok",
            "mode": "sandbox",
            "can_execute": False,
            "version": "0.2.2",
            "source_revision": "7" * 40,
        },
        "gateway_capabilities": {
            "mode": "sandbox",
            "live_outcome": "unavailable",
            "authority": authority,
            "policy_admission": {
                "max_evidence_age_seconds": {"seiche": 691200},
                "max_notional_usd": 100000,
            },
            "upstreams": {
                "undertow": {
                    "side": "sell",
                    "currency": "USD",
                    "modes": ["observe", "paper"],
                    "published_rungs_usd": [1000, 10000, 100000, 1000000],
                }
            },
        },
        "seiche_context": {
            "schema": "seiche.risk-context.v1",
            "ok": True,
            "status": "available",
            "executable": False,
            "executable_quote": False,
            "real_money_eligible": False,
            "can_authorize_order": False,
            "clocks": {
                "snapshot_generated_at": snapshot.isoformat(),
                "evidence_as_of": evidence.isoformat(),
                "evaluated_at": now.isoformat(),
                "snapshot_age_seconds": 60,
                "evidence_age_seconds": int((now - evidence).total_seconds()),
            },
        },
        "seiche_health": {
            "generated_at": snapshot.isoformat(),
            "provenance": [
                {
                    "mnemonic": "SOFR",
                    "source": "fred",
                    "remote_id": "SOFR",
                    "asof": evidence.isoformat(),
                    "freq": "D",
                }
            ],
        },
    }


def _run(payloads: dict, **kwargs) -> dict:
    async def collect():
        expected = {url: name for name, url in diagnostics.ENDPOINTS.items()}
        seen = []

        def handler(request):
            assert request.method == "GET"
            assert str(request.url) in expected
            assert "authorization" not in request.headers
            assert "cookie" not in request.headers
            assert request.headers["accept-encoding"] == "identity"
            seen.append(str(request.url))
            return httpx.Response(200, json=payloads[expected[str(request.url)]])

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer should-not-be-sent"},
            cookies={"session": "should-not-be-sent"},
            auth=("should-not-be-sent", "should-not-be-sent"),
        ) as client:
            result = await diagnostics.collect_readiness(client, **kwargs)
            assert len(seen) == len(expected) == 4
            assert not client.is_closed
        return result

    return asyncio.run(collect())


def _codes(report: dict) -> set[str]:
    return {item["code"] for item in report["blockers"]}


def test_http_200_old_observation_remains_blocked_despite_new_snapshot():
    payloads = _payloads(evidence_age_days=1800)
    payloads["seiche_health"]["provenance"] = [
        {
            "source": "fred",
            "mnemonic": "IOER",
            "remote_id": "IOER",
            "asof": "2021-07-28",
            "freq": "D",
            "private_value": "must-not-appear-in-report",
        }
    ]
    report = _run(payloads, paper_credentials_present=True, account_id_configured=True)
    assert all(source["http_status"] == 200 for source in report["sources"].values())
    assert "seiche_evidence_too_old" in _codes(report)
    assert report["semantic_freshness"]["observed_evidence_age_seconds"] >= 1800 * 86400
    assert (
        report["semantic_freshness"]["provenance"]["oldest_rows"][0]["role"]
        == "historical_splice_leg"
    )
    assert "must-not-appear-in-report" not in json.dumps(report)
    assert report["ready_for_paper_submission"] is False
    assert "every input is stale" in report["semantic_freshness"]["interpretation"]


def test_unknown_observation_dates_are_not_replaced_with_fetch_time():
    payloads = _payloads()
    payloads["seiche_context"]["clocks"]["evidence_as_of"] = None
    payloads["seiche_health"]["provenance"][0]["asof"] = None
    payloads["seiche_health"]["provenance"][0]["fetched_at"] = datetime.now(
        UTC
    ).isoformat()
    report = _run(payloads)
    assert "seiche_observation_clock_unknown" in _codes(report)
    assert report["semantic_freshness"]["evidence_as_of"] is None
    assert (
        report["semantic_freshness"]["provenance"]["unknown_observation_clock_count"]
        == 1
    )


def test_no_credentials_and_fresh_sources_are_not_submission_readiness():
    report = _run(_payloads())
    assert "seiche_evidence_too_old" not in _codes(report)
    assert {
        "paper_credentials_missing",
        "paper_account_id_missing",
        "order_specific_authenticated_receipt_required",
    } <= _codes(report)
    assert report["paper_configuration"]["account_identity_verified"] is False
    assert report["execution_enabled"] is False
    assert report["assessment_performed"] is False
    assert report["broker_calls_performed"] is False


def test_live_buy_and_unpublished_sizes_are_not_advertised_as_supported():
    report = _run(_payloads())
    scope = report["supported_gateway_scope"]
    assert scope["live_trading_supported"] is False
    assert scope["buy_supported"] is False
    assert scope["arbitrary_size_supported"] is False
    assert scope["automatic_resize"] is False
    assert scope["policy_admissible_rungs_usd"] == [1000, 10000, 100000]
    assert 1000000 in scope["published_rungs_usd"]
    payloads = _payloads()
    payloads["gateway_capabilities"]["live_outcome"] = "pass"
    payloads["gateway_capabilities"]["authority"]["can_execute"] = True
    assert "gateway_live_boundary_unexpected" in _codes(_run(payloads))


def test_unexpected_gateway_policy_and_size_declarations_fail_closed():
    payloads = _payloads()
    caps = payloads["gateway_capabilities"]
    caps["policy_admission"]["max_evidence_age_seconds"]["seiche"] = 10**12
    caps["policy_admission"]["max_notional_usd"] = 1000000
    caps["upstreams"]["undertow"]["published_rungs_usd"] = [1234]
    report = _run(payloads)
    assert {
        "seiche_freshness_policy_invalid",
        "gateway_notional_policy_unavailable",
        "gateway_order_scope_unavailable",
    } <= _codes(report)
    assert report["semantic_freshness"]["gateway_max_evidence_age_seconds"] == 691200
    assert report["supported_gateway_scope"]["published_rungs_usd"] == []


def test_future_or_inconsistent_source_clocks_fail_closed():
    payloads = _payloads()
    payloads["seiche_context"]["clocks"]["evidence_age_seconds"] = 0
    assert "seiche_reported_age_mismatch" in _codes(_run(payloads))
    payloads["seiche_context"]["clocks"]["evidence_as_of"] = (
        datetime.now(UTC) + timedelta(days=1)
    ).isoformat()
    assert "seiche_clock_order_invalid" in _codes(_run(payloads))


@pytest.mark.parametrize(
    "raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b"[]", b"not-json"]
)
def test_duplicate_nonfinite_and_nonobject_json_rejected(raw):
    async def collect():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, content=raw, headers={"content-type": "application/json"}
                )
            )
        ) as client:
            report = await diagnostics.collect_readiness(client)
        assert all(
            source["state"] == "unavailable" for source in report["sources"].values()
        )
        assert "source_unavailable" in _codes(report)

    asyncio.run(collect())


def test_unreachable_source_is_sanitized_without_exception_or_request_contents():
    async def collect():
        def handler(request):
            raise httpx.ConnectError("private-token-must-not-leak", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            report = await diagnostics.collect_readiness(client)
        assert "source_unavailable" in _codes(report)
        assert "private-token-must-not-leak" not in json.dumps(report)

    asyncio.run(collect())


def test_redirect_is_rejected_without_following_location():
    async def collect():
        seen = []

        def handler(request):
            seen.append(str(request.url))
            assert str(request.url) in diagnostics.ENDPOINTS.values()
            return httpx.Response(
                302, headers={"location": "https://unapproved.example/orders"}
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        ) as client:
            report = await diagnostics.collect_readiness(client)
        assert len(seen) == 4
        assert all(
            source["reason"] == "http_status_not_200"
            for source in report["sources"].values()
        )

    asyncio.run(collect())


def test_chunked_response_size_limit_without_content_length(monkeypatch):
    monkeypatch.setattr(diagnostics, "MAX_RESPONSE_BYTES", 64)

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"oversize":"'
            yield b"x" * 65

    async def collect():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, stream=Body(), headers={"content-type": "application/json"}
                )
            )
        ) as client:
            report = await diagnostics.collect_readiness(client)
        assert all(
            source["reason"] == "response_size_limit"
            for source in report["sources"].values()
        )

    asyncio.run(collect())


def test_total_deadline_covers_slow_transport(monkeypatch):
    monkeypatch.setattr(diagnostics, "REQUEST_TIMEOUT_SECONDS", 0.01)

    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={})

    async def collect():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            report = await diagnostics.collect_readiness(client)
        assert all(
            source["reason"] == "source_unreachable_or_timeout"
            for source in report["sources"].values()
        )

    asyncio.run(collect())


def test_credentials_must_be_presence_booleans_without_network():
    with pytest.raises(TypeError, match="booleans"):
        asyncio.run(diagnostics.collect_readiness(paper_credentials_present="secret"))
