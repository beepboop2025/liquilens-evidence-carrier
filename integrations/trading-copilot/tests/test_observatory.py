"""Observer invariants with native fixtures; no network, orders or state stores."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import runpy
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from liquilens_evidence.trade_safety import validate_trade_safety_request
from trade_safety_gateway.app import UNDERTOW_URL

from liquilens_trading_copilot import cli, observatory
from liquilens_trading_copilot.config import scoped_policy
from liquilens_trading_copilot.market import PAPER_ORIGIN, InputUnavailable
from liquilens_trading_copilot.observatory import (
    SCHEMA,
    PaperReadTransport,
    collect_observatory,
    format_observatory_markdown,
)
from liquilens_trading_copilot.scoped import (
    CORPORATE_URL,
    FUNDING_URL,
    ScopedUpstreamTransport,
)

ROOT = Path(__file__).resolve().parents[3]
NATIVE = runpy.run_path(
    str(ROOT / "integrations/trade-safety-gateway/tests/test_gateway.py")
)
FIXTURES = runpy.run_path(str(Path(__file__).with_name("test_funding.py")))
NOW = NATIVE["NOW"]
ACCOUNT = "11111111-2222-4333-8444-555555555555"
SECRET = "test-secret-MUST-NOT-APPEAR-IN-REPORT"


def run_report(*, change=None, fault=None, clock=None, **kwargs):
    payloads = {
        FUNDING_URL: FIXTURES["funding_payload"](NOW),
        CORPORATE_URL: FIXTURES["corporate_payload"](NOW),
    }
    if change:
        change(payloads)
    calls = []

    def handler(request):
        calls.append(request)
        assert "APCA-API-KEY-ID" not in request.headers
        assert "cookie" not in request.headers
        assert request.headers["X-LiquiLens-Synthetic"] == "true"
        if str(request.url) in payloads:
            if fault == str(request.url):
                raise httpx.ConnectError(SECRET)
            return httpx.Response(200, json=payloads[str(request.url)])
        assert request.method == "POST" and str(request.url) == UNDERTOW_URL
        rpc = json.loads(request.content)
        assert rpc["method"] == "tools/call"
        assert rpc["params"]["name"] == "trade_safety_exit_context"
        expected = rpc["params"]["arguments"]
        raw = NATIVE["_undertow_bytes"](
            request_hash=expected["request_hash"],
            worst=30 if fault == "cost_hold" else 10,
            spread=28 if fault == "cost_hold" else 8,
        )
        if fault in {
            "rights",
            "bad_digest",
            "bad_binding",
            "authority",
            "upstream_prose",
            "mcp_error",
        }:
            payload = json.loads(raw)["result"]["structuredContent"]
            payload["status"] = "unavailable"
            payload["evidence_class"] = "unavailable"
            payload["measurement"] = None
            payload["reason"] = (
                SECRET if fault == "upstream_prose" else "rights_manifest_not_approved"
            )
            if fault == "bad_binding":
                payload["request_hash"] = "f" * 64
            if fault == "authority":
                payload["authority"]["can_place_order"] = True
            payload = NATIVE["_sealed"](payload, "context_sha256")
            if fault == "bad_digest":
                payload["context_sha256"] = "f" * 64
            raw = NATIVE["_mcp_response"]("trade-safety-undertow-v1", payload)
            if fault == "mcp_error":
                envelope = json.loads(raw)
                envelope["result"]["isError"] = True
                raw = json.dumps(envelope).encode()
        return httpx.Response(
            200, content=raw, headers={"Content-Type": "application/json"}
        )

    async def collect():
        transport = ScopedUpstreamTransport(transport=httpx.MockTransport(handler))
        try:
            return await collect_observatory(
                transport=transport, clock=clock or (lambda: NOW), **kwargs
            )
        finally:
            await transport.aclose()

    return asyncio.run(collect()), calls


def assert_no_authority(report):
    for key in (
        "ready_for_order",
        "receipt_issued",
        "order_authorized",
        "order_submitted",
        "state_modified",
    ):
        assert report[key] is False
    assert report["execution_requirements_not_checked"]
    with pytest.raises(ValueError):
        validate_trade_safety_request(report["scenario"])
    assert SECRET not in json.dumps(report)


def test_all_sources_pass_without_execution_authority_or_native_clock_refresh():
    report, calls = run_report()
    assert report["schema"] == SCHEMA
    assert report["source_checks_passed"] is True
    assert report["source_policy_checks_passed"] is True
    assert report["paper_account"]["state"] == "not_checked"
    assert len(calls) == 3
    assert list(report["sources"]) == ["seiche", "liquilens", "undertow"]
    for row in report["sources"].values():
        assert row["admitted"] and row["state"] == "current"
        assert row["reported_clocks_admitted"] is True
        assert row["source_sha256"]
        assert row["as_of"] < row["retrieved_at"]
        assert row["observation_age_seconds"] > 0
    undertow = report["sources"]["undertow"]
    assert undertow["native_expires_at"] == "2026-09-02T13:59:00Z"
    assert undertow["expires_at"] != undertow["native_expires_at"]
    assert_no_authority(report)
    markdown = format_observatory_markdown(report)
    assert json.loads(markdown.split("```json\n")[1].split("\n```")[0]) == report


def test_diagnostic_hash_is_bound_to_schema_scenario_and_evaluation_clock():
    first, calls = run_report()
    second, _ = run_report(clock=lambda: NOW + timedelta(seconds=1))
    scenario = copy.deepcopy(first["scenario"])
    digest = scenario.pop("scenario_hash")
    expected = hashlib.sha256(
        json.dumps(
            {
                "schema": SCHEMA,
                "scenario": scenario,
                "evaluated_at": first["evaluated_at"],
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    assert expected == digest != second["scenario"]["scenario_hash"]
    rpc = json.loads(calls[2].content)
    assert rpc["params"]["arguments"]["request_hash"] == digest
    assert "account_id" not in json.dumps(rpc)


@pytest.mark.parametrize("url", [FUNDING_URL, CORPORATE_URL])
def test_one_transport_failure_preserves_other_two_rows(url):
    report, calls = run_report(fault=url)
    product = "seiche" if url == FUNDING_URL else "liquilens"
    assert len(calls) == 3
    assert report["source_checks_passed"] is False
    assert report["sources"][product]["state"] == "unavailable"
    assert sum(row["admitted"] for row in report["sources"].values()) == 2
    assert_no_authority(report)


def test_stale_cp_retains_old_observation_without_admitting_its_facts():
    def change(payloads):
        payloads[CORPORATE_URL]["channels"]["cp_market"]["legs"]["rollover"][
            "as_of"
        ] = "2026-08-20"

    report, _ = run_report(change=change)
    row = report["sources"]["liquilens"]
    assert row["state"] == "stale" and not row["admitted"]
    assert row["facts"] == {} and row["as_of"] is None
    assert row["reported_observation_clocks"]["cp_rollover"] == "2026-08-20T00:00:00Z"
    assert row["reported_clocks_admitted"] is False
    assert row["source_sha256"]
    assert_no_authority(report)


def test_invalid_native_contract_cannot_become_a_source_pass():
    def change(payloads):
        payloads[FUNDING_URL]["context_only"] = False

    report, _ = run_report(change=change)
    assert report["sources"]["seiche"]["state"] == "invalid"
    assert report["sources"]["liquilens"]["admitted"] is True


@pytest.mark.parametrize(
    "fault",
    ["rights", "bad_digest", "bad_binding", "authority", "upstream_prose", "mcp_error"],
)
def test_rights_denial_requires_valid_unavailable_envelope(fault):
    report, _ = run_report(fault=fault)
    row = report["sources"]["undertow"]
    assert row["admitted"] is False
    assert row["facts"] == {}
    if fault == "rights":
        assert row["state"] == "restricted"
        assert row["reason_codes"] == ["rights_manifest_not_approved"]
    elif fault == "upstream_prose":
        assert row["state"] == "unavailable"
        assert row["reason_codes"] == ["source_reported_unavailable"]
    else:
        assert row["state"] == "invalid"
    assert_no_authority(report)


def test_cost_policy_hold_does_not_relabel_valid_source_as_unavailable():
    report, _ = run_report(fault="cost_hold")
    assert report["source_checks_passed"] is True
    assert report["source_policy_checks_passed"] is False
    row = report["sources"]["undertow"]
    assert row["admitted"] and row["policy_state"] == "hold"
    assert set(row["policy_reason_codes"]) == {
        "max_exit_cost_bps_exceeded",
        "max_venue_spread_bps_exceeded",
    }
    assert_no_authority(report)


def test_funding_policy_hold_is_separate_from_contract_admission():
    def change(payloads):
        metrics = payloads[FUNDING_URL]["sections"][0]["metrics"]
        next(row for row in metrics if row["id"] == "policy.sofr")["value"] = 3.85
        next(row for row in metrics if row["id"] == "policy.sofr_minus_iorb")[
            "value"
        ] = 20.0

    report, _ = run_report(change=change)
    assert report["source_checks_passed"] is True
    assert report["source_policy_checks_passed"] is False
    assert report["sources"]["seiche"]["facts"]["regime"] == "STRAIN"
    assert_no_authority(report)


def test_default_observer_never_reads_config_credentials_state_or_submission(
    monkeypatch,
):
    def forbidden(*args, **kwargs):
        raise AssertionError("authority or state path invoked")

    monkeypatch.setattr(observatory, "load_secret_file", forbidden)
    monkeypatch.setattr(observatory, "_account_id", forbidden)
    monkeypatch.setattr(cli, "CycleStore", forbidden)
    monkeypatch.setattr(cli, "operator_lock", forbidden)
    monkeypatch.setattr(cli, "prepare_state", forbidden)
    monkeypatch.setattr(cli, "run_configured_cycle", forbidden)
    from liquilens_trading_copilot import scoped

    monkeypatch.setattr(scoped, "issue_trade_safety_receipt", forbidden)
    report, _ = run_report()
    assert report["source_checks_passed"] is True


def account_files(tmp_path):
    config = tmp_path / "config.json"
    env = tmp_path / "paper.env"
    config.write_text(
        json.dumps({"mode": "paper", "account_id": ACCOUNT, "policy": scoped_policy()})
    )
    env.write_text(
        "ALPACA_PAPER_API_KEY=synthetic-key\nALPACA_PAPER_SECRET_KEY=" + SECRET + "\n"
    )
    env.chmod(0o600)
    return config, env


def account_handler(calls, *, mismatch=False, redirect=False, count=0):
    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert str(request.url).startswith(PAPER_ORIGIN + "/v2/")
        assert request.headers["APCA-API-SECRET-KEY"] == SECRET
        if redirect:
            return httpx.Response(
                302, headers={"Location": "https://api.alpaca.markets/v2/account"}
            )
        if request.url.path == "/v2/account":
            return httpx.Response(
                200,
                json={
                    "id": "wrong" if mismatch else ACCOUNT,
                    "status": "ACTIVE",
                    "currency": "USD",
                    "trading_blocked": False,
                    "account_blocked": False,
                    "trade_suspended_by_user": False,
                    "equity": "100000",
                    "last_equity": "100000",
                    "cash": "100000",
                },
            )
        return httpx.Response(200, json=[{"symbol": "OTHER"}] * count)

    return handler


@pytest.mark.parametrize(
    "mismatch,redirect,count",
    [(False, False, 0), (True, False, 0), (False, True, 0), (False, False, 500)],
)
def test_optional_account_is_read_only_and_redacted(
    tmp_path, mismatch, redirect, count
):
    config, env = account_files(tmp_path)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    calls = []
    report, _ = run_report(
        check_account=True,
        config_path=config,
        env_path=env,
        account_transport=httpx.MockTransport(
            account_handler(calls, mismatch=mismatch, redirect=redirect, count=count)
        ),
    )
    account = report["paper_account"]
    assert account["binding_verified"] is not (mismatch or redirect or count == 500)
    assert account["broker_calls_performed"] is True
    assert len(calls) == 3
    assert report["source_checks_passed"] is True
    text = json.dumps(report)
    assert ACCOUNT not in text and "100000" not in text
    assert before == {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    if account["binding_verified"]:
        assert account["position_count"] == account["open_order_count"] == 0
        assert account["status"] == "ACTIVE" and account["currency"] == "USD"
    assert_no_authority(report)


def test_missing_account_affects_only_account_row(tmp_path):
    report, _ = run_report(
        check_account=True,
        config_path=tmp_path / "missing",
        env_path=tmp_path / "missing",
    )
    assert report["source_checks_passed"] is True
    assert report["paper_account"]["state"] == "unavailable"
    assert report["paper_account"]["broker_calls_performed"] is False


@pytest.mark.parametrize(
    "method,url",
    [
        ("POST", PAPER_ORIGIN + "/v2/orders"),
        ("DELETE", PAPER_ORIGIN + "/v2/orders"),
        ("GET", "https://api.alpaca.markets/v2/account"),
        ("GET", PAPER_ORIGIN + "/v2/orders?status=all&limit=500"),
    ],
)
def test_paper_transport_blocks_other_routes_before_network(method, url):
    def forbidden(request):
        raise AssertionError("blocked route reached network")

    async def check():
        async with httpx.AsyncClient(
            transport=PaperReadTransport(httpx.MockTransport(forbidden))
        ) as client:
            with pytest.raises(
                InputUnavailable, match="observer_paper_route_not_allowed"
            ):
                await client.request(method, url)

    asyncio.run(check())


@pytest.mark.parametrize(
    "flags",
    [
        ["--config", "/missing"],
        ["--env-file", "/missing"],
        ["--check-account"],
        ["--state-dir", "/missing"],
        ["--scenario", "candidate"],
    ],
)
def test_cli_rejects_ambiguous_observer_options(monkeypatch, flags):
    monkeypatch.setattr("sys.argv", ["copilot", "observe", *flags])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_cli_observe_does_not_fall_through_to_state(monkeypatch, capsys):
    expected, _ = run_report()

    async def fake_collect(**kwargs):
        assert kwargs == {"check_account": False, "config_path": None, "env_path": None}
        return expected

    def forbidden(*args, **kwargs):
        raise AssertionError("operator state path")

    monkeypatch.setattr(observatory, "collect_observatory", fake_collect)
    monkeypatch.setattr(cli, "load_config", forbidden)
    monkeypatch.setattr(cli, "CycleStore", forbidden)
    monkeypatch.setattr("sys.argv", ["copilot", "observe", "--format", "json"])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out) == expected


def test_all_sources_rejected_still_returns_three_explanatory_rows():
    def change(payloads):
        payloads[FUNDING_URL]["context_only"] = False
        payloads[CORPORATE_URL]["available"] = False

    report, calls = run_report(change=change, fault="rights")
    assert len(calls) == 3
    assert not any(row["admitted"] for row in report["sources"].values())
    assert all(
        row["contribution"] and row["next_action"] for row in report["sources"].values()
    )
    assert report["status"] == "source_checks_blocked"
    assert_no_authority(report)


def test_old_undertow_observation_remains_stale_even_with_native_expiry_future():
    report, _ = run_report(clock=lambda: NOW + timedelta(minutes=6))
    assert report["sources"]["undertow"]["state"] == "stale"
    assert report["sources"]["seiche"]["admitted"] is True
    assert report["sources"]["undertow"]["facts"] == {}


def test_operator_fifo_and_symlink_are_rejected_without_opening_state(tmp_path):
    import os

    config, env = account_files(tmp_path)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    linked = tmp_path / "link"
    linked.symlink_to(config)
    for path in (fifo, linked):
        report, _ = run_report(check_account=True, config_path=path, env_path=env)
        assert report["paper_account"]["state"] == "unavailable"
        assert report["paper_account"]["broker_calls_performed"] is False
        assert report["source_checks_passed"] is True


def test_default_collection_rejects_credential_paths_before_sources(tmp_path):
    with pytest.raises(ValueError, match="explicit_check"):
        asyncio.run(collect_observatory(config_path=tmp_path / "not-read"))


@pytest.mark.parametrize(
    "field,value,state",
    [
        ("withheld", True, "restricted"),
        ("stale", True, "stale"),
        ("available", False, "unavailable"),
    ],
)
def test_native_corporate_flags_preserve_specific_denial(field, value, state):
    def change(payloads):
        payloads[CORPORATE_URL]["channels"]["cp_market"][field] = value

    report, _ = run_report(change=change)
    assert report["sources"]["liquilens"]["state"] == state
    assert report["sources"]["liquilens"]["facts"] == {}
    assert_no_authority(report)


@pytest.mark.parametrize(
    "final_offset,admitted", [(239, True), (240, False), (241, False)]
)
def test_summary_rechecks_source_age_at_final_clock(final_offset, admitted):
    # Fixture Undertow source time is 11:59:00, NOW is 12:00:00.
    # All three fetches finish at age 299s; the report can cross its 300s limit.
    values = iter(
        [NOW + timedelta(seconds=239)] * 4 + [NOW + timedelta(seconds=final_offset)]
    )
    report, _ = run_report(clock=lambda: next(values))
    row = report["sources"]["undertow"]
    assert row["retrieved_at"] == "2026-09-02T12:03:59Z"
    assert row["expires_at"] == "2026-09-02T12:04:00Z"
    assert row["native_expires_at"] == "2026-09-02T13:59:00Z"
    assert row["checked_at"] == report["completed_at"]
    assert row["observation_age_seconds"] == 60 + final_offset
    assert row["admitted"] is admitted
    assert report["source_checks_passed"] is admitted
    assert report["source_policy_checks_passed"] is admitted
    assert all(
        source["checked_at"] == report["completed_at"]
        for source in report["sources"].values()
    )
    if not admitted:
        assert row["state"] == "stale"
        assert row["reason_codes"] == ["source_observation_stale_at_completion"]
        assert row["facts"] == {}
        assert row["policy_state"] == "not_checked"
    assert_no_authority(report)


def test_account_reads_finish_before_final_source_revalidation(tmp_path):
    config, env = account_files(tmp_path)
    observed = NOW + timedelta(seconds=239)
    calls = []
    base_handler = account_handler(calls)

    def account_read(request):
        nonlocal observed
        observed = NOW + timedelta(seconds=241)
        return base_handler(request)

    report, _ = run_report(
        clock=lambda: observed,
        check_account=True,
        config_path=config,
        env_path=env,
        account_transport=httpx.MockTransport(account_read),
    )
    assert len(calls) == 3
    assert report["paper_account"]["binding_verified"] is True
    row = report["sources"]["undertow"]
    assert row["retrieved_at"] == "2026-09-02T12:03:59Z"
    assert row["checked_at"] == "2026-09-02T12:04:01Z"
    assert row["admitted"] is False
    assert report["source_checks_passed"] is False
    assert report["source_policy_checks_passed"] is False
    assert_no_authority(report)


def test_local_diagnostic_expiry_rechecked_independently_of_source_age():
    values = iter([NOW] * 4 + [NOW + timedelta(seconds=61)])
    report, _ = run_report(clock=lambda: next(values))
    for row in report["sources"].values():
        assert row["observation_age_seconds"] < row["max_age_seconds"]
        assert row["state"] == "stale" and row["admitted"] is False
        assert row["reason_codes"] == ["diagnostic_context_expired_at_completion"]
        assert row["retrieved_at"] == "2026-09-02T12:00:00Z"
    assert report["source_checks_passed"] is False
    assert_no_authority(report)


def test_completion_clock_cannot_regress_before_retrieval():
    values = iter([NOW] * 4 + [NOW - timedelta(seconds=1)])
    report, _ = run_report(clock=lambda: next(values))
    assert not any(row["admitted"] for row in report["sources"].values())
    assert all(row["state"] == "invalid" for row in report["sources"].values())
    assert report["source_checks_passed"] is False
    assert_no_authority(report)
