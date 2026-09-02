from __future__ import annotations

import asyncio
import copy
import inspect
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from liquilens_evidence import (
    AsyncInMemoryReceiptConsumer,
    AsyncPaperTradeSafetyOrderGateway,
    InMemoryReceiptConsumer,
    PaperTradeSafetyOrderGateway,
    TradeSafetyExecutionBinding,
    TradeSafetyOrderAuthorization,
    TradeSafetyOrderBlocked,
    TradeSafetyOrderGuardError,
    before_order,
    issue_trade_safety_receipt,
    trade_safety_policy_hash,
    trade_safety_request_hash,
)

ROOT = Path(__file__).resolve().parents[1]
EVALUATED_AT = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
HMAC_KEY = b"operator-owned-paper-test-key"


def _json(name: str) -> dict[str, Any]:
    value = json.loads(
        (ROOT / "examples" / "trade-safety" / name).read_text(encoding="utf-8")
    )
    assert isinstance(value, dict)
    return value


def _receipt(
    *,
    policy_change: dict[str, Any] | None = None,
    evidence_change: tuple[str, str, Any] | None = None,
    hmac_key: bytes | None = HMAC_KEY,
) -> tuple[dict[str, Any], dict[str, Any]]:
    request = _json("request.paper.json")
    policy = _json("policy.paper.json")
    evidence = _json("evidence.paper.json")
    if policy_change is not None:
        policy.update(policy_change)
    if evidence_change is not None:
        product, field, value = evidence_change
        evidence[product][field] = value
    receipt = issue_trade_safety_receipt(
        request=request,
        evidence=evidence,
        policy=policy,
        broker_preview=_json("broker-preview.paper.json"),
        evaluated_at=EVALUATED_AT,
        issuer=_json("issuer.paper.json"),
        ttl_seconds=60,
        hmac_key=hmac_key,
        hmac_key_id="operator-paper-key-v1" if hmac_key is not None else None,
    )
    return request, receipt


def _live_pass_receipt() -> tuple[dict[str, Any], dict[str, Any]]:
    request = _json("request.paper.json")
    request["mode"] = "live"
    request["agent"]["account_id"] = "live-account"
    request["agent"]["authorization_scope"] = ["evidence:read", "orders:live"]
    request_hash = trade_safety_request_hash(request)

    evidence = _json("evidence.paper.json")
    for product in ("seiche", "undertow"):
        evidence[product].update(
            {
                "request_hash": request_hash,
                "state": "eligible",
                "rights_status": "licensed",
                "real_money_eligible": True,
                "executable_quote": product == "undertow",
            }
        )
    evidence["liquilens"]["request_hash"] = request_hash

    broker_preview = _json("broker-preview.paper.json")
    broker_preview.update(
        {
            "state": "verified",
            "provider": "fixture-broker",
            "account_id": request["agent"]["account_id"],
            "request_hash": request_hash,
            "preview_id": "preview-live-001",
            "source_url": "https://broker.example/previews/preview-live-001",
            "source_sha256": "d" * 64,
            "expires_at": "2026-09-02T12:02:00Z",
        }
    )
    receipt = issue_trade_safety_receipt(
        request=request,
        evidence=evidence,
        policy=_json("policy.paper.json"),
        broker_preview=broker_preview,
        evaluated_at=EVALUATED_AT,
        issuer=_json("issuer.paper.json"),
        ttl_seconds=60,
        hmac_key=HMAC_KEY,
        hmac_key_id="operator-paper-key-v1",
    )
    assert receipt["decision"]["outcome"] == "pass"
    return request, receipt


def _binding(
    receipt: dict[str, Any], **changes: Any
) -> TradeSafetyExecutionBinding:
    request = receipt["request"]
    values: dict[str, Any] = {
        "account_id": request["agent"]["account_id"],
        "tenant_id": request["agent"]["tenant_id"],
        "operator_id": request["agent"]["operator_id"],
        "agent_id": request["agent"]["agent_id"],
        "runtime": request["agent"]["runtime"],
        "strategy_id": request["agent"]["strategy_id"],
        "policy_id": receipt["policy"]["policy_id"],
        "policy_version": receipt["policy"]["version"],
        "policy_hash": receipt["policy_hash"],
        "issuer_name": receipt["issuer"]["name"],
        "issuer_version": receipt["issuer"]["version"],
        "issuer_endpoint": receipt["issuer"]["endpoint"],
        "hmac_key_id": receipt["integrity"]["key_id"],
    }
    values.update(changes)
    return TradeSafetyExecutionBinding(**values)


