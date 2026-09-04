from __future__ import annotations

import asyncio
import base64
import copy
import json
import os
import sqlite3
import stat
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from trade_safety_gateway.x402_access import (
    LIQUILENS_EXTENSION,
    PAYMENT_REQUIRED_HEADER,
    PAYMENT_RESPONSE_HEADER,
    CompletedAccess,
    FacilitatorUnavailable,
    HttpxFacilitator,
    PaymentAuthorizationRetired,
    PaymentSettlementFailed,
    PreparedAccess,
    SettledResponseRetired,
    SettlementUncertain,
    SQLiteSettlementJournal,
    X402AccessError,
    X402AccessGate,
    X402Config,
    canonical_body_sha256,
    decode_payment_signature,
    extract_payment_signature,
)

RESOURCE = "https://api.liquilens.in/v1/x402/check"
FACILITATOR = "https://facilitator.example.test/platform/v2/x402"
NETWORK = "eip155:84532"
AMOUNT = "10000"
ASSET = "0x" + "1" * 40
PAY_TO = "0x" + "2" * 40
PAYER = "0x" + "3" * 40
OTHER_PAYER = "0x" + "5" * 40
TRANSACTION = "0x" + "4" * 64
BODY = b'{"policy":{"max":10},"request":{"mode":"paper"}}'
RESPONSE = b'{"decision":"HOLD","receipt":{"kind":"trade-safety"}}'


def _config(**overrides: Any) -> X402Config:
    values: dict[str, Any] = {
        "resource_url": RESOURCE,
        "facilitator_url": FACILITATOR,
        "network": NETWORK,
        "amount": AMOUNT,
        "asset": ASSET,
        "pay_to": PAY_TO,
        "quote_binding_key": b"dedicated-x402-quote-binding-key!!",
        "offer_extra": {"name": "USDC", "version": "2"},
    }
    values.update(overrides)
    return X402Config(**values)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _b64(value: Any) -> str:
    return base64.b64encode(_json_bytes(value)).decode("ascii")


def _decode_b64(value: str) -> dict[str, Any]:
    decoded = json.loads(base64.b64decode(value, validate=True))
    assert isinstance(decoded, dict)
    return decoded


class FakeFacilitator:
    def __init__(
        self,
        *,
        verify_response: Mapping[str, Any] | None = None,
        settle_response: Mapping[str, Any] | None = None,
        verify_error: Exception | None = None,
        settle_error: Exception | None = None,
    ) -> None:
        self.verify_response = verify_response or {"isValid": True, "payer": PAYER}
        self.settle_response = settle_response or {
            "success": True,
            "payer": PAYER,
            "transaction": TRANSACTION,
            "network": NETWORK,
            "amount": AMOUNT,
        }
        self.verify_error = verify_error
        self.settle_error = settle_error
        self.verify_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.settle_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.closed = False

    async def verify(
        self,
        *,
        payment_payload: Mapping[str, Any],
        payment_requirements: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        self.verify_calls.append(
            (
                copy.deepcopy(dict(payment_payload)),
                copy.deepcopy(dict(payment_requirements)),
            )
        )
        if self.verify_error is not None:
            raise self.verify_error
        return copy.deepcopy(dict(self.verify_response))

    async def settle(
        self,
        *,
        payment_payload: Mapping[str, Any],
        payment_requirements: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        self.settle_calls.append(
            (
                copy.deepcopy(dict(payment_payload)),
                copy.deepcopy(dict(payment_requirements)),
            )
        )
        if self.settle_error is not None:
            raise self.settle_error
        return copy.deepcopy(dict(self.settle_response))

    async def aclose(self) -> None:
        self.closed = True


def _gate(
    tmp_path: Path,
    *,
    config: X402Config | None = None,
    facilitator: FakeFacilitator | None = None,
    journal_name: str = "x402.sqlite3",
    journal_kwargs: Mapping[str, Any] | None = None,
    maintenance: bool = False,
) -> tuple[X402AccessGate, SQLiteSettlementJournal, FakeFacilitator]:
    selected_config = config or _config()
    selected_facilitator = facilitator or FakeFacilitator()
    options: dict[str, Any] = {
        "max_cached_response_bytes": selected_config.max_cached_response_bytes
    }
    options.update(dict(journal_kwargs or {}))
    journal = SQLiteSettlementJournal(tmp_path / journal_name, **options)
    return (
        X402AccessGate(
            selected_config,
            facilitator=selected_facilitator,
            journal=journal,
            maintenance=maintenance,
        ),
        journal,
        selected_facilitator,
    )


def _payment(
    gate: X402AccessGate,
    *,
    body: bytes = BODY,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    challenge = gate.challenge(body, resource=RESOURCE)
    required = challenge.payment_required
    payment = {
        "x402Version": 2,
        "resource": copy.deepcopy(required["resource"]),
        "accepted": copy.deepcopy(required["accepts"][0]),
        "payload": {
            "signature": "0x" + "a" * 130,
            "authorization": {
                "from": PAYER,
                "to": PAY_TO,
                "value": AMOUNT,
                "validAfter": "0",
                "validBefore": "9999999999",
                "nonce": "0x" + "b" * 64,
            },
        },
        "extensions": copy.deepcopy(required["extensions"]),
    }
    return _b64(payment), payment, required


def _payment_with_nonce(
    gate: X402AccessGate, nonce_character: str
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    _header, payment, required = _payment(gate)
    payment["payload"]["authorization"]["nonce"] = "0x" + nonce_character * 64
    return _b64(payment), payment, required


def test_successful_access_headers_identity_and_restart_cache(tmp_path: Path) -> None:
    gate, journal, facilitator = _gate(tmp_path)
    payment_header, _payment_payload, required = _payment(gate)

    challenge = gate.challenge(BODY, resource=RESOURCE)
    assert challenge.response_headers == {
        PAYMENT_REQUIRED_HEADER: challenge.header_value
    }
    assert _decode_b64(challenge.header_value) == required
    binding = required["extensions"][LIQUILENS_EXTENSION]["info"]
    assert binding["resource"] == RESOURCE
    assert binding["bodySha256"] == canonical_body_sha256(BODY)
    assert len(binding["offerSha256"]) == 64
    assert len(binding["quoteBinding"]) == 64

    prepared = asyncio.run(
        gate.authorize(
            b'{ "request": {"mode":"paper"}, "policy":{"max":10}}',
            resource=RESOURCE,
            payment_signature=payment_header,
        )
    )
    assert isinstance(prepared, PreparedAccess)
    completed = asyncio.run(
        gate.settle(
            prepared,
            bytearray(RESPONSE),
            status_code=200,
            content_type="application/json",
        )
    )
    assert completed.response_body == RESPONSE
    assert completed.response_headers == {
        PAYMENT_RESPONSE_HEADER: completed.payment_response_header
    }
    assert _decode_b64(completed.payment_response_header) == facilitator.settle_response
    assert completed.payment_identity.transaction == TRANSACTION
    assert completed.payment_identity.body_sha256 == canonical_body_sha256(BODY)
    assert "decision" not in completed.payment_identity.as_dict()
    assert len(facilitator.verify_calls) == 1
    assert len(facilitator.settle_calls) == 1

    journal.close()
    replay_facilitator = FakeFacilitator(
        verify_error=AssertionError("settled replay must not verify"),
        settle_error=AssertionError("settled replay must not settle"),
    )
    replay_gate, replay_journal, _ = _gate(
        tmp_path,
        facilitator=replay_facilitator,
    )
    replay = asyncio.run(
        replay_gate.authorize(
            BODY,
            resource=RESOURCE,
            payment_signature=payment_header,
        )
    )
    assert isinstance(replay, CompletedAccess)
    assert replay.cached is True
    assert replay.response_body == RESPONSE
    assert replay.payment_response_header == completed.payment_response_header
    assert replay.payment_identity == completed.payment_identity
    assert replay_facilitator.verify_calls == []
    assert replay_facilitator.settle_calls == []
    replay_journal.close()


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("amount", "10001"),
        ("network", "eip155:8453"),
        ("asset", "0x" + "6" * 40),
        ("payTo", "0x" + "7" * 40),
    ],
)
def test_accepted_offer_mismatch_fails_before_facilitator(
    tmp_path: Path, field: str, replacement: str
) -> None:
    gate, journal, facilitator = _gate(tmp_path)
    _header, payment, _required = _payment(gate)
    payment["accepted"][field] = replacement

    with pytest.raises(X402AccessError) as caught:
        asyncio.run(
            gate.authorize(
                BODY,
                resource=RESOURCE,
                payment_signature=_b64(payment),
            )
        )
    assert caught.value.code == "accepted_offer_mismatch"
    assert facilitator.verify_calls == []
    journal.close()


def test_body_and_route_are_exactly_bound_before_facilitator(tmp_path: Path) -> None:
    gate, journal, facilitator = _gate(tmp_path)
    payment_header, _payment_payload, _required = _payment(gate)

    with pytest.raises(X402AccessError) as body_error:
        asyncio.run(
            gate.authorize(
                b'{"policy":{"max":11},"request":{"mode":"paper"}}',
                resource=RESOURCE,
                payment_signature=payment_header,
            )
        )
    assert body_error.value.code == "binding_extension_mismatch"

    with pytest.raises(X402AccessError) as route_error:
        asyncio.run(
            gate.authorize(
                BODY,
                resource="https://api.liquilens.in/v1/x402/other",
                payment_signature=payment_header,
            )
        )
    assert route_error.value.code == "resource_mismatch"
    assert facilitator.verify_calls == []
    journal.close()


@pytest.mark.parametrize(
    ("binding_field", "replacement"),
    [
        ("bodySha256", "0" * 64),
        ("resource", "https://api.liquilens.in/v1/x402/other"),
        ("offerSha256", "0" * 64),
        ("quoteBinding", "0" * 64),
    ],
)
def test_liquilens_hmac_echo_tampering_fails_before_facilitator(
    tmp_path: Path, binding_field: str, replacement: str
) -> None:
    gate, journal, facilitator = _gate(tmp_path)
    _header, payment, _required = _payment(gate)
    payment["extensions"][LIQUILENS_EXTENSION]["info"][binding_field] = replacement

    with pytest.raises(X402AccessError) as caught:
        asyncio.run(
            gate.authorize(
                BODY,
                resource=RESOURCE,
                payment_signature=_b64(payment),
            )
        )
    assert caught.value.code == "binding_extension_mismatch"
    assert facilitator.verify_calls == []
    journal.close()


def test_quote_binding_is_authenticated_by_a_dedicated_key(tmp_path: Path) -> None:
    gate, journal, _facilitator = _gate(tmp_path)
    other_gate, other_journal, other_facilitator = _gate(
        tmp_path,
        config=_config(quote_binding_key=b"another-dedicated-quote-key-value"),
        journal_name="other.sqlite3",
    )
    header, _payment_payload, required = _payment(gate)
    other_required = other_gate.challenge(BODY, resource=RESOURCE).payment_required
    first = required["extensions"][LIQUILENS_EXTENSION]["info"]["quoteBinding"]
    second = other_required["extensions"][LIQUILENS_EXTENSION]["info"]["quoteBinding"]
    assert first != second

    with pytest.raises(X402AccessError) as caught:
        asyncio.run(
            other_gate.authorize(
                BODY,
                resource=RESOURCE,
                payment_signature=header,
            )
        )
    assert caught.value.code == "binding_extension_mismatch"
    assert other_facilitator.verify_calls == []
    journal.close()
    other_journal.close()


def test_resource_info_and_bazaar_extension_are_immutable_and_extensible(
    tmp_path: Path,
) -> None:
    resource_extra: dict[str, Any] = {
        "description": "Paid, read-only Trade Safety check",
        "mimeType": "application/json",
        "serviceName": "LiquiLens Trade Safety",
        "tags": ["trade-safety", "ai-agents"],
        "iconUrl": "https://liquilens.in/icon.png",
    }
    bazaar: dict[str, Any] = {
        "bazaar": {
            "info": {
                "discoverable": True,
                "input": {"method": "POST", "bodyType": "json"},
            },
            "schema": {"type": "object", "additionalProperties": True},
        }
    }
    config = _config(
        resource_info_extra=resource_extra,
        required_extensions=bazaar,
    )
    resource_extra["serviceName"] = "mutated-after-config"
    bazaar["bazaar"]["info"]["discoverable"] = False
    gate, journal, facilitator = _gate(tmp_path, config=config)
    _header, payment, required = _payment(gate)
    assert required["resource"]["serviceName"] == "LiquiLens Trade Safety"
    assert required["extensions"]["bazaar"]["info"]["discoverable"] is True

    payment["extensions"]["bazaar"]["info"]["clientHint"] = "appended"
    payment["extensions"][LIQUILENS_EXTENSION]["info"]["clientNonce"] = "abc"
    prepared = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=_b64(payment))
    )
    assert isinstance(prepared, PreparedAccess)
    assert len(facilitator.verify_calls) == 1
    assert gate.abort(prepared) is True
    journal.close()


