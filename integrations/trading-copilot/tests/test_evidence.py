from __future__ import annotations

import asyncio
import copy
import json
import runpy
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from liquilens_evidence import (
    InMemoryReceiptConsumer,
    TradeSafetyExecutionBinding,
    before_order,
)
from liquilens_evidence.trade_safety import (
    TradeSafetyError,
    _receipt_digest,
    trade_safety_policy_hash,
    trade_safety_request_hash,
    verify_trade_safety_receipt,
)
from trade_safety_gateway.app import (
    LIQUILENS_BASE_URL,
    MAX_UPSTREAM_BYTES,
    SEICHE_URL,
    UNDERTOW_URL,
    TradeSafetyGateway,
)

from liquilens_trading_copilot.evidence import (
    LiquiLensStrategyContext,
    OperatorEvidenceError,
    OperatorEvidenceService,
)

# Reuse producer-contract fixtures, rather than inventing already-passed
# normalized evidence. run_path loads definitions; no gateway tests execute.
ROOT = Path(__file__).resolve().parents[3]
FIXTURES = runpy.run_path(
    str(ROOT / "integrations/trade-safety-gateway/tests/test_gateway.py")
)
NOW = FIXTURES["NOW"]
KEY = b"operator-local-test-key-not-a-broker-credential"


def binding(
    request: dict[str, Any], policy: dict[str, Any]
) -> TradeSafetyExecutionBinding:
    agent = request["agent"]
    return TradeSafetyExecutionBinding(
        **{
            name: agent[name]
            for name in (
                "account_id",
                "tenant_id",
                "operator_id",
                "agent_id",
                "runtime",
                "strategy_id",
            )
        },
        policy_id=policy["policy_id"],
        policy_version=policy["version"],
        policy_hash=trade_safety_policy_hash(policy),
        issuer_name="private-paper-operator",
        issuer_version="1.0.0",
        issuer_endpoint="https://operator.example.test/paper",
        hmac_key_id="paper-test-v1",
    )


def setup(
    *,
    request: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    context: LiquiLensStrategyContext | None = None,
    **kwargs: Any,
) -> tuple[Any, dict[str, Any], TradeSafetyExecutionBinding, OperatorEvidenceService]:
    request = request if request is not None else FIXTURES["_request"]()
    policy = policy if policy is not None else FIXTURES["_policy"]()
    upstream = FIXTURES["FakeUpstream"]()
    upstream.responses[UNDERTOW_URL] = FIXTURES["_undertow_bytes"](
        request_hash=trade_safety_request_hash(request)
    )
    execution = binding(request, policy)
    service = OperatorEvidenceService(
        upstream,
        policy=policy,
        binding=execution,
        hmac_key=KEY,
        clock=lambda: NOW,
        liquilens_context=context,
        **kwargs,
    )
    return upstream, request, execution, service


def research_context() -> bytes:
    return json.dumps(
        {
            "slug": "example-bank",
            "historical_evidence": {
                "status": "research_only",
                "validated_backtest_eligible": False,
                "real_money_eligible": False,
            },
            "trajectory": [{"period_end": "2026-09-02"}],
        }
    ).encode()


