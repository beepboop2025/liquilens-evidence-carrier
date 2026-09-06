"""Single-owner, receipt-gated paper cycles and recovery; no live broker lane."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from liquilens_alpaca_paper import (
    AlpacaPaperSubmissionUncertain,
    SQLiteAlpacaPaperSubmissionJournal,
)
from liquilens_evidence import trade_safety_request_hash
from trade_safety_gateway.app import HttpxUpstreamTransport

from .broker import OperatorPaperSubmissionStopped, OperatorPaperTradeSafetyGateway
from .config import SCOPED_PROFILE, CopilotConfig, PaperCredentials
from .evidence import LiquiLensStrategyContext, OperatorEvidenceService, _json
from .market import InputUnavailable, PaperAccountReader, bounded_json, fetch_bars
from .state import CycleStore
from .strategy import Decision, MarketBar, propose


def utc_now() -> datetime:
    return datetime.now(UTC)


def request_for(
    config: CopilotConfig,
    decision: Decision,
    *,
    now: datetime,
) -> dict[str, Any]:
    binding = config.binding()
    if decision.action not in {"buy", "sell"} or decision.notional_usd is None:
        raise ValueError("trade_candidate_required")
    return {
        "schema": "liquilens.trade-safety-request.v1",
        "request_id": str(uuid4()),
        "created_at": now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "expires_at": (now.astimezone(UTC) + timedelta(seconds=90))
        .isoformat()
        .replace("+00:00", "Z"),
        "mode": "paper",
        "agent": {
            "agent_id": binding.agent_id,
            "operator_id": binding.operator_id,
            "tenant_id": binding.tenant_id,
            "account_id": binding.account_id,
            "runtime": binding.runtime,
            "strategy_id": binding.strategy_id,
            "authorization_scope": ["evidence:read", "orders:paper"],
        },
        "order": {
            "instrument": {
                "asset_class": "crypto",
                "symbol": "BTC/USD",
                "identifiers": {},
            },
            "side": decision.action,
            "order_type": "market",
            "notional": {"amount": decision.notional_usd, "currency": "USD"},
            "quantity": None,
            "limit_price": None,
            "stop_price": None,
            "venue": None,
            "time_in_force": "IOC",
        },
        "policy_ref": {
            "policy_id": binding.policy_id,
            "version": binding.policy_version,
        },
        "extensions": {},
    }


def _stopped(path: Path) -> bool:
    return (path / "STOP").exists() or (path / "STOP").is_symlink()


def _order_summary(order: Any) -> dict[str, Any]:
    # Whitelist only broker outcome fields. A receipt is never a fill report.
    return {
        key: str(getattr(order, key)) if getattr(order, key, None) is not None else None
        for key in ("id", "client_order_id", "status", "filled_qty", "filled_avg_price")
    }


class CopilotRunner:
    def __init__(
        self,
        config: CopilotConfig,
        store: CycleStore,
        *,
        account_reader: Any,
        evidence_service: Any,
        broker: Any,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        config.validate()
        self.config, self.store = config, store
        self.account_reader, self.evidence_service, self.broker = (
            account_reader,
            evidence_service,
            broker,
        )
        self.clock = clock

    def record(self, status: str, **fields: Any) -> dict[str, Any]:
        result = {
            "schema": "liquilens.paper-copilot-cycle.v1",
            "mode": "paper",
            "status": status,
            "observed_at": self.clock().isoformat(),
            **fields,
        }
        self.store.event(status, result, self.clock())
        return result

    def reconcile(self) -> list[dict[str, Any]]:
        results = []
        for candidate in self.broker.recovery_candidates():
            response = self.broker.reconcile(candidate.request_hash)
            results.append(
                {
                    "request_hash": candidate.request_hash,
                    "state": str(response.submission.state),
                    "resolution": response.submission.reconciliation_resolution,
                    "broker_order": _order_summary(response.broker_order),
                }
            )
        if results:
            self.store.event("reconciliation", {"results": results}, self.clock())
        return results

    async def observe_orders(self) -> list[dict[str, Any]]:
        observations = []
        for intent in self.store.pending_intents(limit=100):
            observation = await self.account_reader.observe_order(
                intent["request_hash"]
            )
            self.store.order_observation(
                request_hash=intent["request_hash"],
                observation=observation,
                now=self.clock(),
            )
            observations.append(observation)
        return observations

    async def cycle(
        self,
        bars: list[MarketBar],
        *,
        seiche_regime: str | None,
        riptide_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.config.enabled:
            return self.record(
                "disabled", reasons=["operator_paper_execution_disabled"]
            )
        if _stopped(Path(self.config.state_dir)):
            return self.record("stopped", reasons=["operator_stop_file_present"])
        research = None
        if riptide_context is not None:
            # Riptide's native index is display/alert-only. Keep a bounded,
            # detached research record; it is not a fourth permission source.
            research = json.loads(_json(riptide_context, limit=65536))
            if (
                research.get("schema") != "liquilens.riptide-research-context.v1"
                or research.get("financial_authority") != "none"
                or research.get("influences_order_decision") is not False
            ):
                raise ValueError("riptide_research_authority_invalid")
            self.store.event("riptide_research", research, self.clock())
        # Resolve durable uncertainty before even producing another candidate.
        recovered = self.reconcile()
        if self.broker.recovery_candidates():
            return self.record("blocked", reasons=["unresolved_paper_submission"])
        portfolio = await self.account_reader.snapshot()
        try:
            observations = await self.observe_orders()
        except InputUnavailable:
            # Never interpret an order lookup failure as absence or rejection.
            # Any earlier successful observations were already durably recorded.
            return self.record(
                "blocked",
                reasons=["paper_order_observation_unavailable"],
                portfolio=asdict(portfolio),
            )
        if self.store.pending_intents(limit=1):
            # A known nonterminal order outranks a temporarily empty broker list.
            # Also stop if more than the bounded observation batch remains.
            return self.record(
                "blocked",
                reasons=["pending_paper_order"],
                portfolio=asdict(portfolio),
                order_observations=observations,
            )
        decision = propose(
            bars,
            portfolio,
            self.config.strategy,
            now=self.clock(),
            seiche_regime=seiche_regime,
        )
        common = {"decision": asdict(decision), "portfolio": asdict(portfolio)}
        if research is not None:
            common["riptide_research"] = research
        if recovered:
            common["reconciliations"] = recovered
        if observations:
            common["order_observations"] = observations
        if decision.action == "hold":
            return self.record("hold", **common)
        request = request_for(self.config, decision, now=self.clock())
        if research is not None:
            self.store.event(
                "riptide_research_request",
                {
                    "request_hash": trade_safety_request_hash(request),
                    "context": research,
                    "influences_order_decision": False,
                },
                self.clock(),
            )
        assessment = await self.evidence_service.assess(request)
        receipt = assessment.receipt
        common.update(request=request, receipt=receipt)
        # Save evidence BEFORE requesting broker submission. A persistence
        # failure is a hard stop, not permission to run without an audit trail.
        self.store.event("assessment", common, self.clock())
        if assessment.outcome != "pass":
            return self.record(
                "blocked", reasons=list(assessment.reason_codes), **common
            )
        if self.config.evidence_profile == SCOPED_PROFILE:
            # Corporate funding pressure is an additional strategy halt, not
            # a fabricated Seiche regime or missing-data outcome. The raw
            # dated CP input remains in the authenticated receipt.
            cp_spread = receipt["evidence"]["liquilens"]["facts"]["cp_spread_bp"]
            if cp_spread > 50:
                return self.record(
                    "hold",
                    reasons=["scoped_corporate_funding_pressure_above_50bp"],
                    **common,
                )
        # Account changes during evidence retrieval must not evade portfolio,
        # open-order, cash, no-short or loss controls.
        fresh = await self.account_reader.snapshot()
        assessed_regime = (
            receipt.get("evidence", {}).get("seiche", {}).get("facts", {}).get("regime")
        )
        checked = propose(
            bars,
            fresh,
            self.config.strategy,
            now=self.clock(),
            seiche_regime=assessed_regime,
        )
        if (checked.action, checked.notional_usd) != (
            decision.action,
            decision.notional_usd,
        ):
            return self.record(
                "blocked", reasons=["portfolio_changed_before_submission"], **common
            )
        if _stopped(Path(self.config.state_dir)):
            return self.record(
                "stopped", reasons=["operator_stop_file_present"], **common
            )
        if self.config.evidence_profile == SCOPED_PROFILE:
            self.evidence_service.verify_for_submission(receipt)
        # One intent per completed bar, account and strategy survives restarts.
        # A failed/uncertain attempt consumes its slot; never erase it to retry.
        intent = "|".join(
            (
                self.config.account_id or "",
                self.config.strategy_id,
                bars[-1].at.isoformat(),
            )
        )
        if not self.store.reserve(
            intent_key=intent,
            request_hash=trade_safety_request_hash(request),
            amount=decision.notional_usd or 0,
            now=self.clock(),
            max_daily_attempts=self.config.max_daily_attempts,
        ):
            return self.record(
                "blocked", reasons=["intent_used_or_daily_attempt_limit"], **common
            )
        try:
            # The existing adapter independently verifies the exact request,
            # HMAC, policy, expiry, account and replay state before its SDK call.
            result = self.broker.submit(request, receipt)
        except OperatorPaperSubmissionStopped:
            return self.record(
                "stopped",
                reasons=["operator_stop_file_present_before_submission"],
                **common,
            )
        except AlpacaPaperSubmissionUncertain:
            return self.record(
                "uncertain", reasons=["broker_lookup_required_no_resubmit"], **common
            )
        return self.record(
            "submitted", broker_order=_order_summary(result.broker_order), **common
        )


async def run_configured_cycle(
    config: CopilotConfig,
    store: CycleStore,
    credentials: PaperCredentials | None,
    *,
    clock: Callable[[], datetime] = utc_now,
    reconcile_only: bool = False,
) -> dict[str, Any]:
    config.validate()
    missing = []
    if not config.account_id:
        missing.append("paper_account_id_missing")
    if credentials is None:
        missing.append("paper_credentials_or_operator_hmac_missing")
    if not config.enabled and not reconcile_only:
        missing.append("operator_paper_execution_disabled")
    if missing:
        result = {"mode": "paper", "status": "blocked", "reasons": missing}
        store.event("configuration_blocked", result, clock())
        return result
    assert credentials is not None and config.account_id is not None
    binding = config.binding()
    journal = SQLiteAlpacaPaperSubmissionJournal(
        Path(config.state_dir) / "alpaca-submissions.sqlite3",
    )
    try:
        broker = OperatorPaperTradeSafetyGateway(
            state_dir=Path(config.state_dir),
            binding=binding,
            submission_journal=journal,
            hmac_key=credentials.hmac_key,
            api_key=credentials.api_key,
            secret_key=credentials.secret_key,
            clock=clock,
        )
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
            if config.evidence_profile == SCOPED_PROFILE:
                from .scoped import ScopedPaperEvidenceService, ScopedUpstreamTransport

                transport = ScopedUpstreamTransport()
            else:
                transport = HttpxUpstreamTransport()
            try:
                context = (
                    None
                    if not config.liquilens_institution_slug
                    else LiquiLensStrategyContext(
                        institution_slug=config.liquilens_institution_slug,
                        required=config.liquilens_required,
                    )
                )
                if config.evidence_profile == SCOPED_PROFILE:
                    service = ScopedPaperEvidenceService(
                        transport,
                        policy=config.policy,
                        binding=binding,
                        hmac_key=credentials.hmac_key,
                        clock=clock,
                    )
                else:
                    service = OperatorEvidenceService(
                        transport,
                        policy=config.policy,
                        binding=binding,
                        hmac_key=credentials.hmac_key,
                        clock=clock,
                        liquilens_context=context,
                    )
                runner = CopilotRunner(
                    config,
                    store,
                    account_reader=PaperAccountReader(
                        client, credentials, config.account_id
                    ),
                    evidence_service=service,
                    broker=broker,
                    clock=clock,
                )
                if reconcile_only:
                    return {
                        "mode": "paper",
                        "status": "reconciled",
                        "results": runner.reconcile(),
                        "order_observations": await runner.observe_orders(),
                    }
                if _stopped(Path(config.state_dir)):
                    return runner.record(
                        "stopped", reasons=["operator_stop_file_present"]
                    )
                riptide_context = None
                if config.evidence_profile == SCOPED_PROFILE:
                    from .riptide import collect_riptide_context

                    riptide_context = await collect_riptide_context()
                bars = await fetch_bars(client, now=clock(), credentials=credentials)
                if config.evidence_profile == SCOPED_PROFILE:
                    regime = await service.funding_regime()
                else:
                    raw_context = await bounded_json(
                        client, "https://api.seiche.info/api/trade-safety/risk-context"
                    )
                    regime = (
                        raw_context.get("regime")
                        if isinstance(raw_context, dict)
                        else None
                    )
                return await runner.cycle(
                    bars,
                    seiche_regime=regime,
                    riptide_context=riptide_context,
                )
            finally:
                await transport.aclose()
    finally:
        journal.close()
