"""Offline activation must never inspect private state or enter order paths."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pytest

from liquilens_trading_copilot import cli
from liquilens_trading_copilot.demo import (
    SCENARIOS,
    build_demo,
    format_demo_markdown,
)
from liquilens_trading_copilot.strategy import (
    MarketBar,
    PortfolioSnapshot,
    StrategyConfig,
    propose,
)


@pytest.mark.parametrize(
    ("scenario", "action", "reason"),
    [
        ("candidate", "buy", "positive_momentum"),
        ("reduction", "sell", "negative_momentum"),
        ("stale-bars", "hold", "stale_bars"),
        ("loss-halt", "hold", "daily_loss_stop"),
        ("funding-stress", "hold", "seiche_regime_blocks_new_exposure"),
        ("small-residual", "hold", "residual_below_minimum_order_notional"),
    ],
)
def test_scenarios_use_the_unchanged_strategy_and_never_authorize(
    scenario: str, action: str, reason: str
) -> None:
    report = build_demo(scenario)
    decision = report["strategy_decision"]
    assert decision["action"] == action
    assert reason in decision["reasons"]
    inputs = report["inputs"]
    reproduced = propose(
        [
            MarketBar(datetime.fromisoformat(bar["at"]), bar["close"])
            for bar in inputs["bars"]
        ],
        PortfolioSnapshot(**inputs["portfolio"]),
        StrategyConfig(**inputs["strategy_config"]),
        datetime.fromisoformat(inputs["synthetic_now"]),
        inputs["synthetic_regime"],
    )
    assert decision == json.loads(json.dumps(asdict(reproduced)))
    assert inputs["strategy_config"] == asdict(StrategyConfig())
    assert report["execution"]["status"] == "blocked_in_demo"
    for field in (
        "order_authorized",
        "real_money_eligible",
        "receipt_issued",
        "order_submitted",
    ):
        assert report["execution"][field] is False
    assert all(gate["status"] == "not_evaluated" for gate in report["required_gates"])
    assert decision["notional_usd"] == (None if action == "hold" else 1000)
    assert all(item["detail"] != item["code"] for item in report["explanation"])


def test_synthetic_provenance_is_explicit_portable_and_reproducible() -> None:
    first = build_demo()
    assert first == build_demo()
    assert first == json.loads(json.dumps(first, allow_nan=False))
    assert first["schema"] == "liquilens.copilot-offline-demo.v1"
    assert first["synthetic"] is True
    assert first["mode"] == "offline_demo"
    assert first["provenance"]["fixture_id"].startswith("synthetic://")
    canonical = json.dumps(
        first["inputs"], sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    assert (
        first["provenance"]["inputs_sha256"]
        == hashlib.sha256(canonical.encode()).hexdigest()
    )
    assert "2000-01-02" in first["inputs"]["synthetic_now"]
    first["inputs"]["strategy_config"]["max_portfolio_exposure"] = 0.25
    assert build_demo()["inputs"]["strategy_config"]["max_portfolio_exposure"] == 0.10
    assert "not strategy validation" in first["countercase"]
    assert "https://beepboop2025.github.io/market-brief/" in json.dumps(first)


def test_markdown_contains_the_complete_json_and_explains_blockers() -> None:
    report = build_demo("small-residual")
    markdown = format_demo_markdown(report)
    assert "SYNTHETIC INPUTS. NO ORDER PERMISSION" in markdown
    assert "Strategy result: **HOLD**" in markdown
    assert "below the permitted $1,000 order rung" in markdown
    assert "Required checks remain unevaluated" in markdown
    assert "Countercase and limits" in markdown
    embedded = markdown.split("```json\n")[1].split("\n```", 1)[0]
    assert json.loads(embedded) == report
    assert len(markdown.encode()) < 32_768


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_cli_demo_never_loads_secrets_state_clients_or_order_pipeline(
    scenario: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("offline demo entered private, network or execution code")

    for name in (
        "load_config",
        "load_secret_file",
        "CopilotConfig",
        "prepare_state",
        "operator_lock",
        "CycleStore",
        "run_configured_cycle",
        "collect_readiness",
    ):
        monkeypatch.setattr(cli, name, forbidden)
    monkeypatch.setattr(cli.PaperCredentials, "from_environment", forbidden)
    monkeypatch.setattr("httpx.Client.__init__", forbidden)
    monkeypatch.setattr("httpx.AsyncClient.__init__", forbidden)
    monkeypatch.setattr("socket.socket", forbidden)
    monkeypatch.setattr(sys, "argv", ["copilot", "demo", "--scenario", scenario])
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "PRIVATE_DEMO_SENTINEL")
    monkeypatch.setenv("COPILOT_PAPER_HMAC_KEY", "PRIVATE_DEMO_SENTINEL")
    assert cli.main() == 0
    emitted = capsys.readouterr().out
    assert json.loads(emitted) == build_demo(scenario)
    assert "PRIVATE_DEMO_SENTINEL" not in emitted


@pytest.mark.parametrize("flag", ["--config", "--env-file", "--state-dir"])
def test_demo_rejects_private_paths_before_reading_or_writing(
    flag: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    target = tmp_path / "must-not-exist"
    monkeypatch.setattr(sys, "argv", ["copilot", "demo", flag, str(target)])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert "does not accept operator configuration or state" in capsys.readouterr().err
    assert not target.exists()


def test_cli_markdown_and_invalid_demo_options(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(sys, "argv", ["copilot", "demo", "--format", "markdown"])
    assert cli.main() == 0
    assert capsys.readouterr().out == format_demo_markdown(build_demo())
    for args in (
        ["copilot", "demo", "--scenario", "live"],
        ["copilot", "once", "--format", "markdown"],
        ["copilot", "research", "--scenario", "candidate"],
    ):
        monkeypatch.setattr(sys, "argv", args)
        with pytest.raises(SystemExit) as error:
            cli.main()
        assert error.value.code == 2
    with pytest.raises(ValueError, match="unknown_offline_demo_scenario"):
        build_demo("live")


def test_standalone_demo_works_without_installed_dependencies_or_private_files(
    tmp_path: Path,
) -> None:
    source = Path(__file__).resolve().parents[1] / "src"
    # -S removes site-packages. No broker/config/client modules may be imported;
    # network and Python-level file reads are forbidden after harness startup.
    script = """
import builtins, pathlib, runpy, socket, sys
def forbidden(*args, **kwargs):
    raise AssertionError('offline demo attempted I/O')
socket.socket = forbidden
socket.create_connection = forbidden
builtins.open = forbidden
pathlib.Path.open = forbidden
sys.argv = ['demo', '--scenario', 'candidate']
try:
    runpy.run_module('liquilens_trading_copilot.demo', run_name='__main__')
except SystemExit as result:
    assert result.code == 0
assert not any(name in sys.modules for name in (
    'httpx', 'alpaca', 'liquilens_trading_copilot.config',
    'liquilens_trading_copilot.runner', 'liquilens_trading_copilot.state'))
"""
    result = subprocess.run(
        [sys.executable, "-S", "-B", "-c", script],
        env={"PATH": os.defpath, "PYTHONPATH": str(source)},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == build_demo()
    assert result.stderr == ""
    assert list(tmp_path.iterdir()) == []
