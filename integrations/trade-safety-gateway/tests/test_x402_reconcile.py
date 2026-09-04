from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from test_x402_runtime import _environment

from trade_safety_gateway import x402_reconcile
from trade_safety_gateway.x402_access import JournalCounts, ReconciliationRecord
from trade_safety_gateway.x402_runtime import x402_runtime_from_env

PAYMENT_ID = "a" * 64


class _FakeGate:
    def __init__(self) -> None:
        self.reconciled: list[tuple[str, dict[str, Any] | None]] = []
        self.retired: list[str] = []
        self.retention_calls: list[tuple[int, int]] = []

    def journal_counts(self) -> JournalCounts:
        return JournalCounts(
            total=5,
            processing=1,
            settling=1,
            cached_settled=2,
            tombstones=1,
        )

    def reconciliation_records(
        self, *, limit: int = 100
    ) -> tuple[ReconciliationRecord, ...]:
        assert limit == 7
        return (
            ReconciliationRecord(
                payment_id=PAYMENT_ID,
                state="settlement_uncertain",
                body_sha256="b" * 64,
                resource="https://api.liquilens.in/v1/x402/check",
                settlement_started_at_ns=1_788_457_200_000_000_000,
                result_observed=True,
                result_code="settlement_pending",
                network="eip155:8453",
                transaction="0x" + "c" * 64,
            ),
        )

    def reconcile_settled(
        self,
        payment_id: str,
        settlement_response: dict[str, Any] | None = None,
    ) -> None:
        self.reconciled.append((payment_id, settlement_response))

    def retire_unsettled(self, payment_id: str) -> None:
        self.retired.append(payment_id)

    def retire_terminal_responses(
        self,
        *,
        older_than_seconds: int,
        limit: int,
    ) -> int:
        self.retention_calls.append((older_than_seconds, limit))
        return 3


class _FakeRuntime:
    def __init__(self) -> None:
        self.gate = _FakeGate()
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def _install_runtime(monkeypatch: pytest.MonkeyPatch) -> _FakeRuntime:
    runtime = _FakeRuntime()
    monkeypatch.setattr(
        x402_reconcile,
        "x402_runtime_from_env",
        lambda *, maintenance=False: runtime,
    )
    return runtime


