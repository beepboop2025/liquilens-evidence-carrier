from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import pytest
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocol"
FDC3 = ROOT / "integrations" / "fdc3"

REQUEST_SCHEMA_NAME = "liquilens-trade-safety-request-v1.schema.json"
POLICY_SCHEMA_NAME = "liquilens-trade-safety-policy-v1.schema.json"
BROKER_PREVIEW_SCHEMA_NAME = "liquilens-broker-preview-reference-v1.schema.json"
RECEIPT_SCHEMA_NAME = "liquilens-trade-safety-receipt-v1.schema.json"
FDC3_SCHEMA_NAME = "com.liquilens.trade-safety-receipt.schema.json"


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _schemas() -> dict[str, dict]:
    paths = {
        REQUEST_SCHEMA_NAME: PROTOCOL / REQUEST_SCHEMA_NAME,
        POLICY_SCHEMA_NAME: PROTOCOL / POLICY_SCHEMA_NAME,
        BROKER_PREVIEW_SCHEMA_NAME: PROTOCOL / BROKER_PREVIEW_SCHEMA_NAME,
        RECEIPT_SCHEMA_NAME: PROTOCOL / RECEIPT_SCHEMA_NAME,
        FDC3_SCHEMA_NAME: FDC3 / FDC3_SCHEMA_NAME,
    }
    return {name: _load(path) for name, path in paths.items()}


def _validator(name: str) -> jsonschema.Draft202012Validator:
    schemas = _schemas()
    registry = Registry()
    for schema in schemas.values():
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return jsonschema.Draft202012Validator(
        schemas[name],
        registry=registry,
        format_checker=jsonschema.FormatChecker(),
    )


def _request(*, mode: str = "observe") -> dict:
    scopes = {
        "observe": ["orders:observe"],
        "paper": ["orders:paper"],
        "live": ["orders:live"],
    }
    return {
        "schema": "liquilens.trade-safety-request.v1",
        "request_id": "tenant/order-001",
        "created_at": "2026-09-02T06:00:00Z",
        "expires_at": "2026-09-02T06:02:00Z",
        "mode": mode,
        "agent": {
            "agent_id": "copilot-7",
            "operator_id": "operator-42",
            "tenant_id": "tenant-acme",
            "account_id": "account-3",
            "runtime": "agent-runtime/4.2.0",
            "strategy_id": None,
            "authorization_scope": scopes[mode],
        },
        "order": {
            "instrument": {
                "asset_class": "equity",
                "symbol": "EXAMPLE",
                "identifiers": {"FIGI": "BBG000EXAMPLE"},
            },
            "side": "sell",
            "order_type": "limit",
            "notional": {"amount": 25_000, "currency": "USD"},
            "quantity": 100,
            "limit_price": 250,
            "stop_price": None,
            "venue": None,
            "time_in_force": "DAY",
        },
        "policy_ref": {
            "policy_id": "tenant-standard-safety",
            "version": "2026-09-02",
        },
        "extensions": {},
    }


def _policy() -> dict:
    return {
        "schema": "liquilens.trade-safety-policy.v1",
        "policy_id": "tenant-standard-safety",
        "version": "2026-09-02",
        "required_products": ["seiche", "undertow"],
        "max_evidence_age_seconds": {
            "seiche": 900,
            "undertow": 60,
            "liquilens": 86_400,
        },
        "hold_regimes": ["STRESS"],
        "max_notional_usd": 100_000,
        "max_exit_cost_bps": 35,
        "max_venue_spread_bps": 20,
        "missing_evidence": "fail_closed",
        "live_requires_executable_quote": True,
        "live_requires_broker_preview": True,
        "auto_resize": False,
        "extensions": {},
    }


