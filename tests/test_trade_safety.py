from __future__ import annotations

import copy
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from referencing import Registry, Resource

import liquilens_evidence
import liquilens_evidence.trade_safety as trade_safety_runtime
from liquilens_evidence.mcp_server import (
    MCP_PROTOCOL_VERSION,
    EvidenceCarrierMCPServer,
)
from liquilens_evidence.protocol_resources import load_protocol_json
from liquilens_evidence.trade_safety import (
    BROKER_PREVIEW_REFERENCE_SCHEMA,
    TRADE_SAFETY_POLICY_SCHEMA,
    TRADE_SAFETY_RECEIPT_SCHEMA,
    TRADE_SAFETY_REQUEST_SCHEMA,
    TradeSafetyError,
    TradeSafetyOutcome,
    issue_trade_safety_receipt,
    trade_safety_request_hash,
    verify_trade_safety_receipt,
)

ROOT = Path(__file__).resolve().parents[1]
EVALUATED_AT = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
EVALUATED_AT_TEXT = "2026-09-02T12:00:00Z"
HMAC_KEY = b"tenant-local-trade-safety-test-key"


def _request(
    *,
    mode: str = "paper",
    amount: float = 1_000.0,
    order_type: str = "limit",
) -> dict[str, Any]:
    scope = {
        "observe": ["evidence:read"],
        "paper": ["evidence:read", "orders:paper"],
        "live": ["evidence:read", "orders:live"],
    }[mode]
    return {
        "schema": TRADE_SAFETY_REQUEST_SCHEMA,
        "request_id": f"order-check-{mode}-001",
        "created_at": "2026-09-02T11:59:00Z",
        "expires_at": "2026-09-02T12:10:00Z",
        "mode": mode,
        "agent": {
            "agent_id": "copilot-alpha",
            "operator_id": "operator-001",
            "tenant_id": "tenant-001",
            "account_id": "paper-account-001" if mode != "live" else "live-account-001",
            "runtime": "fixture-runtime/1.0",
            "strategy_id": "strategy-001",
            "authorization_scope": scope,
        },
        "order": {
            "instrument": {
                "asset_class": "equity",
                "symbol": "ACME",
                "identifiers": {"figi": "BBG000000001"},
            },
            "side": "buy",
            "order_type": order_type,
            "notional": {"amount": amount, "currency": "USD"},
            "quantity": 10.0,
            "limit_price": 100.0 if order_type in {"limit", "stop_limit"} else None,
            "stop_price": 95.0 if order_type in {"stop", "stop_limit"} else None,
            "venue": "XNAS",
            "time_in_force": "DAY",
        },
        "policy_ref": {"policy_id": "tenant-default", "version": "1.0.0"},
        "extensions": {},
    }


def _policy() -> dict[str, Any]:
    return {
        "schema": TRADE_SAFETY_POLICY_SCHEMA,
        "policy_id": "tenant-default",
        "version": "1.0.0",
        "required_products": ["seiche", "undertow"],
        "max_evidence_age_seconds": {
            "seiche": 300,
            "undertow": 300,
            "liquilens": 300,
        },
        "hold_regimes": ["STRESS"],
        "max_notional_usd": 2_000.0,
        "max_exit_cost_bps": 50.0,
        "max_venue_spread_bps": 20.0,
        "missing_evidence": "fail_closed",
        "live_requires_executable_quote": True,
        "live_requires_broker_preview": True,
        "auto_resize": False,
        "extensions": {},
    }


