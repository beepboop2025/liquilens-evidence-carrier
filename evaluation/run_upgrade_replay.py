"""Compare a fixed candidate against immutable historical construction outputs.

No evidence service, broker, model, credentials or network is called. Admission
uses the candidate CycleStore implementation with an in-memory database; durable
storage behavior is tested separately by the trading package's integration tests.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import sqlite3
import sys
import types
from pathlib import Path

from liquilens_trading_copilot import state, strategy

BASE_HARNESS_SHA256 = "1caa8deeb197530903b6e0402934075f55cdef61e7714a30aa0133661e6a4486"
OLD_ADMISSION = """        elif action != "hold" and self.reservations >= 2:
            action, reasons = "hold", ("daily_reservation_limit",)
"""
NEW_ADMISSION = """        elif action != "hold" and not _candidate_reserve(
            self, proposal, bar
        ):
            action, reasons = "hold", (self._reservation_refusal,)
"""


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def memory_store() -> state.CycleStore:
    # Only storage is synthetic. The unchanged production reserve/daily_budget
    # methods own UTC normalization, deduplication and transactional admission.
    store = object.__new__(state.CycleStore)
    store.db = sqlite3.connect(":memory:")
    store.db.executescript("""
      CREATE TABLE intents(intent_key TEXT PRIMARY KEY,day TEXT NOT NULL,
        request_hash TEXT NOT NULL UNIQUE,amount REAL NOT NULL);
      CREATE TABLE intent_directions(intent_key TEXT PRIMARY KEY,
        side TEXT NOT NULL CHECK(side IN ('buy','sell','unknown')));
      CREATE INDEX intent_day ON intents(day);
    """)
    return store


def load_candidate_harness(path: Path):
    if digest(path) != BASE_HARNESS_SHA256:
        raise ValueError("baseline_harness_identity_mismatch")
    original = path.read_text()
    old_hash = 'strategy_sha = file_sha256(Path(__file__).with_name("strategy.py"))'
    if original.count(OLD_ADMISSION) != 1 or original.count(old_hash) != 1:
        raise ValueError("baseline_adaptation_anchor_mismatch")
    adapted = original.replace(OLD_ADMISSION, NEW_ADMISSION).replace(
        old_hash, "strategy_sha = file_sha256(_candidate_strategy_path)"
    )
    module = types.ModuleType("liquilens_trading_copilot._upgrade_replay")
    module.__file__ = str(path)
    module.__package__ = "liquilens_trading_copilot"
    module._candidate_strategy_path = Path(strategy.__file__)
    sys.modules[module.__name__] = module
    exec(compile(adapted, str(path), "exec"), module.__dict__)
    if module.propose is not strategy.propose:
        raise ValueError("candidate_strategy_import_mismatch")

    def reserve(replay, proposal, bar):
        accepted = replay._store.reserve(
            intent_key=bar.at.isoformat(),
            request_hash=bar.at.isoformat(),
            amount=float(proposal.notional_usd),
            now=bar.at,
            max_daily_attempts=2,
            reserved_daily_exit_attempts=1,
            side=proposal.action,
        )
        replay._budget = replay._store.daily_budget(
            now=bar.at, max_daily_attempts=2, reserved_daily_exit_attempts=1
        )
        replay._reservation_refusal = (
            "daily_reservation_limit"
            if replay._budget["remaining_total"] == 0
            else "daily_entry_capacity_reserved_for_exit"
        )
        return accepted

    module._candidate_reserve = reserve
    base = module.Replay

    class CandidateReplay(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._store = memory_store()
            self._budget = None

        def step(self, bar):
            if self.day != bar.at.date():
                self._budget = self._store.daily_budget(
                    now=bar.at, max_daily_attempts=2, reserved_daily_exit_attempts=1
                )
            result = super().step(bar)
            if self._budget["used_total"] != result["reservations_today"]:
                raise ValueError("replay_and_actual_reservation_store_disagree")
            result["entry_reservations_today"] = self._budget["used_entries_or_unknown"]
            result["reserved_daily_exit_attempts"] = 1
            return result

        def summary(self):
            result = super().summary()
            result["daily_admission"] = {
                "implementation": "candidate_CycleStore.reserve",
                "maximum_total": 2,
                "maximum_entries": 1,
                "reserved_exit_attempts": 1,
                "simulation_storage": "in_memory_not_live_durability_test",
            }
            self._store.close()
            return result

    module.Replay = CandidateReplay
    adaptation = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            adapted.splitlines(keepends=True),
            fromfile="immutable-baseline-harness",
            tofile="candidate-admission-adaptation",
        )
    )
    return module, adaptation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--baseline-summary", type=Path, required=True)
    parser.add_argument("--baseline-harness", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--candidate-revision", required=True)
    args = parser.parse_args()
    baseline = json.loads(args.baseline_summary.read_text())
    source_sha = digest(args.input)
    if source_sha != baseline["source_sha256"]:
        raise ValueError("source_does_not_match_baseline")
    code_paths = {
        name: Path(strategy.__file__).with_name(f"{name}.py")
        for name in ("config", "runner", "state", "strategy")
    }
    hashes = {name: digest(path) for name, path in code_paths.items()}
    harness, adaptation = load_candidate_harness(args.baseline_harness)
    report = harness.run_replay(
        harness.read_bars(args.input, assume_availability=True),
        args.output_dir,
        source_id=baseline["source_id"],
        source_sha256=source_sha,
        strategy_revision=args.candidate_revision,
        input_symbol=baseline["instrument"]["input_symbol"],
        quote_currency=baseline["instrument"]["quote_currency"],
        source_fetched_at=baseline["source_fetched_at"],
    )
    if digest(args.input) != source_sha:
        raise ValueError("source_changed_during_replay")
    for name, path in code_paths.items():
        if digest(path) != hashes[name]:
            raise ValueError("candidate_source_changed_during_replay")
    report["protocol"].update(
        reserved_daily_exit_attempts=1,
        max_daily_entry_reservations_utc=1,
        admission_implementation="candidate_CycleStore.reserve_with_in_memory_ledger",
        selection="approved_conservative_policy_no_return_optimization",
    )
    report["candidate_code_sha256"] = hashes
    report["baseline_summary_sha256"] = digest(args.baseline_summary)
    report["baseline_harness_sha256"] = BASE_HARNESS_SHA256
    report["adapter_sha256"] = digest(Path(__file__))
    baseline_by_id = {row["scenario_id"]: row for row in baseline["scenarios"]}
    if set(baseline_by_id) != {row["scenario_id"] for row in report["scenarios"]}:
        raise ValueError("scenario_grid_mismatch")
    comparison = []
    for row in report["scenarios"]:
        old = baseline_by_id[row["scenario_id"]]
        if (row["bars"], row["first_close"], row["last_close"]) != (
            old["bars"],
            old["first_close"],
            old["last_close"],
        ):
            raise ValueError("observation_coverage_mismatch")
        comparison.append(
            {
                "scenario_id": row["scenario_id"],
                "baseline": {
                    key: old[key]
                    for key in (
                        "net_return",
                        "max_drawdown_close_to_close",
                        "trades",
                        "total_cost_usd",
                        "hold_reason_histogram",
                    )
                },
                "candidate": {
                    key: row[key]
                    for key in (
                        "net_return",
                        "max_drawdown_close_to_close",
                        "trades",
                        "total_cost_usd",
                        "hold_reason_histogram",
                    )
                },
                "net_return_change_percentage_points": 100
                * (row["net_return"] - old["net_return"]),
            }
        )
    (args.output_dir / "harness-adaptation.diff").write_text(adaptation)
    (args.output_dir / "summary.json").write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n"
    )
    (args.output_dir / "baseline-comparison.json").write_text(
        json.dumps(
            {
                "scope": (
                    "descriptive_construction_comparison_not_validation_or_optimization"
                ),
                "baseline_immutable": True,
                "scenarios": comparison,
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir),
                "scenarios": len(comparison),
                "bars_each": report["scenarios"][0]["bars"],
                "strategy_sha256": hashes["strategy"],
            }
        )
    )


if __name__ == "__main__":
    main()
