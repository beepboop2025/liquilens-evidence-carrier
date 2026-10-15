"""End-to-end synthetic broker fixtures; these never contact a real account."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
from test_agent_host import READ_TOKEN, TOKEN, HostSDK, assessed, rig

from liquilens_trading_copilot.agent_cli import initialize
from liquilens_trading_copilot.agent_host import BearerAuthority, private_json
from liquilens_trading_copilot.outcome_consent import grant_outcomes, revoke_outcomes
from liquilens_trading_copilot.outcomes import (
    GRANT_SCHEMA,
    PURPOSE,
    OutcomeError,
    main,
    summarize_pages,
    utc,
)

OUTCOME_TOKEN = "synthetic-outcome-reader-token-0123456789abcdef"
HEADERS = {"Authorization": "Bearer " + OUTCOME_TOKEN}


def write_grant(env, path: Path, **changes) -> dict:
    binding = env.service.binding
    grant = {
        "schema": GRANT_SCHEMA,
        "consent_id": "synthetic-pilot-1",
        "enabled": True,
        "binding": {
            name: getattr(binding, name)
            for name in (
                "tenant_id",
                "operator_id",
                "account_id",
                "agent_id",
            )
        },
        "recipient_id": "synthetic-reviewer",
        "token_sha256": hashlib.sha256(OUTCOME_TOKEN.encode()).hexdigest(),
        "purpose": PURPOSE,
        "not_before": utc(env.clock[0]),
        "expires_at": utc(env.clock[0] + timedelta(hours=1)),
        "records_from": utc(env.clock[0]),
        "records_until": utc(env.clock[0] + timedelta(hours=1)),
        "traffic_class": "synthetic",
        "allow_training": False,
        "allow_cross_customer_aggregation": False,
        **changes,
    }
    path.write_text(json.dumps(grant))
    path.chmod(0o600)
    return grant


async def export(env, *, after=0, through=None, limit=25, **changes):
    return await env.client.post(
        "/v1/outcomes/export",
        headers=HEADERS,
        json={
            "consent_id": "synthetic-pilot-1",
            "after": after,
            "through": through,
            "limit": limit,
            **changes,
        },
    )


async def all_pages(env, *, limit=25) -> list[dict]:
    pages = []
    after, through = 0, None
    while True:
        result = await export(env, after=after, through=through, limit=limit)
        assert result.status_code == 200, result.text
        page = result.json()
        pages.append(page)
        if not page["has_more"]:
            return pages
        after, through = page["next_after"], page["through"]


def test_receipt_to_partial_fill_to_completion_export_and_restart(tmp_path):
    async def run():
        grant_file = tmp_path / "grant.json"
        sdk = HostSDK()
        async with rig(
            tmp_path,
            sdk=sdk,
            outcome_token=OUTCOME_TOKEN,
            outcome_consent_file=grant_file,
        ) as env:
            write_grant(env, grant_file)
            assessment = await assessed(env)
            args = {"assessment_id": assessment["assessment_id"]}
            assert (
                await env.client.post("/v1/orders/submit", json=args)
            ).status_code == 200
            await env.client.post("/v1/orders/reconcile", json=args)
            accepted = summarize_pages(await all_pages(env))
            assert accepted["counts"]["broker_acknowledged"] == 1
            assert accepted["counts"]["fill_observed"] == 0
            env.outcome.update(
                status="partially_filled",
                filled_qty="0.01",
                filled_avg_price="40000.123456789",
            )
            env.clock[0] += timedelta(seconds=1)
            await env.client.post("/v1/orders/reconcile", json=args)
            partial = summarize_pages(await all_pages(env))
            assert partial["counts"]["partial_fill_observed"] == 1
            assert partial["counts"]["fully_filled"] == 0
            env.outcome.update(
                status="filled", filled_qty="0.025", filled_avg_price="40000"
            )
            env.clock[0] += timedelta(seconds=1)
            await env.client.post("/v1/orders/reconcile", json=args)
            pages = await all_pages(env, limit=1)
            assert len(pages) >= 4
            # Retry of any page is harmless; repeated observations aren't trades.
            report = summarize_pages([pages[0], pages[0], *pages[1:]])
            assert report["counts"]["assessed"] == 1
            assert report["counts"]["submission_attempted"] == 1
            assert report["counts"]["fill_observed"] == 1
            assert report["counts"]["fully_filled"] == 1
            assert report["counts"]["partial_fill_observed"] == 0
            assert report["pnl_usd"] is None and report["live_trades"] is None
            last = pages[-1]["records"][-1]["snapshot"]
            assert last["assessment"]["record_hash"] == assessment["record_hash"]
            assert last["assessment"]["policy_hash"] == assessment["policy_hash"]
            assert set(last["assessment"]["evidence"]) == {
                "liquilens",
                "seiche",
                "undertow",
            }
            assert last["broker_observation"]["filled_qty"] == "0.025"
            assert last["broker_observation"]["broker_event_time"] is None
            serialized = json.dumps(pages)
            for secret in (
                TOKEN,
                OUTCOME_TOKEN,
                env.service.binding.account_id,
                "synthetic-accepted-unfilled-1",
                "facts",
                "integrity",
            ):
                assert secret not in serialized
            counts_before = (
                len(env.source_calls),
                len(env.account_calls),
                len(sdk.orders),
            )
            assert await all_pages(env, limit=1) == pages
            assert counts_before == (
                len(env.source_calls),
                len(env.account_calls),
                len(sdk.orders),
            )
            assert len(sdk.orders) == 1
        async with rig(
            tmp_path,
            sdk=sdk,
            outcome_token=OUTCOME_TOKEN,
            outcome_consent_file=grant_file,
        ) as restarted:
            assert await all_pages(restarted, limit=1) == pages
            assert not restarted.source_calls and not restarted.account_calls
            assert summarize_pages(pages) == report

    asyncio.run(run())


@pytest.mark.parametrize("token", [TOKEN, READ_TOKEN])
def test_execution_and_research_tokens_cannot_export(tmp_path, token):
    async def run():
        path = tmp_path / "grant.json"
        async with rig(tmp_path, outcome_consent_file=path) as env:
            write_grant(env, path)
            result = await env.client.post(
                "/v1/outcomes/export",
                json={},
                headers={"Authorization": "Bearer " + token},
            )
            assert result.status_code == 403
            assert not env.source_calls and not env.account_calls

    asyncio.run(run())


@pytest.mark.parametrize(
    "route", ["/v1/assessments", "/v1/orders/submit", "/v1/orders/reconcile"]
)
def test_export_token_has_no_trading_or_assessment_permission(tmp_path, route):
    async def run():
        async with rig(tmp_path, outcome_token=OUTCOME_TOKEN) as env:
            result = await env.client.post(route, json={}, headers=HEADERS)
            assert result.status_code == 403
            assert not env.source_calls and not env.account_calls and not env.sdk.orders

    asyncio.run(run())


@pytest.mark.parametrize(
    "change",
    [
        {"enabled": False},
        {"enabled": 1},
        {"allow_training": True},
        {"allow_cross_customer_aggregation": True},
        {"purpose": "model_training"},
        {"binding": {}},
        {"binding": {"tenant_id": "someone-else"}},
        {"token_sha256": "a" * 64},
        {"extra": "not-allowed"},
        {"not_before": "2999-01-01T00:00:00Z"},
        {"expires_at": "2020-01-01T00:00:00Z"},
        {"records_from": "2020-01-01T00:00:00Z"},
        {"records_until": "2999-01-01T00:00:00Z"},
        {"not_before": "2026-01-01"},
        {"traffic_class": []},
    ],
)
def test_invalid_or_mismatched_consent_is_denied_without_io(tmp_path, change):
    async def run():
        path = tmp_path / "grant.json"
        async with rig(
            tmp_path, outcome_token=OUTCOME_TOKEN, outcome_consent_file=path
        ) as env:
            write_grant(env, path, **change)
            result = await export(env)
            assert result.status_code == 403, result.text
            assert not env.source_calls and not env.account_calls

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure", ["missing", "public", "symlink", "ambiguous", "expired", "revoked"]
)
def test_hot_revocation_expiry_or_invalid_file_stops_next_page(tmp_path, failure):
    async def run():
        path = tmp_path / "grant.json"
        async with rig(
            tmp_path, outcome_token=OUTCOME_TOKEN, outcome_consent_file=path
        ) as env:
            write_grant(env, path)
            await assessed(env, "one")
            await assessed(env, "two")
            first = (await export(env, limit=1)).json()
            assert first["has_more"] is True
            if failure == "missing":
                path.unlink()
            elif failure == "public":
                path.chmod(0o644)
            elif failure == "symlink":
                target = path.with_name("actual.json")
                path.rename(target)
                path.symlink_to(target)
            elif failure == "ambiguous":
                path.write_text('{"enabled":true,"enabled":false}')
            elif failure == "expired":
                env.clock[0] += timedelta(hours=1)
            else:
                write_grant(env, path, enabled=False)
            result = await export(
                env, after=first["next_after"], through=first["through"]
            )
            assert result.status_code == 403, result.text

    asyncio.run(run())


@pytest.mark.parametrize(
    "change",
    [
        {"after": True},
        {"after": -1},
        {"after": 2**64},
        {"after": 0.0},
        {"through": True},
        {"through": -1},
        {"limit": 0},
        {"limit": 26},
        {"limit": True},
        {"consent_id": "wrong"},
        {"tenant_id": "other"},
    ],
)
def test_page_requests_are_bounded_and_cannot_select_identity(tmp_path, change):
    async def run():
        path = tmp_path / "grant.json"
        async with rig(
            tmp_path, outcome_token=OUTCOME_TOKEN, outcome_consent_file=path
        ) as env:
            write_grant(env, path)
            assert (await export(env, **change)).status_code == 422

    asyncio.run(run())


def test_snapshot_watermark_and_consent_time_window(tmp_path):
    async def run():
        path = tmp_path / "grant.json"
        async with rig(
            tmp_path, outcome_token=OUTCOME_TOKEN, outcome_consent_file=path
        ) as env:
            await assessed(env, "before-consent")
            env.clock[0] += timedelta(seconds=1)
            write_grant(
                env, path, records_until=utc(env.clock[0] + timedelta(seconds=2))
            )
            await assessed(env, "permitted-one")
            await assessed(env, "permitted-two")
            page1 = (await export(env, limit=1)).json()
            await assessed(env, "permitted-three")
            page2 = (
                await export(env, after=page1["next_after"], through=page1["through"])
            ).json()
            assert summarize_pages([page1, page2])["counts"]["assessed"] == 2
            assert summarize_pages(await all_pages(env))["counts"]["assessed"] == 3
            env.clock[0] += timedelta(seconds=2)
            await assessed(env, "after-window")
            assert summarize_pages(await all_pages(env))["counts"]["assessed"] == 3
            assert (await export(env, through=2**62)).status_code == 409

    asyncio.run(run())


@pytest.mark.parametrize(
    "status,quantity",
    [("canceled", "0"), ("rejected", "0"), ("expired", "0"), ("canceled", "0.01")],
)
def test_terminal_and_partial_cancellation_counts(tmp_path, status, quantity):
    async def run():
        path = tmp_path / "grant.json"
        async with rig(
            tmp_path, outcome_token=OUTCOME_TOKEN, outcome_consent_file=path
        ) as env:
            write_grant(env, path)
            item = await assessed(env)
            args = {"assessment_id": item["assessment_id"]}
            await env.client.post("/v1/orders/submit", json=args)
            env.outcome.update(
                status=status,
                filled_qty=quantity,
                filled_avg_price="40000" if quantity != "0" else None,
            )
            response = await env.client.post("/v1/orders/reconcile", json=args)
            assert response.status_code == 200, response.text
            report = summarize_pages(await all_pages(env))
            assert report["counts"]["fully_filled"] == 0
            assert report["counts"]["terminal_without_fill"] == (quantity == "0")
            assert report["counts"]["partial_fill_observed"] == (quantity != "0")

    asyncio.run(run())


def test_timeout_then_restart_keeps_uncertainty_and_never_resubmits(tmp_path):
    async def run():
        path = tmp_path / "grant.json"
        sdk = HostSDK()
        sdk.timeout = True
        async with rig(
            tmp_path, sdk=sdk, outcome_token=OUTCOME_TOKEN, outcome_consent_file=path
        ) as env:
            write_grant(env, path)
            item = await assessed(env)
            args = {"assessment_id": item["assessment_id"]}
            await env.client.post("/v1/orders/submit", json=args)
            pages = await all_pages(env)
            report = summarize_pages(pages)
            assert report["counts"]["submission_attempted"] == 1
            assert report["counts"]["broker_acknowledged"] == 0
            assert report["counts"]["no_broker_observation"] == 1
        async with rig(
            tmp_path, sdk=sdk, outcome_token=OUTCOME_TOKEN, outcome_consent_file=path
        ) as env:
            assert await all_pages(env) == pages
            await env.client.post("/v1/orders/submit", json=args)
            assert len(sdk.orders) == 1
            await env.client.post("/v1/orders/reconcile", json=args)
            assert (
                summarize_pages(await all_pages(env))["counts"]["broker_acknowledged"]
                == 1
            )
            assert len(sdk.orders) == 1

    asyncio.run(run())


@pytest.mark.parametrize("stale,enabled", [(True, True), (False, False)])
def test_blocked_order_remains_assessed_without_a_submission_claim(
    tmp_path, stale, enabled
):
    async def run():
        path = tmp_path / "grant.json"
        async with rig(
            tmp_path,
            stale=stale,
            enabled=enabled,
            outcome_token=OUTCOME_TOKEN,
            outcome_consent_file=path,
        ) as env:
            write_grant(env, path)
            item = await assessed(env)
            response = await env.client.post(
                "/v1/orders/submit", json={"assessment_id": item["assessment_id"]}
            )
            assert response.status_code == 409
            report = summarize_pages(await all_pages(env))
            assert report["counts"]["assessed"] == 1
            assert report["counts"]["source_policy_passed"] == int(not stale)
            assert report["counts"]["submission_attempted"] == 0
            assert not env.sdk.orders

    asyncio.run(run())


def test_corrupt_snapshot_and_incomplete_or_changed_pages_are_rejected(tmp_path):
    async def run():
        path = tmp_path / "grant.json"
        async with rig(
            tmp_path, outcome_token=OUTCOME_TOKEN, outcome_consent_file=path
        ) as env:
            write_grant(env, path)
            await assessed(env, "one")
            await assessed(env, "two")
            pages = await all_pages(env, limit=1)
            bad = copy.deepcopy(pages)
            bad[0]["records"][0]["snapshot"]["order"]["side"] = "buy"
            for candidate in (
                pages[:1],
                pages[1:],
                list(reversed(pages)),
                bad,
                [pages[0], {**pages[0], "next_after": 0}, pages[1]],
            ):
                with pytest.raises(OutcomeError, match="invalid_or_incomplete"):
                    summarize_pages(candidate)
            env.store.db.execute(
                "UPDATE agent_outcome_snapshots SET snapshot_hash=? WHERE sequence=1",
                ("a" * 64,),
            )
            env.store.db.commit()
            assert (await export(env)).status_code == 503

    asyncio.run(run())


def test_owner_provisioning_revocation_and_offline_summary(
    tmp_path, monkeypatch, capsys
):
    state = tmp_path.resolve() / "private"
    initialize(state)
    config = private_json(state / "config.json")
    config["account_id"] = "synthetic-provisioned-account"
    (state / "config.json").write_text(json.dumps(config))
    result = grant_outcomes(
        state,
        consent_id="pilot-1",
        recipient_id="reviewer-1",
        expires_at="2099-01-01T00:00:00Z",
        traffic_class="synthetic",
    )
    assert result["restart_host_required"] is True
    assert "token" not in json.dumps(result)
    token = (state / "agent-outcomes.token").read_text().strip()
    authority = BearerAuthority(
        private_json(state / "agent-auth.json"), agent_id=config["agent_id"]
    )
    assert authority.authenticate(["Bearer " + token]) == frozenset({"outcomes:read"})
    for name in ("agent-outcomes.token", "outcome-consent.json", "agent-auth.json"):
        assert (state / name).stat().st_mode & 0o777 == 0o600
    assert private_json(state / "config.json")["enabled"] is False
    with pytest.raises(ValueError, match="already_provisioned"):
        grant_outcomes(
            state,
            consent_id="pilot-2",
            recipient_id="reviewer-1",
            expires_at="2099-01-01T00:00:00Z",
            traffic_class="synthetic",
        )
    revoked = revoke_outcomes(state)
    assert revoked["restart_host_required"] is False
    assert private_json(state / "outcome-consent.json")["enabled"] is False
    broken = tmp_path / "broken.json"
    broken.write_text('{"http_status":403,"result":{"private":"SECRET"}}')
    monkeypatch.setattr("sys.argv", ["outcomes", str(broken)])
    assert main() == 2
    assert "SECRET" not in capsys.readouterr().out


def test_interrupted_snapshot_capture_recovers_from_same_submission(
    tmp_path, monkeypatch
):
    from liquilens_trading_copilot import agent_service

    real_capture = agent_service.capture_outcome

    async def run():
        path = tmp_path / "grant.json"
        async with rig(
            tmp_path, outcome_token=OUTCOME_TOKEN, outcome_consent_file=path
        ) as env:
            write_grant(env, path)
            item = await assessed(env)
            args = {"assessment_id": item["assessment_id"]}

            def fail(*args):
                raise OSError("synthetic disk failure; SECRET")

            monkeypatch.setattr(agent_service, "capture_outcome", fail)
            response = await env.client.post("/v1/orders/submit", json=args)
            assert response.status_code == 503
            assert "SECRET" not in response.text
            assert len(env.sdk.orders) == 1
            monkeypatch.setattr(agent_service, "capture_outcome", real_capture)
            # Recovery reads durable status, never tries a fresh order.
            response = await env.client.post("/v1/orders/status", json=args)
            assert response.status_code == 200
            report = summarize_pages(await all_pages(env))
            assert report["counts"]["broker_acknowledged"] == 1
            assert report["counts"]["no_broker_observation"] == 1
            assert len(env.sdk.orders) == 1

    asyncio.run(run())


def test_export_cannot_race_an_inflight_broker_worker(tmp_path):
    async def run():
        path = tmp_path / "grant.json"
        async with rig(
            tmp_path, outcome_token=OUTCOME_TOKEN, outcome_consent_file=path
        ) as env:
            write_grant(env, path)
            item = await assessed(env)
            env.sdk.wait = True
            pending = asyncio.create_task(
                env.service.run("submit", {"assessment_id": item["assessment_id"]})
            )
            try:
                assert await asyncio.to_thread(env.sdk.entered.wait, 3)
                response = await export(env)
                assert response.status_code == 503
                assert response.json()["error"] == "operator_lane_busy"
            finally:
                env.sdk.release.set()
                await pending
            assert (await export(env)).status_code == 200
            assert len(env.sdk.orders) == 1

    asyncio.run(run())


def test_saved_export_can_be_summarized_using_documented_cli(
    tmp_path, monkeypatch, capsys
):
    async def run():
        path = tmp_path / "grant.json"
        async with rig(
            tmp_path, outcome_token=OUTCOME_TOKEN, outcome_consent_file=path
        ) as env:
            write_grant(env, path)
            await assessed(env)
            response = await export(env)
            page_path = tmp_path / "page.json"
            page_path.write_text(
                json.dumps({"http_status": 200, "result": response.json()})
            )
            monkeypatch.setattr("sys.argv", ["outcomes", str(page_path)])
            assert main() == 0
            report = json.loads(capsys.readouterr().out)
            assert report["counts"]["assessed"] == 1
            assert report["consent"]["traffic_class"] == "synthetic"

    asyncio.run(run())