def test_native_sources_produce_authenticated_exact_paper_authorization() -> None:
    upstream, request, execution, service = setup()
    result = asyncio.run(service.assess(request))
    assert result.outcome == "pass"
    assert result.strategy_context is None
    assert result.source_receipt["integrity"]["profile"] == "sha256"
    receipt = result.receipt
    verified = verify_trade_safety_receipt(receipt, evaluated_at=NOW, hmac_key=KEY)
    assert verified.authenticated
    assert receipt["issuer"]["name"] == "private-paper-operator"
    assert receipt["evidence"] == result.source_receipt["evidence"]
    assert receipt["expires_at"] <= result.source_receipt["expires_at"]
    for section in receipt["evidence"].values():
        assert section["real_money_eligible"] is False
        assert section["executable_quote"] is False
    auth = before_order(
        request,
        receipt,
        evaluated_at=NOW,
        binding=execution,
        hmac_key=KEY,
        receipt_consumer=InMemoryReceiptConsumer(clock=lambda: NOW),
    )
    assert auth.request_hash == trade_safety_request_hash(request)
    assert len(upstream.calls) == 2
    receipt["decision"]["outcome"] = "unavailable"
    assert result.outcome == "pass"  # caller mutation cannot change sealed result


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"side": "buy"}, "undertow_trade_safety_context_supports_only_sell_orders"),
        (
            {"quantity": 100.0},
            "quantity_requires_broker_normalized_economic_order_binding",
        ),
        (
            {"notional": {"amount": 500.0, "currency": "USD"}},
            "undertow_requires_an_exact_published_usd_rung",
        ),
        (
            {"notional": {"amount": 1000.0, "currency": "EUR"}},
            "undertow_supports_only_usd_notional",
        ),
    ],
)
def test_unsupported_orders_remain_unavailable_without_sell_substitution(
    change: dict[str, Any], reason: str
) -> None:
    request = FIXTURES["_request"]()
    request["order"].update(change)
    upstream, request, _, service = setup(request=request)
    result = asyncio.run(service.assess(request))
    assert result.outcome == "unavailable"
    assert result.receipt["request"] == request
    assert reason in result.receipt["evidence"]["undertow"]["limitations"]
    assert all(url != UNDERTOW_URL for _, url, _ in upstream.calls)


@pytest.mark.parametrize(
    "native,expected",
    [
        (b'{"projection_sha256":"forged"}', "unavailable"),
        (FIXTURES["_seiche_bytes"](regime="STRESS"), "hold"),
        (FIXTURES["_seiche_bytes"](oldest_headline_asof="2026-08-20"), "unavailable"),
    ],
)
def test_native_invalid_stress_and_stale_are_not_promoted(
    native: bytes, expected: str
) -> None:
    upstream, request, _, service = setup()
    upstream.responses[SEICHE_URL] = native
    result = asyncio.run(service.assess(request))
    assert result.outcome == expected
    assert (
        result.receipt["evidence"]["seiche"]
        == result.source_receipt["evidence"]["seiche"]
    )


def test_live_request_and_changed_operator_binding_fail_before_network() -> None:
    upstream, request, _, service = setup()
    live = copy.deepcopy(request)
    live["mode"] = "live"
    live["agent"]["authorization_scope"] = ["orders:live"]
    with pytest.raises(OperatorEvidenceError, match="paper-only"):
        asyncio.run(service.assess(live))
    request["agent"]["account_id"] = "another-account"
    with pytest.raises(OperatorEvidenceError, match="binding differs"):
        asyncio.run(service.assess(request))
    assert upstream.calls == []


def test_exact_policy_mismatch_and_looser_policy_are_rejected() -> None:
    request, policy = FIXTURES["_request"](), FIXTURES["_policy"]()
    execution = binding(request, policy)
    policy["max_notional_usd"] = 200_000.0
    with pytest.raises(OperatorEvidenceError, match="policy does not match"):
        OperatorEvidenceService(
            FIXTURES["FakeUpstream"](),
            policy=policy,
            binding=execution,
            hmac_key=KEY,
            clock=lambda: NOW,
        )
    with pytest.raises(TradeSafetyError, match="max_notional"):
        setup(policy=policy)