def _evidence(
    request: dict[str, Any],
    *,
    live_eligible: bool = False,
    executable_quote: bool = False,
) -> dict[str, Any]:
    request_hash = trade_safety_request_hash(request)
    urls = {
        "seiche": "https://api.seiche.info/v2/world-markets",
        "undertow": "https://liquilens-undertow.com/evidence/order-check",
        "liquilens": "https://liquilens.in/evidence/institution-context",
    }
    hashes = {"seiche": "a" * 64, "undertow": "b" * 64, "liquilens": "c" * 64}
    facts = {
        "seiche": {"regime": "CALM"},
        "undertow": {
            "requested_size_usd": request["order"]["notional"]["amount"],
            "published_rung_used_usd": request["order"]["notional"]["amount"],
            "worst_sell_cost_bps": 25.0,
            "venue_spread_bps": 10.0,
        },
        "liquilens": {"institution_context": "not_aggregated"},
    }
    return {
        product: {
            "product": product,
            "request_hash": request_hash,
            "state": "eligible",
            "evidence_class": "derived",
            "source_url": urls[product],
            "source_schema": f"{product}.trade-safety-input.v1",
            "source_sha256": hashes[product],
            "as_of": "2026-09-02T11:58:30Z",
            "knowledge_time": "2026-09-02T11:59:00Z",
            "retrieved_at": "2026-09-02T11:59:10Z",
            "expires_at": "2026-09-02T12:05:00Z",
            "rights_status": "licensed",
            "real_money_eligible": live_eligible,
            "executable_quote": executable_quote and product == "undertow",
            "limitations": ["fixture_only_not_a_recommendation"],
            "facts": facts[product],
        }
        for product in ("seiche", "undertow", "liquilens")
    }


def _issuer() -> dict[str, str]:
    return {
        "name": "tenant-trade-safety-gateway",
        "version": "1.0.0",
        "endpoint": "https://tenant.example/trade-safety",
    }


def _broker_preview(
    request: dict[str, Any], *, verified: bool = False
) -> dict[str, Any]:
    preview: dict[str, Any] = {
        "schema": BROKER_PREVIEW_REFERENCE_SCHEMA,
        "state": "not_applicable",
        "provider": None,
        "account_id": request["agent"]["account_id"],
        "request_hash": trade_safety_request_hash(request),
        "preview_id": None,
        "source_url": None,
        "source_sha256": None,
        "retrieved_at": "2026-09-02T11:59:20Z",
        "expires_at": None,
        "limitations": ["reference_only_not_an_order_instruction"],
        "facts": {},
    }
    if verified:
        preview.update(
            {
                "state": "verified",
                "provider": "fixture-broker",
                "preview_id": "preview-001",
                "source_url": "https://broker.example/previews/preview-001",
                "source_sha256": "d" * 64,
                "expires_at": "2026-09-02T12:02:00Z",
                "facts": {"preview_state": "verified"},
            }
        )
    return preview


def _issue(
    request: dict[str, Any],
    evidence: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
    broker_preview: dict[str, Any] | None = None,
    hmac_key: bytes | None = None,
    ttl_seconds: int = 60,
) -> dict[str, Any]:
    preview = (
        broker_preview
        if broker_preview is not None
        else _broker_preview(request, verified=request["mode"] == "live")
    )
    return issue_trade_safety_receipt(
        request=request,
        evidence=evidence,
        policy=policy or _policy(),
        broker_preview=preview,
        evaluated_at=EVALUATED_AT,
        issuer=_issuer(),
        ttl_seconds=ttl_seconds,
        hmac_key=hmac_key,
        hmac_key_id="tenant-key-v1" if hmac_key is not None else None,
    )


def _modern_request(
    request_id: int,
    method: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    values = dict(params or {})
    values["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {
            "name": "trade-safety-test-client",
            "version": "1.0.0",
        },
    }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": values,
    }


def _mcp_result(response: dict[str, Any] | None) -> dict[str, Any]:
    assert response is not None
    assert "error" not in response
    value = response["result"]
    assert isinstance(value, dict)
    return value


def test_runtime_names_are_exported_from_package() -> None:
    assert liquilens_evidence.BROKER_PREVIEW_REFERENCE_SCHEMA == (
        BROKER_PREVIEW_REFERENCE_SCHEMA
    )
    assert liquilens_evidence.TRADE_SAFETY_REQUEST_SCHEMA == (
        TRADE_SAFETY_REQUEST_SCHEMA
    )
    assert liquilens_evidence.TRADE_SAFETY_POLICY_SCHEMA == TRADE_SAFETY_POLICY_SCHEMA
    assert liquilens_evidence.TRADE_SAFETY_RECEIPT_SCHEMA == (
        TRADE_SAFETY_RECEIPT_SCHEMA
    )
    assert liquilens_evidence.issue_trade_safety_receipt is issue_trade_safety_receipt
    assert liquilens_evidence.trade_safety_request_hash is trade_safety_request_hash
    assert callable(liquilens_evidence.validate_broker_preview_reference)
    assert liquilens_evidence.verify_trade_safety_receipt is (
        verify_trade_safety_receipt
    )