def test_resource_info_or_required_extension_cannot_be_overwritten(
    tmp_path: Path,
) -> None:
    config = _config(
        resource_info_extra={"serviceName": "LiquiLens"},
        required_extensions={
            "bazaar": {
                "info": {"discoverable": True},
                "schema": {"type": "object"},
            }
        },
    )
    gate, journal, facilitator = _gate(tmp_path, config=config)
    _header, payment, _required = _payment(gate)
    payment["resource"]["serviceName"] = "Impostor"
    with pytest.raises(X402AccessError) as resource_error:
        asyncio.run(
            gate.authorize(BODY, resource=RESOURCE, payment_signature=_b64(payment))
        )
    assert resource_error.value.code == "resource_mismatch"

    _header, payment, _required = _payment(gate)
    payment["extensions"]["bazaar"]["info"]["discoverable"] = False
    with pytest.raises(X402AccessError) as extension_error:
        asyncio.run(
            gate.authorize(BODY, resource=RESOURCE, payment_signature=_b64(payment))
        )
    assert extension_error.value.code == "binding_extension_mismatch"
    assert facilitator.verify_calls == []
    journal.close()


@pytest.mark.parametrize(
    "value",
    [
        "not+base64=garbage",
        "AAAA\n",
        _b64([]),
        base64.b64encode(b'{"x":1,"x":2}').decode("ascii"),
        "éééé",
    ],
)
def test_malformed_payment_signature_is_rejected(value: str) -> None:
    with pytest.raises(X402AccessError) as caught:
        decode_payment_signature(value)
    assert caught.value.code == "malformed_payment_signature"
    assert caught.value.http_status == 400


def test_oversize_and_duplicate_payment_signature_headers_are_rejected() -> None:
    with pytest.raises(X402AccessError) as oversize:
        decode_payment_signature("A" * 68, max_header_bytes=64)
    assert oversize.value.code == "payment_signature_too_large"
    assert oversize.value.http_status == 413

    with pytest.raises(X402AccessError) as duplicate:
        extract_payment_signature(
            [(b"payment-signature", b"e30="), (b"Payment-Signature", b"e30=")]
        )
    assert duplicate.value.code == "duplicate_payment_signature"
    assert duplicate.value.http_status == 400

    with pytest.raises(X402AccessError) as non_ascii:
        extract_payment_signature([(b"payment-signature", b"\xff")])
    assert non_ascii.value.code == "malformed_payment_signature"
    assert non_ascii.value.http_status == 400


