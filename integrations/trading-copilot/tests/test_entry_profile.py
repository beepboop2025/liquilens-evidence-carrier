"""Synthetic native SELL scenario proofs, with no source or broker network I/O."""

from __future__ import annotations

import asyncio
import copy
import runpy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from liquilens_evidence import TradeSafetyExecutionBinding
from liquilens_evidence.trade_safety import (
    TradeSafetyError,
    issue_trade_safety_receipt,
    trade_safety_policy_hash,
    trade_safety_request_hash,
)
from trade_safety_gateway.app import (
    LIQUILENS_BASE_URL,
    UNDERTOW_URL,
    TradeSafetyGateway,
)

from liquilens_trading_copilot.config import default_policy
from liquilens_trading_copilot.entry_profile import (
    ENTRY_PROFILE_ID,
    EntryProfileError,
    build_liquidation_scenario,
    project_liquidation_section,
    verify_entry_profile_receipt,
)

ROOT = Path(__file__).resolve().parents[3]
NATIVE = runpy.run_path(
    str(ROOT / "integrations/trade-safety-gateway/tests/test_gateway.py")
)
NOW = NATIVE["NOW"]
KEY = b"synthetic-distinct-private-funding-exit-profile"


def original(side: str = "buy") -> dict[str, Any]:
    request = NATIVE["_request"]()
    request["order"]["side"] = side
    request["order"]["instrument"]["identifiers"] = {}
    request["policy_ref"] = {"policy_id": ENTRY_PROFILE_ID, "version": "1.0.0"}
    return request


def setup(
    side: str = "buy",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], TradeSafetyExecutionBinding]:
    request = original(side)
    policy = default_policy()
    policy["policy_id"] = ENTRY_PROFILE_ID
    policy["required_products"].append("liquilens")
    # Core validation accepts the separately scoped funding policy. This helper
    # does not relabel the legacy LiquiLens institution contract as funding data.
    policy["max_evidence_age_seconds"]["liquilens"] = 8 * 86400
    execution = TradeSafetyExecutionBinding(
        **{
            name: request["agent"][name]
            for name in (
                "account_id",
                "tenant_id",
                "operator_id",
                "agent_id",
                "runtime",
                "strategy_id",
            )
        },
        policy_id=ENTRY_PROFILE_ID,
        policy_version="1.0.0",
        policy_hash=trade_safety_policy_hash(policy),
        issuer_name="synthetic-private-funding-exit",
        issuer_version="1.0.0",
        issuer_endpoint="https://operator.example.test/funding-exit",
        hmac_key_id="synthetic-private-funding-exit-v1",
    )
    # Acquire real parser output using its ordinary admitted policy; these are
    # independent synthetic native-section fixtures, not the new service.
    parse_policy = NATIVE["_policy"]()
    parse_policy["policy_id"] = ENTRY_PROFILE_ID
    upstream = NATIVE["FakeUpstream"]()
    source = asyncio.run(
        TradeSafetyGateway(upstream, clock=lambda: NOW).assess(request, parse_policy)
    )
    scenario = build_liquidation_scenario(request)
    upstream.responses[UNDERTOW_URL] = NATIVE["_undertow_bytes"](
        request_hash=trade_safety_request_hash(scenario)
    )
    scenario_receipt = asyncio.run(
        TradeSafetyGateway(upstream, clock=lambda: NOW).assess(scenario, parse_policy)
    )
    section = scenario_receipt["evidence"]["undertow"]
    evidence = source["evidence"]
    evidence["undertow"] = project_liquidation_section(request, scenario, section)
    # An unavailable third product remains unavailable even with a valid exit
    # association. Full funding-source composition belongs to the scoped service.
    assert evidence["liquilens"]["source_url"].startswith(LIQUILENS_BASE_URL)
    receipt = issue_trade_safety_receipt(
        request=request,
        evidence=evidence,
        policy=policy,
        broker_preview=source["broker_preview"],
        evaluated_at=NOW,
        issuer={
            "name": execution.issuer_name,
            "version": execution.issuer_version,
            "endpoint": execution.issuer_endpoint,
        },
        ttl_seconds=60,
        hmac_key=KEY,
        hmac_key_id=execution.hmac_key_id,
    )
    return request, section, receipt, execution


def resign(receipt: dict[str, Any]) -> dict[str, Any]:
    return issue_trade_safety_receipt(
        request=receipt["request"],
        evidence=receipt["evidence"],
        policy=receipt["policy"],
        broker_preview=receipt["broker_preview"],
        evaluated_at=NOW,
        issuer=receipt["issuer"],
        ttl_seconds=60,
        hmac_key=KEY,
        hmac_key_id=receipt["integrity"]["key_id"],
    )


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_explicit_native_sell_association_retains_both_distinct_identities(
    side: str,
) -> None:
    request, section, receipt, execution = setup(side)
    scenario = build_liquidation_scenario(request)
    saved = copy.deepcopy(request)
    assert scenario == build_liquidation_scenario(request)
    assert request == saved
    assert scenario["request_id"] != request["request_id"]
    assert scenario["order"]["side"] == "sell"
    assert scenario["agent"] == request["agent"]
    assert scenario["expires_at"] == request["expires_at"]
    verified = verify_entry_profile_receipt(
        receipt, evaluated_at=NOW, hmac_key=KEY, binding=execution
    )
    derived = verified["evidence"]["undertow"]
    assert derived["request_hash"] == trade_safety_request_hash(request)
    assert derived["source_sha256"] == section["source_sha256"]
    assert derived["facts"]["native_request"]["side"] == "sell"
    assert derived["facts"]["native_request"][
        "request_hash"
    ] == trade_safety_request_hash(scenario)
    assert derived["facts"]["gateway_binding"] == section["facts"]["gateway_binding"]
    assert derived["facts"]["worst_sell_cost_bps"] == 10
    for name, value in section["facts"].items():
        assert derived["facts"][name] == value
    proof = derived["facts"]["operator_liquidation_scenario"]
    assert proof["original_side"] == side
    assert proof["original_order_hash"] != proof["scenario_request_hash"]
    assert proof["scenario_request"] == scenario
    assert proof["buy_execution_cost_available"] is False
    assert proof["fill_evidence"] is False
    assert derived["executable_quote"] is False
    assert derived["real_money_eligible"] is False
    assert verified["decision"]["outcome"] == "unavailable"