def test_hash_only_paper_pass_limit_hold_and_unavailable() -> None:
    request = _request()
    evidence = _evidence(request)
    passed = _issue(request, evidence)
    assert passed == _issue(request, evidence)
    verified = verify_trade_safety_receipt(passed, evaluated_at=EVALUATED_AT)
    assert verified.outcome is TradeSafetyOutcome.PASS
    assert verified.policy_satisfied is True
    assert verified.authenticated is False
    assert passed["decision"]["enforced"] is True
    assert passed["authority"] == {
        "financial_authority": "operator_policy_check_only",
        "can_execute": False,
        "can_recommend": False,
        "can_allocate_capital": False,
        "is_credit_rating": False,
        "is_executable_quote": False,
    }

    limited_policy = _policy()
    limited_policy["max_notional_usd"] = 500.0
    limited = _issue(request, evidence, policy=limited_policy)
    assert limited["decision"]["outcome"] == "limit"
    assert limited["decision"]["resubmit_required"] is True
    assert "max_notional_usd_exceeded" in limited["decision"]["reason_codes"]

    held_evidence = copy.deepcopy(evidence)
    held_evidence["seiche"]["facts"]["regime"] = "STRESS"
    held = _issue(request, held_evidence)
    assert held["decision"]["outcome"] == "hold"
    assert held["decision"]["resubmit_required"] is False

    unavailable_evidence = copy.deepcopy(evidence)
    unavailable_evidence["undertow"]["state"] = "unavailable"
    unavailable = _issue(request, unavailable_evidence)
    assert unavailable["decision"]["outcome"] == "unavailable"
    assert "undertow_evidence_unavailable" in unavailable["decision"]["reason_codes"]


def test_unsupported_request_and_policy_extensions_fail_closed() -> None:
    request = _request()
    request["extensions"] = {"acme.execution/reduce_only": True}
    with pytest.raises(TradeSafetyError, match="unsupported execution semantics"):
        trade_safety_request_hash(request)

    request = _request()
    policy = _policy()
    policy["extensions"] = {"acme.policy/deny_all": True}
    with pytest.raises(TradeSafetyError, match="unsupported operator constraints"):
        _issue(request, _evidence(request), policy=policy)


@pytest.mark.parametrize(
    ("product", "fact"),
    [
        ("seiche", "regime"),
        ("undertow", "requested_size_usd"),
        ("undertow", "published_rung_used_usd"),
        ("undertow", "worst_sell_cost_bps"),
        ("undertow", "venue_spread_bps"),
    ],
)
def test_usable_product_sections_require_normative_facts(
    product: str, fact: str
) -> None:
    request = _request()
    evidence = _evidence(request)
    del evidence[product]["facts"][fact]
    with pytest.raises(TradeSafetyError, match="requires"):
        _issue(request, evidence)


@pytest.mark.parametrize("fact", ["requested_size_usd", "published_rung_used_usd"])
def test_undertow_size_facts_must_be_positive(fact: str) -> None:
    request = _request()
    evidence = _evidence(request)
    evidence["undertow"]["facts"][fact] = 0
    with pytest.raises(TradeSafetyError, match="finite positive number"):
        _issue(request, evidence)


def test_live_fails_closed_without_authentication_or_eligible_quote() -> None:
    request = _request(mode="live")

    eligible = _evidence(request, live_eligible=True, executable_quote=True)
    unsigned = _issue(request, eligible)
    assert unsigned["decision"]["outcome"] == "unavailable"
    assert unsigned["integrity"]["profile"] == "sha256"
    assert "live_receipt_authentication_missing" in unsigned["decision"]["reason_codes"]

    ineligible = _evidence(request)
    authenticated_but_ineligible = _issue(request, ineligible, hmac_key=HMAC_KEY)
    assert authenticated_but_ineligible["decision"]["outcome"] == "unavailable"
    reasons = authenticated_but_ineligible["decision"]["reason_codes"]
    assert "seiche_not_real_money_eligible" in reasons
    assert "undertow_not_real_money_eligible" in reasons
    assert "undertow_executable_quote_unavailable" in reasons

    no_quote = _evidence(request, live_eligible=True, executable_quote=False)
    authenticated_without_quote = _issue(request, no_quote, hmac_key=HMAC_KEY)
    assert authenticated_without_quote["decision"]["outcome"] == "unavailable"
    assert (
        "undertow_executable_quote_unavailable"
        in authenticated_without_quote["decision"]["reason_codes"]
    )

    authenticated_without_preview = _issue(
        request,
        eligible,
        broker_preview=_broker_preview(request),
        hmac_key=HMAC_KEY,
    )
    assert authenticated_without_preview["decision"]["outcome"] == "unavailable"
    assert (
        "broker_preview_unavailable"
        in authenticated_without_preview["decision"]["reason_codes"]
    )