def test_v1_and_unknown_payment_fields_fail_before_facilitator(tmp_path: Path) -> None:
    gate, journal, facilitator = _gate(tmp_path)
    _header, payment, _required = _payment(gate)
    payment["x402Version"] = 1
    with pytest.raises(X402AccessError) as v1_error:
        asyncio.run(
            gate.authorize(BODY, resource=RESOURCE, payment_signature=_b64(payment))
        )
    assert v1_error.value.code == "invalid_x402_version"
    assert v1_error.value.http_status == 400

    payment["x402Version"] = 2
    payment["scheme"] = "exact"
    with pytest.raises(X402AccessError) as shape_error:
        asyncio.run(
            gate.authorize(BODY, resource=RESOURCE, payment_signature=_b64(payment))
        )
    assert shape_error.value.code == "invalid_payment_payload_shape"
    assert shape_error.value.http_status == 400
    assert facilitator.verify_calls == []
    journal.close()


@pytest.mark.parametrize(
    "overrides",
    [
        {"network": "base"},
        {"network": "EIP155:8453"},
        {"amount": "010000"},
        {"facilitator_url": "http://facilitator.example.test"},
        {"resource_url": "https://api.liquilens.in/v1/x402/check?price=1"},
        {"scheme": "upto"},
        {"quote_binding_key": b"too-short"},
        {"asset": "USDC"},
        {"offer_extra": {"paymentFlow": "upfront"}},
        {"resource_info_extra": {"serviceName": "x" * 33}},
        {
            "required_extensions": {
                LIQUILENS_EXTENSION: {
                    "info": {},
                    "schema": {"type": "object"},
                }
            }
        },
    ],
)
def test_config_is_strict_and_fail_closed(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _config(**overrides)


@pytest.mark.parametrize(
    "verify_response",
    [
        {"isValid": False, "invalidReason": "insufficient_funds", "payer": PAYER},
        {"isValid": True, "payer": "not-an-address"},
        {"isValid": 1, "payer": PAYER},
        {"isValid": True, "payer": PAYER, "unexpected": True},
    ],
)
def test_verify_failures_do_not_claim_or_settle(
    tmp_path: Path, verify_response: dict[str, Any]
) -> None:
    facilitator = FakeFacilitator(verify_response=verify_response)
    gate, journal, _ = _gate(tmp_path, facilitator=facilitator)
    header, _payment_payload, _required = _payment(gate)
    with pytest.raises(X402AccessError):
        asyncio.run(gate.authorize(BODY, resource=RESOURCE, payment_signature=header))
    assert len(facilitator.verify_calls) == 1
    assert facilitator.settle_calls == []
    journal.close()


def test_verify_transport_failure_is_unavailable(tmp_path: Path) -> None:
    facilitator = FakeFacilitator(verify_error=OSError("offline"))
    gate, journal, _ = _gate(tmp_path, facilitator=facilitator)
    header, _payment_payload, _required = _payment(gate)
    with pytest.raises(FacilitatorUnavailable):
        asyncio.run(gate.authorize(BODY, resource=RESOURCE, payment_signature=header))
    journal.close()


def test_optional_facilitator_payer_uses_verified_exact_evm_authorization(
    tmp_path: Path,
) -> None:
    facilitator = FakeFacilitator(
        verify_response={"isValid": True},
        settle_response={
            "success": True,
            "transaction": TRANSACTION,
            "network": NETWORK,
            "amount": AMOUNT,
        },
    )
    gate, journal, _ = _gate(tmp_path, facilitator=facilitator)
    header, _payment_payload, _required = _payment(gate)

    prepared = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=header)
    )
    assert isinstance(prepared, PreparedAccess)
    assert prepared.payer == PAYER
    completed = asyncio.run(gate.settle(prepared, RESPONSE))

    assert completed.payment_identity.payer == PAYER
    assert "payer" not in _decode_b64(completed.payment_response_header)
    journal.close()


def test_facilitator_verify_payer_cannot_differ_from_evm_authorization(
    tmp_path: Path,
) -> None:
    facilitator = FakeFacilitator(
        verify_response={"isValid": True, "payer": OTHER_PAYER}
    )
    gate, journal, _ = _gate(tmp_path, facilitator=facilitator)
    header, _payment_payload, _required = _payment(gate)

    with pytest.raises(FacilitatorUnavailable):
        asyncio.run(gate.authorize(BODY, resource=RESOURCE, payment_signature=header))

    assert facilitator.settle_calls == []
    assert gate.journal_counts().total == 0
    journal.close()


def _bad_settlement(case: str) -> dict[str, Any]:
    value: dict[str, Any] = {
        "success": True,
        "payer": PAYER,
        "transaction": TRANSACTION,
        "network": NETWORK,
        "amount": AMOUNT,
    }
    if case == "unsuccessful":
        value.update(success=False, errorReason="insufficient_funds")
    elif case == "network":
        value["network"] = "eip155:8453"
    elif case == "amount":
        value["amount"] = "10001"
    elif case == "transaction":
        value["transaction"] = ""
    elif case == "payer_shape":
        value["payer"] = "not-an-address"
    elif case == "payer_mismatch":
        value["payer"] = OTHER_PAYER
    elif case == "unknown_field":
        value["receipt"] = "must-not-pass"
    else:  # pragma: no cover - protects the test helper itself
        raise AssertionError(case)
    return value


@pytest.mark.parametrize(
    "case",
    [
        "network",
        "amount",
        "transaction",
        "payer_shape",
        "payer_mismatch",
        "unknown_field",
    ],
)
def test_settlement_validation_is_sticky_fail_closed(tmp_path: Path, case: str) -> None:
    facilitator = FakeFacilitator(settle_response=_bad_settlement(case))
    gate, journal, _ = _gate(tmp_path, facilitator=facilitator)
    header, _payment_payload, _required = _payment(gate)
    prepared = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=header)
    )
    assert isinstance(prepared, PreparedAccess)
    with pytest.raises(SettlementUncertain):
        asyncio.run(gate.settle(prepared, RESPONSE))
    assert len(facilitator.settle_calls) == 1

    with pytest.raises(SettlementUncertain):
        asyncio.run(gate.authorize(BODY, resource=RESOURCE, payment_signature=header))
    assert len(facilitator.settle_calls) == 1
    journal.close()


def test_terminal_failed_settlement_is_protocol_visible_and_never_resettled(
    tmp_path: Path,
) -> None:
    failure = _bad_settlement("unsuccessful")
    failure["transaction"] = ""
    facilitator = FakeFacilitator(settle_response=failure)
    gate, journal, _ = _gate(tmp_path, facilitator=facilitator)
    header, _payment_payload, _required = _payment(gate)
    prepared = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=header)
    )
    assert isinstance(prepared, PreparedAccess)

    with pytest.raises(PaymentSettlementFailed) as first:
        asyncio.run(gate.settle(prepared, RESPONSE))
    assert first.value.http_status == 402
    assert first.value.error_reason == "insufficient_funds"
    assert _decode_b64(first.value.payment_response_header) == failure

    with pytest.raises(PaymentSettlementFailed) as replay:
        asyncio.run(gate.authorize(BODY, resource=RESOURCE, payment_signature=header))
    assert replay.value.payment_response_header == first.value.payment_response_header
    assert len(facilitator.verify_calls) == 1
    assert len(facilitator.settle_calls) == 1
    assert gate.journal_counts().tombstones == 1
    journal.close()


def test_failed_settlement_with_transaction_remains_reconcilable(
    tmp_path: Path,
) -> None:
    failure = _bad_settlement("unsuccessful")
    facilitator = FakeFacilitator(settle_response=failure)
    gate, journal, _ = _gate(tmp_path, facilitator=facilitator)
    header, _payment_payload, _required = _payment(gate)
    prepared = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=header)
    )
    assert isinstance(prepared, PreparedAccess)

    with pytest.raises(SettlementUncertain):
        asyncio.run(gate.settle(prepared, RESPONSE))
    records = gate.reconciliation_records()
    assert len(records) == 1
    assert records[0].payment_id == prepared.payment_id
    assert records[0].transaction == TRANSACTION
    assert gate.journal_counts().tombstones == 0
    journal.close()