def _evidence_section(
    product: str,
    *,
    request_hash: str,
    real_money_eligible: bool = False,
    executable_quote: bool = False,
) -> dict:
    facts: dict[str, object]
    if product == "seiche":
        facts = {"regime": "CALM"}
    elif product == "undertow":
        facts = {
            "requested_size_usd": 25_000,
            "published_rung_used_usd": 50_000,
            "worst_sell_cost_bps": 8,
            "venue_spread_bps": 3,
        }
    else:
        raise AssertionError(f"unsupported eligible fixture product: {product}")
    return {
        "product": product,
        "state": "eligible",
        "evidence_class": "derived",
        "source_url": f"https://evidence.example/{product}",
        "source_schema": None,
        "source_sha256": ("1" if product == "seiche" else "2") * 64,
        "request_hash": request_hash,
        "as_of": "2026-09-02T06:00:00Z",
        "knowledge_time": "2026-09-02T06:00:05Z",
        "retrieved_at": "2026-09-02T06:00:06Z",
        "expires_at": "2026-09-02T06:05:00Z",
        "rights_status": "allowed",
        "real_money_eligible": real_money_eligible,
        "executable_quote": executable_quote,
        "limitations": ["conformance fixture; not market evidence"],
        "facts": facts,
    }


def _liquilens_not_applicable(*, request_hash: str) -> dict:
    return {
        "product": "liquilens",
        "state": "not_applicable",
        "evidence_class": "structural",
        "source_url": "https://liquilens.in/protocol/",
        "source_schema": None,
        "source_sha256": None,
        "request_hash": request_hash,
        "as_of": None,
        "knowledge_time": None,
        "retrieved_at": "2026-09-02T06:00:06Z",
        "expires_at": None,
        "rights_status": "allowed",
        "real_money_eligible": False,
        "executable_quote": False,
        "limitations": ["no covered institution relationship in request"],
        "facts": {},
    }


def _broker_preview(*, request_hash: str, state: str = "not_applicable") -> dict:
    base = {
        "schema": "liquilens.broker-preview-reference.v1",
        "state": state,
        "provider": None,
        "account_id": "account-3",
        "request_hash": request_hash,
        "preview_id": None,
        "source_url": None,
        "source_sha256": None,
        "retrieved_at": "2026-09-02T06:00:07Z",
        "expires_at": None,
        "limitations": ["conformance fixture; not a broker order"],
        "facts": {},
    }
    if state == "verified":
        base.update(
            {
                "provider": "tenant-broker",
                "preview_id": "preview-001",
                "source_url": "https://broker.example/previews/preview-001",
                "source_sha256": "b" * 64,
                "expires_at": "2026-09-02T06:01:00Z",
                "facts": {"estimated_fees_usd": 2.5},
            }
        )
    return base


def _receipt(*, mode: str = "observe") -> dict:
    request_hash = "a" * 64
    live = mode == "live"
    receipt = {
        "schema": "liquilens.trade-safety-receipt.v1",
        "canonicalization": "liquilens-hash-tree-v1",
        "receipt_id": "trade_safety_" + "d" * 24,
        "record_hash": "d" * 64,
        "evaluated_at": "2026-09-02T06:00:10Z",
        "expires_at": "2026-09-02T06:01:00Z",
        "request": _request(mode=mode),
        "request_hash": request_hash,
        "policy": _policy(),
        "policy_hash": "c" * 64,
        "evidence": {
            "seiche": _evidence_section(
                "seiche",
                request_hash=request_hash,
                real_money_eligible=live,
            ),
            "undertow": _evidence_section(
                "undertow",
                request_hash=request_hash,
                real_money_eligible=live,
                executable_quote=live,
            ),
            "liquilens": _liquilens_not_applicable(request_hash=request_hash),
        },
        "broker_preview": _broker_preview(
            request_hash=request_hash,
            state="verified" if live else "not_applicable",
        ),
        "decision": {
            "outcome": "pass",
            "enforced": mode != "observe",
            "reason_codes": ["operator_policy_satisfied"],
            "constraints": {
                "required_products": ["seiche", "undertow"],
                "max_notional_usd": 100_000,
                "max_exit_cost_bps": 35,
                "max_venue_spread_bps": 20,
                "auto_resize": False,
            },
            "summary": "Operator policy satisfied for this exact fixture order.",
            "resubmit_required": False,
        },
        "issuer": {
            "name": "liquilens-trade-safety",
            "version": "0.17.1-conformance",
            "endpoint": "https://liquilens.in/protocol/",
        },
        "integrity": {
            "profile": "hmac-sha256" if live else "sha256",
            "key_id": "tenant-key-2026-09" if live else None,
            "signature": "e" * 64 if live else None,
        },
        "authority": {
            "financial_authority": "operator_policy_check_only",
            "can_execute": False,
            "can_recommend": False,
            "can_allocate_capital": False,
            "is_credit_rating": False,
            "is_executable_quote": False,
        },
    }
    return receipt