def test_semantic_freshness_future_receipts_and_rights_fail_closed() -> None:
    request = _request()

    stale_observation = _evidence(request)
    stale_observation["seiche"]["as_of"] = "2026-09-02T11:00:00Z"
    stale_observation["seiche"]["knowledge_time"] = "2026-09-02T11:59:05Z"
    stale = _issue(request, stale_observation)
    assert stale["decision"]["outcome"] == "unavailable"
    assert "seiche_evidence_too_old" in stale["decision"]["reason_codes"]

    future_retrieval = _evidence(request)
    future_retrieval["undertow"]["retrieved_at"] = "2026-09-02T12:00:01Z"
    future_retrieval["undertow"]["expires_at"] = "2026-09-02T12:05:01Z"
    future = _issue(request, future_retrieval)
    assert future["decision"]["outcome"] == "unavailable"
    assert "undertow_evidence_not_yet_retrieved" in future["decision"]["reason_codes"]

    live_request = _request(mode="live")
    metadata_only = _evidence(live_request, live_eligible=True, executable_quote=True)
    metadata_only["seiche"]["rights_status"] = "metadata_only"
    with pytest.raises(TradeSafetyError, match="allowed or licensed rights"):
        _issue(live_request, metadata_only, hmac_key=HMAC_KEY)

    future_preview = _broker_preview(live_request, verified=True)
    future_preview["retrieved_at"] = "2026-09-02T12:00:01Z"
    future_preview["expires_at"] = "2026-09-02T12:02:00Z"
    future_preview_receipt = _issue(
        live_request,
        _evidence(live_request, live_eligible=True, executable_quote=True),
        broker_preview=future_preview,
        hmac_key=HMAC_KEY,
    )
    assert future_preview_receipt["decision"]["outcome"] == "unavailable"
    assert (
        "broker_preview_not_yet_retrieved"
        in future_preview_receipt["decision"]["reason_codes"]
    )


def test_receipt_expiry_is_clamped_to_evidence_age_and_live_preview() -> None:
    request = _request()
    evidence = _evidence(request)
    policy = _policy()
    policy["max_evidence_age_seconds"]["seiche"] = 100
    receipt = _issue(request, evidence, policy=policy, ttl_seconds=60)
    assert receipt["expires_at"] == "2026-09-02T12:00:10Z"
    overlong = copy.deepcopy(receipt)
    overlong["expires_at"] = "2026-09-02T12:01:00Z"
    overlong_digest = trade_safety_runtime._receipt_digest(overlong)
    overlong["record_hash"] = overlong_digest
    overlong["receipt_id"] = f"trade_safety_{overlong_digest[:24]}"
    with pytest.raises(TradeSafetyError, match="exceeds its request, evidence"):
        verify_trade_safety_receipt(overlong, evaluated_at=EVALUATED_AT)

    verify_trade_safety_receipt(
        receipt,
        evaluated_at=EVALUATED_AT + timedelta(seconds=9),
    )
    with pytest.raises(TradeSafetyError, match="receipt is expired"):
        verify_trade_safety_receipt(
            receipt,
            evaluated_at=EVALUATED_AT + timedelta(seconds=10),
        )

    live_request = _request(mode="live")
    live_preview = _broker_preview(live_request, verified=True)
    live_preview["expires_at"] = "2026-09-02T12:00:30Z"
    live_receipt = _issue(
        live_request,
        _evidence(live_request, live_eligible=True, executable_quote=True),
        broker_preview=live_preview,
        hmac_key=HMAC_KEY,
        ttl_seconds=60,
    )
    assert live_receipt["expires_at"] == "2026-09-02T12:00:30Z"


