"""Admission adapter tests; set COPILOT_BASELINE_HARNESS to the saved source."""

from __future__ import annotations

import os
import runpy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from liquilens_trading_copilot import strategy

ADAPTER = runpy.run_path(str(Path(__file__).with_name("run_upgrade_replay.py")))


@pytest.fixture
def harness():
    path = os.getenv("COPILOT_BASELINE_HARNESS")
    if not path:
        pytest.skip("explicit saved baseline harness required")
    module, patch = ADAPTER["load_candidate_harness"](Path(path))
    assert module.propose is strategy.propose
    assert "_candidate_reserve" in patch
    return module


def bar(module, hour):
    at = datetime(2026, 9, 6, 20, tzinfo=UTC) + timedelta(hours=hour)
    return module.HistoricalBar(at, 100000, 100000, 100000, 100000, 1, at, at, True)


def test_actual_candidate_budget_preserves_exit_and_next_open_fill(harness):
    replay = harness.Replay(harness.Scenario("CALM", 5, 1), "synthetic")
    offers = iter(["buy", "buy", "sell", "sell", "buy"])
    harness.propose = lambda *args: strategy.Decision(
        next(offers), 1000, ("synthetic",), {}
    )
    rows = [replay.step(bar(harness, hour)) for hour in range(5)]
    assert [row["action"] for row in rows] == ["buy", "hold", "sell", "hold", "buy"]
    assert rows[1]["reasons"] == ["daily_entry_capacity_reserved_for_exit"]
    assert rows[3]["reasons"] == ["daily_reservation_limit"]
    assert [row["reservations_today"] for row in rows] == [1, 1, 2, 2, 1]
    assert rows[0]["fill"] is None
    assert rows[1]["fill"]["execution_time"] == rows[0]["timestamp"]
    assert rows[1]["fill"]["cost_usd"] == 0.5
    assert rows[2]["fill"] is None
    assert rows[3]["fill"]["action"] == "sell"
    assert rows[3]["cash_usd"] == 99999
    assert rows[3]["btc_quantity"] == 0
    assert replay.summary()["trades"] == 2


def test_gap_rejection_does_not_reopen_spent_entry(harness):
    replay = harness.Replay(harness.Scenario("CALM", 5, 1), "synthetic")
    harness.propose = lambda *args: strategy.Decision("buy", 1000, ("synthetic",), {})
    first = replay.step(bar(harness, 0))
    gap = replay.step(bar(harness, 2))
    after = replay.step(bar(harness, 3))
    assert first["action"] == "buy"
    assert gap["fill"]["reason"] == "execution_bar_gap"
    assert gap["action"] == "hold"
    assert after["reasons"] == ["daily_entry_capacity_reserved_for_exit"]
    assert after["reservations_today"] == after["entry_reservations_today"] == 1
    assert replay.summary()["trades"] == 0


def test_extra_execution_lag_unchanged(harness):
    replay = harness.Replay(harness.Scenario("CALM", 5, 2), "synthetic")
    offers = iter(["buy", "hold", "hold"])
    harness.propose = lambda *args: strategy.Decision(
        next(offers), 1000, ("synthetic",), {}
    )
    rows = [replay.step(bar(harness, hour)) for hour in range(3)]
    assert rows[0]["fill"] is rows[1]["fill"] is None
    assert rows[2]["fill"]["execution_time"] == rows[1]["timestamp"]
    assert replay.summary()["trades"] == 1


def test_missing_funding_oracle_remains_independent(harness):
    replay = harness.Replay(
        harness.Scenario("CALM", 5, 1, "full_copilot_availability"), "synthetic"
    )
    harness.propose = lambda *args: strategy.Decision("buy", 1000, ("synthetic",), {})
    row = replay.step(bar(harness, 0))
    assert row["proposal_action"] == "buy"
    assert row["action"] == "hold" and row["reservations_today"] == 0
    assert replay.summary()["missing_evidence_oracle_mismatches"] == 1


def test_changed_baseline_source_is_rejected(tmp_path):
    path = tmp_path / "harness.py"
    path.write_text("raise AssertionError('must never execute altered source')")
    with pytest.raises(ValueError, match="baseline_harness_identity_mismatch"):
        ADAPTER["load_candidate_harness"](path)