def _clock(offset: int = 30):
    return lambda: EVALUATED_AT + timedelta(seconds=offset)


def _consumer(offset: int = 30) -> InMemoryReceiptConsumer:
    return InMemoryReceiptConsumer(clock=_clock(offset))


def _gateway(
    submit,
    receipt: dict[str, Any],
    *,
    binding: TradeSafetyExecutionBinding | None = None,
    offset: int = 30,
    hmac_key: bytes | None = HMAC_KEY,
) -> PaperTradeSafetyOrderGateway:
    return PaperTradeSafetyOrderGateway(
        submit,
        binding=binding or _binding(receipt),
        receipt_consumer=_consumer(offset),
        hmac_key=hmac_key,
        clock=_clock(offset),
    )


def test_before_order_returns_sealed_exact_authorization():
    request, receipt = _receipt()
    binding = _binding(receipt)
    assert trade_safety_policy_hash(receipt["policy"]) == binding.policy_hash
    authorization = before_order(
        request,
        receipt,
        evaluated_at=EVALUATED_AT + timedelta(seconds=30),
        binding=binding,
        receipt_consumer=_consumer(),
        hmac_key=HMAC_KEY,
    )
    assert authorization.authenticated is True
    assert authorization.request == request
    assert authorization.order == request["order"]
    assert authorization.receipt_id == receipt["receipt_id"]
    assert authorization.binding == binding
    with pytest.raises(TypeError, match="must come from before_order"):
        TradeSafetyOrderAuthorization(
            request_json="{}",
            receipt_json="{}",
            request_hash="0" * 64,
            receipt_id="trade_safety_" + "0" * 24,
            authenticated=False,
            binding=binding,
            _seal=object(),
        )


@pytest.mark.parametrize(
    ("policy_change", "evidence_change", "reason_code"),
    (
        ({"max_notional_usd": 10.0}, None, "decision_limit"),
        (None, ("seiche", "facts", {"regime": "STRESS"}), "decision_hold"),
        (None, ("undertow", "state", "unavailable"), "decision_unavailable"),
    ),
)
def test_non_pass_outcomes_block_before_submit(
    policy_change, evidence_change, reason_code
):
    request, receipt = _receipt(
        policy_change=policy_change,
        evidence_change=evidence_change,
    )
    called = False

    def submit(_: TradeSafetyOrderAuthorization) -> None:
        nonlocal called
        called = True

    with pytest.raises(TradeSafetyOrderBlocked) as caught:
        _gateway(submit, receipt).submit(request, receipt)
    assert caught.value.reason_code == reason_code
    assert called is False


def test_exact_order_mutation_blocks_before_submit():
    request, receipt = _receipt()
    changed = copy.deepcopy(request)
    changed["order"]["quantity"] = 11.0
    called = False

    def submit(_: TradeSafetyOrderAuthorization) -> None:
        nonlocal called
        called = True

    with pytest.raises(TradeSafetyOrderBlocked) as caught:
        _gateway(submit, receipt).submit(changed, receipt)
    assert caught.value.reason_code == "request_mismatch"
    assert called is False


def test_gateway_owns_clock_and_expired_receipt_cannot_be_backdated():
    request, receipt = _receipt()
    called = False

    def submit(_: TradeSafetyOrderAuthorization) -> None:
        nonlocal called
        called = True

    gateway = _gateway(submit, receipt, offset=61)
    assert "evaluated_at" not in inspect.signature(gateway.submit).parameters
    with pytest.raises(TradeSafetyOrderBlocked) as expired:
        gateway.submit(request, receipt)
    assert expired.value.reason_code == "receipt_invalid"
    with pytest.raises(TypeError, match="evaluated_at"):
        gateway.submit(request, receipt, evaluated_at=EVALUATED_AT)  # type: ignore[call-arg]
    assert called is False


