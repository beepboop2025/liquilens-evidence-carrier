"""Offline onboarding must explain blockers without changing account state."""

import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from liquilens_trading_copilot.agent_cli import initialize
from liquilens_trading_copilot.agent_diagnostics import diagnose_agent_host


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*_args, **_kwargs):
        pytest.fail("offline diagnostics attempted network access")

    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket, "create_connection", fail)


def snapshot(directory: Path):
    return {
        p.name: (p.stat().st_mode, hashlib.sha256(p.read_bytes()).hexdigest())
        for p in directory.iterdir()
        if p.is_file()
    }


def provision(directory: Path):
    initialize(directory)
    p = directory / "config.json"
    value = json.loads(p.read_text())
    value.update(account_id="private-synthetic-account", enabled=True)
    p.write_text(json.dumps(value))
    (directory / "paper.env").write_text(
        "ALPACA_PAPER_API_KEY=private-synthetic-key\n"
        "ALPACA_PAPER_SECRET_KEY=private-synthetic-secret\n"
        "COPILOT_PAPER_HMAC_KEY=private-synthetic-receipt-key-long-enough\n"
    )


def test_new_setup_explains_missing_account_and_credentials_without_mutation(tmp_path):
    directory = tmp_path.resolve() / "paper"
    initialize(directory)
    before = snapshot(directory)
    result = diagnose_agent_host(directory)
    assert snapshot(directory) == before
    assert result["local_configuration_ready"] is False
    assert result["execution_enabled"] is False
    blocked = {c["code"] for c in result["checks"] if c["status"] == "blocked"}
    assert blocked == {"paper_account_id_missing", "paper_credentials_missing"}
    assert result["network_accessed"] is False and result["state_modified"] is False
    assert all(c["status"] == "not_checked" for c in result["external_requirements"])


def test_configured_host_never_claims_source_or_order_eligibility(tmp_path):
    directory = tmp_path.resolve() / "paper"
    provision(directory)
    (directory / "STOP").touch()
    before = snapshot(directory)
    result = diagnose_agent_host(directory)
    assert snapshot(directory) == before
    assert result["local_configuration_ready"] is True
    assert result["execution_enabled"] is True and result["stop_active"] is True
    assert result["ready_for_order"] is False
    encoded = json.dumps(result)
    assert "private-synthetic" not in encoded
    for name in ("agent-read.token", "agent-execution.token"):
        assert (directory / name).read_text().strip() not in encoded


def test_missing_or_shared_state_is_reported_without_creating_or_chmod(tmp_path):
    directory = tmp_path.resolve() / "missing"
    result = diagnose_agent_host(directory)
    assert result["local_configuration_ready"] is False and not directory.exists()
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    result = diagnose_agent_host(directory)
    assert result["checks"][0]["status"] == "blocked"
    assert directory.stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize("failure", ["token_scope", "secret_symlink", "invalid_config"])
def test_malformed_or_privilege_mismatched_configuration_is_not_ready(
    tmp_path, failure
):
    directory = tmp_path.resolve() / "paper"
    provision(directory)
    if failure == "token_scope":
        (directory / "agent-read.token").write_text(
            (directory / "agent-execution.token").read_text()
        )
    elif failure == "secret_symlink":
        p = directory / "paper.env"
        p.rename(directory / "saved.env")
        p.symlink_to(directory / "saved.env")
    else:
        (directory / "config.json").write_text('{"enabled":true,"enabled":false}')
    result = diagnose_agent_host(directory)
    assert result["local_configuration_ready"] is False
    assert result["broker_connected"] is False and result["order_submitted"] is False


def test_doctor_cli_accepts_operator_alias_and_keeps_init_disabled(
    tmp_path, monkeypatch, capsys
):
    from liquilens_trading_copilot.agent_cli import main

    directory = tmp_path.resolve() / "paper"
    initialize(directory)
    alias = tmp_path / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    monkeypatch.setattr("sys.argv", ["host", "doctor", "--state-dir", str(alias)])
    assert main() == 2
    result = json.loads(capsys.readouterr().out)
    assert result["execution_enabled"] is False
    assert result["state_modified"] is False


def test_invalid_issuer_binding_cannot_claim_local_readiness(tmp_path):
    directory = tmp_path.resolve() / "paper"
    provision(directory)
    p = directory / "config.json"
    value = json.loads(p.read_text())
    value["issuer_endpoint"] = None
    p.write_text(json.dumps(value))
    result = diagnose_agent_host(directory)
    assert result["local_configuration_ready"] is False
    assert "invalid_execution_binding" in {c["code"] for c in result["checks"]}


@pytest.mark.parametrize("filename", ["config.json", "paper.env", "agent-read.token"])
def test_fifo_inputs_fail_promptly_instead_of_hanging(tmp_path, filename):
    directory = tmp_path.resolve() / "paper"
    provision(directory)
    path = directory / filename
    path.unlink()
    os.mkfifo(path, 0o600)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "liquilens_trading_copilot.agent_cli",
            "doctor",
            "--state-dir",
            str(directory),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["local_configuration_ready"] is False