def test_optional_settlement_amount_may_be_omitted(tmp_path: Path) -> None:
    settlement = {
        "success": True,
        "payer": PAYER,
        "transaction": TRANSACTION,
        "network": NETWORK,
    }
    facilitator = FakeFacilitator(settle_response=settlement)
    gate, journal, _ = _gate(tmp_path, facilitator=facilitator)
    header, _payment_payload, _required = _payment(gate)
    prepared = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=header)
    )
    assert isinstance(prepared, PreparedAccess)
    completed = asyncio.run(gate.settle(prepared, RESPONSE))
    assert completed.payment_identity.amount == AMOUNT
    assert "amount" not in _decode_b64(completed.payment_response_header)
    journal.close()


def test_cached_response_digest_detects_journal_corruption(tmp_path: Path) -> None:
    gate, journal, _facilitator = _gate(tmp_path)
    header, _payment_payload, _required = _payment(gate)
    prepared = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=header)
    )
    assert isinstance(prepared, PreparedAccess)
    asyncio.run(gate.settle(prepared, RESPONSE))
    journal.close()

    connection = sqlite3.connect(tmp_path / "x402.sqlite3")
    connection.execute(
        "UPDATE x402_access_journal_v1 SET response_body = ?",
        (b'{"tampered":true}',),
    )
    connection.commit()
    connection.close()

    replay_gate, replay_journal, replay_facilitator = _gate(tmp_path)
    with pytest.raises(X402AccessError) as caught:
        asyncio.run(
            replay_gate.authorize(
                BODY,
                resource=RESOURCE,
                payment_signature=header,
            )
        )
    assert caught.value.code == "journal_corrupt"
    assert replay_facilitator.verify_calls == []
    replay_journal.close()


def test_settle_exception_is_sticky_and_never_retried(tmp_path: Path) -> None:
    facilitator = FakeFacilitator(settle_error=TimeoutError("unknown outcome"))
    gate, journal, _ = _gate(tmp_path, facilitator=facilitator)
    header, _payment_payload, _required = _payment(gate)
    prepared = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=header)
    )
    assert isinstance(prepared, PreparedAccess)
    with pytest.raises(SettlementUncertain):
        asyncio.run(gate.settle(prepared, RESPONSE))
    with pytest.raises(SettlementUncertain):
        asyncio.run(gate.settle(prepared, RESPONSE))
    assert len(facilitator.settle_calls) == 1
    journal.close()


def test_oversize_payment_response_is_sticky_after_successful_settle(
    tmp_path: Path,
) -> None:
    settlement = {
        "success": True,
        "payer": PAYER,
        "transaction": TRANSACTION,
        "network": NETWORK,
        "amount": AMOUNT,
        "extensions": {"padding": "x" * 50_000},
    }
    facilitator = FakeFacilitator(settle_response=settlement)
    gate, journal, _ = _gate(tmp_path, facilitator=facilitator)
    header, _payment_payload, _required = _payment(gate)
    prepared = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=header)
    )
    assert isinstance(prepared, PreparedAccess)
    with pytest.raises(SettlementUncertain):
        asyncio.run(gate.settle(prepared, RESPONSE))
    with pytest.raises(SettlementUncertain):
        asyncio.run(gate.authorize(BODY, resource=RESOURCE, payment_signature=header))
    assert len(facilitator.settle_calls) == 1
    journal.close()


def test_response_bound_fails_before_settlement_and_can_abort(tmp_path: Path) -> None:
    config = _config(max_cached_response_bytes=16)
    gate, journal, facilitator = _gate(tmp_path, config=config)
    header, _payment_payload, _required = _payment(gate)
    prepared = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=header)
    )
    assert isinstance(prepared, PreparedAccess)
    with pytest.raises(X402AccessError) as caught:
        asyncio.run(gate.settle(prepared, b"x" * 17))
    assert caught.value.code == "response_too_large"
    assert facilitator.settle_calls == []
    assert gate.abort(prepared) is True
    journal.close()


def test_abort_only_releases_pre_settlement_claim(tmp_path: Path) -> None:
    gate, journal, facilitator = _gate(tmp_path)
    header, _payment_payload, _required = _payment(gate)
    first = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=header)
    )
    assert isinstance(first, PreparedAccess)
    assert gate.abort(first) is True
    second = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=header)
    )
    assert isinstance(second, PreparedAccess)
    assert len(facilitator.verify_calls) == 2
    assert gate.abort(second) is True
    journal.close()


@pytest.mark.parametrize(
    ("status", "headers", "body"),
    [
        (302, {"content-type": "application/json"}, b"{}"),
        (200, {"content-type": "text/plain"}, b"{}"),
        (
            200,
            {"content-type": "application/json", "content-encoding": "gzip"},
            b"{}",
        ),
        (200, {"content-type": "application/json"}, b"x" * 65),
        (200, {"content-type": "application/json"}, b"not-json"),
    ],
)
def test_http_facilitator_rejects_status_type_encoding_bounds_and_json(
    status: int, headers: dict[str, str], body: bytes
) -> None:
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            status,
            headers=headers,
            content=body,
            request=request,
        )

    config = _config(max_facilitator_response_bytes=64)
    facilitator = HttpxFacilitator(
        config,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(FacilitatorUnavailable):
        asyncio.run(
            facilitator.verify(
                payment_payload={"x402Version": 2},
                payment_requirements=config.payment_requirements(),
            )
        )
    assert calls == [f"{FACILITATOR}/verify"]
    asyncio.run(facilitator.aclose())


def test_http_facilitator_uses_v2_fixed_envelope() -> None:
    requests: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.headers["accept"] == "application/json"
        assert request.headers["accept-encoding"] == "identity"
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"isValid": True, "payer": PAYER},
            request=request,
        )

    config = _config()
    facilitator = HttpxFacilitator(
        config,
        transport=httpx.MockTransport(handler),
    )
    response = asyncio.run(
        facilitator.verify(
            payment_payload={"x402Version": 2, "payload": {"test": True}},
            payment_requirements=config.payment_requirements(),
        )
    )
    assert response == {"isValid": True, "payer": PAYER}
    assert requests[0]["x402Version"] == 2
    assert set(requests[0]) == {
        "x402Version",
        "paymentPayload",
        "paymentRequirements",
    }
    asyncio.run(facilitator.aclose())


def test_http_facilitator_never_persists_response_cookies() -> None:
    cookies: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        cookies.append(request.headers.get("cookie"))
        response = (
            {"isValid": True, "payer": PAYER}
            if request.url.path.endswith("/verify")
            else {
                "success": True,
                "payer": PAYER,
                "transaction": TRANSACTION,
                "network": NETWORK,
            }
        )
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "set-cookie": "payer_session=forbidden; Path=/; Secure",
            },
            json=response,
            request=request,
        )

    async def scenario() -> None:
        config = _config()
        facilitator = HttpxFacilitator(
            config,
            transport=httpx.MockTransport(handler),
        )
        try:
            await facilitator.verify(
                payment_payload={"x402Version": 2},
                payment_requirements=config.payment_requirements(),
            )
            await facilitator.settle(
                payment_payload={"x402Version": 2},
                payment_requirements=config.payment_requirements(),
            )
        finally:
            await facilitator.aclose()

    asyncio.run(scenario())
    assert cookies == [None, None]


def test_processing_claim_blocks_concurrent_repeat(tmp_path: Path) -> None:
    gate, journal, facilitator = _gate(tmp_path)
    header, _payment_payload, _required = _payment(gate)
    prepared = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=header)
    )
    assert isinstance(prepared, PreparedAccess)
    with pytest.raises(X402AccessError) as caught:
        asyncio.run(gate.authorize(BODY, resource=RESOURCE, payment_signature=header))
    assert caught.value.code == "payment_processing"
    assert len(facilitator.verify_calls) == 1
    assert gate.abort(prepared) is True
    journal.close()


