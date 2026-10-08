from __future__ import annotations

import asyncio
import hashlib
import json
import socket
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import uvicorn
from liquilens_alpaca_paper import (
    AlpacaPaperAgentTools,
    SQLiteAlpacaPaperSubmissionJournal,
)
from test_paper_pipeline import ACCOUNT, KEY, NATIVE, NOW, SyntheticSDK
from test_scoped_pipeline import source_payloads
from trade_safety_gateway.app import (
    LIQUILENS_BASE_URL,
    SEICHE_URL,
    UNDERTOW_URL,
    HttpxUpstreamTransport,
)

from liquilens_trading_copilot.agent_cli import initialize
from liquilens_trading_copilot.agent_client import AgentHostClient
from liquilens_trading_copilot.agent_host import (
    BearerAuthority,
    create_agent_app,
    private_json,
)
from liquilens_trading_copilot.agent_service import (
    AgentServiceError,
    PaperAgentService,
    agent_binding,
)
from liquilens_trading_copilot.config import (
    SCOPED_PROFILE,
    CopilotConfig,
    PaperCredentials,
    default_policy,
    scoped_policy,
)
from liquilens_trading_copilot.evidence import (
    LiquiLensStrategyContext,
    OperatorEvidenceService,
)
from liquilens_trading_copilot.market import PaperAccountReader
from liquilens_trading_copilot.scoped import (
    CORPORATE_URL,
    FUNDING_URL,
    ScopedPaperEvidenceService,
    ScopedUpstreamTransport,
)
from liquilens_trading_copilot.state import CycleStore, StateError, operator_lock

TOKEN = "synthetic-agent-execution-token-0123456789abcdef"
READ_TOKEN = "synthetic-agent-read-token-0123456789abcdef"


def auth_config(agent_id: str) -> dict[str, Any]:
    return {
        "schema": "liquilens.agent-host-auth.v1",
        "agent_id": agent_id,
        "tokens": [
            {"sha256": hashlib.sha256(token.encode()).hexdigest(), "scopes": scopes}
            for token, scopes in (
                (TOKEN, ["read", "assess", "submit", "reconcile"]),
                (READ_TOKEN, ["read", "assess"]),
            )
        ],
    }


class HostSDK(SyntheticSDK):
    def __init__(self) -> None:
        super().__init__()
        self.timeout = False
        self.wait = False
        self.entered, self.release = Event(), Event()
        self.stop: Path | None = None

    def get_account(self) -> dict[str, str]:
        if self.stop is not None:
            self.stop.touch()
        return super().get_account()

    def submit_order(self, order_data: Any) -> Any:
        self.entered.set()
        if self.wait:
            assert self.release.wait(timeout=5)
        response = super().submit_order(order_data)
        if self.timeout:
            raise TimeoutError("SECRET provider body")
        return response

    def get_order_by_client_id(self, client_id: str) -> Any:
        return SimpleNamespace(
            id="synthetic-accepted-unfilled-1", client_order_id=client_id
        )


