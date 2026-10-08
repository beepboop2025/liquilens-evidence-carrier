"""Doctor validates local setup without asserting financial eligibility."""

import json
import socket
from dataclasses import asdict

import pytest
from test_live_connector import KEY, Broker, bundle, lane
from test_live_journal import fingerprints

from liquilens_trading_copilot import live_cli
from liquilens_trading_copilot.live_diagnostics import live_readiness


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("offline readiness attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(live_cli, "AlpacaLiveTransport", forbidden)


def test_fresh_init_doctor_reports_unprovisioned_state_without_mutation(tmp_path):
    live_cli.initialize(tmp_path.resolve())
    before = fingerprints(tmp_path)
    report = live_readiness(tmp_path.resolve())
    assert report["network_accessed"] is False
    assert report["live_ready"] is False
    assert report["checks"]["limits"] is True
    assert report["activation_configured"] is False
    assert "broker_credentials_not_provisioned" in report["local_reason_codes"]
    assert "live_binding_missing_or_invalid" in report["local_reason_codes"]
    assert report["journal_present"] is False
    assert report["unresolved_count"] is None
    assert fingerprints(tmp_path) == before


def test_configured_keys_and_activation_never_imply_live_qualification(tmp_path):
    live_cli.initialize(tmp_path.resolve())
    request, receipt, binding = bundle()
    config_path = tmp_path / "live-config.json"
    config = json.loads(config_path.read_text())
    config.update(
        binding=asdict(binding),
        live_enabled=True,
        **{"activation_acknowledgment": "LIVE-ACCOUNT:" + binding.account_id},
    )
    config_path.write_text(json.dumps(config))
    secrets = {
        "api_key": "PRIVATE_API_KEY",
        "secret_key": "PRIVATE_SECRET",
        "receipt_hmac_key_hex": KEY.hex(),
    }
    (tmp_path / "live-secrets.json").write_text(json.dumps(secrets))
    broker = Broker()
    connector = lane(tmp_path, binding, broker)
    connector.submit(request, receipt)
    connector.broker.close()
    before = fingerprints(tmp_path)
    report = live_readiness(tmp_path.resolve())
    assert all(report["checks"].values())
    assert report["activation_configured"] is True
    assert report["unresolved_count"] == 1
    assert report["live_ready"] is False
    assert (
        "alpaca_limit_order_broker_preview_unavailable"
        in report["external_requirements"]
    )
    assert "unresolved_order_blocks_submission" in report["local_reason_codes"]
    rendered = json.dumps(report)
    assert "PRIVATE" not in rendered and KEY.hex() not in rendered
    assert binding.account_id not in rendered
    assert fingerprints(tmp_path) == before


@pytest.mark.parametrize("failure", ["permissions", "malformed", "stop"])
def test_doctor_reports_local_failures_without_repair(tmp_path, failure):
    live_cli.initialize(tmp_path.resolve())
    if failure == "permissions":
        (tmp_path / "live-secrets.json").chmod(0o644)
    elif failure == "malformed":
        (tmp_path / "live-config.json").write_text('{"bad":"SECRET"')
    else:
        (tmp_path / "STOP").touch()
    before = fingerprints(tmp_path)
    report = live_readiness(tmp_path.resolve())
    expected = {
        "permissions": "live_secrets_missing_or_invalid",
        "malformed": "live_configuration_missing_or_invalid",
        "stop": "stop_present",
    }[failure]
    assert expected in report["local_reason_codes"]
    assert "SECRET" not in json.dumps(report)
    assert fingerprints(tmp_path) == before


def test_missing_state_doctor_does_not_initialize_it(tmp_path):
    path = tmp_path / "not-initialized"
    result = live_readiness(path.resolve())
    assert "local_state_unavailable" in result["local_reason_codes"]
    assert not path.exists()


def test_doctor_cli_uses_nonzero_readiness_exit_without_contacting_broker(
    tmp_path, monkeypatch, capsys
):
    live_cli.initialize(tmp_path.resolve())
    monkeypatch.setattr(
        "sys.argv", ["liquilens-live", "doctor", "--state-dir", str(tmp_path)]
    )
    assert live_cli.main() == 2
    assert json.loads(capsys.readouterr().out)["live_ready"] is False