def test_concurrent_verify_race_has_one_owner_and_one_settlement(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        release = asyncio.Event()

        class RacingFacilitator(FakeFacilitator):
            async def verify(
                self,
                *,
                payment_payload: Mapping[str, Any],
                payment_requirements: Mapping[str, Any],
            ) -> Mapping[str, Any]:
                self.verify_calls.append(
                    (
                        copy.deepcopy(dict(payment_payload)),
                        copy.deepcopy(dict(payment_requirements)),
                    )
                )
                if len(self.verify_calls) == 2:
                    release.set()
                await release.wait()
                return copy.deepcopy(dict(self.verify_response))

        facilitator = RacingFacilitator()
        gate, journal, _ = _gate(tmp_path, facilitator=facilitator)
        header, _payment_payload, _required = _payment(gate)
        results = await asyncio.gather(
            gate.authorize(BODY, resource=RESOURCE, payment_signature=header),
            gate.authorize(BODY, resource=RESOURCE, payment_signature=header),
            return_exceptions=True,
        )
        prepared = [item for item in results if isinstance(item, PreparedAccess)]
        rejected = [item for item in results if isinstance(item, X402AccessError)]
        assert len(prepared) == 1
        assert len(rejected) == 1
        assert rejected[0].code == "payment_processing"
        completed = await gate.settle(prepared[0], RESPONSE)
        replay = await gate.authorize(
            BODY,
            resource=RESOURCE,
            payment_signature=header,
        )
        assert isinstance(completed, CompletedAccess)
        assert isinstance(replay, CompletedAccess)
        assert replay.cached is True
        assert len(facilitator.verify_calls) == 2
        assert len(facilitator.settle_calls) == 1
        journal.close()

    asyncio.run(scenario())


def test_client_extension_info_cannot_create_a_second_settlement_identity(
    tmp_path: Path,
) -> None:
    gate, journal, facilitator = _gate(tmp_path)
    first_header, second_payment, _required = _payment(gate)
    second_payment["extensions"][LIQUILENS_EXTENSION]["info"]["clientHint"] = (
        "allowed-but-not-a-new-payment"
    )
    first = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=first_header)
    )
    assert isinstance(first, PreparedAccess)
    with pytest.raises(X402AccessError) as caught:
        asyncio.run(
            gate.authorize(
                BODY,
                resource=RESOURCE,
                payment_signature=_b64(second_payment),
            )
        )
    assert caught.value.code == "payment_payload_mismatch"
    assert len(facilitator.verify_calls) == 1
    assert gate.abort(first) is True
    journal.close()


def test_settlement_pending_is_redacted_persisted_and_reconciled(
    tmp_path: Path,
) -> None:
    pending = {
        "success": False,
        "errorReason": "settlement_pending",
        "payer": PAYER,
        "transaction": TRANSACTION,
        "network": NETWORK,
    }
    facilitator = FakeFacilitator(settle_response=pending)
    gate, journal, _ = _gate(tmp_path, facilitator=facilitator)
    header, _payment_payload, _required = _payment(gate)
    prepared = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=header)
    )
    assert isinstance(prepared, PreparedAccess)

    with pytest.raises(SettlementUncertain):
        asyncio.run(gate.settle(prepared, RESPONSE))
    records = gate.reconciliation_records()
    assert len(records) == 1
    assert records[0].payment_id == prepared.payment_id
    assert records[0].result_observed is True
    assert records[0].result_code == "settlement_pending"
    assert records[0].network == NETWORK
    assert records[0].transaction == TRANSACTION
    assert PAYER not in repr(records[0])
    assert "signature" not in repr(records[0])
    journal.close()

    replay_facilitator = FakeFacilitator(
        verify_error=AssertionError("reconciliation must not verify"),
        settle_error=AssertionError("reconciliation must not settle again"),
    )
    replay_gate, replay_journal, _ = _gate(
        tmp_path,
        facilitator=replay_facilitator,
        maintenance=True,
    )
    with pytest.raises(X402AccessError) as pending_result:
        replay_gate.reconcile_settled(prepared.payment_id)
    assert pending_result.value.code == "reconciliation_result_required"
    replay_gate.reconcile_settled(
        prepared.payment_id,
        {
            "success": True,
            "payer": PAYER,
            "transaction": TRANSACTION,
            "network": NETWORK,
            "amount": AMOUNT,
        },
    )
    completed = asyncio.run(
        replay_gate.authorize(
            BODY,
            resource=RESOURCE,
            payment_signature=header,
        )
    )
    assert isinstance(completed, CompletedAccess)
    assert completed.cached is True
    assert completed.response_body == RESPONSE
    assert replay_facilitator.verify_calls == []
    assert replay_facilitator.settle_calls == []
    replay_journal.close()


def test_unrecorded_post_start_crash_requires_operator_result_after_restart(
    tmp_path: Path,
) -> None:
    gate, journal, _facilitator = _gate(tmp_path)
    header, _payment_payload, _required = _payment(gate)
    prepared = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=header)
    )
    assert isinstance(prepared, PreparedAccess)
    assert (
        journal.begin_settlement(
            prepared,
            response_body=RESPONSE,
            status_code=200,
            content_type="application/json",
        )
        is None
    )
    journal.close()

    recovery_facilitator = FakeFacilitator(
        verify_error=AssertionError("uncertain recovery must not verify"),
        settle_error=AssertionError("uncertain recovery must not settle"),
    )
    recovery_gate, recovery_journal, _ = _gate(
        tmp_path,
        facilitator=recovery_facilitator,
        maintenance=True,
    )
    record = recovery_gate.reconciliation_records()[0]
    assert record.result_observed is False
    with pytest.raises(X402AccessError) as missing:
        recovery_gate.reconcile_settled(prepared.payment_id)
    assert missing.value.code == "reconciliation_result_required"

    recovery_gate.reconcile_settled(
        prepared.payment_id,
        copy.deepcopy(FakeFacilitator().settle_response),
    )
    completed = asyncio.run(
        recovery_gate.authorize(
            BODY,
            resource=RESOURCE,
            payment_signature=header,
        )
    )
    assert isinstance(completed, CompletedAccess)
    assert completed.response_body == RESPONSE
    assert recovery_facilitator.verify_calls == []
    assert recovery_facilitator.settle_calls == []
    recovery_journal.close()


def test_recorded_success_can_complete_after_restart_without_raw_operator_data(
    tmp_path: Path,
) -> None:
    gate, journal, facilitator = _gate(tmp_path)
    header, _payment_payload, _required = _payment(gate)
    prepared = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=header)
    )
    assert isinstance(prepared, PreparedAccess)
    journal.begin_settlement(
        prepared,
        response_body=RESPONSE,
        status_code=200,
        content_type="application/json",
    )
    journal.record_settlement_result(prepared, facilitator.settle_response)
    journal.close()

    recovery_gate, recovery_journal, _ = _gate(
        tmp_path,
        facilitator=FakeFacilitator(
            verify_error=AssertionError("recorded recovery must not verify"),
            settle_error=AssertionError("recorded recovery must not settle"),
        ),
        maintenance=True,
    )
    record = recovery_gate.reconciliation_records()[0]
    assert record.result_code == "success"
    with pytest.raises(X402AccessError) as cannot_retire:
        recovery_gate.retire_unsettled(prepared.payment_id)
    assert cannot_retire.value.code == "reconciliation_settlement_observed"
    assert cannot_retire.value.http_status == 409
    assert recovery_gate.reconciliation_records()[0].result_code == "success"
    recovery_gate.reconcile_settled(prepared.payment_id)
    completed = asyncio.run(
        recovery_gate.authorize(
            BODY,
            resource=RESOURCE,
            payment_signature=header,
        )
    )
    assert isinstance(completed, CompletedAccess)
    assert completed.response_body == RESPONSE
    recovery_journal.close()