def _output(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    captured = capsys.readouterr()
    assert captured.err == ""
    return json.loads(captured.out)


def test_importing_operator_module_does_not_construct_web_runtime() -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("TRADE_SAFETY_X402_")
    }
    # Eager web-app construction would reject this deliberately partial runtime.
    environment["TRADE_SAFETY_X402_PAY_TO"] = "configured-only-for-import-test"

    completed = subprocess.run(
        [sys.executable, "-c", "import trade_safety_gateway.x402_reconcile"],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""


def test_status_is_redacted_and_closes_runtime(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = _install_runtime(monkeypatch)

    assert x402_reconcile.main(["status", "--limit", "7"]) == 0

    payload = _output(capsys)
    assert payload["schema"] == "liquilens.trade-safety-x402-reconciliation.v1"
    assert payload["counts"] == {
        "cached_settled": 2,
        "processing": 1,
        "settling": 1,
        "tombstones": 1,
        "total": 5,
    }
    assert payload["records"] == [
        {
            "body_sha256": "b" * 64,
            "network": "eip155:8453",
            "payment_id": PAYMENT_ID,
            "resource": "https://api.liquilens.in/v1/x402/check",
            "result_code": "settlement_pending",
            "result_observed": True,
            "settlement_started_at_ns": 1_788_457_200_000_000_000,
            "state": "settlement_uncertain",
            "transaction": "0x" + "c" * 64,
        }
    ]
    serialized = json.dumps(payload, sort_keys=True).lower()
    assert "signature" not in serialized
    assert "paymentpayload" not in serialized
    assert "payto" not in serialized
    assert "payer" not in serialized
    assert runtime.closed is True


def test_reconcile_settled_reads_only_private_strict_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    runtime = _install_runtime(monkeypatch)
    response_path = tmp_path / "settlement.json"
    response_path.write_text(
        json.dumps(
            {
                "success": True,
                "payer": "0x" + "1" * 40,
                "transaction": "0x" + "2" * 64,
                "network": "eip155:8453",
            }
        ),
        encoding="utf-8",
    )
    response_path.chmod(0o600)

    assert (
        x402_reconcile.main(
            [
                "reconcile-settled",
                PAYMENT_ID,
                "--response",
                str(response_path),
            ]
        )
        == 0
    )

    assert runtime.gate.reconciled == [
        (
            PAYMENT_ID,
            {
                "success": True,
                "payer": "0x" + "1" * 40,
                "transaction": "0x" + "2" * 64,
                "network": "eip155:8453",
            },
        )
    ]
    assert _output(capsys) == {
        "ok": True,
        "payment_id": PAYMENT_ID,
        "state": "settled",
    }
    assert runtime.closed is True


@pytest.mark.parametrize(
    "contents",
    [
        '{"success":true,"success":false}',
        '{"success":NaN}',
        "[]",
    ],
)
def test_reconcile_settled_rejects_non_strict_json_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    contents: str,
) -> None:
    runtime = _install_runtime(monkeypatch)
    response_path = tmp_path / "settlement.json"
    response_path.write_text(contents, encoding="utf-8")
    response_path.chmod(0o600)

    assert (
        x402_reconcile.main(
            [
                "reconcile-settled",
                PAYMENT_ID,
                "--response",
                str(response_path),
            ]
        )
        == 1
    )

    assert _output(capsys) == {
        "code": "reconciliation_input_invalid",
        "ok": False,
    }
    assert runtime.gate.reconciled == []
    assert runtime.closed is True


def test_reconcile_settled_rejects_public_or_symlink_file(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    runtime = _install_runtime(monkeypatch)
    response_path = tmp_path / "settlement.json"
    response_path.write_text('{"success":true}', encoding="utf-8")
    response_path.chmod(0o644)

    assert (
        x402_reconcile.main(
            ["reconcile-settled", PAYMENT_ID, "--response", str(response_path)]
        )
        == 1
    )
    assert _output(capsys)["code"] == "reconciliation_input_invalid"

    response_path.chmod(0o600)
    link_path = tmp_path / "settlement-link.json"
    link_path.symlink_to(response_path)
    assert (
        x402_reconcile.main(
            ["reconcile-settled", PAYMENT_ID, "--response", str(link_path)]
        )
        == 1
    )
    assert _output(capsys)["code"] == "reconciliation_input_invalid"
    assert runtime.gate.reconciled == []
    assert runtime.closed is True


def test_retirement_requires_explicit_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = _install_runtime(monkeypatch)

    assert x402_reconcile.main(["retire-unsettled", PAYMENT_ID]) == 1

    assert _output(capsys) == {
        "code": "reconciliation_confirmation_required",
        "ok": False,
    }
    assert runtime.gate.retired == []
    assert runtime.closed is True


def test_retirement_with_confirmation_creates_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = _install_runtime(monkeypatch)

    assert (
        x402_reconcile.main(["retire-unsettled", PAYMENT_ID, "--confirm-not-settled"])
        == 0
    )

    assert runtime.gate.retired == [PAYMENT_ID]
    assert _output(capsys) == {
        "ok": True,
        "payment_id": PAYMENT_ID,
        "state": "payment_authorization_retired",
    }
    assert runtime.closed is True


def test_terminal_response_retention_is_confirmed_bounded_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = _install_runtime(monkeypatch)

    assert (
        x402_reconcile.main(
            [
                "retire-terminal-responses",
                "--older-than-days",
                "30",
                "--limit",
                "7",
                "--confirm-replay-loss",
            ]
        )
        == 0
    )

    assert runtime.gate.retention_calls == [(30 * 86_400, 7)]
    assert _output(capsys) == {
        "ok": True,
        "older_than_days": 30,
        "retired_count": 3,
        "state": "terminal_response_material_retired",
    }
    assert runtime.closed is True


def test_terminal_response_retention_requires_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = _install_runtime(monkeypatch)

    assert (
        x402_reconcile.main(["retire-terminal-responses", "--older-than-days", "30"])
        == 1
    )

    assert _output(capsys) == {
        "code": "retention_confirmation_required",
        "ok": False,
    }
    assert runtime.gate.retention_calls == []
    assert runtime.closed is True


def test_retirement_is_mechanically_blocked_while_gateway_runtime_is_active(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    environment = _environment(tmp_path / "x402.sqlite3")
    runtime = x402_runtime_from_env(environment)
    assert runtime is not None
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    try:
        assert (
            x402_reconcile.main(
                ["retire-unsettled", PAYMENT_ID, "--confirm-not-settled"]
            )
            == 1
        )
        assert _output(capsys) == {
            "code": "reconciliation_runtime_active",
            "ok": False,
        }
    finally:
        asyncio.run(runtime.aclose())


@pytest.mark.parametrize("value", ["0", "101", "1.5", "many"])
def test_status_limit_matches_journal_contract(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        x402_reconcile._limit(value)


@pytest.mark.parametrize("value", ["0", "36501", "1.5", "many"])
def test_retention_days_are_bounded(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        x402_reconcile._retention_days(value)
