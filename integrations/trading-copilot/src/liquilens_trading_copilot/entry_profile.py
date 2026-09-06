"""Explicit private paper funding/exit context; never buy-side execution data.

These helpers do not fetch sources, issue receipts, or submit orders. The caller
must first parse fresh native Undertow bytes using the existing gateway parser.
The transform retains the native SELL identity and independently revalidates its
canonical context before associating that hypothetical exit with an exact order.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from liquilens_evidence import TradeSafetyExecutionBinding
from liquilens_evidence.order_guard import _assert_execution_binding
from liquilens_evidence.trade_safety import (
    TradeSafetyError,
    _validate_evidence_section,
    trade_safety_request_hash,
    validate_trade_safety_request,
    verify_trade_safety_receipt,
)
from trade_safety_gateway.app import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    UNDERTOW_URL,
    RawUpstreamResponse,
    _projected_section,
    _undertow_contract_request,
)
from trade_safety_gateway.upstream_contracts import (
    UNDERTOW_SCHEMA,
    UNDERTOW_SCHEMA_URL,
    parse_undertow_context,
)

from .evidence import _json, _timestamp

ENTRY_PROFILE_ID = "liquilens.paper-funding-exit.v1"
ENTRY_PROFILE_POLICY_VERSION = "1.0.0"
_PROOF_KEY = "operator_liquidation_scenario"
_GATEWAY_LIMITATIONS = (
    "gateway_receipt_binds_native_context_to_canonical_request_hash",
    "gateway_expiry_is_bounded_by_native_expiry",
)
_PROFILE_LIMITATIONS = (
    "private_paper_profile_associates_distinct_native_sell_scenario_with_original_order",
    "hypothetical_sell_cost_is_not_buy_execution_cost_or_spread",
    "hypothetical_exit_context_is_not_a_fill_or_future_liquidity_guarantee",
)


class EntryProfileError(TradeSafetyError):
    """A private entry/exit association could not prove its exact scope."""


def _snapshot(
    value: Mapping[str, Any], limit: int = MAX_RESPONSE_BYTES
) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(_json(dict(value), limit=limit))
    return result


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _json(dict(value), limit=MAX_RESPONSE_BYTES).encode()
    ).hexdigest()


def _original(value: Mapping[str, Any]) -> dict[str, Any]:
    request = validate_trade_safety_request(_snapshot(value, MAX_REQUEST_BYTES))
    order = request["order"]
    if (
        request["mode"] != "paper"
        or "orders:paper" not in request["agent"]["authorization_scope"]
        or "orders:live" in request["agent"]["authorization_scope"]
        or request["policy_ref"]
        != {"policy_id": ENTRY_PROFILE_ID, "version": ENTRY_PROFILE_POLICY_VERSION}
        or order["instrument"]
        != {"asset_class": "crypto", "symbol": "BTC/USD", "identifiers": {}}
        or order["side"] not in {"buy", "sell"}
        or order["order_type"] != "market"
        or order["notional"] != {"amount": 1000, "currency": "USD"}
        or any(
            order[field] is not None
            for field in ("quantity", "venue", "limit_price", "stop_price")
        )
        or order["time_in_force"] != "IOC"
    ):
        raise EntryProfileError(
            "entry profile requires exact private paper BTC/USD $1,000 market order"
        )
    return request


def build_liquidation_scenario(original_request: Mapping[str, Any]) -> dict[str, Any]:
    """Build a deterministic, distinct SELL scenario without changing its parent."""
    original = _original(original_request)
    scenario = _snapshot(original, MAX_REQUEST_BYTES)
    scenario["request_id"] = str(
        uuid5(
            NAMESPACE_URL,
            ENTRY_PROFILE_ID + ":liquidation:" + trade_safety_request_hash(original),
        )
    )
    scenario["order"]["side"] = "sell"
    if trade_safety_request_hash(scenario) == trade_safety_request_hash(original):
        raise EntryProfileError("liquidation scenario must have a distinct identity")
    return scenario


def _validated_scenario_section(
    scenario: dict[str, Any], supplied: Mapping[str, Any]
) -> dict[str, Any]:
    """Recheck native semantics and canonical context, not just caller labels.

    The native context's fields survive gateway projection. Reconstructing that
    canonical document checks its native digest, rights, clocks and measurements.
    The raw HTTP body hash is retained; this is not an independent network fetch.
    """
    section = _validate_evidence_section("undertow", _snapshot(supplied))
    scenario_hash = trade_safety_request_hash(scenario)
    if (
        section["state"] != "context_only"
        or section["request_hash"] != scenario_hash
        or section["source_schema"] != UNDERTOW_SCHEMA
        or section["source_url"] != UNDERTOW_URL
        or section["real_money_eligible"] is not False
        or section["executable_quote"] is not False
        or _PROOF_KEY in section["facts"]
        or any(item in section["limitations"] for item in _PROFILE_LIMITATIONS)
    ):
        raise EntryProfileError("scenario section must retain native sell context")
    facts = section["facts"]
    native = {
        "schema": UNDERTOW_SCHEMA,
        "schema_url": UNDERTOW_SCHEMA_URL,
        "status": "available",
        "reason": None,
        "request_hash": scenario_hash,
        "request": facts.get("native_request"),
        "evidence_class": "derived",
        **{
            name: facts.get(name)
            for name in (
                "measurement",
                "coverage",
                "peg",
                "source",
                "pit",
                "clocks",
                "rights",
                "authority",
                "context_sha256",
            )
        },
        "limitations": [
            item for item in section["limitations"] if item not in _GATEWAY_LIMITATIONS
        ],
    }
    try:
        projected = parse_undertow_context(
            native,
            expected_request=_undertow_contract_request(scenario, scenario_hash),
            request_hash=scenario_hash,
            retrieved_at=_timestamp(section["retrieved_at"]),
            max_age_seconds=300,
            source_url=UNDERTOW_URL,
        )
        expected = _projected_section(
            product="undertow",
            raw=RawUpstreamResponse(body=b""),
            request_hash=scenario_hash,
            retrieved_at=_timestamp(section["retrieved_at"]),
            request_expires_at=_timestamp(scenario["expires_at"]),
            projected=projected,
            source_url=UNDERTOW_URL,
            local_expiry_limitation=_GATEWAY_LIMITATIONS[1],
        )
    except (ValueError, KeyError, TypeError) as error:
        raise EntryProfileError(
            "native liquidation context failed revalidation"
        ) from error
    # The surrounding JSON-RPC body is not reconstructed or falsely rehashed.
    expected["source_sha256"] = section["source_sha256"]
    if expected != section:
        raise EntryProfileError(
            "scenario section differs from verified native projection"
        )
    return section


def project_liquidation_section(
    original_request: Mapping[str, Any],
    scenario_request: Mapping[str, Any],
    scenario_section: Mapping[str, Any],
) -> dict[str, Any]:
    """Associate proven hypothetical SELL context; preserve all native facts."""
    original = _original(original_request)
    scenario = build_liquidation_scenario(original)
    if scenario != _snapshot(scenario_request, MAX_REQUEST_BYTES):
        raise EntryProfileError("liquidation scenario differs from exact parent order")
    section = _validated_scenario_section(scenario, scenario_section)
    original_hash = trade_safety_request_hash(original)
    source_digest = _digest(section)
    # This outer hash denotes association by the operator. Native request and
    # gateway_binding hashes below remain the distinct SELL scenario identity.
    section["request_hash"] = original_hash
    section["limitations"].extend(_PROFILE_LIMITATIONS)
    section["facts"][_PROOF_KEY] = {
        "profile_id": ENTRY_PROFILE_ID,
        "scenario_role": "hypothetical_liquidation_at_original_order_notional",
        "original_order_hash": original_hash,
        "original_side": original["order"]["side"],
        "scenario_request_hash": trade_safety_request_hash(scenario),
        "scenario_request": scenario,
        "scenario_section_sha256": source_digest,
        "native_source_sha256": section["source_sha256"],
        "native_context_sha256": section["facts"]["context_sha256"],
        "buy_execution_cost_available": False,
        "future_exit_liquidity_guaranteed": False,
        "fill_evidence": False,
    }
    return _snapshot(section)


def verify_entry_profile_receipt(
    receipt: Mapping[str, Any],
    *,
    evaluated_at: datetime,
    hmac_key: bytes,
    binding: TradeSafetyExecutionBinding,
) -> dict[str, Any]:
    """Verify authentication, exact execution binding and the distinct scenario.

    No receipt is consumed here. The existing paper adapter must still perform
    its normal pass-only, identity, expiry and durable replay checks at submit.
    """
    verified = verify_trade_safety_receipt(
        _snapshot(receipt), evaluated_at=evaluated_at, hmac_key=hmac_key
    )
    if not verified.authenticated:
        raise EntryProfileError("private entry profile requires authenticated receipt")
    result = verified.receipt
    original = _original(result["request"])
    _assert_execution_binding(original, result, binding)
    policy = result["policy"]
    if (
        set(policy["required_products"]) != {"seiche", "undertow", "liquilens"}
        or policy["max_notional_usd"] is None
        or policy["max_notional_usd"] > 1000
        or policy["max_exit_cost_bps"] is None
        or policy["max_exit_cost_bps"] > 25
        or policy["max_venue_spread_bps"] is None
        or policy["max_venue_spread_bps"] > 15
        or policy["max_evidence_age_seconds"]["seiche"] > 8 * 86400
        or policy["max_evidence_age_seconds"]["liquilens"] > 8 * 86400
        or policy["max_evidence_age_seconds"]["undertow"] > 300
        or not {"STRAIN", "STRESS"} <= set(policy["hold_regimes"])
        or policy["live_requires_executable_quote"] is not True
        or policy["live_requires_broker_preview"] is not True
    ):
        raise EntryProfileError("entry profile policy exceeds its paper scope")
    derived = result["evidence"]["undertow"]
    proof = derived["facts"].get(_PROOF_KEY)
    if not isinstance(proof, dict):
        raise EntryProfileError("entry profile liquidation proof is missing")
    source = _snapshot(derived)
    del source["facts"][_PROOF_KEY]
    scenario = build_liquidation_scenario(original)
    source["request_hash"] = trade_safety_request_hash(scenario)
    source["limitations"] = [
        item for item in source["limitations"] if item not in _PROFILE_LIMITATIONS
    ]
    expected = project_liquidation_section(original, scenario, source)
    if expected != derived:
        raise EntryProfileError(
            "entry profile scenario proof differs from exact reconstruction"
        )
    return result