def test_authenticated_live_pass_fixture() -> None:
    request = _request(mode="live")
    evidence = _evidence(request, live_eligible=True, executable_quote=True)
    receipt = _issue(request, evidence, hmac_key=HMAC_KEY)

    verified = verify_trade_safety_receipt(
        receipt,
        evaluated_at=EVALUATED_AT,
        hmac_key=HMAC_KEY,
    )
    assert verified.authenticated is True
    assert verified.outcome is TradeSafetyOutcome.PASS
    assert verified.policy_satisfied is True
    assert receipt["integrity"]["profile"] == "hmac-sha256"
    assert receipt["integrity"]["key_id"] == "tenant-key-v1"
    assert receipt["integrity"]["signature"] is not None


def test_tamper_order_mismatch_expiry_and_bad_hmac_fail_closed() -> None:
    request = _request()
    evidence = _evidence(request)
    receipt = _issue(request, evidence)

    order_tamper = copy.deepcopy(receipt)
    order_tamper["request"]["order"]["notional"]["amount"] = 1_001.0
    with pytest.raises(TradeSafetyError, match="request_hash"):
        verify_trade_safety_receipt(order_tamper, evaluated_at=EVALUATED_AT)

    mismatched_evidence = _evidence(request)
    mismatched_evidence["undertow"]["facts"]["requested_size_usd"] = 900.0
    mismatch = _issue(request, mismatched_evidence)
    assert mismatch["decision"]["outcome"] == "unavailable"
    assert "undertow_order_size_mismatch" in mismatch["decision"]["reason_codes"]

    wrong_rung_evidence = _evidence(request)
    wrong_rung_evidence["undertow"]["facts"][
        "published_rung_used_usd"
    ] = 1_000_000.0
    wrong_rung = _issue(request, wrong_rung_evidence)
    assert wrong_rung["decision"]["outcome"] == "unavailable"
    assert "undertow_published_rung_mismatch" in wrong_rung["decision"][
        "reason_codes"
    ]

    future_request = copy.deepcopy(receipt)
    future_request["request"]["created_at"] = "2026-09-02T12:00:30Z"
    future_request_hash = trade_safety_runtime._hash_object(future_request["request"])
    future_request["request_hash"] = future_request_hash
    for section in future_request["evidence"].values():
        section["request_hash"] = future_request_hash
    future_request["broker_preview"]["request_hash"] = future_request_hash
    future_digest = trade_safety_runtime._receipt_digest(future_request)
    future_request["record_hash"] = future_digest
    future_request["receipt_id"] = f"trade_safety_{future_digest[:24]}"
    with pytest.raises(TradeSafetyError, match="before request.created_at"):
        verify_trade_safety_receipt(future_request, evaluated_at=EVALUATED_AT)

    verify_trade_safety_receipt(
        receipt,
        evaluated_at=EVALUATED_AT + timedelta(seconds=59),
    )
    with pytest.raises(TradeSafetyError, match="receipt is expired"):
        verify_trade_safety_receipt(
            receipt,
            evaluated_at=EVALUATED_AT + timedelta(seconds=60),
        )

    live_request = _request(mode="live")
    wrong_account_preview = _broker_preview(live_request, verified=True)
    wrong_account_preview["account_id"] = "different-live-account"
    with pytest.raises(TradeSafetyError, match="account_id does not match"):
        _issue(
            live_request,
            _evidence(live_request, live_eligible=True, executable_quote=True),
            broker_preview=wrong_account_preview,
            hmac_key=HMAC_KEY,
        )

    expired_preview = _broker_preview(live_request, verified=True)
    expired_preview["expires_at"] = "2026-09-02T11:59:30Z"
    preview_expired_receipt = _issue(
        live_request,
        _evidence(live_request, live_eligible=True, executable_quote=True),
        broker_preview=expired_preview,
        hmac_key=HMAC_KEY,
    )
    assert preview_expired_receipt["decision"]["outcome"] == "unavailable"
    assert (
        "broker_preview_expired" in preview_expired_receipt["decision"]["reason_codes"]
    )

    live_receipt = _issue(
        live_request,
        _evidence(live_request, live_eligible=True, executable_quote=True),
        hmac_key=HMAC_KEY,
    )
    with pytest.raises(TradeSafetyError, match="HMAC signature is invalid"):
        verify_trade_safety_receipt(
            live_receipt,
            evaluated_at=EVALUATED_AT,
            hmac_key=b"wrong-tenant-key",
        )


