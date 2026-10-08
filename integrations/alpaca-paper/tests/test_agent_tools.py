from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any

import pytest
from liquilens_evidence import issue_trade_safety_receipt, trade_safety_request_hash
from test_adapter import (
    EVALUATED_AT,
    HMAC_KEY,
    FakeAlpacaClient,
    _binding,
    _json,
    _receipt,
)

from liquilens_alpaca_paper import (
    AlpacaPaperAgentTools,
    PaperAgentToolProtocolError,
    SQLiteAlpacaPaperSubmissionJournal,
)


def _tools(
    tmp_path: Path,
    *,
    enabled: Any = lambda: True,
    client: Any = None,
    clock: Any = lambda: EVALUATED_AT + timedelta(seconds=30),
) -> tuple[AlpacaPaperAgentTools, SQLiteAlpacaPaperSubmissionJournal, FakeAlpacaClient]:
    _, receipt = _receipt()
    client = client or FakeAlpacaClient()
    journal = SQLiteAlpacaPaperSubmissionJournal(
        tmp_path / "journal.sqlite3", clock=clock
    )
    return (
        AlpacaPaperAgentTools(
            binding=_binding(receipt),
            submission_journal=journal,
            hmac_key=HMAC_KEY,
            clock=clock,
            execution_enabled=enabled,
            api_key="synthetic-paper-key",
            secret_key="synthetic-paper-secret",
            _client_factory=lambda **kwargs: client,
        ),
        journal,
        client,
    )


def _submit(tools: AlpacaPaperAgentTools) -> dict[str, Any]:
    request, receipt = _receipt()
    return tools.call_tool(
        "submit_paper_order", {"request": request, "receipt": receipt}
    )


def test_tool_discovery_and_mcp_results_are_detached(tmp_path: Path) -> None:
    tools, journal, client = _tools(tmp_path)
    try:
        discovered = tools.list_tools()
        assert discovered["resultType"] == "complete"
        assert discovered["cacheScope"] == "private"
        assert discovered["ttlMs"] == 0
        assert len(discovered["tools"]) == 4
        annotations = {
            item["name"]: item["annotations"] for item in discovered["tools"]
        }
        assert annotations["submit_paper_order"]["destructiveHint"] is True
        assert annotations["reconcile_paper_order"]["readOnlyHint"] is False
        discovered["tools"].clear()
        assert len(tools.list_tools()["tools"]) == 4
        result = tools.call_tool("paper_execution_capabilities", {})
        assert result["resultType"] == "complete"
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
        assert result["structuredContent"]["live_execution_supported"] is False
        assert client.account_reads == 0
    finally:
        journal.close()


def test_exact_request_survives_restart_without_second_submission(
    tmp_path: Path,
) -> None:
    tools, journal, client = _tools(tmp_path)
    result = _submit(tools)
    assert result["isError"] is False
    assert result["structuredContent"]["status"] == "submitted"
    assert result["structuredContent"]["fill_status"] == "not_observed"
    journal.close()
    restarted, journal, _ = _tools(tmp_path, client=client)
    try:
        replay = _submit(restarted)["structuredContent"]
        assert replay["status"] == "already_recorded"
        assert replay["submit_attempts"] == 1
        request, _ = _receipt()
        status = restarted.call_tool("paper_order_status", {"request": request})
        assert status["structuredContent"]["submission_state"] == "submitted"
        assert len(client.submitted) == 1
    finally:
        journal.close()


@pytest.mark.parametrize("mode", ["live", "observe"])
def test_nonpaper_requests_never_reach_broker(tmp_path: Path, mode: str) -> None:
    tools, journal, client = _tools(tmp_path)
    try:
        request, receipt = _receipt()
        request["mode"] = mode
        response = tools.call_tool(
            "submit_paper_order", {"request": request, "receipt": receipt}
        )
        assert response["isError"] is True
        assert client.account_reads == 0
        assert not client.submitted
    finally:
        journal.close()


@pytest.mark.parametrize(
    "field",
    ["account_id", "tenant_id", "operator_id", "agent_id", "runtime", "strategy_id"],
)
@pytest.mark.parametrize("operation", ["paper_order_status", "reconcile_paper_order"])
def test_read_and_reconcile_enforce_the_same_principal(
    tmp_path: Path, field: str, operation: str
) -> None:
    tools, journal, client = _tools(tmp_path)
    try:
        request, _ = _receipt()
        request["agent"][field] = "another-principal"
        response = tools.call_tool(operation, {"request": request})
        assert (
            response["structuredContent"]["reason_code"] == "execution_binding_mismatch"
        )
        assert client.account_reads == 0
        assert not client.lookups
    finally:
        journal.close()