@asynccontextmanager
async def rig(
    state_dir: Path,
    *,
    enabled: bool = True,
    stale: bool = False,
    sdk: HostSDK | None = None,
    config_change: dict | None = None,
    max_requests: int = 60,
    scoped: bool = False,
):
    state_dir = state_dir.resolve()
    policy = default_policy()
    policy["required_products"].append("liquilens")
    config = CopilotConfig(
        account_id=ACCOUNT,
        enabled=enabled,
        state_dir=str(state_dir),
        policy=policy,
        liquilens_institution_slug="synthetic-bank",
        liquilens_required=True,
    )
    config = replace(config, **(config_change or {}))
    if scoped:
        config = replace(
            config,
            evidence_profile=SCOPED_PROFILE,
            policy=scoped_policy(),
            liquilens_institution_slug=None,
            liquilens_required=False,
        )
    binding = agent_binding(config)
    sdk = sdk or HostSDK()
    source_calls, account_calls = [], []
    clock = [NOW]
    portfolio = {
        "cash": "98500",
        "equity": "100000",
        "last_equity": "100000",
        "btc": "1500",
        "orders": [],
    }
    outcome = {
        "id": "synthetic-accepted-unfilled-1",
        "status": "accepted",
        "filled_qty": "0",
        "filled_avg_price": None,
        "side": "sell",
    }
    scoped_sources = source_payloads() if scoped else {}

    def source_response(request: httpx.Request) -> httpx.Response:
        source_calls.append(request)
        if scoped and str(request.url) in {FUNDING_URL, CORPORATE_URL}:
            key = "funding" if str(request.url) == FUNDING_URL else "corporate"
            body = json.dumps(scoped_sources[key]).encode()
        elif str(request.url) == SEICHE_URL:
            body = NATIVE["_seiche_bytes"](
                oldest_headline_asof="2026-08-20" if stale else "2026-08-26"
            )
        elif str(request.url) == UNDERTOW_URL:
            message = json.loads(request.content)
            body = NATIVE["_undertow_bytes"](
                request_hash=message["params"]["arguments"]["request_hash"]
            )
        else:
            assert str(request.url) == LIQUILENS_BASE_URL + "synthetic-bank"
            body = json.dumps(
                {
                    "slug": "synthetic-bank",
                    "historical_evidence": {
                        "status": "research_only",
                        "validated_backtest_eligible": False,
                        "real_money_eligible": False,
                    },
                    "trajectory": [{"period_end": "2026-09-02"}],
                }
            ).encode()
        return httpx.Response(
            200, content=body, headers={"Content-Type": "application/json"}
        )

    def account_response(request: httpx.Request) -> httpx.Response:
        account_calls.append(request)
        assert request.url.host == "paper-api.alpaca.markets"
        assert request.method == "GET"
        if request.url.path == "/v2/orders:by_client_order_id":
            return httpx.Response(
                200,
                json={
                    "client_order_id": request.url.params["client_order_id"],
                    "symbol": "BTC/USD",
                    **outcome,
                },
            )
        return httpx.Response(
            200,
            json={
                "/v2/account": {
                    "id": ACCOUNT,
                    "status": "ACTIVE",
                    "currency": "USD",
                    "cash": portfolio["cash"],
                    "equity": portfolio["equity"],
                    "last_equity": portfolio["last_equity"],
                    "trading_blocked": False,
                    "account_blocked": False,
                    "trade_suspended_by_user": False,
                },
                "/v2/positions": [
                    {"symbol": "BTCUSD", "market_value": portfolio["btc"]}
                ],
                "/v2/orders": portfolio["orders"],
            }[request.url.path],
        )

    transport_type = ScopedUpstreamTransport if scoped else HttpxUpstreamTransport
    transport = transport_type(transport=httpx.MockTransport(source_response))
    with operator_lock(state_dir):
        store = CycleStore(state_dir)
        journal = SQLiteAlpacaPaperSubmissionJournal(
            state_dir / "submissions.sqlite3", clock=lambda: clock[0]
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(account_response)
        ) as account_client:
            evidence_type = (
                ScopedPaperEvidenceService if scoped else OperatorEvidenceService
            )
            evidence = evidence_type(
                transport,
                policy=config.policy,
                binding=binding,
                hmac_key=KEY,
                clock=lambda: clock[0],
                **(
                    {}
                    if scoped
                    else {
                        "liquilens_context": LiquiLensStrategyContext(
                            "synthetic-bank", required=True
                        )
                    }
                ),
            )
            service = PaperAgentService(
                config,
                store,
                evidence=evidence,
                account_reader=PaperAccountReader(
                    account_client,
                    PaperCredentials("synthetic-key", "synthetic-secret", KEY),
                    ACCOUNT,
                ),
                journal=journal,
                hmac_key=KEY,
                clock=lambda: clock[0],
                tools_factory=lambda **options: AlpacaPaperAgentTools(
                    binding=binding,
                    submission_journal=journal,
                    hmac_key=KEY,
                    clock=lambda: clock[0],
                    _client_factory=lambda **kwargs: sdk,
                    **options,
                ),
            )

            @asynccontextmanager
            async def factory():
                try:
                    yield service
                finally:
                    await service.close()

            app = create_agent_app(
                factory,
                authority=BearerAuthority(
                    auth_config(config.agent_id), agent_id=config.agent_id
                ),
                requests_per_minute=max_requests,
            )
            try:
                async with (
                    app.router.lifespan_context(app),
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://localhost",
                        headers={"Authorization": "Bearer " + TOKEN},
                    ) as client,
                ):
                    yield SimpleNamespace(
                        client=client,
                        service=service,
                        sdk=sdk,
                        app=app,
                        store=store,
                        source_calls=source_calls,
                        account_calls=account_calls,
                        portfolio=portfolio,
                        outcome=outcome,
                        clock=clock,
                        journal=journal,
                    )
            finally:
                await service.close()
                await transport.aclose()
                journal.close()
                store.close()