@pytest.mark.parametrize(
    "change", ["live", "large", "quantity", "legacy_policy", "currency", "venue"]
)
def test_profile_does_not_widen_legacy_order_scope(change: str) -> None:
    request = original()
    if change == "live":
        request["mode"] = "live"
    elif change == "large":
        request["order"]["notional"]["amount"] = 10_000
    elif change == "quantity":
        request["order"]["quantity"] = 0.01
    elif change == "legacy_policy":
        request["policy_ref"]["policy_id"] = "copilot-paper-v1"
    elif change == "currency":
        request["order"]["notional"]["currency"] = "EUR"
    else:
        request["order"]["venue"] = "coinbase"
    with pytest.raises(TradeSafetyError):
        build_liquidation_scenario(request)


@pytest.mark.parametrize("change", ["side", "identity", "expiry", "notional"])
def test_scenario_cannot_be_borrowed_from_another_order(change: str) -> None:
    request, section, _, _ = setup()
    scenario = build_liquidation_scenario(request)
    if change == "side":
        scenario["order"]["side"] = "buy"
    elif change == "identity":
        scenario["agent"]["account_id"] = "another-account"
    elif change == "expiry":
        scenario["expires_at"] = "2026-09-02T12:02:00Z"
    else:
        scenario["order"]["notional"]["amount"] = 10_000
    with pytest.raises(EntryProfileError, match="exact parent"):
        project_liquidation_section(request, scenario, section)


@pytest.mark.parametrize(
    "change", ["cost", "rights", "native_hash", "source_hash", "unavailable"]
)
def test_native_projection_cannot_be_forged_or_promoted(change: str) -> None:
    request, section, _, _ = setup()
    if change == "cost":
        section["facts"]["measurement"]["worst"]["sell_cost_bps"] = 0
    elif change == "rights":
        section["facts"]["rights"]["status"] = "not_reviewed"
    elif change == "native_hash":
        section["facts"]["native_request"]["request_hash"] = "b" * 64
    elif change == "source_hash":
        section["facts"]["gateway_binding"]["binding_sha256"] = "a" * 64
    else:
        section["state"] = "unavailable"
    with pytest.raises(TradeSafetyError):
        project_liquidation_section(
            request, build_liquidation_scenario(request), section
        )


@pytest.mark.parametrize(
    "change", ["original_hash", "scenario_hash", "section_hash", "profile", "missing"]
)
def test_valid_hmac_does_not_hide_wrong_scenario_crosslinks(change: str) -> None:
    _, _, receipt, execution = setup()
    facts = receipt["evidence"]["undertow"]["facts"]
    proof = facts["operator_liquidation_scenario"]
    if change == "missing":
        del facts["operator_liquidation_scenario"]
    else:
        name = {
            "original_hash": "original_order_hash",
            "scenario_hash": "scenario_request_hash",
            "section_hash": "scenario_section_sha256",
            "profile": "profile_id",
        }[change]
        proof[name] = "f" * 64
    receipt = resign(receipt)
    with pytest.raises(EntryProfileError):
        verify_entry_profile_receipt(
            receipt, evaluated_at=NOW, hmac_key=KEY, binding=execution
        )


def test_profile_rejects_hmac_expiry_binding_and_loose_policy() -> None:
    _, _, receipt, execution = setup()
    with pytest.raises(TradeSafetyError):
        verify_entry_profile_receipt(
            receipt, evaluated_at=NOW, hmac_key=b"wrong-key", binding=execution
        )
    with pytest.raises(TradeSafetyError):
        verify_entry_profile_receipt(
            receipt,
            evaluated_at=NOW + timedelta(seconds=61),
            hmac_key=KEY,
            binding=execution,
        )
    with pytest.raises(TradeSafetyError):
        verify_entry_profile_receipt(
            receipt,
            evaluated_at=NOW,
            hmac_key=KEY,
            binding=replace(execution, account_id="wrong-account"),
        )
    receipt["policy"]["max_exit_cost_bps"] = 50
    receipt = resign(receipt)
    execution = replace(
        execution, policy_hash=trade_safety_policy_hash(receipt["policy"])
    )
    with pytest.raises(EntryProfileError, match="exceeds"):
        verify_entry_profile_receipt(
            receipt, evaluated_at=NOW, hmac_key=KEY, binding=execution
        )