def test_all_trade_safety_schemas_are_valid_draft_2020_12() -> None:
    expected_ids = {
        REQUEST_SCHEMA_NAME: (
            "https://liquilens.in/protocol/"
            "liquilens-trade-safety-request-v1.schema.json"
        ),
        POLICY_SCHEMA_NAME: (
            "https://liquilens.in/protocol/liquilens-trade-safety-policy-v1.schema.json"
        ),
        BROKER_PREVIEW_SCHEMA_NAME: (
            "https://liquilens.in/protocol/"
            "liquilens-broker-preview-reference-v1.schema.json"
        ),
        RECEIPT_SCHEMA_NAME: (
            "https://liquilens.in/protocol/"
            "liquilens-trade-safety-receipt-v1.schema.json"
        ),
        FDC3_SCHEMA_NAME: (
            "https://liquilens.in/protocol/fdc3/"
            "com.liquilens.trade-safety-receipt.schema.json"
        ),
    }
    for name, schema in _schemas().items():
        jsonschema.Draft202012Validator.check_schema(schema)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] == expected_ids[name]


def test_observe_live_and_fdc3_receipts_validate() -> None:
    receipt_validator = _validator(RECEIPT_SCHEMA_NAME)
    fdc3_validator = _validator(FDC3_SCHEMA_NAME)

    observe = _receipt()
    live = _receipt(mode="live")
    receipt_validator.validate(observe)
    receipt_validator.validate(live)

    context = {
        "type": "com.liquilens.trade-safety-receipt",
        "name": "Trade safety receipt for tenant/order-001",
        "id": {
            "liquilensTradeSafetyReceiptId": observe["receipt_id"],
            "tenantOrderId": "tenant/order-001",
        },
        "receiptSchema": (
            "https://liquilens.in/protocol/"
            "liquilens-trade-safety-receipt-v1.schema.json"
        ),
        "receipt": observe,
    }
    fdc3_validator.validate(context)


@pytest.mark.parametrize(
    ("order_type", "limit_price", "stop_price"),
    [
        ("market", 250, None),
        ("market", None, 245),
        ("limit", None, None),
        ("limit", 250, 245),
        ("stop", 250, 245),
        ("stop", None, None),
        ("stop_limit", 250, None),
        ("stop_limit", None, 245),
        ("other", 250, None),
    ],
)
def test_request_schema_rejects_ambiguous_price_shapes(
    order_type: str, limit_price: int | None, stop_price: int | None
) -> None:
    request = _request()
    request["order"].update(
        {
            "order_type": order_type,
            "limit_price": limit_price,
            "stop_price": stop_price,
        }
    )
    with pytest.raises(jsonschema.ValidationError):
        _validator(REQUEST_SCHEMA_NAME).validate(request)


def test_mode_scopes_and_fixed_policy_safety_switches_fail_closed() -> None:
    request_validator = _validator(REQUEST_SCHEMA_NAME)
    policy_validator = _validator(POLICY_SCHEMA_NAME)

    paper = _request(mode="paper")
    paper["agent"]["authorization_scope"] = ["orders:observe"]
    with pytest.raises(jsonschema.ValidationError):
        request_validator.validate(paper)

    live = _request(mode="live")
    live["agent"]["authorization_scope"] = ["orders:paper"]
    with pytest.raises(jsonschema.ValidationError):
        request_validator.validate(live)

    for field, unsafe_value in (
        ("missing_evidence", "best_effort"),
        ("live_requires_executable_quote", False),
        ("live_requires_broker_preview", False),
        ("auto_resize", True),
    ):
        unsafe = _policy()
        unsafe[field] = unsafe_value
        with pytest.raises(jsonschema.ValidationError):
            policy_validator.validate(unsafe)

    missing_baseline = _policy()
    missing_baseline["required_products"] = ["undertow"]
    with pytest.raises(jsonschema.ValidationError):
        policy_validator.validate(missing_baseline)