def test_protocol_json_schemas_accept_runtime_receipt_when_available() -> None:
    names = (
        "liquilens-trade-safety-request-v1.schema.json",
        "liquilens-trade-safety-policy-v1.schema.json",
        "liquilens-broker-preview-reference-v1.schema.json",
        "liquilens-trade-safety-receipt-v1.schema.json",
    )
    missing = [name for name in names if not (ROOT / "protocol" / name).is_file()]
    if missing:
        pytest.skip("trade-safety protocol schemas are being added separately")

    request_schema, policy_schema, broker_preview_schema, receipt_schema = (
        load_protocol_json(name) for name in names
    )
    registry = Registry()
    for schema in (
        request_schema,
        policy_schema,
        broker_preview_schema,
        receipt_schema,
    ):
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))

    request = _request(mode="live")
    receipt = _issue(
        request,
        _evidence(request, live_eligible=True, executable_quote=True),
        hmac_key=HMAC_KEY,
    )
    format_checker = jsonschema.FormatChecker()
    jsonschema.Draft202012Validator(
        request_schema,
        registry=registry,
        format_checker=format_checker,
    ).validate(request)
    jsonschema.Draft202012Validator(
        policy_schema,
        registry=registry,
        format_checker=format_checker,
    ).validate(_policy())
    jsonschema.Draft202012Validator(
        broker_preview_schema,
        registry=registry,
        format_checker=format_checker,
    ).validate(_broker_preview(request, verified=True))
    jsonschema.Draft202012Validator(
        receipt_schema,
        registry=registry,
        format_checker=format_checker,
    ).validate(receipt)