def test_nonstandard_facilitator_error_is_not_echoed_by_operator_view(
    tmp_path: Path,
) -> None:
    secret = "signature=0x" + "a" * 130
    facilitator = FakeFacilitator(
        settle_response={
            "success": False,
            "errorReason": secret,
            "payer": PAYER,
            "transaction": secret,
            "network": NETWORK,
        }
    )
    gate, journal, _ = _gate(tmp_path, facilitator=facilitator)
    header, _payment_payload, _required = _payment(gate)
    prepared = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=header)
    )
    assert isinstance(prepared, PreparedAccess)
    with pytest.raises(SettlementUncertain):
        asyncio.run(gate.settle(prepared, RESPONSE))
    record = gate.reconciliation_records()[0]
    assert record.result_code == "unsuccessful"
    assert record.transaction is None
    assert secret not in repr(record)
    journal.close()


def test_settled_cache_retirement_keeps_non_repayable_tombstone_across_restart(
    tmp_path: Path,
) -> None:
    facilitator = FakeFacilitator()
    gate, journal, _ = _gate(
        tmp_path,
        facilitator=facilitator,
        journal_kwargs={"max_entries": 1, "max_terminal_entries": 10},
    )
    first_header, _first_payment, _required = _payment_with_nonce(gate, "b")
    first = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=first_header)
    )
    assert isinstance(first, PreparedAccess)
    asyncio.run(gate.settle(first, b'{"response":1}'))

    second_header, _second_payment, _required = _payment_with_nonce(gate, "c")
    second = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=second_header)
    )
    assert isinstance(second, PreparedAccess)
    asyncio.run(gate.settle(second, b'{"response":2}'))
    counts = gate.journal_counts()
    assert counts.total == 2
    assert counts.cached_settled == 1
    assert counts.tombstones == 1
    assert len(facilitator.verify_calls) == 2
    assert len(facilitator.settle_calls) == 2
    journal.close()

    replay_facilitator = FakeFacilitator(
        verify_error=AssertionError("retired payment must not verify"),
        settle_error=AssertionError("retired payment must not settle"),
    )
    replay_gate, replay_journal, _ = _gate(
        tmp_path,
        facilitator=replay_facilitator,
        journal_kwargs={"max_entries": 1, "max_terminal_entries": 10},
    )
    with pytest.raises(SettledResponseRetired) as retired:
        asyncio.run(
            replay_gate.authorize(
                BODY,
                resource=RESOURCE,
                payment_signature=first_header,
            )
        )
    assert retired.value.code == "settled_response_retired"
    assert retired.value.http_status == 409
    second_replay = asyncio.run(
        replay_gate.authorize(
            BODY,
            resource=RESOURCE,
            payment_signature=second_header,
        )
    )
    assert isinstance(second_replay, CompletedAccess)
    assert second_replay.response_body == b'{"response":2}'
    assert replay_facilitator.verify_calls == []
    assert replay_facilitator.settle_calls == []
    replay_journal.close()


def test_offline_age_retirement_is_bounded_private_and_does_not_refresh_on_replay(
    tmp_path: Path,
) -> None:
    day_ns = 86_400 * 1_000_000_000
    now = [10 * day_ns]
    options = {"clock_ns": lambda: now[0]}
    gate, journal, _facilitator = _gate(tmp_path, journal_kwargs=options)
    first_header, _payment_payload, _required = _payment_with_nonce(gate, "b")
    first = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=first_header)
    )
    assert isinstance(first, PreparedAccess)
    asyncio.run(gate.settle(first, b'{"response":1}'))

    now[0] += 2 * day_ns
    second_header, _payment_payload, _required = _payment_with_nonce(gate, "c")
    second = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=second_header)
    )
    assert isinstance(second, PreparedAccess)
    asyncio.run(gate.settle(second, b'{"response":2}'))
    journal.close()

    offline_facilitator = FakeFacilitator(
        verify_error=AssertionError("retention replay must not verify"),
        settle_error=AssertionError("retention replay must not settle"),
    )
    maintenance_gate, maintenance_journal, _ = _gate(
        tmp_path,
        facilitator=offline_facilitator,
        journal_kwargs=options,
        maintenance=True,
    )
    assert (
        maintenance_gate.retire_terminal_responses(
            older_than_seconds=86_400,
            limit=1,
        )
        == 1
    )

    with pytest.raises(SettledResponseRetired):
        asyncio.run(
            maintenance_gate.authorize(
                BODY,
                resource=RESOURCE,
                payment_signature=first_header,
            )
        )
    second_updated = maintenance_journal._connection.execute(
        "SELECT updated_at_ns FROM x402_access_journal_v1 WHERE payment_key = ?",
        (second.payment_id,),
    ).fetchone()[0]
    replay = asyncio.run(
        maintenance_gate.authorize(
            BODY,
            resource=RESOURCE,
            payment_signature=second_header,
        )
    )
    assert isinstance(replay, CompletedAccess)
    assert (
        maintenance_journal._connection.execute(
            "SELECT updated_at_ns FROM x402_access_journal_v1 WHERE payment_key = ?",
            (second.payment_id,),
        ).fetchone()[0]
        == second_updated
    )
    retired = maintenance_journal._connection.execute(
        "SELECT * FROM x402_access_journal_v1 WHERE payment_key = ?",
        (first.payment_id,),
    ).fetchone()
    assert retired["terminal_reason"] == "settled_response_retired"
    assert retired["payment_payload_sha256"] == first.payment_payload_sha256
    for column in (
        "payer",
        "payment_payload",
        "payment_requirements",
        "response_body",
        "response_sha256",
        "settlement_result",
        "payment_response",
        "identity_json",
    ):
        assert retired[column] in {None, ""}
    assert offline_facilitator.verify_calls == []
    assert offline_facilitator.settle_calls == []
    maintenance_journal.close()


def test_offline_age_retirement_removes_cached_failed_settlement_header(
    tmp_path: Path,
) -> None:
    day_ns = 86_400 * 1_000_000_000
    now = [10 * day_ns]
    options = {"clock_ns": lambda: now[0]}
    failure = _bad_settlement("unsuccessful")
    failure["transaction"] = ""
    facilitator = FakeFacilitator(settle_response=failure)
    gate, journal, _ = _gate(
        tmp_path,
        facilitator=facilitator,
        journal_kwargs=options,
    )
    header, _payment_payload, _required = _payment(gate)
    prepared = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=header)
    )
    assert isinstance(prepared, PreparedAccess)
    with pytest.raises(PaymentSettlementFailed):
        asyncio.run(gate.settle(prepared, RESPONSE))
    journal.close()

    now[0] += 2 * day_ns
    maintenance_gate, maintenance_journal, _ = _gate(
        tmp_path,
        facilitator=FakeFacilitator(
            verify_error=AssertionError("retired failure must not verify"),
            settle_error=AssertionError("retired failure must not settle"),
        ),
        journal_kwargs=options,
        maintenance=True,
    )
    assert maintenance_gate.retire_terminal_responses(older_than_seconds=86_400) == 1
    with pytest.raises(PaymentAuthorizationRetired):
        asyncio.run(
            maintenance_gate.authorize(
                BODY,
                resource=RESOURCE,
                payment_signature=header,
            )
        )
    row = maintenance_journal._connection.execute(
        "SELECT settlement_result, payment_response, payment_payload_sha256 "
        "FROM x402_access_journal_v1 WHERE payment_key = ?",
        (prepared.payment_id,),
    ).fetchone()
    assert row["settlement_result"] is None
    assert row["payment_response"] is None
    assert row["payment_payload_sha256"] == prepared.payment_payload_sha256
    maintenance_journal.close()


