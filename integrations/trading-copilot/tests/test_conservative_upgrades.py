"""Synthetic policy regressions and durable state; no broker/network execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from threading import Barrier

import pytest
from test_paper_pipeline import KEY, SyntheticSDK, negative_bars, pipeline
from test_strategy import CONFIG, PORTFOLIO, sample_bars

from liquilens_trading_copilot.config import (
    ConfigurationError,
    load_config,
)
from liquilens_trading_copilot.runner import _audit_receipt
from liquilens_trading_copilot.state import CycleStore, StateError
from liquilens_trading_copilot.strategy import propose

NOW = datetime(2026, 9, 6, 23, 30, tzinfo=UTC)


def reserve(store, identity, side="buy", now=NOW):
    return store.reserve(
        intent_key=identity,
        request_hash=identity,
        amount=1000,
        now=now,
        max_daily_attempts=2,
        side=side,
        reserved_daily_exit_attempts=1,
    )


@pytest.mark.parametrize("sides", [("buy", "sell"), ("sell", "buy"), ("sell", "sell")])
def test_two_total_attempts_retain_reduction_capacity(tmp_path, sides):
    store = CycleStore(tmp_path / "state")
    assert reserve(store, "first", sides[0])
    assert reserve(store, "second", sides[1])
    assert not reserve(store, "third", "sell")
    assert not reserve(store, "fourth", "buy")
    assert store.status()["intent_count"] == 2
    store.close()


def test_second_buy_is_held_and_failed_or_unknown_attempt_remains_spent(tmp_path):
    state = tmp_path / "state"
    store = CycleStore(state)
    assert reserve(store, "uncertain-buy")
    store.close()
    store = CycleStore(state)
    assert not reserve(store, "other-buy")
    assert not reserve(store, "uncertain-buy", "sell")
    assert reserve(store, "reduction", "sell")
    assert not reserve(store, "extra-reduction", "sell")
    store.close()


def test_legacy_intents_are_conservatively_counted_without_rewriting_them(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    db = sqlite3.connect(state / "audit.sqlite3")
    db.execute(
        "CREATE TABLE intents (intent_key TEXT PRIMARY KEY,day TEXT NOT NULL,"
        "request_hash TEXT NOT NULL UNIQUE,amount REAL NOT NULL)"
    )
    db.execute(
        "INSERT INTO intents VALUES (?,?,?,?)", ("old", "2026-09-06", "old", 1000)
    )
    db.commit()
    db.close()
    store = CycleStore(state)
    assert len(store.db.execute("PRAGMA table_info(intents)").fetchall()) == 4
    assert not reserve(store, "entry")
    assert reserve(store, "exit", "sell")
    assert store.db.execute(
        "SELECT * FROM intents WHERE intent_key='old'"
    ).fetchone() == (
        "old",
        "2026-09-06",
        "old",
        1000,
    )
    store.close()


def test_concurrent_buy_reservations_cannot_consume_the_exit_slot(tmp_path):
    state = tmp_path / "state"
    CycleStore(state).close()
    barrier = Barrier(4)

    def attempt(index):
        store = CycleStore(state)
        try:
            barrier.wait(timeout=5)
            return reserve(store, f"entry-{index}")
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        accepted = list(pool.map(attempt, range(4)))
    assert sum(accepted) == 1
    store = CycleStore(state)
    assert reserve(store, "exit", "sell")
    assert store.status()["intent_count"] == 2
    store.close()


def test_reservations_use_utc_day_not_callers_local_date(tmp_path):
    store = CycleStore(tmp_path / "state")
    assert reserve(store, "entry")
    local_next_day = NOW.astimezone(timezone(timedelta(hours=2)))
    assert local_next_day.date() != NOW.date()
    assert not reserve(store, "same-instant-entry", now=local_next_day)
    assert reserve(store, "exit", "sell", local_next_day)
    assert reserve(store, "next-utc-entry", now=NOW + timedelta(hours=1))
    with pytest.raises(StateError, match="aware_reservation_clock"):
        reserve(store, "naive", now=NOW.replace(tzinfo=None))
    store.close()


def test_old_configuration_gets_safe_defaults_and_invalid_reserves_fail(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"state_dir": str(tmp_path / "state")}))
    config = load_config(path)
    assert config.max_daily_attempts == 2 and config.reserved_daily_exit_attempts == 1
    for value in (-1, 3, True, 0.5):
        with pytest.raises(ConfigurationError, match="reserved_exit_attempts"):
            replace(config, reserved_daily_exit_attempts=value).validate()


@pytest.mark.parametrize(
    "holding,expected", [(1389.32, "sell"), (1000, "sell"), (999.99, "hold")]
)
def test_negative_momentum_reduction_bypasses_only_tolerance(holding, expected):
    bars = sample_bars(trend=-0.01)
    equity = 138962.31882903568
    portfolio = replace(
        PORTFOLIO,
        equity_usd=equity,
        cash_usd=equity - holding,
        btc_notional_usd=holding,
    )
    decision = propose(bars, portfolio, CONFIG, bars[-1].at, "CALM")
    assert decision.action == expected
    if expected == "sell":
        assert decision.notional_usd == 1000
    else:
        assert decision.reasons == ("residual_below_minimum_order_notional",)
    stopped = propose(
        bars, replace(portfolio, daily_pnl_usd=-equity), CONFIG, bars[-1].at, "CALM"
    )
    assert stopped.action == "hold" and stopped.reasons == ("daily_loss_stop",)
    pending = propose(
        bars, replace(portfolio, open_orders=1), CONFIG, bars[-1].at, "CALM"
    )
    assert pending.reasons == ("open_orders_pending",)


def test_positive_drift_is_explicit_and_never_forces_sale():
    bars = sample_bars()
    portfolio = replace(PORTFOLIO, cash_usd=85000, btc_notional_usd=15000)
    decision = propose(bars, portfolio, CONFIG, bars[-1].at, "CALM")
    assert decision.action == "hold" and decision.reasons == ("no_sell_signal",)
    assert decision.metrics["above_configured_exposure_ceiling"] == 1
    assert decision.metrics["exposure_limit_semantics"] == "entry_target_not_maintained"
    assert decision.metrics["exposure_above_target_usd"] == 5000


def test_decision_inputs_and_native_source_receipt_are_preserved_privately(tmp_path):
    async def run():
        sdk = SyntheticSDK()
        async with pipeline(tmp_path, sdk) as system:
            result = await system.runner.cycle(negative_bars(), seiche_regime="CALM")
            assert result["status"] == "submitted"
            events = [
                (kind, json.loads(raw))
                for kind, raw in system.store.db.execute(
                    "SELECT kind,record FROM events ORDER BY id"
                )
            ]
            kinds = [kind for kind, _ in events]
            assert (
                kinds.index("strategy_inputs")
                < kinds.index("assessment")
                < kinds.index("submission_recheck")
                < kinds.index("submitted")
            )
            assert kinds.index("source_receipt") < kinds.index("assessment")
            inputs = next(value for kind, value in events if kind == "strategy_inputs")
            assert len(inputs["bars"]) == len(negative_bars())
            assert inputs["execution_permission"] is False
            assert (
                inputs["knowledge_time_basis"]
                == "local_decision_observation_not_source_publication"
            )
            canonical = json.dumps(inputs, sort_keys=True, separators=(",", ":"))
            assert (
                hashlib.sha256(canonical.encode()).hexdigest()
                == result["strategy_inputs_sha256"]
            )
            assert KEY.decode() not in json.dumps(events)
            assert '"headers"' not in json.dumps(events)
            assert result["daily_budget"]["used_total"] == 1
            assert result["daily_budget"]["used_entries_or_unknown"] == 0
            source = next(value for kind, value in events if kind == "source_receipt")
            assert source["request_hash"] == result["receipt"]["request_hash"]
            with pytest.raises(StateError, match="contract_invalid"):
                _audit_receipt(
                    {**source, "headers": {"Authorization": "must-not-export"}}
                )
            corrupted = json.loads(json.dumps(source))
            corrupted["evidence"]["seiche"]["facts"]["api_key"] = "must-not-export"
            with pytest.raises(StateError, match="credential_field"):
                _audit_receipt(corrupted)

    asyncio.run(run())


@pytest.mark.parametrize(
    "failed_event",
    ["strategy_inputs", "source_receipt", "assessment", "submission_recheck"],
)
def test_audit_persistence_failure_prevents_submission(
    tmp_path, monkeypatch, failed_event
):
    async def run():
        sdk = SyntheticSDK()
        async with pipeline(tmp_path, sdk) as system:
            original = system.store.event

            def event(kind, record, now):
                if kind == failed_event:
                    raise StateError("synthetic_disk_write_failure")
                return original(kind, record, now)

            monkeypatch.setattr(system.store, "event", event)
            with pytest.raises(StateError, match="synthetic_disk_write_failure"):
                await system.runner.cycle(negative_bars(), seiche_regime="CALM")
            assert sdk.orders == [] and sdk.account_reads == 0
            assert system.store.status()["intent_count"] == 0

    asyncio.run(run())
