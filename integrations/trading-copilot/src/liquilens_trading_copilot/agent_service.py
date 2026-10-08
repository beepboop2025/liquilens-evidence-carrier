"""An operator-owned proposal lifecycle for external paper agents.

The agent supplies a stable intent ID and a bounded BTC/USD proposal. Identity,
policy, source retrieval, receipts, clocks and credentials stay with the host.
No agent-supplied evidence or receipt is ever signed or accepted for execution.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from liquilens_evidence import (
    TradeSafetyExecutionBinding,
    trade_safety_request_hash,
    verify_trade_safety_receipt,
)

from .config import SCOPED_PROFILE, CopilotConfig
from .runner import _audit_receipt
from .state import CycleStore
from .strategy import PortfolioSnapshot, _valid_config, _valid_portfolio


class AgentServiceError(RuntimeError):
    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code, self.status = code, status


def agent_binding(config: CopilotConfig) -> TradeSafetyExecutionBinding:
    return replace(config.binding(), runtime="liquilens-agent-host/0.1.0")


def _json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode()) > 524288:
        raise AgentServiceError("agent_record_too_large", 413)
    return encoded


def proposal(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "intent_id",
        "side",
        "notional_usd",
    }:
        raise AgentServiceError("invalid_proposal_fields", 422)
    if (
        not isinstance(value["intent_id"], str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}", value["intent_id"]) is None
        or value["side"] not in ("buy", "sell")
        or type(value["notional_usd"]) not in (int, float)
    ):
        raise AgentServiceError("invalid_proposal", 422)
    try:
        amount = float(value["notional_usd"])
    except (ValueError, OverflowError) as error:
        raise AgentServiceError("invalid_proposal_amount", 422) from error
    if not math.isfinite(amount) or not 0 < amount <= 1000:
        raise AgentServiceError("proposal_amount_out_of_scope", 422)
    return {**value, "notional_usd": amount}


def account_guard(
    config: CopilotConfig, candidate: dict[str, Any], snapshot: Any
) -> None:
    """Apply account limits to an external proposal, without choosing a strategy."""
    if not isinstance(snapshot, PortfolioSnapshot) or not _valid_portfolio(snapshot):
        raise AgentServiceError("invalid_portfolio")
    limits = config.strategy
    if not _valid_config(limits):
        raise AgentServiceError("invalid_account_limits")
    if snapshot.open_orders != 0:
        raise AgentServiceError("open_orders_pending")
    starting_equity = snapshot.equity_usd - snapshot.daily_pnl_usd
    if not math.isfinite(starting_equity) or starting_equity <= 0:
        raise AgentServiceError("invalid_daily_pnl_basis")
    if (
        max(0, -snapshot.daily_pnl_usd / starting_equity)
        >= limits.max_daily_loss_fraction
    ):
        raise AgentServiceError("daily_loss_stop")
    amount = candidate["notional_usd"]
    if amount > min(config.policy["max_notional_usd"], limits.order_notional_usd):
        raise AgentServiceError("operator_order_limit")
    if amount < limits.min_order_notional_usd:
        raise AgentServiceError("below_supported_order_size")
    if candidate["side"] == "buy":
        if amount > snapshot.cash_usd:
            raise AgentServiceError("insufficient_cash")
        if (
            snapshot.btc_notional_usd + amount
            > snapshot.equity_usd * limits.max_portfolio_exposure
        ):
            raise AgentServiceError("portfolio_exposure_limit")
    elif amount > snapshot.btc_notional_usd:
        raise AgentServiceError("short_position_not_permitted")


class PaperAgentService:
    """One serialized owner per account, with restart-stable proposal identities.

    Construct under ``operator_lock`` for the service lifetime. All CycleStore
    calls run on the owning event-loop thread. The broker dispatcher alone runs
    in a worker; cancellation of an HTTP waiter cannot release the lane while
    that worker is still submitting.
    """

    def __init__(
        self,
        config: CopilotConfig,
        store: CycleStore,
        *,
        evidence: Any,
        account_reader: Any,
        journal: Any,
        tools_factory: Callable[..., Any],
        hmac_key: bytes,
        clock: Callable[[], datetime],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        config.validate()
        if not _valid_config(config.strategy):
            raise AgentServiceError("invalid_account_limits")
        self.config, self.store = config, store
        self.evidence, self.account, self.journal = evidence, account_reader, journal
        self.clock, self.monotonic, self.key = clock, monotonic, hmac_key
        self.binding = agent_binding(config)
        self._preflight_at: float | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._busy = False
        self._closed = False
        self.tools = tools_factory(execution_enabled=self.execution_enabled)
        store.db.executescript("""
            CREATE TABLE IF NOT EXISTS agent_host_identity (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                binding TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS agent_assessments (
                intent_id TEXT PRIMARY KEY, proposal TEXT NOT NULL,
                assessment_id TEXT NOT NULL UNIQUE, request TEXT NOT NULL,
                receipt TEXT NOT NULL, observed_at TEXT NOT NULL);
        """)
        identity = _json(asdict(self.binding))
        with store.db:
            store.db.execute(
                "INSERT OR IGNORE INTO agent_host_identity VALUES(1,?)", (identity,)
            )
            stored = store.db.execute(
                "SELECT binding FROM agent_host_identity WHERE singleton=1"
            ).fetchone()
            if stored is None or stored[0] != identity:
                raise AgentServiceError("state_execution_binding_changed")

    def now(self) -> datetime:
        value = self.clock()
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise AgentServiceError("operator_clock_unavailable", 503)
        return value.astimezone(UTC)

    def enabled(self) -> bool:
        stop = Path(self.config.state_dir) / "STOP"
        return (
            self.config.enabled is True and not stop.exists() and not stop.is_symlink()
        )

    def execution_enabled(self) -> bool:
        return (
            self.enabled()
            and self._preflight_at is not None
            and 0 <= self.monotonic() - self._preflight_at <= 10
        )

    async def run(self, action: str, payload: Any) -> dict[str, Any]:
        if action not in {"assess", "submit", "status", "reconcile"}:
            raise AgentServiceError("unknown_agent_operation", 404)
        if self._closed or self._busy:
            raise AgentServiceError("operator_lane_busy", 503)
        self._busy = True

        async def owned() -> dict[str, Any]:
            try:
                result = await self._dispatch(action, payload)
                self.store.event("agent_" + action, result, self.now())
                return result
            finally:
                self._preflight_at = None
                self._busy = False

        task = asyncio.create_task(owned())
        self._tasks.add(task)

        def finished(done: asyncio.Task[Any]) -> None:
            self._tasks.discard(done)
            if not done.cancelled():
                done.exception()  # Retrieve failures even after an HTTP disconnect.

        task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def close(self) -> None:
        self._closed = True
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def _dispatch(self, action: str, payload: Any) -> dict[str, Any]:
        if action == "assess":
            return await self._assess(proposal(payload))
        if (
            not isinstance(payload, dict)
            or set(payload) != {"assessment_id"}
            or not isinstance(payload["assessment_id"], str)
            or re.fullmatch(r"[0-9a-f]{64}", payload["assessment_id"]) is None
        ):
            raise AgentServiceError("invalid_assessment_identity", 422)
        row = self.store.db.execute(
            "SELECT proposal,request,receipt FROM agent_assessments "
            "WHERE assessment_id=?",
            (payload["assessment_id"],),
        ).fetchone()
        if row is None:
            raise AgentServiceError("assessment_not_found", 404)
        candidate, request, receipt = (json.loads(item) for item in row)
        if trade_safety_request_hash(request) != payload["assessment_id"]:
            raise AgentServiceError("stored_assessment_identity_invalid", 503)
        if action == "submit":
            result = await self._submit(candidate, request, receipt)
        else:
            result = await self._tool(
                "reconcile_paper_order"
                if action == "reconcile"
                else "paper_order_status",
                {"request": request},
            )
        if action == "reconcile" and self._reserved(payload["assessment_id"]):
            if result.get("reconciliation_resolution") == "not_submitted":
                # The existing intent remains reserved for operator investigation.
                result["operator_review_required"] = True
            elif not result.get("tool_error"):
                observed = await self.account.observe_order(payload["assessment_id"])
                if (
                    observed["account_id"] != self.binding.account_id
                    or observed["side"] != request["order"]["side"]
                    or observed["broker_order_id"] != result.get("broker_order_id")
                ):
                    raise AgentServiceError("broker_order_binding_mismatch", 503)
                self.store.order_observation(
                    request_hash=payload["assessment_id"],
                    observation=observed,
                    now=self.now(),
                )
        result["schema"] = "liquilens.agent-order.v1"
        previous = self.store.db.execute(
            "SELECT observed_at,record FROM order_observations WHERE request_hash=?",
            (payload["assessment_id"],),
        ).fetchone()
        if previous is not None:
            observation = json.loads(previous[1])
            result["order_observation"] = observation
            result["broker_observed_at"] = previous[0]
            result["broker_observation_currentness"] = "last_observed"
            result["fill_status"] = (
                "filled"
                if observation["status"] == "filled"
                else "partial"
                if Decimal(observation["filled_qty"]) > 0
                else "not_observed"
            )
        return result

    def _reserved(self, request_hash: str) -> bool:
        return (
            self.store.db.execute(
                "SELECT 1 FROM intents WHERE request_hash=?", (request_hash,)
            ).fetchone()
            is not None
        )

    async def _tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await asyncio.to_thread(self.tools.call_tool, name, arguments)
        return {**result["structuredContent"], "tool_error": result["isError"]}

    def _assessment_result(
        self, request: dict[str, Any], receipt: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "schema": "liquilens.agent-assessment.v1",
            "mode": "paper",
            "assessment_id": trade_safety_request_hash(request),
            "request": request,
            "source_policy_decision": receipt["decision"],
            "evidence": receipt["evidence"],
            "receipt_id": receipt["receipt_id"],
            "record_hash": receipt["record_hash"],
            "expires_at": receipt["expires_at"],
            "policy_hash": receipt["policy_hash"],
            "submission_authorized": False,
        }

    async def _assess(self, candidate: dict[str, Any]) -> dict[str, Any]:
        if (
            self.config.evidence_profile == SCOPED_PROFILE
            and candidate["notional_usd"] != 1000
        ):
            raise AgentServiceError("profile_requires_1000_usd_notional", 422)
        old = self.store.db.execute(
            "SELECT proposal,request,receipt FROM agent_assessments WHERE intent_id=?",
            (candidate["intent_id"],),
        ).fetchone()
        if old is not None:
            if old[0] != _json(candidate):
                raise AgentServiceError("intent_id_already_bound_to_another_proposal")
            return self._assessment_result(json.loads(old[1]), json.loads(old[2]))
        if (
            self.store.db.execute("SELECT count(*) FROM agent_assessments").fetchone()[
                0
            ]
            >= 10000
        ):
            raise AgentServiceError("assessment_capacity_reached", 503)
        now = self.now()
        request = {
            "schema": "liquilens.trade-safety-request.v1",
            "request_id": "agent-"
            + hashlib.sha256(candidate["intent_id"].encode()).hexdigest(),
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "expires_at": (now + timedelta(seconds=90))
            .isoformat()
            .replace("+00:00", "Z"),
            "mode": "paper",
            "agent": {
                **{
                    key: getattr(self.binding, key)
                    for key in (
                        "account_id",
                        "tenant_id",
                        "operator_id",
                        "agent_id",
                        "runtime",
                        "strategy_id",
                    )
                },
                "authorization_scope": ["evidence:read", "orders:paper"],
            },
            "order": {
                "instrument": {
                    "asset_class": "crypto",
                    "symbol": "BTC/USD",
                    "identifiers": {},
                },
                "side": candidate["side"],
                "order_type": "market",
                "notional": {"amount": candidate["notional_usd"], "currency": "USD"},
                "quantity": None,
                "limit_price": None,
                "stop_price": None,
                "venue": None,
                "time_in_force": "IOC",
            },
            "policy_ref": {
                "policy_id": self.binding.policy_id,
                "version": self.binding.policy_version,
            },
            "extensions": {},
        }
        assessment = await self.evidence.assess(request)
        receipt = _audit_receipt(assessment.receipt)
        verify_trade_safety_receipt(receipt, evaluated_at=self.now(), hmac_key=self.key)
        if (
            receipt["request"] != request
            or receipt["policy_hash"] != self.binding.policy_hash
        ):
            raise AgentServiceError("issuer_binding_mismatch", 503)
        encoded_receipt = _json(receipt)
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO agent_assessments VALUES(?,?,?,?,?,?)",
                (
                    candidate["intent_id"],
                    _json(candidate),
                    trade_safety_request_hash(request),
                    _json(request),
                    encoded_receipt,
                    self.now().isoformat(),
                ),
            )
        return self._assessment_result(request, receipt)

    async def _submit(
        self,
        candidate: dict[str, Any],
        request: dict[str, Any],
        receipt: dict[str, Any],
    ) -> dict[str, Any]:
        digest = trade_safety_request_hash(request)
        if self.journal.get(digest) is not None or self._reserved(digest):
            result = await self._tool("paper_order_status", {"request": request})
            return {**result, "duplicate_intent": True, "resubmit_allowed": False}
        if not self.enabled():
            raise AgentServiceError("operator_execution_disabled")
        if self.journal.recovery_candidates(limit=1) or self.store.pending_intents(
            limit=1
        ):
            raise AgentServiceError("unresolved_or_pending_order")
        verified = verify_trade_safety_receipt(
            receipt, evaluated_at=self.now(), hmac_key=self.key
        )
        if verified.receipt["decision"]["outcome"] != "pass":
            raise AgentServiceError("source_policy_did_not_pass")
        if self.config.evidence_profile == SCOPED_PROFILE:
            self.evidence.verify_for_submission(receipt)
            if receipt["evidence"]["liquilens"]["facts"]["cp_spread_bp"] > 50:
                raise AgentServiceError("corporate_funding_pressure_above_50bp")
        snapshot = await self.account.snapshot()
        account_guard(self.config, candidate, snapshot)
        self._preflight_at = self.monotonic()
        # Persist the account check before reserving or requesting submission.
        self.store.event(
            "agent_account_preflight",
            {
                "request_hash": digest,
                "portfolio": asdict(snapshot),
                "execution_permission": False,
            },
            self.now(),
        )
        if not self.execution_enabled():
            raise AgentServiceError("operator_execution_disabled")
        if not self.store.reserve(
            intent_key="|".join(
                (
                    self.binding.account_id,
                    self.binding.strategy_id or "",
                    candidate["intent_id"],
                )
            ),
            request_hash=digest,
            amount=candidate["notional_usd"],
            now=self.now(),
            max_daily_attempts=self.config.max_daily_attempts,
            side=candidate["side"],
            reserved_daily_exit_attempts=self.config.reserved_daily_exit_attempts,
        ):
            raise AgentServiceError("intent_used_or_daily_attempt_limit")
        return await self._tool(
            "submit_paper_order", {"request": request, "receipt": receipt}
        )