def test_identity_capacity_is_reserved_before_terminal_state(tmp_path: Path) -> None:
    facilitator = FakeFacilitator()
    gate, journal, _ = _gate(
        tmp_path,
        facilitator=facilitator,
        journal_kwargs={"max_entries": 2, "max_terminal_entries": 1},
    )
    first_header, _payment_payload, _required = _payment_with_nonce(gate, "b")
    first = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=first_header)
    )
    assert isinstance(first, PreparedAccess)
    second_header, _payment_payload, _required = _payment_with_nonce(gate, "c")
    with pytest.raises(X402AccessError) as full:
        asyncio.run(
            gate.authorize(
                BODY,
                resource=RESOURCE,
                payment_signature=second_header,
            )
        )
    assert full.value.code == "journal_terminal_capacity"
    assert full.value.http_status == 503
    assert gate.journal_counts().total == 1
    assert facilitator.settle_calls == []
    assert gate.abort(first) is True
    journal.close()


def test_processing_lease_recovers_after_restart_with_new_owner(
    tmp_path: Path,
) -> None:
    now = [1_000_000_000]
    options = {
        "max_entries": 1,
        "max_terminal_entries": 10,
        "processing_lease_seconds": 1,
        "clock_ns": lambda: now[0],
    }
    gate, journal, _facilitator = _gate(tmp_path, journal_kwargs=options)
    header, _payment_payload, _required = _payment(gate)
    original = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=header)
    )
    assert isinstance(original, PreparedAccess)
    journal.close()

    recovery_facilitator = FakeFacilitator()
    recovery_gate, recovery_journal, _ = _gate(
        tmp_path,
        facilitator=recovery_facilitator,
        journal_kwargs=options,
    )
    with pytest.raises(X402AccessError) as active:
        asyncio.run(
            recovery_gate.authorize(
                BODY,
                resource=RESOURCE,
                payment_signature=header,
            )
        )
    assert active.value.code == "payment_processing"
    assert recovery_facilitator.verify_calls == []

    now[0] += 1_000_000_001
    recovered = asyncio.run(
        recovery_gate.authorize(
            BODY,
            resource=RESOURCE,
            payment_signature=header,
        )
    )
    assert isinstance(recovered, PreparedAccess)
    assert recovered._owner_token != original._owner_token
    with pytest.raises(X402AccessError) as stale_owner:
        asyncio.run(recovery_gate.settle(original, RESPONSE))
    assert stale_owner.value.code == "payment_owner_mismatch"
    assert recovery_facilitator.settle_calls == []
    assert recovery_gate.abort(recovered) is True
    recovery_journal.close()


def test_expired_processing_row_frees_hot_capacity_for_new_identity(
    tmp_path: Path,
) -> None:
    now = [1_000_000_000]
    options = {
        "max_entries": 1,
        "max_terminal_entries": 10,
        "processing_lease_seconds": 1,
        "clock_ns": lambda: now[0],
    }
    gate, journal, _facilitator = _gate(tmp_path, journal_kwargs=options)
    first_header, _payment_payload, _required = _payment_with_nonce(gate, "b")
    first = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=first_header)
    )
    assert isinstance(first, PreparedAccess)
    now[0] += 1_000_000_001

    second_header, _payment_payload, _required = _payment_with_nonce(gate, "c")
    second = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=second_header)
    )
    assert isinstance(second, PreparedAccess)
    assert gate.journal_counts().total == 1
    assert gate.abort(second) is True
    journal.close()


def test_settling_never_expires_or_frees_capacity(tmp_path: Path) -> None:
    now = [1_000_000_000]
    options = {
        "max_entries": 1,
        "max_terminal_entries": 10,
        "processing_lease_seconds": 1,
        "clock_ns": lambda: now[0],
    }
    gate, journal, _facilitator = _gate(tmp_path, journal_kwargs=options)
    first_header, _payment_payload, _required = _payment_with_nonce(gate, "b")
    first = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=first_header)
    )
    assert isinstance(first, PreparedAccess)
    journal.begin_settlement(
        first,
        response_body=RESPONSE,
        status_code=200,
        content_type="application/json",
    )
    now[0] += 100_000_000_000
    journal.close()

    facilitator = FakeFacilitator()
    recovery_gate, recovery_journal, _ = _gate(
        tmp_path,
        facilitator=facilitator,
        journal_kwargs=options,
    )
    with pytest.raises(SettlementUncertain):
        asyncio.run(
            recovery_gate.authorize(
                BODY,
                resource=RESOURCE,
                payment_signature=first_header,
            )
        )
    second_header, _payment_payload, _required = _payment_with_nonce(recovery_gate, "c")
    with pytest.raises(X402AccessError) as capacity:
        asyncio.run(
            recovery_gate.authorize(
                BODY,
                resource=RESOURCE,
                payment_signature=second_header,
            )
        )
    assert capacity.value.code == "journal_full"
    assert facilitator.settle_calls == []
    recovery_journal.close()


def test_settled_exact_replay_survives_quote_and_metadata_rotation(
    tmp_path: Path,
) -> None:
    config_a = _config(
        resource_info_extra={"serviceName": "LiquiLens v1"},
        required_extensions={
            "bazaar": {
                "info": {"discoverable": True},
                "schema": {"type": "object"},
            }
        },
    )
    gate_a, journal_a, _facilitator_a = _gate(tmp_path, config=config_a)
    header, original_payment, _required = _payment(gate_a)
    prepared = asyncio.run(
        gate_a.authorize(BODY, resource=RESOURCE, payment_signature=header)
    )
    assert isinstance(prepared, PreparedAccess)
    completed = asyncio.run(gate_a.settle(prepared, RESPONSE))
    journal_a.close()

    config_b = _config(
        amount="20000",
        pay_to="0x" + "6" * 40,
        quote_binding_key=b"rotated-dedicated-x402-quote-key!",
        resource_info_extra={"serviceName": "LiquiLens v2"},
        required_extensions={
            "bazaar-v2": {
                "info": {"discoverable": True, "version": 2},
                "schema": {"type": "object"},
            }
        },
    )
    rotated_facilitator = FakeFacilitator(
        verify_error=AssertionError("settled rotation replay must not verify"),
        settle_error=AssertionError("settled rotation replay must not settle"),
    )
    gate_b, journal_b, _ = _gate(
        tmp_path,
        config=config_b,
        facilitator=rotated_facilitator,
    )
    replay = asyncio.run(
        gate_b.authorize(
            BODY,
            resource=RESOURCE,
            payment_signature=header,
        )
    )
    assert isinstance(replay, CompletedAccess)
    assert replay.cached is True
    assert replay.response_body == RESPONSE
    assert replay.payment_identity == completed.payment_identity

    tampered = copy.deepcopy(original_payment)
    tampered["extensions"][LIQUILENS_EXTENSION]["info"]["clientHint"] = (
        "different-full-payload"
    )
    with pytest.raises(X402AccessError) as mismatch:
        asyncio.run(
            gate_b.authorize(
                BODY,
                resource=RESOURCE,
                payment_signature=_b64(tampered),
            )
        )
    assert mismatch.value.code == "payment_payload_mismatch"
    assert rotated_facilitator.verify_calls == []
    assert rotated_facilitator.settle_calls == []
    journal_b.close()