@pytest.mark.parametrize("corruption", ["order", "receipt", "expiry", "hash_only"])
def test_invalid_authority_cannot_submit(tmp_path: Path, corruption: str) -> None:
    def clock() -> datetime:
        return EVALUATED_AT + timedelta(seconds=61 if corruption == "expiry" else 30)

    tools, journal, client = _tools(tmp_path, clock=clock)
    try:
        request, receipt = _receipt()
        if corruption == "order":
            request["order"]["quantity"] = 10
        elif corruption == "receipt":
            receipt["decision"]["outcome"] = "hold"
        elif corruption == "hash_only":
            receipt = _json("receipt.paper.pass.json")
        response = tools.call_tool(
            "submit_paper_order", {"request": request, "receipt": receipt}
        )
        assert response["isError"] is True
        assert not client.submitted
        assert not journal.recovery_candidates()
    finally:
        journal.close()


def test_valid_authenticated_missing_evidence_receipt_stays_blocked(
    tmp_path: Path,
) -> None:
    tools, journal, client = _tools(tmp_path)
    try:
        request, _ = _receipt()
        evidence = _json("evidence.paper.json")
        evidence["undertow"]["state"] = "unavailable"
        receipt = issue_trade_safety_receipt(
            request=request,
            evidence=evidence,
            policy=_json("policy.paper.json"),
            broker_preview=_json("broker-preview.paper.json"),
            evaluated_at=EVALUATED_AT,
            issuer=_json("issuer.paper.json"),
            ttl_seconds=60,
            hmac_key=HMAC_KEY,
            hmac_key_id="operator-paper-key-v1",
        )
        assert receipt["decision"]["outcome"] == "unavailable"
        response = tools.call_tool(
            "submit_paper_order", {"request": request, "receipt": receipt}
        )
        assert response["isError"] is True
        assert not client.submitted
    finally:
        journal.close()


def test_timeout_stays_uncertain_until_lookup_and_cannot_be_retried(
    tmp_path: Path,
) -> None:
    tools, journal, client = _tools(tmp_path)
    try:
        client.submit_error = TimeoutError("SECRET credential-bearing broker body")
        result = _submit(tools)
        assert result["structuredContent"]["status"] == "uncertain"
        assert result["structuredContent"]["next_tool"] == "reconcile_paper_order"
        assert "SECRET" not in json.dumps(result)
        assert _submit(tools)["structuredContent"]["status"] == "already_recorded"
        request, receipt = _receipt()
        another = copy.deepcopy(request)
        another["request_id"] = "another-attempt"
        blocked = tools.call_tool(
            "submit_paper_order", {"request": another, "receipt": receipt}
        )
        assert (
            blocked["structuredContent"]["reason_code"] == "unresolved_paper_submission"
        )
        client.lookup_error = TimeoutError("SECRET")
        result = tools.call_tool("reconcile_paper_order", {"request": request})
        assert result["isError"] is True
        assert "SECRET" not in json.dumps(result)
        assert journal.recovery_candidates()[0].state == "uncertain"
        client.lookup_error = None
        result = tools.call_tool("reconcile_paper_order", {"request": request})
        assert result["structuredContent"]["status"] == "reconciled"
        assert (
            result["structuredContent"]["reconciliation_resolution"]
            == "broker_order_found"
        )
        assert result["structuredContent"]["fill_status"] == "not_observed"
        assert not journal.recovery_candidates()
        assert len(client.submitted) == 1
    finally:
        journal.close()


@pytest.mark.parametrize("enabled", [lambda: False, lambda: "true", lambda: 1])
def test_disabled_or_ambiguous_enable_never_contacts_broker(
    tmp_path: Path, enabled: Any
) -> None:
    tools, journal, client = _tools(tmp_path, enabled=enabled)
    try:
        assert (
            _submit(tools)["structuredContent"]["reason_code"]
            == "operator_execution_disabled"
        )
        assert client.account_reads == 0
    finally:
        journal.close()