async def assessed(env: Any, intent: str = "agent-intent-1") -> dict:
    result = await env.client.post(
        "/v1/assessments",
        json={"intent_id": intent, "side": "sell", "notional_usd": 1000},
    )
    assert result.status_code == 200, result.text
    return result.json()


def test_full_authenticated_three_product_lifecycle_and_restart(tmp_path: Path) -> None:
    async def run():
        sdk = HostSDK()
        async with rig(tmp_path, sdk=sdk) as env:
            assessment = await assessed(env)
            assert set(assessment["evidence"]) == {"seiche", "undertow", "liquilens"}
            assert assessment["source_policy_decision"]["outcome"] == "pass"
            assert assessment["submission_authorized"] is False
            assert "integrity" not in assessment
            args = {"assessment_id": assessment["assessment_id"]}
            submitted = await env.client.post("/v1/orders/submit", json=args)
            assert submitted.json()["status"] == "submitted", submitted.text
            assert submitted.json()["fill_status"] == "not_observed"
            duplicate = await env.client.post("/v1/orders/submit", json=args)
            assert duplicate.json()["duplicate_intent"] is True
            assert len(sdk.orders) == 1
            observed = await env.client.post("/v1/orders/reconcile", json=args)
            assert observed.json()["order_observation"]["status"] == "accepted"
            assert observed.json()["order_observation"]["terminal"] is False
            env.outcome.update(
                status="filled", filled_qty="0.025", filled_avg_price="40000"
            )
            filled = await env.client.post("/v1/orders/reconcile", json=args)
            assert filled.json()["order_observation"]["status"] == "filled"
            assert not env.store.pending_intents()
        async with rig(tmp_path, sdk=sdk) as restarted:
            same = await assessed(restarted)
            assert same["assessment_id"] == assessment["assessment_id"]
            assert not restarted.source_calls
            repeated = await restarted.client.post("/v1/orders/submit", json=args)
            assert repeated.json()["duplicate_intent"] is True
            assert repeated.json()["fill_status"] == "filled"
            assert repeated.json()["broker_observation_currentness"] == "last_observed"
            assert not restarted.account_calls
            assert len(sdk.orders) == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "headers,status",
    [
        ({"Authorization": "wrong"}, 401),
        ({"Authorization": "Bearer " + "x" * 44}, 401),
        ({"Origin": "https://untrusted.example"}, 403),
        ({"Host": "untrusted.example"}, 400),
    ],
)
def test_transport_rejects_unauthorized_origins_and_hosts(
    tmp_path: Path, headers: dict, status: int
) -> None:
    async def run():
        async with rig(tmp_path) as env:
            response = await env.client.post(
                "/v1/assessments", headers=headers, json={}
            )
            assert response.status_code == status
            assert not env.source_calls and not env.account_calls and not env.sdk.orders

    asyncio.run(run())


def test_read_token_cannot_submit_and_body_cannot_supply_credentials(
    tmp_path: Path,
) -> None:
    async def run():
        async with rig(tmp_path) as env:
            item = await assessed(env)
            response = await env.client.post(
                "/v1/orders/submit",
                headers={"Authorization": "Bearer " + READ_TOKEN},
                json={"assessment_id": item["assessment_id"]},
            )
            assert response.status_code == 403
            response = await env.client.post(
                "/v1/orders/submit",
                json={
                    "assessment_id": item["assessment_id"],
                    "enabled": True,
                    "api_key": "SECRET",
                },
            )
            assert response.status_code == 422
            assert "SECRET" not in response.text
            assert not env.account_calls and not env.sdk.orders

    asyncio.run(run())