def test_missing_receipt_blocks_before_submit():
    request, receipt = _receipt()
    gateway = _gateway(lambda _: pytest.fail("submit was called"), receipt)
    with pytest.raises(TradeSafetyOrderBlocked) as missing:
        gateway.submit(request, {})
    assert missing.value.reason_code == "receipt_invalid"


def test_atomic_paper_consumer_rejects_replay():
    request, receipt = _receipt()
    submitted: list[str] = []
    consumer = _consumer()
    gateway = PaperTradeSafetyOrderGateway(
        lambda authorization: submitted.append(authorization.receipt_id),
        binding=_binding(receipt),
        receipt_consumer=consumer,
        hmac_key=HMAC_KEY,
        clock=_clock(),
    )
    gateway.submit(request, receipt)
    with pytest.raises(TradeSafetyOrderBlocked) as replay:
        gateway.submit(request, receipt)
    assert replay.value.reason_code == "receipt_replay"
    assert submitted == [receipt["receipt_id"]]


@pytest.mark.parametrize(
    ("change", "reason_code"),
    (
        ({"account_id": "different-account"}, "execution_context_mismatch"),
        ({"tenant_id": "different-tenant"}, "execution_context_mismatch"),
        ({"operator_id": "different-operator"}, "execution_context_mismatch"),
        ({"agent_id": "different-agent"}, "execution_context_mismatch"),
        ({"runtime": "different-runtime/1"}, "execution_context_mismatch"),
        ({"strategy_id": "different-strategy"}, "execution_context_mismatch"),
        ({"policy_id": "different-policy"}, "policy_identity_mismatch"),
        ({"policy_hash": "0" * 64}, "policy_hash_mismatch"),
        ({"issuer_name": "different-issuer"}, "issuer_mismatch"),
    ),
)
def test_operator_binding_blocks_cross_context_submission(change, reason_code):
    request, receipt = _receipt()
    called = False

    def submit(_: TradeSafetyOrderAuthorization) -> None:
        nonlocal called
        called = True

    with pytest.raises(TradeSafetyOrderBlocked) as caught:
        _gateway(submit, receipt, binding=_binding(receipt, **change)).submit(
            request, receipt
        )
    assert caught.value.reason_code == reason_code
    assert called is False


def test_integrity_key_identity_is_pinned_separately_from_key_bytes():
    request, receipt = _receipt()
    binding = _binding(receipt, hmac_key_id="different-key-id")
    with pytest.raises(TradeSafetyOrderBlocked) as caught:
        _gateway(
            lambda _: pytest.fail("submit was called"),
            receipt,
            binding=binding,
            hmac_key=HMAC_KEY,
        ).submit(request, receipt)
    assert caught.value.reason_code == "integrity_key_mismatch"


def test_agent_facing_gateway_rejects_hash_only_receipts_by_configuration():
    _, receipt = _receipt(hmac_key=None)
    with pytest.raises(TradeSafetyOrderGuardError, match="authenticated HMAC"):
        PaperTradeSafetyOrderGateway(
            lambda _: pytest.fail("submit was called"),
            binding=_binding(receipt),
            receipt_consumer=_consumer(),
            clock=_clock(),
        )


def test_live_mode_is_unconditionally_held_by_reference_gateway():
    request, receipt = _live_pass_receipt()
    called = False

    def submit(_: TradeSafetyOrderAuthorization) -> None:
        nonlocal called
        called = True

    with pytest.raises(TradeSafetyOrderBlocked) as held:
        _gateway(submit, receipt).submit(request, receipt)
    assert held.value.reason_code == "mode_not_supported"
    assert called is False


def test_async_gateway_uses_native_async_consumer():
    request, receipt = _receipt()
    submitted: list[str] = []

    async def submit(authorization: TradeSafetyOrderAuthorization) -> str:
        submitted.append(authorization.request_hash)
        return "paper-order-001"

    async def exercise() -> str:
        gateway = AsyncPaperTradeSafetyOrderGateway(
            submit,
            binding=_binding(receipt),
            receipt_consumer=AsyncInMemoryReceiptConsumer(clock=_clock()),
            hmac_key=HMAC_KEY,
            clock=_clock(),
        )
        return await gateway.submit(request, receipt)

    assert asyncio.run(exercise()) == "paper-order-001"
    assert submitted == [receipt["request_hash"]]