def test_required_extra_context_is_hashed_research_separate_from_instrument() -> None:
    policy = FIXTURES["_policy"]()
    policy["required_products"].append("liquilens")
    upstream, request, execution, service = setup(
        policy=policy, context=LiquiLensStrategyContext("example-bank", required=True)
    )
    upstream.responses[LIQUILENS_BASE_URL + "example-bank"] = research_context()
    result = asyncio.run(service.assess(request))
    assert result.outcome == "pass"
    assert result.source_receipt["evidence"]["liquilens"]["state"] == "not_applicable"
    assert result.strategy_context is not None
    assert result.strategy_context.period_end == "2026-09-02"
    assert result.strategy_context.required
    receipt = result.receipt
    assert receipt["request"]["order"]["instrument"]["identifiers"] == {}
    assert receipt["request"]["extensions"] == {}
    context = receipt["evidence"]["liquilens"]["facts"]["operator_strategy_context"]
    assert context["role"] == "institution_research_context"
    assert context["context_sha256"] == result.strategy_context.context_sha256
    assert context["source_sha256"] == result.strategy_context.source_sha256
    assert receipt["evidence"]["liquilens"]["real_money_eligible"] is False
    before_order(
        request,
        receipt,
        evaluated_at=NOW,
        binding=execution,
        hmac_key=KEY,
        receipt_consumer=InMemoryReceiptConsumer(clock=lambda: NOW),
    )
    context["institution_slug"] = "another-bank"
    with pytest.raises(TradeSafetyError):
        verify_trade_safety_receipt(receipt, evaluated_at=NOW, hmac_key=KEY)


@pytest.mark.parametrize("required,expected", [(True, "unavailable"), (False, "pass")])
def test_extra_context_failure_is_explicit_and_obeys_exact_policy(
    required: bool, expected: str
) -> None:
    policy = FIXTURES["_policy"]()
    if required:
        policy["required_products"].append("liquilens")
    upstream, request, _, service = setup(
        policy=policy,
        context=LiquiLensStrategyContext("example-bank", required=required),
    )
    upstream.responses[LIQUILENS_BASE_URL + "example-bank"] = b"{}"
    result = asyncio.run(service.assess(request))
    assert result.outcome == expected
    assert result.strategy_context is not None
    assert result.strategy_context.state == "unavailable"
    assert (
        result.strategy_context.limitation
        == "liquilens_strategy_context_identity_mismatch"
    )
    if required:
        assert "liquilens_evidence_unavailable" in result.reason_codes


def test_required_context_cannot_omit_policy_or_hide_native_failure() -> None:
    with pytest.raises(OperatorEvidenceError, match="requirement differs"):
        setup(context=LiquiLensStrategyContext("example-bank", required=True))
    policy = FIXTURES["_policy"]()
    policy["required_products"].append("liquilens")
    upstream, request, _, service = setup(
        policy=policy, context=LiquiLensStrategyContext("example-bank", required=True)
    )
    upstream.responses[LIQUILENS_BASE_URL + "example-bank"] = research_context()
    upstream.responses[SEICHE_URL] = b"{}"
    result = asyncio.run(service.assess(request))
    assert result.outcome == "unavailable"
    assert "seiche_evidence_unavailable" in result.reason_codes


@pytest.mark.parametrize("required", [True, False])
@pytest.mark.parametrize("slug", [None, "another-bank"])
def test_context_native_identity_must_match_even_when_other_fields_are_valid(
    required: bool, slug: str | None
) -> None:
    policy = FIXTURES["_policy"]()
    if required:
        policy["required_products"].append("liquilens")
    upstream, request, _, service = setup(
        policy=policy,
        context=LiquiLensStrategyContext("example-bank", required=required),
    )
    payload = json.loads(research_context())
    if slug is None:
        del payload["slug"]
    else:
        payload["slug"] = slug
    upstream.responses[LIQUILENS_BASE_URL + "example-bank"] = json.dumps(
        payload
    ).encode()
    result = asyncio.run(service.assess(request))
    assert result.outcome == ("unavailable" if required else "pass")
    assert result.strategy_context is not None
    assert result.strategy_context.state == "unavailable"
    assert (
        result.strategy_context.limitation
        == "liquilens_strategy_context_identity_mismatch"
    )
    assert result.strategy_context.period_end is None
    assert result.strategy_context.source_sha256 is not None


