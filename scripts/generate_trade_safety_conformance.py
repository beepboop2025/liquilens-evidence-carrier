"""Generate deterministic raw-byte Trade Safety v1 conformance cases."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from liquilens_evidence import issue_trade_safety_receipt
from liquilens_evidence.canonical import canonical_hash_bytes
from liquilens_evidence.trade_safety import TRADE_SAFETY_HMAC_DOMAIN

OUTPUT = ROOT / "protocol" / "conformance" / "trade-safety-v1" / "corpus.json"
EVALUATED_AT = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
HMAC_KEY = b"public-conformance-fixture-key-not-for-production"
HMAC_KEY_ID = "public-conformance-fixture-v1"


def _load(name: str) -> dict[str, Any]:
    value = json.loads(
        (ROOT / "examples" / "trade-safety" / name).read_text(encoding="utf-8")
    )
    if not isinstance(value, dict):
        raise TypeError(f"fixture must be an object: {name}")
    return value


def _raw(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _binding(receipt: dict[str, Any]) -> dict[str, Any]:
    request = receipt["request"]
    agent = request["agent"]
    issuer = receipt["issuer"]
    return {
        "account_id": agent["account_id"],
        "tenant_id": agent["tenant_id"],
        "operator_id": agent["operator_id"],
        "agent_id": agent["agent_id"],
        "runtime": agent["runtime"],
        "strategy_id": agent["strategy_id"],
        "policy_id": receipt["policy"]["policy_id"],
        "policy_version": receipt["policy"]["version"],
        "policy_hash": receipt["policy_hash"],
        "issuer_name": issuer["name"],
        "issuer_version": issuer["version"],
        "issuer_endpoint": issuer["endpoint"],
        "hmac_key_id": receipt["integrity"]["key_id"],
    }


def _rebind_and_sign(receipt: dict[str, Any]) -> dict[str, Any]:
    """Re-sign an adversarial fixture whose chronology the issuer would reject."""

    from liquilens_evidence import trade_safety_request_hash

    request_hash = trade_safety_request_hash(receipt["request"])
    receipt["request_hash"] = request_hash
    for section in receipt["evidence"].values():
        section["request_hash"] = request_hash
    receipt["broker_preview"]["request_hash"] = request_hash
    payload = {
        key: value
        for key, value in receipt.items()
        if key not in {"receipt_id", "record_hash"}
    }
    payload["integrity"] = dict(payload["integrity"])
    payload["integrity"]["signature"] = None
    digest = hashlib.sha256(canonical_hash_bytes(payload)).hexdigest()
    receipt["receipt_id"] = f"trade_safety_{digest[:24]}"
    receipt["record_hash"] = digest
    receipt["integrity"]["signature"] = hmac.new(
        HMAC_KEY,
        TRADE_SAFETY_HMAC_DOMAIN + digest.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return receipt


def _issued(
    *,
    policy_change: dict[str, Any] | None = None,
    evidence_change: tuple[str, str, Any] | None = None,
    request_change: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    request = _load("request.paper.json")
    policy = _load("policy.paper.json")
    evidence = _load("evidence.paper.json")
    if policy_change:
        policy.update(policy_change)
    if evidence_change:
        product, field, value = evidence_change
        evidence[product][field] = value
    if request_change:
        request["order"].update(request_change)
        requested_notional = request["order"]["notional"]["amount"]
        evidence["undertow"]["facts"]["requested_size_usd"] = requested_notional
        evidence["undertow"]["facts"]["published_rung_used_usd"] = (
            requested_notional
        )
        # Evidence and preview are exact-request-bound; recompute their binding.
        from liquilens_evidence import trade_safety_request_hash

        request_hash = trade_safety_request_hash(request)
        for product in evidence.values():
            product["request_hash"] = request_hash
        preview = _load("broker-preview.paper.json")
        preview["request_hash"] = request_hash
    else:
        preview = _load("broker-preview.paper.json")
    receipt = issue_trade_safety_receipt(
        request=request,
        evidence=evidence,
        policy=policy,
        broker_preview=preview,
        evaluated_at=EVALUATED_AT,
        issuer=_load("issuer.paper.json"),
        ttl_seconds=60,
        hmac_key=HMAC_KEY,
        hmac_key_id=HMAC_KEY_ID,
    )
    return request, receipt


def _case(
    identifier: str,
    description: str,
    request: bytes,
    receipt: bytes,
    *,
    binding: dict[str, Any],
    verifier_ok: bool,
    guard: str,
    evaluated_at: str = "2026-09-02T12:00:30Z",
    attempts: int = 1,
) -> dict[str, Any]:
    return {
        "id": identifier,
        "description": description,
        "request_utf8_base64": _b64(request),
        "receipt_utf8_base64": _b64(receipt),
        "evaluated_at": evaluated_at,
        "binding": binding,
        "expected": {
            "verifier_ok": verifier_ok,
            "guard": guard,
            "attempts": attempts,
        },
    }


def build_corpus() -> dict[str, Any]:
    request, passed = _issued()
    limit_request, limited = _issued(policy_change={"max_notional_usd": 10.0})
    hold_request, held = _issued(
        evidence_change=("seiche", "facts", {"regime": "STRESS"})
    )
    unavailable_request, unavailable = _issued(
        evidence_change=("undertow", "state", "unavailable")
    )
    huge_request, huge = _issued(
        policy_change={"max_notional_usd": None},
        request_change={
            "notional": {"amount": 9_007_199_254_740_993, "currency": "USD"},
            "quantity": 9_007_199_254_740_993,
        },
    )

    pass_request_raw = _raw(request)
    pass_receipt_raw = _raw(passed)
    lexical_request_raw = pass_request_raw.replace(b'"amount":1000.0', b'"amount":1000')
    live_request_raw = pass_request_raw.replace(b'"mode":"paper"', b'"mode":"live"')
    tampered_receipt = pass_receipt_raw.replace(
        b"operator-authored policy is satisfied",
        b"tampered policy is satisfied",
    )
    signature = passed["integrity"]["signature"]
    assert isinstance(signature, str)
    bad_signature = ("0" if signature[0] != "0" else "1") + signature[1:]
    bad_hmac = pass_receipt_raw.replace(signature.encode(), bad_signature.encode())
    duplicate_key = pass_receipt_raw.replace(
        b"{", b'{"schema":"duplicate-must-fail",', 1
    )
    account_binding = _binding(passed)
    account_binding["account_id"] = "different-paper-account"
    policy_binding = _binding(passed)
    policy_binding["policy_hash"] = "0" * 64
    issuer_binding = _binding(passed)
    issuer_binding["issuer_name"] = "different-trusted-issuer"
    key_binding = _binding(passed)
    key_binding["hmac_key_id"] = "different-key-id"
    future_request_receipt = json.loads(json.dumps(passed))
    future_request_receipt["request"]["created_at"] = "2026-09-02T12:00:30Z"
    future_request_receipt = _rebind_and_sign(future_request_receipt)
    future_request = future_request_receipt["request"]

    cases = [
        _case("paper-pass", "Authenticated exact paper request reaches the callback once.", pass_request_raw, pass_receipt_raw, binding=_binding(passed), verifier_ok=True, guard="submit"),
        _case("paper-limit", "Operator notional limit blocks submission.", _raw(limit_request), _raw(limited), binding=_binding(limited), verifier_ok=True, guard="decision_limit"),
        _case("paper-hold", "Held Seiche regime blocks submission.", _raw(hold_request), _raw(held), binding=_binding(held), verifier_ok=True, guard="decision_hold"),
        _case("paper-unavailable", "Unavailable Undertow evidence fails closed.", _raw(unavailable_request), _raw(unavailable), binding=_binding(unavailable), verifier_ok=True, guard="decision_unavailable"),
        _case("integer-beyond-js-safe-range", "Integer lexeme survives hashing beyond Number safe range.", _raw(huge_request), _raw(huge), binding=_binding(huge), verifier_ok=True, guard="submit"),
        _case("integer-float-request-mismatch", "1000 and 1000.0 are distinct exact requests.", lexical_request_raw, pass_receipt_raw, binding=_binding(passed), verifier_ok=True, guard="request_mismatch"),
        _case("live-unconditional-hold", "Live mode is rejected before receipt use.", live_request_raw, pass_receipt_raw, binding=_binding(passed), verifier_ok=True, guard="mode_not_supported"),
        _case("tampered-content", "Content mutation without a new identity is invalid.", pass_request_raw, tampered_receipt, binding=_binding(passed), verifier_ok=False, guard="receipt_invalid"),
        _case("wrong-hmac", "Wrong authenticated signature is invalid.", pass_request_raw, bad_hmac, binding=_binding(passed), verifier_ok=False, guard="receipt_invalid"),
        _case("duplicate-key", "Duplicate object keys are rejected before parsing can overwrite one.", pass_request_raw, duplicate_key, binding=_binding(passed), verifier_ok=False, guard="receipt_invalid"),
        _case("invalid-utf8", "Malformed UTF-8 is rejected without replacement decoding.", pass_request_raw, b"\x80" + pass_receipt_raw, binding=_binding(passed), verifier_ok=False, guard="receipt_invalid"),
        _case("expired", "A receipt is invalid at its exclusive expiry boundary.", pass_request_raw, pass_receipt_raw, binding=_binding(passed), verifier_ok=False, guard="receipt_invalid", evaluated_at="2026-09-02T12:01:00Z"),
        _case("not-yet-valid", "A receipt cannot be verified before its evaluation clock.", pass_request_raw, pass_receipt_raw, binding=_binding(passed), verifier_ok=False, guard="receipt_invalid", evaluated_at="2026-09-02T11:59:59Z"),
        _case("future-request", "A cryptographically valid receipt cannot predate its bound request.", _raw(future_request), _raw(future_request_receipt), binding=_binding(future_request_receipt), verifier_ok=False, guard="receipt_invalid"),
        _case("account-binding-mismatch", "A receipt cannot cross broker credential accounts.", pass_request_raw, pass_receipt_raw, binding=account_binding, verifier_ok=True, guard="execution_context_mismatch"),
        _case("policy-binding-mismatch", "Operator-approved policy content is pinned independently.", pass_request_raw, pass_receipt_raw, binding=policy_binding, verifier_ok=True, guard="policy_hash_mismatch"),
        _case("issuer-binding-mismatch", "A valid HMAC from an unexpected issuer is not trusted.", pass_request_raw, pass_receipt_raw, binding=issuer_binding, verifier_ok=True, guard="issuer_mismatch"),
        _case("key-binding-mismatch", "HMAC key identity cannot be substituted.", pass_request_raw, pass_receipt_raw, binding=key_binding, verifier_ok=True, guard="integrity_key_mismatch"),
        _case("replay", "A valid receipt has one atomic paper claim.", pass_request_raw, pass_receipt_raw, binding=_binding(passed), verifier_ok=True, guard="receipt_replay", attempts=2),
    ]
    return {
        "schema": "liquilens.trade-safety-conformance.v1",
        "protocol_schema": "liquilens.trade-safety-receipt.v1",
        "canonicalization": "liquilens-hash-tree-v1",
        "generated_by": "scripts/generate_trade_safety_conformance.py",
        "fixture_hmac_key_base64": _b64(HMAC_KEY),
        "warning": "Public deterministic test key; never use it outside conformance.",
        "cases": cases,
    }


def render() -> str:
    return json.dumps(
        build_corpus(),
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    expected = render()
    if arguments.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != expected:
            print(f"conformance corpus is stale: {OUTPUT}", file=sys.stderr)
            return 1
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(expected, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
