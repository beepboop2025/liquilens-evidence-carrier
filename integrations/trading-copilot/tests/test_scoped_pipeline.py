"""Synthetic three-source paper pipeline; no real API, order, fill or return.

Only external HTTP and the Alpaca SDK client are mocked. Source parsers,
scenario association, authenticated receipts, strategy, runner, actual order
guard/adapter and both durable SQLite stores run their production code.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import runpy
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from liquilens_alpaca_paper import (
    AlpacaPaperSubmissionState,
    SQLiteAlpacaPaperSubmissionJournal,
    client_order_id_for_request_hash,
)
from liquilens_evidence import TradeSafetyOrderBlocked, trade_safety_request_hash
from liquilens_evidence.trade_safety import (
    TradeSafetyError,
    issue_trade_safety_receipt,
    verify_trade_safety_receipt,
)
from trade_safety_gateway.app import UNDERTOW_URL

from liquilens_trading_copilot.broker import OperatorPaperTradeSafetyGateway
from liquilens_trading_copilot.config import (
    SCOPED_PROFILE,
    CopilotConfig,
    PaperCredentials,
    scoped_policy,
)
from liquilens_trading_copilot.entry_profile import build_liquidation_scenario
from liquilens_trading_copilot.market import PaperAccountReader
from liquilens_trading_copilot.runner import CopilotRunner
from liquilens_trading_copilot.scoped import (
    CORPORATE_URL,
    FUNDING_URL,
    ScopedAssessment,
    ScopedPaperEvidenceService,
    ScopedUpstreamTransport,
)
from liquilens_trading_copilot.state import CycleStore
from liquilens_trading_copilot.strategy import MarketBar

ROOT = Path(__file__).resolve().parents[3]
NATIVE = runpy.run_path(
    str(ROOT / "integrations/trade-safety-gateway/tests/test_gateway.py")
)
PIPELINE = runpy.run_path(str(Path(__file__).with_name("test_paper_pipeline.py")))
NOW = NATIVE["NOW"]
KEY = b"synthetic-three-source-paper-profile-key-no-credentials"
ACCOUNT = PIPELINE["ACCOUNT"]
SyntheticSDK = PIPELINE["SyntheticSDK"]


def source_payloads() -> dict[str, dict[str, Any]]:
    # The funding parser owner provides native-shaped builders. Reuse their
    # actual contract, with all dates aligned to the native Undertow fixture.
    fixtures = runpy.run_path(str(Path(__file__).with_name("test_funding.py")))
    return {
        "funding": fixtures["funding_payload"](now=NOW),
        "corporate": fixtures["corporate_payload"](now=NOW),
    }


def bars(side: str) -> list[MarketBar]:
    trend = 0.01 if side == "buy" else -0.01
    return [
        MarketBar(
            NOW - timedelta(hours=32 - index),
            30_000 * math.exp(trend * index + 0.001 * math.sin(index)),
        )
        for index in range(33)
    ]


@asynccontextmanager
async def scoped_pipeline(
    state_dir: Path,
    sdk: Any,
    *,
    side: str = "buy",
    source_change: Callable[[dict[str, dict[str, Any]]], None] | None = None,
    native_fault: str | None = None,
    receipt_change: Callable[[dict[str, Any]], None] | None = None,
    verifier_key: bytes = KEY,
    observed_status: str = "accepted",
    observation_http_status: int = 200,
) -> AsyncIterator[SimpleNamespace]:
    config = CopilotConfig(
        enabled=True,
        account_id=ACCOUNT,
        state_dir=str(state_dir.resolve()),
        evidence_profile=SCOPED_PROFILE,
        policy=scoped_policy(),
    )
    payloads = source_payloads()
    if source_change is not None:
        source_change(payloads)
    credentials = PaperCredentials("synthetic-key", "synthetic-secret", KEY)
    source_calls: list[httpx.Request] = []
    source_bodies: dict[str, bytes] = {}
    account_calls: list[httpx.Request] = []

    def serve_sources(request: httpx.Request) -> httpx.Response:
        source_calls.append(request)
        assert "APCA-API-KEY-ID" not in request.headers
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        if str(request.url) in {FUNDING_URL, CORPORATE_URL}:
            assert request.method == "GET"
            source = "funding" if str(request.url) == FUNDING_URL else "corporate"
            body = json.dumps(payloads[source], allow_nan=False).encode()
        else:
            assert str(request.url) == UNDERTOW_URL
            assert request.method == "POST"
            rpc = json.loads(request.content)
            assert rpc["method"] == "tools/call"
            assert rpc["params"]["name"] == "trade_safety_exit_context"
            args = rpc["params"]["arguments"]
            assert args["side"] == "sell"
            assert args["instrument"] == "BTC/USD"
            assert args["requested_size_usd"] == 1000
            request_hash = args["request_hash"]
            if native_fault == "scenario_hash":
                request_hash = "f" * 64
            body = NATIVE["_undertow_bytes"](
                request_hash=request_hash,
                requested=10_000 if native_fault == "scenario_size" else 1000,
                rung=10_000 if native_fault == "scenario_size" else 1000,
            )
            if native_fault == "rights_manifest_not_approved":
                native = json.loads(body)["result"]["structuredContent"]
                native["status"] = "unavailable"
                native["reason"] = "rights_manifest_not_approved"
                body = NATIVE["_mcp_response"](
                    "trade-safety-undertow-v1",
                    NATIVE["_sealed"](native, "context_sha256"),
                )
            if native_fault in {
                "native_future",
                "native_stale",
                "native_unknown_clock",
            }:
                native = json.loads(body)["result"]["structuredContent"]
                if native_fault == "native_unknown_clock":
                    native["clocks"]["oldest_observation_at"] = None
                else:
                    shift = timedelta(
                        seconds=120 if native_fault == "native_future" else -600
                    )

                    def moved(value: str) -> str:
                        return (
                            (
                                datetime.fromisoformat(value.replace("Z", "+00:00"))
                                + shift
                            )
                            .isoformat()
                            .replace("+00:00", "Z")
                        )

                    for key, value in native["clocks"].items():
                        if isinstance(value, str):
                            native["clocks"][key] = moved(value)
                        elif isinstance(value, dict):
                            native["clocks"][key] = {
                                name: moved(at) for name, at in value.items()
                            }
                    native["peg"]["observation_at"] = moved(
                        native["peg"]["observation_at"]
                    )
                    native["pit"]["key"] = moved(native["pit"]["key"])
                # Retain a correct producer checksum: clocks, rather than a
                # trivially broken digest, must prevent eligibility.
                body = NATIVE["_mcp_response"](
                    "trade-safety-undertow-v1",
                    NATIVE["_sealed"](native, "context_sha256"),
                )
        source_bodies[str(request.url)] = body
        return httpx.Response(
            200, content=body, headers={"Content-Type": "application/json"}
        )

    def serve_account(request: httpx.Request) -> httpx.Response:
        account_calls.append(request)
        assert request.method == "GET"
        assert request.url.host == "paper-api.alpaca.markets"
        assert request.headers["APCA-API-KEY-ID"] == credentials.api_key
        holding = 0 if side == "buy" else 1500
        if observed_status == "filled" and sdk.orders:
            holding += 1000 if side == "buy" else -1000
        if request.url.path == "/v2/orders:by_client_order_id":
            if observation_http_status != 200:
                return httpx.Response(
                    observation_http_status,
                    json={"message": "synthetic order unavailable"},
                )
            assert len(sdk.orders) == 1
            assert (
                request.url.params["client_order_id"] == sdk.orders[0].client_order_id
            )
            return httpx.Response(
                observation_http_status,
                json={
                    "id": "synthetic-accepted-unfilled-1",
                    "client_order_id": sdk.orders[0].client_order_id,
                    "symbol": "BTC/USD",
                    "side": side,
                    "status": observed_status,
                    "filled_qty": "0.02" if observed_status == "filled" else "0",
                    "filled_avg_price": "50000"
                    if observed_status == "filled"
                    else None,
                },
            )
        responses = {
            "/v2/account": {
                "id": ACCOUNT,
                "status": "ACTIVE",
                "currency": "USD",
                "cash": str(100_000 - holding),
                "equity": "100000",
                "last_equity": "100000",
                "trading_blocked": False,
                "account_blocked": False,
                "trade_suspended_by_user": False,
            },
            "/v2/positions": []
            if not holding
            else [{"symbol": "BTCUSD", "market_value": str(holding)}],
            "/v2/orders": [],
        }
        assert request.url.path in responses
        return httpx.Response(200, json=responses[request.url.path])

    transport = ScopedUpstreamTransport(transport=httpx.MockTransport(serve_sources))
    service = ScopedPaperEvidenceService(
        transport,
        binding=config.binding(),
        policy=config.policy,
        hmac_key=KEY,
        clock=lambda: NOW,
    )
    if receipt_change is not None:
        original_assess = service.assess

        async def modified_assessment(request: dict[str, Any]) -> ScopedAssessment:
            # This deliberately compromised issuer still uses a valid HMAC.
            # The separate profile verifier must detect altered crosslinks.
            assessed = await original_assess(request)
            receipt = assessed.receipt
            receipt_change(receipt)
            changed = issue_trade_safety_receipt(
                request=receipt["request"],
                evidence=receipt["evidence"],
                policy=receipt["policy"],
                broker_preview=receipt["broker_preview"],
                evaluated_at=NOW,
                issuer=receipt["issuer"],
                ttl_seconds=30,
                hmac_key=KEY,
                hmac_key_id=receipt["integrity"]["key_id"],
            )
            return ScopedAssessment(json.dumps(changed))

        service.assess = modified_assessment  # type: ignore[method-assign]
    store = CycleStore(Path(config.state_dir))
    journal = SQLiteAlpacaPaperSubmissionJournal(
        Path(config.state_dir) / "alpaca-submissions.sqlite3", clock=lambda: NOW
    )
    try:
        broker = OperatorPaperTradeSafetyGateway(
            state_dir=Path(config.state_dir),
            binding=config.binding(),
            submission_journal=journal,
            hmac_key=verifier_key,
            api_key=credentials.api_key,
            secret_key=credentials.secret_key,
            clock=lambda: NOW,
            _client_factory=lambda **_kwargs: sdk,
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(serve_account),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            runner = CopilotRunner(
                config,
                store,
                account_reader=PaperAccountReader(client, credentials, ACCOUNT),
                evidence_service=service,
                broker=broker,
                clock=lambda: NOW,
            )
            yield SimpleNamespace(
                runner=runner,
                service=service,
                broker=broker,
                store=store,
                journal=journal,
                config=config,
                payloads=payloads,
                source_calls=source_calls,
                source_bodies=source_bodies,
                account_calls=account_calls,
            )
    finally:
        await transport.aclose()
        journal.close()
        store.close()


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_three_native_sources_authorize_only_original_paper_candidate(
    tmp_path: Path, side: str
) -> None:
    async def run() -> None:
        sdk = SyntheticSDK()
        async with scoped_pipeline(tmp_path, sdk, side=side) as system:
            initial_regime = await system.service.funding_regime()
            assert initial_regime == "CALM"
            result = await system.runner.cycle(bars(side), seiche_regime=initial_regime)
            assert result["status"] == "submitted", result
            assert len(sdk.orders) == 1
            assert result["decision"]["action"] == side
            if side == "buy":
                assert result["portfolio"]["btc_notional_usd"] == 0
                assert result["portfolio"]["cash_usd"] == 100_000
            request, receipt = result["request"], result["receipt"]
            assert request["order"]["side"] == side
            assert request["mode"] == "paper"
            assert request["policy_ref"]["policy_id"] == SCOPED_PROFILE
            assert receipt["request"] == request
            assert receipt["decision"]["outcome"] == "pass"
            assert receipt["integrity"]["key_id"] == "operator-paper-funding-exit-v1"
            assert verify_trade_safety_receipt(
                receipt, evaluated_at=NOW, hmac_key=KEY
            ).authenticated
            assert set(receipt["evidence"]) == {"seiche", "liquilens", "undertow"}
            assert all(
                section["state"] == "context_only"
                for section in receipt["evidence"].values()
            )
            assert all(
                section["executable_quote"] is False
                for section in receipt["evidence"].values()
            )
            assert all(
                section["real_money_eligible"] is False
                for section in receipt["evidence"].values()
            )
            for product, url in (
                ("seiche", FUNDING_URL),
                ("liquilens", CORPORATE_URL),
                ("undertow", UNDERTOW_URL),
            ):
                section = receipt["evidence"][product]
                assert section["source_url"] == url
                assert (
                    section["source_sha256"]
                    == hashlib.sha256(system.source_bodies[url]).hexdigest()
                )
            original_hash = trade_safety_request_hash(request)
            scenario = build_liquidation_scenario(request)
            scenario_hash = trade_safety_request_hash(scenario)
            assert original_hash != scenario_hash
            native_request = next(
                json.loads(call.content)["params"]["arguments"]
                for call in system.source_calls
                if str(call.url) == UNDERTOW_URL
            )
            assert native_request["side"] == "sell"
            assert native_request["request_hash"] == scenario_hash
            section = receipt["evidence"]["undertow"]
            proof = section["facts"]["operator_liquidation_scenario"]
            assert section["request_hash"] == original_hash
            assert proof["scenario_request"] == scenario
            assert proof["original_side"] == side
            assert proof["buy_execution_cost_available"] is False
            assert proof["future_exit_liquidity_guaranteed"] is False
            assert proof["fill_evidence"] is False
            order = sdk.orders[0]
            assert order.side.value == side
            assert order.symbol == "BTC/USD" and order.notional == 1000
            assert order.qty is None and order.time_in_force.value == "ioc"
            assert order.client_order_id == client_order_id_for_request_hash(
                original_hash
            )
            record = system.journal.get(original_hash)
            assert record.state == AlpacaPaperSubmissionState.SUBMITTED
            assert record.submit_attempts == 1
            assert result["broker_order"]["filled_qty"] == "0"
            assert result["broker_order"]["filled_avg_price"] is None
            assert system.store.status()["filled_order_count"] == 0
            assert system.store.status()["intent_count"] == 1
        # Reopening both stores cannot convert a new request UUID into another
        # submission for the same completed bar. Accepted is still unfilled.
        async with scoped_pipeline(tmp_path, sdk, side=side) as restarted:
            replay = await restarted.runner.cycle(bars(side), seiche_regime="CALM")
            assert replay["status"] == "blocked"
            assert replay["reasons"] == ["pending_paper_order"]
            assert "request" not in replay
            assert len(sdk.orders) == 1
            assert restarted.store.status()["filled_order_count"] == 0
            assert restarted.store.status()["order_observation_count"] == 1

    asyncio.run(run())


def test_stop_during_sdk_account_lookup_prevents_submit_and_retains_intent(
    tmp_path: Path,
) -> None:
    class StoppingSDK(SyntheticSDK):
        def get_account(self) -> dict[str, str]:
            (tmp_path / "STOP").write_text("Synthetic stop during account lookup.\n")
            return super().get_account()

    async def run() -> None:
        sdk = StoppingSDK()
        async with scoped_pipeline(
            tmp_path, sdk, observation_http_status=404
        ) as system:
            result = await system.runner.cycle(bars("buy"), seiche_regime="CALM")
            assert result["status"] == "stopped"
            assert result["reasons"] == ["operator_stop_file_present_before_submission"]
            assert sdk.account_reads == 1
            assert sdk.orders == []
            assert system.store.status()["intent_count"] == 1
            assert (
                system.journal.get(trade_safety_request_hash(result["request"])) is None
            )
            (tmp_path / "STOP").unlink()
            resumed = await system.runner.cycle(bars("buy"), seiche_regime="CALM")
            assert resumed["status"] == "blocked"
            assert resumed["reasons"] == ["paper_order_observation_unavailable"]
            assert system.store.status()["intent_count"] == 1
            assert sdk.orders == []
            assert sdk.account_reads == 1

    asyncio.run(run())


def test_stop_during_receipt_claim_retains_claim_without_starting_sdk_submission(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        sdk = SyntheticSDK()
        async with scoped_pipeline(
            tmp_path, sdk, observation_http_status=404
        ) as system:
            original_consume = system.journal.consume

            def consume_and_stop(**values: str) -> bool:
                consumed = original_consume(**values)
                (tmp_path / "STOP").write_text("Synthetic stop during receipt claim.\n")
                return consumed

            system.journal.consume = consume_and_stop
            result = await system.runner.cycle(bars("buy"), seiche_regime="CALM")
            assert result["status"] == "stopped"
            assert result["reasons"] == ["operator_stop_file_present_before_submission"]
            request_hash = trade_safety_request_hash(result["request"])
            claim = system.journal.get(request_hash)
            assert claim.state == AlpacaPaperSubmissionState.CLAIMED
            assert claim.submit_attempts == 0
            assert claim.broker_order_id is None
            assert system.store.status()["intent_count"] == 1
            assert sdk.orders == []
            # Read-only reconciliation can resolve the claim as not submitted,
            # even with STOP present; its attempt budget is never released.
            reconciled = system.runner.reconcile()
            assert reconciled[0]["resolution"] == "not_submitted"
            assert system.journal.get(request_hash).submit_attempts == 0
            (tmp_path / "STOP").unlink()
            resumed = await system.runner.cycle(bars("buy"), seiche_regime="CALM")
            assert resumed["status"] == "blocked"
            assert resumed["reasons"] == ["paper_order_observation_unavailable"]
            assert sdk.orders == []
            assert system.store.status()["intent_count"] == 1

    asyncio.run(run())


def test_stop_does_not_disable_read_only_account_bound_order_reconciliation(
    tmp_path: Path,
) -> None:
    class ReconcilableSDK(SyntheticSDK):
        def get_order_by_client_id(self, client_id: str) -> SimpleNamespace:
            assert client_id == self.orders[0].client_order_id
            return SimpleNamespace(
                id="synthetic-accepted-unfilled-1",
                client_order_id=client_id,
                status="accepted",
                filled_qty="0",
                filled_avg_price=None,
            )

    async def run() -> None:
        sdk = ReconcilableSDK()
        async with scoped_pipeline(tmp_path, sdk) as system:
            result = await system.runner.cycle(bars("buy"), seiche_regime="CALM")
            assert result["status"] == "submitted"
            (tmp_path / "STOP").write_text("Synthetic stop before reconciliation.\n")
            request_hash = trade_safety_request_hash(result["request"])
            reconciled = system.broker.reconcile(request_hash)
            assert (
                reconciled.submission.reconciliation_resolution == "broker_order_found"
            )
            assert reconciled.broker_order.status == "accepted"
            assert sdk.account_reads == 2
            assert len(sdk.orders) == 1
            assert system.store.status()["intent_count"] == 1

    asyncio.run(run())


def test_known_accepted_order_blocks_next_bar_despite_empty_open_orders_list(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        sdk = SyntheticSDK()
        async with scoped_pipeline(tmp_path, sdk) as system:
            previous_hour = [
                replace(bar, at=bar.at - timedelta(hours=1)) for bar in bars("buy")
            ]
            initial = await system.runner.cycle(previous_hour, seiche_regime="CALM")
            assert initial["status"] == "submitted"
            source_calls = len(system.source_calls)
            subsequent = await system.runner.cycle(bars("buy"), seiche_regime="CALM")
            assert subsequent["portfolio"]["open_orders"] == 0
            assert subsequent["order_observations"][0]["status"] == "accepted"
            assert subsequent["order_observations"][0]["terminal"] is False
            assert subsequent["status"] == "blocked"
            assert subsequent["reasons"] == ["pending_paper_order"]
            assert len(system.source_calls) == source_calls
            assert len(sdk.orders) == 1
            assert system.store.status()["intent_count"] == 1
            assert system.store.status()["pending_intent_count"] == 1

    asyncio.run(run())


@pytest.mark.parametrize("status", ["filled", "canceled"])
def test_terminal_order_observation_allows_next_bar_without_releasing_prior_intent(
    tmp_path: Path,
    status: str,
) -> None:
    async def run() -> None:
        sdk = SyntheticSDK()
        async with scoped_pipeline(tmp_path, sdk, observed_status=status) as system:
            previous_hour = [
                replace(bar, at=bar.at - timedelta(hours=1)) for bar in bars("buy")
            ]
            initial = await system.runner.cycle(previous_hour, seiche_regime="CALM")
            first_hash = trade_safety_request_hash(initial["request"])
            assert initial["status"] == "submitted"
            subsequent = await system.runner.cycle(bars("buy"), seiche_regime="CALM")
            assert subsequent["status"] == "submitted", subsequent
            assert subsequent["order_observations"][0]["status"] == status
            assert subsequent["order_observations"][0]["terminal"] is True
            assert len(sdk.orders) == 2
            assert system.store.status()["intent_count"] == 2
            assert system.store.status()["order_observation_count"] == 1
            assert system.store.status()["pending_intent_count"] == 1
            assert system.store.status()["filled_order_count"] == int(
                status == "filled"
            )
            # Observing a terminal outcome never rewrites the consumed receipt
            # or makes the first request eligible for another broker submission.
            first = system.journal.get(first_hash)
            assert first.state == AlpacaPaperSubmissionState.SUBMITTED
            assert first.submit_attempts == 1
            assert first.receipt_id == initial["receipt"]["receipt_id"]

    asyncio.run(run())


def test_unavailable_known_order_lookup_blocks_new_bar_and_keeps_pending_audit(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        sdk = SyntheticSDK()
        async with scoped_pipeline(
            tmp_path, sdk, observation_http_status=404
        ) as system:
            previous_hour = [
                replace(bar, at=bar.at - timedelta(hours=1)) for bar in bars("buy")
            ]
            initial = await system.runner.cycle(previous_hour, seiche_regime="CALM")
            assert initial["status"] == "submitted"
            subsequent = await system.runner.cycle(bars("buy"), seiche_regime="CALM")
            assert subsequent["status"] == "blocked"
            assert subsequent["reasons"] == ["paper_order_observation_unavailable"]
            assert len(sdk.orders) == 1
            assert system.store.status()["intent_count"] == 1
            assert system.store.status()["pending_intent_count"] == 1
            assert system.store.status()["order_observation_count"] == 0
            assert system.store.status()["latest"]["kind"] == "blocked"

    asyncio.run(run())


def corrupt_source(payloads: dict[str, dict[str, Any]], fault: str) -> None:
    funding, corporate = payloads["funding"], payloads["corporate"]
    if fault == "unknown_funding_schema":
        funding["schema"] = "unknown.v2"
    elif fault == "funding_unknown_clock":
        funding["sections"][0]["metrics"][0]["asof"] = None
    elif fault in {"future_funding", "stale_funding"}:
        at = (
            NOW + timedelta(days=1)
            if fault == "future_funding"
            else NOW - timedelta(days=9)
        )
        stamp = at.date().isoformat()
        for section in funding["sections"]:
            for metric in section["metrics"]:
                metric["asof"] = stamp
                if "alignment" in metric:
                    alignment = metric["alignment"]
                    alignment["input_asof"] = {
                        key: stamp for key in alignment["input_asof"]
                    }
                    alignment["latest_common_asof"] = stamp
        for source in funding["source_metadata"]:
            source["asof"] = stamp
    elif fault == "unknown_corporate_schema":
        corporate["schema_version"] = 999
    elif fault == "corporate_unknown_clock":
        corporate["channels"]["cp_market"]["legs"]["spread"]["as_of"] = None
    elif fault in {"future_corporate", "stale_corporate"}:
        at = (
            NOW + timedelta(days=1)
            if fault == "future_corporate"
            else NOW - timedelta(days=9)
        )
        corporate["channels"]["cp_market"]["legs"]["spread"]["as_of"] = (
            at.date().isoformat()
        )
    else:
        raise AssertionError(fault)


@pytest.mark.parametrize(
    "fault",
    [
        "unknown_funding_schema",
        "funding_unknown_clock",
        "future_funding",
        "stale_funding",
        "unknown_corporate_schema",
        "corporate_unknown_clock",
        "future_corporate",
        "stale_corporate",
    ],
)
def test_unknown_future_stale_sources_never_reach_sdk(
    tmp_path: Path, fault: str
) -> None:
    async def run() -> None:
        sdk = SyntheticSDK()
        async with scoped_pipeline(
            tmp_path, sdk, source_change=lambda values: corrupt_source(values, fault)
        ) as system:
            # Simulate a prior valid regime read. The actual assessment must
            # reject a source that became invalid before this exact order.
            result = await system.runner.cycle(bars("buy"), seiche_regime="CALM")
            assert result["status"] == "blocked", result
            assert result["receipt"]["decision"]["outcome"] == "unavailable"
            product = "liquilens" if "corporate" in fault else "seiche"
            assert result["receipt"]["evidence"][product]["state"] == "unavailable"
            assert system.store.status()["intent_count"] == 0
            assert sdk.orders == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "fault",
    [
        "scenario_hash",
        "scenario_size",
        "native_future",
        "native_stale",
        "native_unknown_clock",
    ],
)
def test_native_exit_scenario_mismatch_blocks_original_buy(
    tmp_path: Path, fault: str
) -> None:
    async def run() -> None:
        sdk = SyntheticSDK()
        async with scoped_pipeline(tmp_path, sdk, native_fault=fault) as system:
            result = await system.runner.cycle(bars("buy"), seiche_regime="CALM")
            assert result["status"] == "blocked"
            assert result["receipt"]["evidence"]["undertow"]["state"] == "unavailable"
            assert system.store.status()["intent_count"] == 0
            assert sdk.orders == []

    asyncio.run(run())


def test_native_rights_refusal_blocks_fresh_cash_eligible_paper_buy(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        sdk = SyntheticSDK()
        async with scoped_pipeline(
            tmp_path, sdk, native_fault="rights_manifest_not_approved"
        ) as system:
            result = await system.runner.cycle(bars("buy"), seiche_regime="CALM")
            assert result["decision"]["action"] == "buy"
            assert result["decision"]["notional_usd"] == 1000
            assert result["portfolio"]["cash_usd"] == 100_000
            assert result["portfolio"]["btc_notional_usd"] == 0
            assert result["status"] == "blocked"
            receipt = result["receipt"]
            assert receipt["request"]["order"]["side"] == "buy"
            assert receipt["decision"]["outcome"] == "unavailable"
            assert receipt["evidence"]["seiche"]["state"] == "context_only"
            assert receipt["evidence"]["liquilens"]["state"] == "context_only"
            refused = receipt["evidence"]["undertow"]
            assert refused["state"] == "unavailable"
            raw = system.source_bodies[UNDERTOW_URL]
            assert refused["source_sha256"] == hashlib.sha256(raw).hexdigest()
            native = json.loads(raw)["result"]["structuredContent"]
            assert native["status"] == "unavailable"
            assert native["reason"] == "rights_manifest_not_approved"
            assert native == NATIVE["_sealed"](native, "context_sha256")
            assert native["request_hash"] == trade_safety_request_hash(
                build_liquidation_scenario(receipt["request"])
            )
            assert system.store.status()["intent_count"] == 0
            assert (
                system.journal.get(trade_safety_request_hash(receipt["request"]))
                is None
            )
            assert sdk.orders == []
            assert sdk.account_reads == 0

    asyncio.run(run())


def test_new_funding_stress_cannot_use_earlier_calm_strategy_read(
    tmp_path: Path,
) -> None:
    def stressed(payloads: dict[str, dict[str, Any]]) -> None:
        for metric in payloads["funding"]["sections"][0]["metrics"]:
            if metric["id"] == "policy.sofr":
                metric["value"] = 3.96
            elif metric["id"] == "policy.sofr_minus_iorb":
                metric["value"] = 31.0

    async def run() -> None:
        sdk = SyntheticSDK()
        async with scoped_pipeline(tmp_path, sdk, source_change=stressed) as system:
            result = await system.runner.cycle(bars("buy"), seiche_regime="CALM")
            assert result["status"] == "blocked"
            assert (
                result["receipt"]["evidence"]["seiche"]["facts"]["regime"] == "STRESS"
            )
            assert result["receipt"]["decision"]["outcome"] == "hold"
            assert system.store.status()["intent_count"] == 0
            assert sdk.orders == []

    asyncio.run(run())


def test_exact_corporate_threshold_keeps_other_receipt_and_order_gates(
    tmp_path: Path,
) -> None:
    def threshold(payloads: dict[str, dict[str, Any]]) -> None:
        payloads["corporate"]["channels"]["cp_market"]["legs"]["spread"][
            "spread_bp"
        ] = 50.0

    async def run() -> None:
        sdk = SyntheticSDK()
        async with scoped_pipeline(tmp_path, sdk, source_change=threshold) as system:
            result = await system.runner.cycle(bars("buy"), seiche_regime="CALM")
            assert result["status"] == "submitted", result
            assert (
                result["receipt"]["evidence"]["liquilens"]["facts"]["cp_spread_bp"]
                == 50.0
            )
            assert len(sdk.orders) == 1
            assert result["broker_order"]["filled_qty"] == "0"

    asyncio.run(run())


def test_wrong_hmac_denied_by_actual_adapter_after_valid_scoped_assessment(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        sdk = SyntheticSDK()
        async with scoped_pipeline(
            tmp_path, sdk, verifier_key=b"other-paper-key"
        ) as system:
            with pytest.raises(TradeSafetyOrderBlocked):
                await system.runner.cycle(bars("buy"), seiche_regime="CALM")
            row = system.store.db.execute(
                "SELECT record FROM events WHERE kind='assessment'"
            ).fetchone()
            receipt = json.loads(row[0])["receipt"]
            assert receipt["decision"]["outcome"] == "pass"
            assert verify_trade_safety_receipt(
                receipt, evaluated_at=NOW, hmac_key=KEY
            ).authenticated
            assert sdk.orders == []
            assert (
                system.journal.get(trade_safety_request_hash(receipt["request"]))
                is None
            )

    asyncio.run(run())


def test_authenticated_changed_scenario_rejected_before_intent_and_sdk(
    tmp_path: Path,
) -> None:
    def corrupt(receipt: dict[str, Any]) -> None:
        receipt["evidence"]["undertow"]["facts"]["operator_liquidation_scenario"][
            "scenario_request_hash"
        ] = "f" * 64

    async def run() -> None:
        sdk = SyntheticSDK()
        async with scoped_pipeline(tmp_path, sdk, receipt_change=corrupt) as system:
            with pytest.raises(TradeSafetyError):
                await system.runner.cycle(bars("buy"), seiche_regime="CALM")
            assert system.store.status()["intent_count"] == 0
            assert sdk.orders == []

    asyncio.run(run())


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_corporate_pressure_stops_both_sides_without_liquidation(
    tmp_path: Path, side: str
) -> None:
    def pressure(payloads: dict[str, dict[str, Any]]) -> None:
        payloads["corporate"]["channels"]["cp_market"]["legs"]["spread"][
            "spread_bp"
        ] = 51

    async def run() -> None:
        sdk = SyntheticSDK()
        async with scoped_pipeline(
            tmp_path, sdk, side=side, source_change=pressure
        ) as system:
            result = await system.runner.cycle(bars(side), seiche_regime="CALM")
            assert result["receipt"]["decision"]["outcome"] == "pass"
            assert result["status"] == "hold"
            assert result["reasons"] == ["scoped_corporate_funding_pressure_above_50bp"]
            assert system.store.status()["intent_count"] == 0
            assert sdk.orders == []

    asyncio.run(run())