@pytest.mark.parametrize(
    "body,expected",
    [
        (b'{"intent_id":"one","intent_id":"two"}', 400),
        (b'{"intent_id":"one","side":"sell","notional_usd":NaN}', 400),
        (b'{"intent_id":"one","side":"sell","notional_usd":1e999}', 422),
        (b'{"mode":"live"}', 422),
        (b"x" * 4097, 413),
    ],
)
def test_malformed_or_unbounded_requests_have_no_side_effects(
    tmp_path: Path, body: bytes, expected: int
) -> None:
    async def run():
        async with rig(tmp_path) as env:
            response = await env.client.post(
                "/v1/assessments",
                content=body,
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == expected, response.text
            assert not env.source_calls and not env.sdk.orders

    asyncio.run(run())


@pytest.mark.parametrize("disabled,stale", [(True, False), (False, True)])
def test_disabled_execution_or_stale_source_cannot_reach_broker(
    tmp_path: Path, disabled: bool, stale: bool
) -> None:
    async def run():
        async with rig(tmp_path, enabled=not disabled, stale=stale) as env:
            item = await assessed(env)
            result = await env.client.post(
                "/v1/orders/submit", json={"assessment_id": item["assessment_id"]}
            )
            assert result.status_code == 409, result.text
            assert not env.account_calls and not env.sdk.orders

    asyncio.run(run())


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"orders": [{"id": "pending"}]}, "open_orders_pending"),
        ({"btc": "500"}, "short_position_not_permitted"),
        (
            {"equity": "95000", "last_equity": "100000", "cash": "93500"},
            "daily_loss_stop",
        ),
    ],
)
def test_account_conditions_are_rechecked_at_submission(
    tmp_path: Path, change: dict, reason: str
) -> None:
    async def run():
        async with rig(tmp_path) as env:
            item = await assessed(env)
            env.portfolio.update(change)
            response = await env.client.post(
                "/v1/orders/submit", json={"assessment_id": item["assessment_id"]}
            )
            assert response.json()["error"] == reason
            assert not env.sdk.orders

    asyncio.run(run())


def test_stable_intent_rejects_changed_proposals_without_refetch(
    tmp_path: Path,
) -> None:
    async def run():
        async with rig(tmp_path) as env:
            await assessed(env)
            count = len(env.source_calls)
            response = await env.client.post(
                "/v1/assessments",
                json={
                    "intent_id": "agent-intent-1",
                    "side": "buy",
                    "notional_usd": 1000,
                },
            )
            assert response.status_code == 409
            assert len(env.source_calls) == count

    asyncio.run(run())


def test_timeout_is_reconciled_without_second_submission(tmp_path: Path) -> None:
    async def run():
        async with rig(tmp_path) as env:
            env.sdk.timeout = True
            item = await assessed(env)
            args = {"assessment_id": item["assessment_id"]}
            result = await env.client.post("/v1/orders/submit", json=args)
            assert result.json()["status"] == "uncertain"
            assert "SECRET" not in result.text
            repeated = await env.client.post("/v1/orders/submit", json=args)
            assert repeated.json()["duplicate_intent"] is True
            another = await assessed(env, "agent-intent-2")
            blocked = await env.client.post(
                "/v1/orders/submit", json={"assessment_id": another["assessment_id"]}
            )
            assert blocked.json()["error"] == "unresolved_or_pending_order"
            response = await env.client.post("/v1/orders/reconcile", json=args)
            assert response.json()["order_observation"]["status"] == "accepted"
            assert len(env.sdk.orders) == 1

    asyncio.run(run())


def test_http_waiter_cancellation_does_not_release_an_inflight_submission(
    tmp_path: Path,
) -> None:
    async def run():
        async with rig(tmp_path) as env:
            env.sdk.wait = True
            item = await assessed(env)
            args = {"assessment_id": item["assessment_id"]}
            pending = asyncio.create_task(env.service.run("submit", args))
            try:
                assert await asyncio.to_thread(env.sdk.entered.wait, 3)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                with pytest.raises(AgentServiceError, match="operator_lane_busy"):
                    await env.service.run("submit", args)
            finally:
                env.sdk.release.set()
                await asyncio.gather(*tuple(env.service._tasks), return_exceptions=True)
            replay = await env.service.run("submit", args)
            assert replay["duplicate_intent"] is True
            assert len(env.sdk.orders) == 1

    asyncio.run(run())