def test_default_is_disabled_and_enable_failure_is_closed(tmp_path: Path) -> None:
    _, receipt = _receipt()
    client = FakeAlpacaClient()
    with SQLiteAlpacaPaperSubmissionJournal(tmp_path / "journal.sqlite3") as journal:
        tools = AlpacaPaperAgentTools(
            binding=_binding(receipt),
            submission_journal=journal,
            hmac_key=HMAC_KEY,
            clock=lambda: EVALUATED_AT,
            _client_factory=lambda **kwargs: client,
        )
        assert (
            _submit(tools)["structuredContent"]["reason_code"]
            == "operator_execution_disabled"
        )
        assert client.account_reads == 0

    def broken() -> bool:
        raise OSError("SECRET")

    tools, journal, client = _tools(tmp_path, enabled=broken)
    try:
        assert (
            _submit(tools)["structuredContent"]["reason_code"]
            == "operator_execution_disabled"
        )
        assert client.account_reads == 0
    finally:
        journal.close()


def test_stop_during_account_lookup_retains_claim_and_reconciles_without_submission(
    tmp_path: Path,
) -> None:
    enabled = True

    class StoppingClient(FakeAlpacaClient):
        def get_account(self) -> dict[str, str]:
            nonlocal enabled
            enabled = False
            return super().get_account()

    tools, journal, client = _tools(
        tmp_path, enabled=lambda: enabled, client=StoppingClient()
    )
    try:
        result = _submit(tools)
        assert (
            result["structuredContent"]["reason_code"] == "operator_execution_disabled"
        )
        assert not client.submitted
        assert journal.recovery_candidates()[0].state == "claimed"
        request, _ = _receipt()
        result = tools.call_tool("reconcile_paper_order", {"request": request})
        assert (
            result["structuredContent"]["reconciliation_resolution"] == "not_submitted"
        )
        assert not client.lookups
        enabled = True
        assert _submit(tools)["structuredContent"]["status"] == "already_recorded"
        assert not client.submitted
    finally:
        journal.close()


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("live_submit", {}),
        ("submit_paper_order", {"request": {}, "receipt": {}, "clock": "2000-01-01"}),
        ("paper_execution_capabilities", {"enabled": True}),
        ("paper_order_status", {"request": []}),
        ("paper_order_status", {"request": {"value": float("nan")}}),
        ("paper_order_status", {"request": {"value": "x" * 2_097_152}}),
    ],
)
def test_invalid_transport_inputs_are_rejected_without_side_effects(
    tmp_path: Path, name: str, arguments: Any
) -> None:
    tools, journal, client = _tools(tmp_path)
    try:
        with pytest.raises(PaperAgentToolProtocolError):
            tools.call_tool(name, arguments)
        assert client.account_reads == 0
        assert not client.submitted
    finally:
        journal.close()


def test_unknown_status_and_reconciliation_do_not_query_broker(tmp_path: Path) -> None:
    tools, journal, client = _tools(tmp_path)
    try:
        request, _ = _receipt()
        for operation in ("paper_order_status", "reconcile_paper_order"):
            result = tools.call_tool(operation, {"request": request})
            assert result["structuredContent"]["status"] == "not_recorded"
            assert result["structuredContent"][
                "request_hash"
            ] == trade_safety_request_hash(request)
        assert client.account_reads == 0
        assert not client.lookups
    finally:
        journal.close()


def test_concurrent_calls_cannot_reconcile_an_inflight_claim(tmp_path: Path) -> None:
    entered, release = Event(), Event()

    class SlowClient(FakeAlpacaClient):
        def submit_order(self, order_data: Any) -> dict[str, str]:
            entered.set()
            assert release.wait(timeout=5)
            return super().submit_order(order_data)

    tools, journal, client = _tools(tmp_path, client=SlowClient())
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(_submit, tools)
            try:
                assert entered.wait(timeout=5)
                request, _ = _receipt()
                for name in ("paper_order_status", "reconcile_paper_order"):
                    result = tools.call_tool(name, {"request": request})
                    assert (
                        result["structuredContent"]["reason_code"]
                        == "operator_lane_busy"
                    )
            finally:
                release.set()
            assert (
                pending.result(timeout=5)["structuredContent"]["status"] == "submitted"
            )
        assert len(client.submitted) == 1
    finally:
        journal.close()