def test_broker_preview_states_are_explicit_and_request_bound() -> None:
    validator = _validator(BROKER_PREVIEW_SCHEMA_NAME)
    request_hash = "a" * 64
    validator.validate(_broker_preview(request_hash=request_hash))
    validator.validate(_broker_preview(request_hash=request_hash, state="verified"))

    incomplete = _broker_preview(request_hash=request_hash, state="verified")
    incomplete["source_sha256"] = None
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(incomplete)

    leaked = _broker_preview(request_hash=request_hash)
    leaked["provider"] = "broker-that-should-not-be-present"
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(leaked)

    malformed_hash = _broker_preview(request_hash="A" * 64)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(malformed_hash)


def test_evidence_request_binding_and_authority_cannot_be_omitted_or_widened() -> None:
    validator = _validator(RECEIPT_SCHEMA_NAME)

    missing_binding = _receipt()
    del missing_binding["evidence"]["undertow"]["request_hash"]
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(missing_binding)

    widened = _receipt()
    widened["authority"]["can_execute"] = True
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(widened)

    hidden_product = _receipt()
    del hidden_product["evidence"]["liquilens"]
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(hidden_product)


def test_hash_only_or_under_evidenced_live_pass_is_schema_invalid() -> None:
    validator = _validator(RECEIPT_SCHEMA_NAME)

    hash_only = _receipt(mode="live")
    hash_only["integrity"] = {
        "profile": "sha256",
        "key_id": None,
        "signature": None,
    }
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(hash_only)

    no_quote = _receipt(mode="live")
    no_quote["evidence"]["undertow"]["executable_quote"] = False
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(no_quote)

    no_preview = _receipt(mode="live")
    no_preview["broker_preview"] = _broker_preview(request_hash="a" * 64)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(no_preview)

    context_only = _receipt(mode="live")
    context_only["evidence"]["seiche"]["state"] = "context_only"
    context_only["evidence"]["seiche"]["real_money_eligible"] = False
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(context_only)


def test_fdc3_intent_fragments_use_namespaced_non_executing_contract() -> None:
    asset = _load(FDC3 / "trade-safety-intents.json")
    assert asset["schema"] == "liquilens.fdc3.trade-safety-intents.v1"
    assert asset["fdc3_version"] == "2.2"

    listens_for = asset["provider_interop"]["intents"]["listensFor"]
    raises = asset["consumer_interop"]["intents"]["raises"]
    assert set(listens_for) == {
        "liquilens.EvaluateTradeSafety",
        "liquilens.ViewTradeSafetyReceipt",
    }
    assert set(raises) == set(listens_for)
    assert all(
        re.fullmatch(r"[a-z][A-Za-z0-9]*\.[A-Z][A-Za-z0-9]*", intent)
        for intent in listens_for
    )
    assert listens_for["liquilens.EvaluateTradeSafety"]["resultType"] == (
        "com.liquilens.trade-safety-receipt"
    )

    safety = asset["safety_contract"]
    assert safety["fdc3_order_is_complete_request"] is False
    assert safety["reject_identifier_only_order"] is True
    assert safety["live_requires_request_bound_broker_preview"] is True
    assert safety["hash_only_live_outcome"] == "unavailable"
    assert safety["execution_side_effects"] is False
    assert safety["returns_execution_result"] is False


def test_protocol_documentation_keeps_non_authority_and_fail_closed_boundary() -> None:
    text = (ROOT / "docs" / "TRADE-SAFETY-RECEIPT-V1.md").read_text(encoding="utf-8")
    for required in (
        "there is no execution edge here",
        "current conditional or contextual LiquiLens",
        "Every section also contains `request_hash`",
        "broker preview",
        "A hash-only receipt is always `unavailable`",
        "`auto_resize` is permanently",
        'can_execute": false',
    ):
        assert required in text