def test_cached_legacy_payment_response_obeys_current_header_bound(
    tmp_path: Path,
) -> None:
    settlement = {
        "success": True,
        "payer": PAYER,
        "transaction": TRANSACTION,
        "network": NETWORK,
        "amount": AMOUNT,
        "extensions": {"padding": "x" * 12_000},
    }
    facilitator = FakeFacilitator(settle_response=settlement)
    config_a = _config(max_payment_header_bytes=32 * 1024)
    gate_a, journal_a, _ = _gate(
        tmp_path,
        config=config_a,
        facilitator=facilitator,
    )
    header, _payment_payload, _required = _payment(gate_a)
    prepared = asyncio.run(
        gate_a.authorize(BODY, resource=RESOURCE, payment_signature=header)
    )
    assert isinstance(prepared, PreparedAccess)
    completed = asyncio.run(gate_a.settle(prepared, RESPONSE))
    assert len(completed.payment_response_header) > len(header)
    journal_a.close()

    current_bound = len(header) + 128
    assert current_bound < len(completed.payment_response_header)
    replay_facilitator = FakeFacilitator(
        verify_error=AssertionError("oversized cache must not verify"),
        settle_error=AssertionError("oversized cache must not settle"),
    )
    gate_b, journal_b, _ = _gate(
        tmp_path,
        config=_config(max_payment_header_bytes=current_bound),
        facilitator=replay_facilitator,
    )
    with pytest.raises(X402AccessError) as caught:
        asyncio.run(
            gate_b.authorize(
                BODY,
                resource=RESOURCE,
                payment_signature=header,
            )
        )
    assert caught.value.code == "journal_corrupt"
    assert caught.value.http_status == 503
    assert replay_facilitator.verify_calls == []
    assert replay_facilitator.settle_calls == []
    journal_b.close()


def test_operator_retirement_is_terminal_and_never_repayable(tmp_path: Path) -> None:
    facilitator = FakeFacilitator(settle_error=TimeoutError("unknown outcome"))
    gate, journal, _ = _gate(tmp_path, facilitator=facilitator)
    header, _payment_payload, _required = _payment(gate)
    prepared = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=header)
    )
    assert isinstance(prepared, PreparedAccess)
    with pytest.raises(SettlementUncertain):
        asyncio.run(gate.settle(prepared, RESPONSE))
    with pytest.raises(X402AccessError) as serving:
        gate.retire_unsettled(prepared.payment_id)
    assert serving.value.code == "reconciliation_runtime_active"
    assert len(facilitator.verify_calls) == 1
    assert len(facilitator.settle_calls) == 1
    journal.close()

    maintenance_gate, maintenance_journal, _ = _gate(
        tmp_path,
        facilitator=FakeFacilitator(),
        maintenance=True,
    )
    maintenance_gate.retire_unsettled(prepared.payment_id)
    maintenance_journal.close()

    replay_facilitator = FakeFacilitator(
        verify_error=AssertionError("retired authorization must not verify"),
        settle_error=AssertionError("retired authorization must not settle"),
    )
    replay_gate, replay_journal, _ = _gate(tmp_path, facilitator=replay_facilitator)
    with pytest.raises(PaymentAuthorizationRetired) as retired:
        asyncio.run(
            replay_gate.authorize(
                BODY,
                resource=RESOURCE,
                payment_signature=header,
            )
        )
    assert retired.value.code == "payment_authorization_retired"
    assert retired.value.http_status == 409
    assert replay_facilitator.verify_calls == []
    assert replay_facilitator.settle_calls == []
    replay_journal.close()


def test_reconciliation_success_and_terminal_transition_are_one_atomic_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate, journal, _facilitator = _gate(tmp_path, maintenance=True)
    header, _payment_payload, _required = _payment(gate)
    prepared = asyncio.run(
        gate.authorize(BODY, resource=RESOURCE, payment_signature=header)
    )
    assert isinstance(prepared, PreparedAccess)
    journal.begin_settlement(
        prepared,
        response_body=RESPONSE,
        status_code=200,
        content_type="application/json",
    )
    competing_gate, competing_journal, _ = _gate(
        tmp_path,
        facilitator=FakeFacilitator(
            verify_error=AssertionError("operator race must not verify"),
            settle_error=AssertionError("operator race must not settle"),
        ),
        maintenance=True,
    )

    validation_entered = threading.Event()
    allow_commit = threading.Event()
    retirement_started = threading.Event()
    original_validation = SQLiteSettlementJournal._validate_completion_row

    def blocking_validation(
        row: sqlite3.Row,
        *,
        payment_id: str,
        payment_response: str,
        identity: Any,
    ) -> None:
        original_validation(
            row,
            payment_id=payment_id,
            payment_response=payment_response,
            identity=identity,
        )
        validation_entered.set()
        assert allow_commit.wait(timeout=2)

    monkeypatch.setattr(
        SQLiteSettlementJournal,
        "_validate_completion_row",
        staticmethod(blocking_validation),
    )
    reconciliation_errors: list[BaseException] = []
    retirement_errors: list[BaseException] = []

    def reconcile() -> None:
        try:
            gate.reconcile_settled(
                prepared.payment_id,
                copy.deepcopy(FakeFacilitator().settle_response),
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            reconciliation_errors.append(exc)

    def retire() -> None:
        retirement_started.set()
        try:
            competing_gate.retire_unsettled(prepared.payment_id)
        except BaseException as exc:
            retirement_errors.append(exc)

    reconciliation_thread = threading.Thread(target=reconcile)
    retirement_thread = threading.Thread(target=retire)
    reconciliation_thread.start()
    assert validation_entered.wait(timeout=2)
    externally_visible = competing_gate.reconciliation_records()
    assert len(externally_visible) == 1
    assert externally_visible[0].result_observed is False
    retirement_thread.start()
    assert retirement_started.wait(timeout=2)
    allow_commit.set()
    reconciliation_thread.join(timeout=2)
    retirement_thread.join(timeout=2)
    assert not reconciliation_thread.is_alive()
    assert not retirement_thread.is_alive()
    assert reconciliation_errors == []
    assert len(retirement_errors) == 1
    assert isinstance(retirement_errors[0], X402AccessError)
    assert retirement_errors[0].code == "reconciliation_not_found"

    replay = asyncio.run(
        gate.authorize(
            BODY,
            resource=RESOURCE,
            payment_signature=header,
        )
    )
    assert isinstance(replay, CompletedAccess)
    assert replay.response_body == RESPONSE
    assert gate.reconciliation_records() == ()
    competing_journal.close()
    journal.close()


def test_sqlite_database_wal_and_shm_are_private_under_permissive_umask(
    tmp_path: Path,
) -> None:
    previous_umask = os.umask(0)
    journal: SQLiteSettlementJournal | None = None
    try:
        gate, journal, _facilitator = _gate(tmp_path)
        header, _payment_payload, _required = _payment(gate)
        prepared = asyncio.run(
            gate.authorize(BODY, resource=RESOURCE, payment_signature=header)
        )
        assert isinstance(prepared, PreparedAccess)
        for path in (
            tmp_path / "x402.sqlite3",
            tmp_path / "x402.sqlite3-wal",
            tmp_path / "x402.sqlite3-shm",
        ):
            assert path.exists()
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
    finally:
        os.umask(previous_umask)
        if journal is not None:
            journal.close()


def test_nonempty_legacy_journal_fails_startup_until_explicit_migration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE x402_access_journal_v1 (
            payment_key TEXT PRIMARY KEY,
            body_sha256 TEXT NOT NULL,
            resource TEXT NOT NULL,
            state TEXT NOT NULL CHECK (
                state IN ('processing', 'settling', 'settled')
            ),
            owner_token TEXT,
            payer TEXT NOT NULL,
            response_body BLOB,
            status_code INTEGER,
            content_type TEXT,
            payment_response TEXT,
            identity_json BLOB,
            updated_at_ns INTEGER NOT NULL
        ) WITHOUT ROWID
        """
    )
    connection.execute(
        """
        INSERT INTO x402_access_journal_v1 (
            payment_key, body_sha256, resource, state, owner_token, payer,
            response_body, status_code, content_type, payment_response,
            identity_json, updated_at_ns
        ) VALUES (?, ?, ?, 'settled', NULL, ?, ?, 200, 'application/json',
                  'e30=', ?, 1)
        """,
        (
            "a" * 64,
            "b" * 64,
            RESOURCE,
            PAYER,
            RESPONSE,
            b"{}",
        ),
    )
    connection.commit()
    connection.close()

    for _attempt in range(2):
        with pytest.raises(X402AccessError) as blocked:
            SQLiteSettlementJournal(path)
        assert blocked.value.code == "journal_migration_required"
        assert blocked.value.http_status == 503