def test_rehashed_but_forged_pass_is_not_blessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream, request, _, service = setup()
    upstream.responses[SEICHE_URL] = b"{}"
    native = asyncio.run(
        TradeSafetyGateway(upstream, clock=lambda: NOW).assess(
            request, FIXTURES["_policy"]()
        )
    )
    assert native["decision"]["outcome"] == "unavailable"
    native["decision"]["outcome"] = "pass"
    digest = _receipt_digest(native)
    native["record_hash"] = digest
    native["receipt_id"] = "trade_safety_" + digest[:24]

    async def forged(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return native

    monkeypatch.setattr(TradeSafetyGateway, "assess", forged)
    with pytest.raises(TradeSafetyError, match="deterministic policy"):
        asyncio.run(service.assess(request))


def test_injected_transport_still_has_body_and_time_bounds() -> None:
    upstream, request, _, service = setup()
    upstream.responses[SEICHE_URL] = b"x" * (MAX_UPSTREAM_BYTES + 1)
    result = asyncio.run(service.assess(request))
    assert result.outcome == "unavailable"
    upstream, request, _, service = setup(total_timeout_seconds=0.01)

    async def slow(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(1)

    upstream.request = slow
    with pytest.raises(OperatorEvidenceError, match="deadline"):
        asyncio.run(service.assess(request))


def test_expired_source_cannot_be_renewed(monkeypatch: pytest.MonkeyPatch) -> None:
    upstream, request, _, service = setup()
    native = asyncio.run(
        TradeSafetyGateway(upstream, clock=lambda: NOW).assess(
            request, FIXTURES["_policy"]()
        )
    )

    async def previous(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return native

    monkeypatch.setattr(TradeSafetyGateway, "assess", previous)
    service._clock = lambda: NOW + timedelta(seconds=61)
    with pytest.raises(TradeSafetyError, match="expired"):
        asyncio.run(service.assess(request))


def test_source_failure_cancels_pending_extra_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream, request, _, service = setup(
        context=LiquiLensStrategyContext("example-bank")
    )
    cancelled = False

    async def context_wait(*args: Any, **kwargs: Any) -> Any:
        nonlocal cancelled
        try:
            await asyncio.sleep(1)
        finally:
            cancelled = True

    async def failed_source(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0.001)
        raise TradeSafetyError("source could not finish")

    upstream.request = context_wait
    monkeypatch.setattr(TradeSafetyGateway, "assess", failed_source)

    async def check_before_loop_teardown() -> None:
        with pytest.raises(TradeSafetyError, match="could not finish"):
            await service.assess(request)
        assert cancelled

    asyncio.run(check_before_loop_teardown())


def test_required_context_preserves_old_period_end_as_unavailable() -> None:
    policy = FIXTURES["_policy"]()
    policy["required_products"].append("liquilens")
    upstream, request, _, service = setup(
        policy=policy, context=LiquiLensStrategyContext("example-bank", required=True)
    )
    upstream.responses[LIQUILENS_BASE_URL + "example-bank"] = (
        research_context().replace(b"2026-09-02", b"2026-08-01")
    )
    result = asyncio.run(service.assess(request))
    assert result.outcome == "unavailable"
    assert "liquilens_evidence_too_old" in result.reason_codes
    assert result.strategy_context is not None
    assert result.strategy_context.period_end == "2026-08-01"
    assert result.receipt["evidence"]["liquilens"]["as_of"].startswith("2026-08-01")


def test_strategy_context_cannot_use_extensions_or_ambiguous_instrument_ids() -> None:
    upstream, request, _, service = setup(
        context=LiquiLensStrategyContext("example-bank")
    )
    request["extensions"] = {"strategy_hash": "a" * 64}
    with pytest.raises(TradeSafetyError, match="unsupported execution semantics"):
        asyncio.run(service.assess(request))
    request["extensions"] = {}
    request["order"]["instrument"]["identifiers"] = {
        "liquilens_institution_slug": "example-bank"
    }
    with pytest.raises(OperatorEvidenceError, match="separate from identifiers"):
        asyncio.run(service.assess(request))
    with pytest.raises(OperatorEvidenceError, match="slug"):
        LiquiLensStrategyContext("../another-path")
    assert upstream.calls == []