def test_gateway_rejects_callback_mode_mismatch_before_any_side_effect():
    _, receipt = _receipt()
    calls: list[str] = []

    class AsyncSubmitter:
        async def __call__(self, _: TradeSafetyOrderAuthorization) -> str:
            calls.append("async")
            return "paper-order-async"

    class SyncSubmitter:
        def __call__(self, _: TradeSafetyOrderAuthorization) -> str:
            calls.append("sync")
            return "paper-order-sync"

    with pytest.raises(TradeSafetyOrderGuardError, match="synchronous submit_order"):
        PaperTradeSafetyOrderGateway(
            AsyncSubmitter(),
            binding=_binding(receipt),
            receipt_consumer=_consumer(),
            hmac_key=HMAC_KEY,
            clock=_clock(),
        )
    with pytest.raises(TradeSafetyOrderGuardError, match="async submit_order"):
        AsyncPaperTradeSafetyOrderGateway(
            SyncSubmitter(),
            binding=_binding(receipt),
            receipt_consumer=AsyncInMemoryReceiptConsumer(clock=_clock()),
            hmac_key=HMAC_KEY,
            clock=_clock(),
        )
    assert calls == []


class _BrokenConsumer:
    def consume(self, **_: Any) -> bool:
        raise RuntimeError("store unavailable")


def test_consumer_failure_blocks_before_submit():
    request, receipt = _receipt()
    gateway = PaperTradeSafetyOrderGateway(
        lambda _: pytest.fail("submit was called"),
        binding=_binding(receipt),
        receipt_consumer=_BrokenConsumer(),
        hmac_key=HMAC_KEY,
        clock=_clock(),
    )
    with pytest.raises(TradeSafetyOrderBlocked) as caught:
        gateway.submit(request, receipt)
    assert caught.value.reason_code == "receipt_consumer_unavailable"


def test_in_memory_consumer_is_bounded_and_prunes_only_expired_claims():
    now = [EVALUATED_AT]
    consumer = InMemoryReceiptConsumer(clock=lambda: now[0], max_entries=1)
    expiry_one = "2026-09-02T12:01:00Z"
    expiry_two = "2026-09-02T12:02:00Z"
    assert consumer.consume(
        receipt_id="trade_safety_" + "1" * 24,
        request_hash="1" * 64,
        expires_at=expiry_one,
    )
    assert not consumer.consume(
        receipt_id="trade_safety_" + "2" * 24,
        request_hash="2" * 64,
        expires_at=expiry_two,
    )
    now[0] = EVALUATED_AT + timedelta(seconds=61)
    assert consumer.consume(
        receipt_id="trade_safety_" + "2" * 24,
        request_hash="2" * 64,
        expires_at=expiry_two,
    )
    now[0] = EVALUATED_AT + timedelta(seconds=30)
    assert not consumer.consume(
        receipt_id="trade_safety_" + "1" * 24,
        request_hash="1" * 64,
        expires_at=expiry_one,
    )


def test_async_in_memory_consumer_rejects_backward_clock():
    now = [EVALUATED_AT]
    consumer = AsyncInMemoryReceiptConsumer(clock=lambda: now[0], max_entries=1)

    async def exercise() -> None:
        assert await consumer.consume(
            receipt_id="trade_safety_" + "1" * 24,
            request_hash="1" * 64,
            expires_at="2026-09-02T12:01:00Z",
        )
        now[0] = EVALUATED_AT + timedelta(seconds=61)
        assert await consumer.consume(
            receipt_id="trade_safety_" + "2" * 24,
            request_hash="2" * 64,
            expires_at="2026-09-02T12:02:00Z",
        )
        now[0] = EVALUATED_AT + timedelta(seconds=30)
        assert not await consumer.consume(
            receipt_id="trade_safety_" + "1" * 24,
            request_hash="1" * 64,
            expires_at="2026-09-02T12:01:00Z",
        )

    asyncio.run(exercise())