def test_cli_issues_and_verifies_hash_only_and_authenticated_receipts(
    tmp_path: Path,
) -> None:
    def write_json(name: str, value: dict[str, Any]) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    request = _request()
    paths = {
        "request": write_json("request.json", request),
        "evidence": write_json("evidence.json", _evidence(request)),
        "policy": write_json("policy.json", _policy()),
        "broker_preview": write_json("broker-preview.json", _broker_preview(request)),
        "issuer": write_json("issuer.json", _issuer()),
    }
    issued = subprocess.run(
        [
            sys.executable,
            "-m",
            "liquilens_evidence.evidence_cli",
            "issue-trade-safety",
            "--request",
            str(paths["request"]),
            "--evidence",
            str(paths["evidence"]),
            "--policy",
            str(paths["policy"]),
            "--broker-preview",
            str(paths["broker_preview"]),
            "--issuer",
            str(paths["issuer"]),
            "--as-of",
            EVALUATED_AT_TEXT,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert issued.returncode == 0, issued.stderr
    receipt = json.loads(issued.stdout)
    assert receipt["decision"]["outcome"] == "pass"
    assert receipt["integrity"]["profile"] == "sha256"
    receipt_path = write_json("receipt.json", receipt)

    verified = subprocess.run(
        [
            sys.executable,
            "-m",
            "liquilens_evidence.evidence_cli",
            "verify-trade-safety",
            str(receipt_path),
            "--as-of",
            EVALUATED_AT_TEXT,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stderr
    result = json.loads(verified.stdout)
    assert result["receipt_id"] == receipt["receipt_id"]
    assert result["outcome"] == "pass"
    assert result["authenticated"] is False

    live_request = _request(mode="live")
    paths["request"] = write_json("live-request.json", live_request)
    paths["evidence"] = write_json(
        "live-evidence.json",
        _evidence(live_request, live_eligible=True, executable_quote=True),
    )
    paths["broker_preview"] = write_json(
        "live-broker-preview.json", _broker_preview(live_request, verified=True)
    )
    key_path = tmp_path / "tenant.key"
    key_path.write_bytes(HMAC_KEY)
    signed = subprocess.run(
        [
            sys.executable,
            "-m",
            "liquilens_evidence.evidence_cli",
            "issue-trade-safety",
            "--request",
            str(paths["request"]),
            "--evidence",
            str(paths["evidence"]),
            "--policy",
            str(paths["policy"]),
            "--broker-preview",
            str(paths["broker_preview"]),
            "--issuer",
            str(paths["issuer"]),
            "--as-of",
            EVALUATED_AT_TEXT,
            "--hmac-key-file",
            str(key_path),
            "--hmac-key-id",
            "tenant-key-v1",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert signed.returncode == 0, signed.stderr
    signed_receipt = json.loads(signed.stdout)
    assert signed_receipt["decision"]["outcome"] == "pass"
    signed_path = write_json("signed-receipt.json", signed_receipt)

    signed_verified = subprocess.run(
        [
            sys.executable,
            "-m",
            "liquilens_evidence.evidence_cli",
            "verify-trade-safety",
            str(signed_path),
            "--as-of",
            EVALUATED_AT_TEXT,
            "--hmac-key-file",
            str(key_path),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert signed_verified.returncode == 0, signed_verified.stderr
    assert json.loads(signed_verified.stdout)["authenticated"] is True

    missing_key = subprocess.run(
        [
            sys.executable,
            "-m",
            "liquilens_evidence.evidence_cli",
            "verify-trade-safety",
            str(signed_path),
            "--as-of",
            EVALUATED_AT_TEXT,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert missing_key.returncode == 2
    assert "hmac_key is required" in missing_key.stderr


def test_mcp_verifies_hash_only_receipt_and_rejects_signed_live_receipt(
    tmp_path: Path,
) -> None:
    request = _request()
    receipt = _issue(request, _evidence(request))
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    server = EvidenceCarrierMCPServer(tmp_path)
    listed = _mcp_result(server.handle(_modern_request(1, "tools/list")))
    tools = {tool["name"]: tool for tool in listed["tools"]}
    tool = tools["verify_trade_safety_receipt"]
    assert tool["annotations"] == {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    assert tool["inputSchema"]["required"] == ["path", "evaluated_at"]
    assert "HMAC receipts fail closed" in tool["description"]

    resources = _mcp_result(server.handle(_modern_request(5, "resources/list")))[
        "resources"
    ]
    receipt_schema_uri = "liquilens-evidence://protocol/trade-safety-receipt-schema"
    assert receipt_schema_uri in {resource["uri"] for resource in resources}
    schema_resource = _mcp_result(
        server.handle(_modern_request(6, "resources/read", {"uri": receipt_schema_uri}))
    )
    assert json.loads(schema_resource["contents"][0]["text"])["$id"].endswith(
        "liquilens-trade-safety-receipt-v1.schema.json"
    )

    verified = _mcp_result(
        server.handle(
            _modern_request(
                2,
                "tools/call",
                {
                    "name": "verify_trade_safety_receipt",
                    "arguments": {
                        "path": receipt_path.name,
                        "evaluated_at": EVALUATED_AT_TEXT,
                    },
                },
            )
        )
    )
    assert verified["isError"] is False
    structured = verified["structuredContent"]
    assert structured["receipt_id"] == receipt["receipt_id"]
    assert structured["outcome"] == "pass"
    assert structured["authenticated"] is False
    assert structured["authority"]["can_execute"] is False

    live_request = _request(mode="live")
    signed_receipt = _issue(
        live_request,
        _evidence(live_request, live_eligible=True, executable_quote=True),
        hmac_key=HMAC_KEY,
    )
    signed_path = tmp_path / "signed-receipt.json"
    signed_path.write_text(json.dumps(signed_receipt), encoding="utf-8")
    rejected = _mcp_result(
        server.handle(
            _modern_request(
                3,
                "tools/call",
                {
                    "name": "verify_trade_safety_receipt",
                    "arguments": {
                        "path": signed_path.name,
                        "evaluated_at": EVALUATED_AT_TEXT,
                    },
                },
            )
        )
    )
    assert rejected["isError"] is True
    assert rejected["structuredContent"]["error"]["code"] == (
        "trade_safety_receipt_rejected"
    )
    assert "hmac_key is required" in rejected["content"][0]["text"]

    secret_argument = _mcp_result(
        server.handle(
            _modern_request(
                4,
                "tools/call",
                {
                    "name": "verify_trade_safety_receipt",
                    "arguments": {
                        "path": receipt_path.name,
                        "evaluated_at": EVALUATED_AT_TEXT,
                        "hmac_key": "must-not-be-accepted",
                    },
                },
            )
        )
    )
    assert secret_argument["isError"] is True
    assert "unsupported arguments: hmac_key" in secret_argument["content"][0]["text"]
