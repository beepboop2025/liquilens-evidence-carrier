"""A credential reload must never activate the attached paper host."""

import json

import pytest

from liquilens_trading_copilot import agent_cli


def test_enabled_configuration_refused_before_credentials_application_or_journals(
    tmp_path, monkeypatch, capsys
):
    state = tmp_path.resolve()
    agent_cli.initialize(state)
    path = state / "config.json"
    config = json.loads(path.read_text())
    config["enabled"] = True
    path.write_text(json.dumps(config))
    before = {p.name: p.read_bytes() for p in state.iterdir()}

    def forbidden(*args, **kwargs):
        pytest.fail("enabled startup reached credentials, application or journal setup")

    monkeypatch.setattr(agent_cli, "load_secret_file", forbidden)
    monkeypatch.setattr(agent_cli, "create_agent_app", forbidden)
    monkeypatch.setattr(agent_cli, "configured_service", forbidden)
    monkeypatch.setattr(
        "sys.argv", ["host", "serve", "--state-dir", str(state), "--require-disabled"]
    )
    assert agent_cli.main() == 2
    assert json.loads(capsys.readouterr().out) == {
        "status": "blocked",
        "error": "private_agent_configuration_or_service_unavailable",
    }
    assert {p.name: p.read_bytes() for p in state.iterdir()} == before


def test_disabled_loaded_config_stays_disabled_after_later_file_edit(
    tmp_path, monkeypatch
):
    import uvicorn

    state = tmp_path.resolve()
    agent_cli.initialize(state)
    monkeypatch.setattr(
        agent_cli,
        "load_secret_file",
        lambda _path: {
            "ALPACA_PAPER_API_KEY": "fixture-key",
            "ALPACA_PAPER_SECRET_KEY": "fixture-secret",
            "COPILOT_PAPER_HMAC_KEY": "h" * 32,
        },
    )
    monkeypatch.setattr(agent_cli, "create_agent_app", lambda factory, **_: factory)
    monkeypatch.setattr(
        agent_cli, "configured_service", lambda config, *_, **__: config
    )
    observed = []

    def run(factory, **kwargs):
        path = state / "config.json"
        config = json.loads(path.read_text())
        config["enabled"] = True
        path.write_text(json.dumps(config))
        observed.append(factory().enabled)
        assert kwargs["host"] == "127.0.0.1"

    monkeypatch.setattr(uvicorn, "run", run)
    monkeypatch.setattr(
        "sys.argv", ["host", "serve", "--state-dir", str(state), "--require-disabled"]
    )
    assert agent_cli.main() == 0
    assert observed == [False]


@pytest.mark.parametrize("command", ["init", "doctor"])
def test_disabled_guard_is_only_valid_for_serve(command, tmp_path, monkeypatch, capsys):
    state = tmp_path.resolve()
    monkeypatch.setattr(
        "sys.argv", ["host", command, "--state-dir", str(state), "--require-disabled"]
    )
    with pytest.raises(SystemExit) as raised:
        agent_cli.main()
    assert raised.value.code == 2
    assert "--require-disabled applies only to serve" in capsys.readouterr().err
    assert list(state.iterdir()) == []