def test_late_stop_retains_intent_and_never_submits(tmp_path: Path) -> None:
    async def run():
        async with rig(tmp_path) as env:
            item = await assessed(env)
            env.sdk.stop = tmp_path.resolve() / "STOP"
            result = await env.client.post(
                "/v1/orders/submit", json={"assessment_id": item["assessment_id"]}
            )
            assert result.json()["reason_code"] == "operator_execution_disabled"
            assert not env.sdk.orders
            assert env.store.pending_intents()

    asyncio.run(run())


def test_expiry_does_not_refresh_on_identical_assessment_request(
    tmp_path: Path,
) -> None:
    async def run():
        async with rig(tmp_path) as env:
            first = await assessed(env)
            env.clock[0] += timedelta(minutes=10)
            assert (await assessed(env))["expires_at"] == first["expires_at"]
            response = await env.client.post(
                "/v1/orders/submit", json={"assessment_id": first["assessment_id"]}
            )
            assert response.status_code == 503
            assert not env.sdk.orders

    asyncio.run(run())


def test_initialization_is_private_disabled_and_non_overwriting(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    result = initialize(root)
    config = private_json(root / "config.json")
    assert result["status"] == "initialized_disabled" and config["enabled"] is False
    authority = BearerAuthority(
        private_json(root / "agent-auth.json"), agent_id=config["agent_id"]
    )
    token = (root / "agent-read.token").read_text().strip()
    assert authority.authenticate(["Bearer " + token]) == {"read", "assess"}
    assert token not in json.dumps(result)
    for name in result["files"]:
        assert (root / name).stat().st_mode & 0o077 == 0
    with pytest.raises(ValueError, match="already_exists"):
        initialize(root)
    (root / "agent-auth.json").chmod(0o644)
    with pytest.raises(ValueError, match="private_agent_file_invalid"):
        private_json(root / "agent-auth.json")


def test_single_process_account_lock_and_http_rate_limit(tmp_path: Path) -> None:
    async def run():
        async with rig(tmp_path, max_requests=1) as env:
            with (
                pytest.raises(StateError, match="another_copilot_process"),
                operator_lock(tmp_path.resolve()),
            ):
                pass
            first = await env.client.get("/v1/capabilities")
            second = await env.client.get("/v1/capabilities")
            assert first.status_code == 200 and second.status_code == 429
            assert not env.source_calls and not env.sdk.orders

    asyncio.run(run())


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_default_profile_connects_three_sources_to_external_proposal(
    tmp_path: Path, side: str
) -> None:
    async def run():
        async with rig(tmp_path, scoped=True) as env:
            capabilities = (await env.client.get("/v1/capabilities")).json()
            assert capabilities["supported_notional_usd"] == [1000]
            assert capabilities["live_execution_supported"] is False
            response = await env.client.post(
                "/v1/assessments",
                json={"intent_id": "scoped-1", "side": side, "notional_usd": 1000},
            )
            assert response.status_code == 200, response.text
            item = response.json()
            assert item["source_policy_decision"]["outcome"] == "pass"
            assert set(item["evidence"]) == {"seiche", "undertow", "liquilens"}
            assert {str(call.url) for call in env.source_calls} == {
                FUNDING_URL,
                CORPORATE_URL,
                UNDERTOW_URL,
            }
            undertow = next(
                call for call in env.source_calls if str(call.url) == UNDERTOW_URL
            )
            assert json.loads(undertow.content)["params"]["arguments"]["side"] == "sell"
            for call in env.source_calls:
                assert "Authorization" not in call.headers
                assert "APCA-API-KEY-ID" not in call.headers
            args = {"assessment_id": item["assessment_id"]}
            result = await env.client.post("/v1/orders/submit", json=args)
            assert result.json()["status"] == "submitted", result.text
            env.outcome["side"] = side
            result = await env.client.post("/v1/orders/reconcile", json=args)
            assert result.json()["order_observation"]["side"] == side
            assert len(env.sdk.orders) == 1

    asyncio.run(run())


def test_default_profile_rejects_unsupported_size_before_source_io(
    tmp_path: Path,
) -> None:
    async def run():
        async with rig(tmp_path, scoped=True) as env:
            result = await env.client.post(
                "/v1/assessments",
                json={"intent_id": "size-1", "side": "buy", "notional_usd": 500},
            )
            assert result.status_code == 422
            assert not env.source_calls and not env.sdk.orders

    asyncio.run(run())


@pytest.mark.parametrize("change", [{"side": "buy"}, {"id": "different-order"}])
def test_first_broker_observation_must_match_submitted_order(
    tmp_path: Path, change: dict
) -> None:
    async def run():
        async with rig(tmp_path) as env:
            item = await assessed(env)
            args = {"assessment_id": item["assessment_id"]}
            await env.client.post("/v1/orders/submit", json=args)
            env.outcome.update(
                change, status="filled", filled_qty="0.025", filled_avg_price="40000"
            )
            result = await env.client.post("/v1/orders/reconcile", json=args)
            assert result.status_code == 503
            assert result.json()["error"] == "broker_order_binding_mismatch"
            assert env.store.pending_intents()
            assert len(env.sdk.orders) == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "portfolio,reason",
    [
        ({"cash": "500"}, "insufficient_cash"),
        ({"btc": "99500", "cash": "500"}, "insufficient_cash"),
        ({"btc": "20000", "cash": "80000"}, "portfolio_exposure_limit"),
    ],
)
def test_external_buy_cannot_exceed_account_limits(
    tmp_path: Path, portfolio: dict, reason: str
) -> None:
    async def run():
        async with rig(tmp_path, scoped=True) as env:
            item = (
                await env.client.post(
                    "/v1/assessments",
                    json={
                        "intent_id": "buy-limits-1",
                        "side": "buy",
                        "notional_usd": 1000,
                    },
                )
            ).json()
            env.portfolio.update(portfolio)
            result = await env.client.post(
                "/v1/orders/submit", json={"assessment_id": item["assessment_id"]}
            )
            assert result.json()["error"] == reason
            assert not env.sdk.orders

    asyncio.run(run())


