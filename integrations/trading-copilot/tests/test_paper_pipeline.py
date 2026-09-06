"""Offline integration proof only: synthetic accepted orders, never paper fills.

The strategy, account parser, native producer-contract validation, HMAC issuer,
runner, broker adapter and SQLite journals are real. HTTP uses MockTransport;
only the Alpaca SDK's external client is replaced. No credential or network
access is needed, and no result in this file is evidence of actual trading.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import runpy
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest
from liquilens_alpaca_paper import (
    AlpacaPaperAccountMismatch,
    AlpacaPaperConfigurationError,
    AlpacaPaperSubmissionState,
    SQLiteAlpacaPaperSubmissionJournal,
    client_order_id_for_request_hash,
)
from liquilens_evidence import TradeSafetyOrderBlocked, trade_safety_request_hash
from liquilens_evidence.trade_safety import verify_trade_safety_receipt
from trade_safety_gateway.app import (
    LIQUILENS_BASE_URL,
    SEICHE_URL,
    UNDERTOW_URL,
    HttpxUpstreamTransport,
)

from liquilens_trading_copilot.broker import OperatorPaperTradeSafetyGateway
from liquilens_trading_copilot.config import (
    CopilotConfig,
    PaperCredentials,
    default_policy,
)
from liquilens_trading_copilot.evidence import (
    LiquiLensStrategyContext,
    OperatorEvidenceService,
)
from liquilens_trading_copilot.market import PAPER_ORIGIN, PaperAccountReader
from liquilens_trading_copilot.runner import CopilotRunner
from liquilens_trading_copilot.state import CycleStore
from liquilens_trading_copilot.strategy import MarketBar

ROOT = Path(__file__).resolve().parents[3]
NATIVE = runpy.run_path(
    str(ROOT / "integrations/trade-safety-gateway/tests/test_gateway.py")
)
NOW = NATIVE["NOW"]
KEY = b"synthetic-pipeline-operator-key-no-broker-credentials"
ACCOUNT = "synthetic-paper-account"


def negative_bars() -> list[MarketBar]:
    return [
        MarketBar(
            at=NOW - timedelta(hours=32 - index),
            close=30_000 * math.exp(-0.01 * index + 0.001 * math.sin(index)),
        )
        for index in range(33)
    ]


class SyntheticSDK:
    """No I/O and no fill simulation; implements only the SDK call boundary."""

    _sandbox = True

    def __init__(self, *, account: str = ACCOUNT, origin: str = PAPER_ORIGIN):
        self.account = account
        self._base_url = origin
        self.orders: list[Any] = []
        self.account_reads = 0

    def get_account(self) -> dict[str, str]:
        self.account_reads += 1
        return {"id": self.account}

    def submit_order(self, order_data: Any) -> SimpleNamespace:
        self.orders.append(order_data)
        return SimpleNamespace(
            id="synthetic-accepted-unfilled-1",
            client_order_id=order_data.client_order_id,
            status="accepted",
            filled_qty="0",
            filled_avg_price=None,
        )

    def get_order_by_client_id(self, client_id: str) -> Any:
        raise AssertionError("a completed synthetic submission needs no recovery")


@asynccontextmanager
async def pipeline(
    state_dir: Path,
    sdk: SyntheticSDK,
    *,
    stale_seiche: bool = False,
    verifier_key: bytes = KEY,
    observed_status: str = "accepted",
) -> AsyncIterator[SimpleNamespace]:
    policy = default_policy()
    policy["required_products"].append("liquilens")
    config = CopilotConfig(
        account_id=ACCOUNT,
        enabled=True,
        state_dir=str(state_dir.resolve()),
        policy=policy,
        liquilens_institution_slug="synthetic-bank",
        liquilens_required=True,
    )
    credentials = PaperCredentials("synthetic-api-key", "synthetic-secret", KEY)
    source_calls: list[httpx.Request] = []
    account_calls: list[httpx.Request] = []

    def source_response(request: httpx.Request) -> httpx.Response:
        source_calls.append(request)
        if str(request.url) == SEICHE_URL:
            assert request.method == "GET"
            body = NATIVE["_seiche_bytes"](
                oldest_headline_asof="2026-08-20" if stale_seiche else "2026-08-26"
            )
        elif str(request.url) == UNDERTOW_URL:
            assert request.method == "POST"
            message = json.loads(request.content)
            body = NATIVE["_undertow_bytes"](
                request_hash=message["params"]["arguments"]["request_hash"]
            )
        else:
            assert str(request.url) == LIQUILENS_BASE_URL + "synthetic-bank"
            assert request.method == "GET"
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
        assert request.url.scheme == "https"
        assert request.method == "GET"
        assert request.headers["APCA-API-KEY-ID"] == credentials.api_key
        assert request.headers["APCA-API-SECRET-KEY"] == credentials.secret_key
        if request.url.path == "/v2/orders:by_client_order_id":
            assert len(sdk.orders) == 1
            order = sdk.orders[0]
            assert request.url.params["client_order_id"] == order.client_order_id
            return httpx.Response(
                200,
                json={
                    "id": "synthetic-accepted-unfilled-1",
                    "client_order_id": order.client_order_id,
                    "symbol": "BTC/USD",
                    "side": "sell",
                    "status": observed_status,
                    "filled_qty": "0",
                    "filled_avg_price": None,
                },
            )
        responses = {
            "/v2/account": {
                "id": ACCOUNT,
                "status": "ACTIVE",
                "currency": "USD",
                "cash": "98500",
                "equity": "100000",
                "last_equity": "100000",
                "trading_blocked": False,
                "account_blocked": False,
                "trade_suspended_by_user": False,
            },
            "/v2/positions": [{"symbol": "BTCUSD", "market_value": "1500"}],
            "/v2/orders": [],
        }
        assert request.url.path in responses
        return httpx.Response(200, json=responses[request.url.path])

    def client_factory(**kwargs: Any) -> SyntheticSDK:
        assert kwargs == {
            "api_key": credentials.api_key,
            "secret_key": credentials.secret_key,
            "oauth_token": None,
            "paper": True,
            "raw_data": False,
        }
        return sdk

    store = CycleStore(Path(config.state_dir))
    journal = SQLiteAlpacaPaperSubmissionJournal(
        Path(config.state_dir) / "alpaca-submissions.sqlite3", clock=lambda: NOW
    )
    service = OperatorEvidenceService(
        HttpxUpstreamTransport(transport=httpx.MockTransport(source_response)),
        policy=config.policy,
        binding=config.binding(),
        hmac_key=KEY,
        clock=lambda: NOW,
        liquilens_context=LiquiLensStrategyContext("synthetic-bank", required=True),
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
            _client_factory=client_factory,
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(account_response),
            follow_redirects=False,
            trust_env=False,
        ) as account_client:
            runner = CopilotRunner(
                config,
                store,
                account_reader=PaperAccountReader(account_client, credentials, ACCOUNT),
                evidence_service=service,
                broker=broker,
                clock=lambda: NOW,
            )
            yield SimpleNamespace(
                runner=runner,
                store=store,
                journal=journal,
                source_calls=source_calls,
                account_calls=account_calls,
            )
    finally:
        await service.aclose()
        journal.close()
        store.close()


def test_synthetic_native_to_paper_adapter_and_durable_same_bar_replay(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        sdk = SyntheticSDK()
        async with pipeline(tmp_path, sdk) as system:
            result = await system.runner.cycle(negative_bars(), seiche_regime="CALM")
            assert result["status"] == "submitted", result
            assert len(sdk.orders) == 1
            assert result["decision"]["action"] == "sell"
            request, receipt = result["request"], result["receipt"]
            assert str(UUID(request["request_id"])) == request["request_id"]
            assert request["mode"] == "paper"
            assert request["agent"]["account_id"] == ACCOUNT
            assert request["order"]["notional"] == {"amount": 1000, "currency": "USD"}
            assert receipt["request"] == request
            assert receipt["decision"]["outcome"] == "pass"
            assert verify_trade_safety_receipt(
                receipt, evaluated_at=NOW, hmac_key=KEY
            ).authenticated
            assert set(receipt["evidence"]) == {"seiche", "undertow", "liquilens"}
            assert all(
                section["state"] == "context_only"
                for section in receipt["evidence"].values()
            )
            request_hash = trade_safety_request_hash(request)
            native_request = next(
                json.loads(call.content)["params"]["arguments"]
                for call in system.source_calls
                if str(call.url) == UNDERTOW_URL
            )
            assert native_request["request_hash"] == request_hash
            order = sdk.orders[0]
            assert order.symbol == "BTC/USD"
            assert order.side.value == "sell"
            assert order.type.value == "market"
            assert order.time_in_force.value == "ioc"
            assert order.notional == 1000
            assert order.qty is None
            assert order.client_order_id == client_order_id_for_request_hash(
                request_hash
            )
            record = system.journal.get(request_hash)
            assert record is not None
            assert record.state == AlpacaPaperSubmissionState.SUBMITTED
            assert record.submit_attempts == 1
            assert record.receipt_id == receipt["receipt_id"]
            assert record.broker_order_id == "synthetic-accepted-unfilled-1"
            assert result["broker_order"]["filled_qty"] == "0"
            assert result["broker_order"]["filled_avg_price"] is None
            assert len(system.account_calls) == 6  # fresh before and after evidence
            assert sdk.account_reads == 1  # adapter independently binds credentials
        # A later terminal cancellation clears the pending-order gate but never
        # releases this bar's durable intent, even with a new request UUID.
        async with pipeline(tmp_path, sdk, observed_status="canceled") as restarted:
            replay = await restarted.runner.cycle(negative_bars(), seiche_regime="CALM")
            assert replay["status"] == "blocked"
            assert replay["reasons"] == ["intent_used_or_daily_attempt_limit"]
            assert replay["request"]["request_id"] != request["request_id"]
            assert restarted.store.status()["intent_count"] == 1
            assert restarted.journal.get(request_hash).submit_attempts == 1
            assert len(sdk.orders) == 1

    asyncio.run(run())


def test_stale_native_seiche_cannot_reach_paper_submission(tmp_path: Path) -> None:
    async def run() -> None:
        sdk = SyntheticSDK()
        async with pipeline(tmp_path, sdk, stale_seiche=True) as system:
            result = await system.runner.cycle(negative_bars(), seiche_regime="CALM")
            assert result["status"] == "blocked"
            assert result["receipt"]["decision"]["outcome"] == "unavailable"
            section = result["receipt"]["evidence"]["seiche"]
            assert section["state"] == "unavailable"
            assert (
                section["source_sha256"]
                == hashlib.sha256(
                    NATIVE["_seiche_bytes"](oldest_headline_asof="2026-08-20")
                ).hexdigest()
            )
            assert system.store.status()["intent_count"] == 0
            assert (
                system.journal.get(trade_safety_request_hash(result["request"])) is None
            )
            assert sdk.orders == []
            assert sdk.account_reads == 0

    asyncio.run(run())


@pytest.mark.parametrize("fault", ["adapter_account_mismatch", "hmac_mismatch"])
def test_adapter_independently_rejects_account_and_hmac_faults(
    tmp_path: Path, fault: str
) -> None:
    async def run() -> None:
        sdk = SyntheticSDK(
            account="another-paper-account"
            if fault == "adapter_account_mismatch"
            else ACCOUNT
        )
        key = b"wrong-authenticator-key" if fault == "hmac_mismatch" else KEY
        error = (
            AlpacaPaperAccountMismatch
            if fault == "adapter_account_mismatch"
            else TradeSafetyOrderBlocked
        )
        async with pipeline(tmp_path, sdk, verifier_key=key) as system:
            with pytest.raises(error):
                await system.runner.cycle(negative_bars(), seiche_regime="CALM")
            # The service passed; the independent broker boundary denied it.
            raw = system.store.db.execute(
                "SELECT record FROM events WHERE kind='assessment'"
            ).fetchone()[0]
            assessment = json.loads(raw)
            assert assessment["receipt"]["decision"]["outcome"] == "pass"
            assert verify_trade_safety_receipt(
                assessment["receipt"], evaluated_at=NOW, hmac_key=KEY
            ).authenticated
            assert (
                system.journal.get(trade_safety_request_hash(assessment["request"]))
                is None
            )
            assert sdk.orders == []

    asyncio.run(run())


def test_actual_adapter_refuses_sdk_live_origin_even_with_paper_flag(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        sdk = SyntheticSDK(origin="https://api.alpaca.markets")
        with pytest.raises(
            AlpacaPaperConfigurationError, match="official paper endpoint"
        ):
            async with pipeline(tmp_path, sdk):
                pytest.fail("a live-origin SDK client must not construct a pipeline")
        assert sdk.orders == []
        assert sdk.account_reads == 0

    asyncio.run(run())