def test_restarted_host_cannot_change_execution_identity(tmp_path: Path) -> None:
    async def run():
        async with rig(tmp_path) as env:
            await assessed(env)
        with pytest.raises(AgentServiceError, match="state_execution_binding_changed"):
            async with rig(tmp_path, config_change={"agent_id": "different-agent"}):
                pass

    asyncio.run(run())


@pytest.mark.parametrize("use_mcp", [False, True])
def test_reference_client_through_real_loopback_http(
    tmp_path: Path, use_mcp: bool
) -> None:
    async def run():
        async with rig(tmp_path, scoped=True) as env:
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            server = uvicorn.Server(
                uvicorn.Config(
                    env.app,
                    lifespan="off",
                    ws="none",
                    access_log=False,
                    log_level="critical",
                    proxy_headers=False,
                )
            )
            serving = asyncio.create_task(server.serve(sockets=[listener]))
            client = AgentHostClient(f"http://127.0.0.1:{port}", token=TOKEN)
            from liquilens_trading_copilot.agent_mcp import PaperAgentMCP

            bridge = PaperAgentMCP(client, allow_submit=True)
            bridge.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "http-integration", "version": "1"},
                    },
                }
            )
            names = {
                "capabilities": "paper_capabilities",
                "assess": "assess_paper_order",
                "submit": "submit_paper_order",
                "reconcile": "reconcile_paper_order",
            }

            def call(operation, payload=None):
                if not use_mcp:
                    return client.call(operation, payload)
                result = bridge.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": names[operation],
                            "arguments": payload or {},
                        },
                    }
                )
                assert not result["result"]["isError"]
                return result["result"]["structuredContent"]

            try:
                async with asyncio.timeout(5):
                    while not server.started:
                        await asyncio.sleep(0.01)
                capabilities = await asyncio.to_thread(call, "capabilities")
                assert capabilities["result"]["live_execution_supported"] is False
                item = await asyncio.to_thread(
                    call,
                    "assess",
                    {
                        "intent_id": "http-client-1",
                        "side": "sell",
                        "notional_usd": 1000,
                    },
                )
                args = {"assessment_id": item["result"]["assessment_id"]}
                submitted = await asyncio.to_thread(call, "submit", args)
                assert submitted["result"]["status"] == "submitted"
                repeated = await asyncio.to_thread(call, "submit", args)
                assert repeated["result"]["duplicate_intent"] is True
                observed = await asyncio.to_thread(call, "reconcile", args)
                assert observed["result"]["order_observation"]["terminal"] is False
                assert len(env.sdk.orders) == 1
            finally:
                client.close()
                server.should_exit = True
                await asyncio.wait_for(serving, 10)
                listener.close()

    asyncio.run(run())
