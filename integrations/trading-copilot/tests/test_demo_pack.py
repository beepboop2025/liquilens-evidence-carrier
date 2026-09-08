"""Browser packs preserve the actual Python strategy and explicit demo limits."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from liquilens_trading_copilot import demo_pack
from liquilens_trading_copilot.demo import SCENARIOS, build_demo
from liquilens_trading_copilot.strategy import StrategyConfig


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def test_pack_contains_exact_existing_reports_and_unchanged_policy_defaults() -> None:
    pack = demo_pack.build_demo_pack()
    assert pack["schema"] == "liquilens.copilot-demo-pack.v1"
    assert pack["synthetic"] is True
    assert pack["mode"] == "offline_demo"
    assert pack["scenario_count"] == 6
    assert pack["scenario_order"] == list(SCENARIOS)
    assert pack["scenarios"] == [build_demo(scenario) for scenario in SCENARIOS]
    assert pack["policy_limits"]["strategy_config"] == asdict(StrategyConfig())
    assert pack["policy_limits"]["execution_policy_evaluated"] is False
    flags = (
        "order_authorized",
        "real_money_eligible",
        "receipt_issued",
        "order_submitted",
    )
    for flag in flags:
        assert pack["execution"][flag] is False
    for report in pack["scenarios"]:
        assert report["synthetic"] is True
        assert all(report["execution"][flag] is False for flag in flags)
        assert all(
            gate["status"] == "not_evaluated" for gate in report["required_gates"]
        )


def test_hashes_cover_real_source_bytes_and_all_synthetic_inputs() -> None:
    pack = demo_pack.build_demo_pack()
    provenance = pack["provenance"]
    source = Path(demo_pack.__file__).parent
    digests = {
        name: hashlib.sha256((source / name).read_bytes()).hexdigest()
        for name in ("strategy.py", "demo.py", "demo_pack.py")
    }
    assert provenance["strategy_sha256"] == digests["strategy.py"]
    assert provenance["source_files"] == {
        f"integrations/trading-copilot/src/liquilens_trading_copilot/{name}": digest
        for name, digest in digests.items()
    }
    dataset = {report["scenario"]: report["inputs"] for report in pack["scenarios"]}
    assert (
        provenance["dataset_sha256"] == hashlib.sha256(canonical(dataset)).hexdigest()
    )
    generator = {name: digests[name] for name in ("demo.py", "demo_pack.py")}
    assert (
        provenance["generator_sha256"]
        == hashlib.sha256(canonical(generator)).hexdigest()
    )
    dataset["candidate"]["bars"][0]["close"] += 1
    assert (
        provenance["dataset_sha256"] != hashlib.sha256(canonical(dataset)).hexdigest()
    )
    assert provenance["source_ref"] is None
    assert provenance["source_url"] is None
    assert provenance["source_verified"] is False
    assert provenance["source_verification"] == "unversioned_local_sources"


def test_output_is_bounded_deterministic_and_immune_to_prior_result_mutation() -> None:
    first = demo_pack.build_demo_pack()
    encoded = demo_pack.serialize_demo_pack(first)
    assert encoded == demo_pack.serialize_demo_pack(demo_pack.build_demo_pack())
    assert encoded.encode() == canonical(first) + b"\n"
    assert len(encoded.encode()) < 256 * 1024
    assert json.loads(encoded) == first
    first["scenarios"][0]["inputs"]["bars"][0]["close"] = 0
    first["policy_limits"]["strategy_config"]["max_portfolio_exposure"] = 1
    assert encoded == demo_pack.serialize_demo_pack(demo_pack.build_demo_pack())
    with pytest.raises(ValueError, match="256_kib"):
        demo_pack.serialize_demo_pack({"excessive": "x" * (256 * 1024)})


@pytest.fixture
def source_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    root = tmp_path / "source"
    package = root / "integrations/trading-copilot/src/liquilens_trading_copilot"
    package.mkdir(parents=True)
    for name, raw in demo_pack._sources().items():
        (package / name).write_bytes(raw)
    environment = {
        "PATH": os.defpath,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Synthetic test",
        "GIT_AUTHOR_EMAIL": "synthetic@example.test",
        "GIT_COMMITTER_NAME": "Synthetic test",
        "GIT_COMMITTER_EMAIL": "synthetic@example.test",
    }

    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(root), *args],
            env=environment,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()

    git("init", "-q")
    git("add", ".")
    git(
        "-c",
        "commit.gpgsign=false",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-qm",
        "Synthetic source",
    )
    monkeypatch.setattr(demo_pack, "__file__", str(package / "demo_pack.py"))
    return git("rev-parse", "HEAD")


def test_verified_pack_pins_matching_actual_sources_to_exact_commit(
    source_commit: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_run = subprocess.run
    executions = []

    def inspect_run(*args: object, **kwargs: object):
        executions.append((args, kwargs))
        return original_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", inspect_run)
    monkeypatch.setenv("GIT_SSH_COMMAND", "MUST_NOT_EXECUTE")
    monkeypatch.setenv("RAILWAY_TOKEN", "MUST_NOT_EXPORT")
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "MUST_NOT_EXPORT")
    pack = demo_pack.build_demo_pack(source_commit)
    provenance = pack["provenance"]
    assert provenance["source_verified"] is True
    assert provenance["source_ref"] == source_commit
    assert provenance["source_url"] == (
        "https://github.com/beepboop2025/liquilens-evidence-carrier/blob/"
        f"{source_commit}/integrations/trading-copilot/src/liquilens_trading_copilot/strategy.py"
    )
    assert len(executions) == 3
    for args, kwargs in executions:
        assert "protocol.allow=never" in args[0]
        assert kwargs["env"]["GIT_NO_LAZY_FETCH"] == "1"
        assert kwargs["env"]["GIT_NO_REPLACE_OBJECTS"] == "1"
        assert not any(
            name in kwargs["env"]
            for name in ("GIT_SSH_COMMAND", "RAILWAY_TOKEN", "ALPACA_PAPER_API_KEY")
        )
        assert kwargs.get("shell", False) is False
    assert "MUST_NOT" not in demo_pack.serialize_demo_pack(pack)


@pytest.mark.parametrize("name", ["strategy.py", "demo.py", "demo_pack.py"])
def test_source_ref_never_attests_different_strategy_or_generator_bytes(
    source_commit: str, name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = demo_pack._sources()
    sources[name] += b"\n# Different local source\n"
    monkeypatch.setattr(demo_pack, "_sources", lambda: sources)
    with pytest.raises(ValueError, match="do_not_match_commit"):
        demo_pack.build_demo_pack(source_commit)


@pytest.mark.parametrize(
    "reference", ["HEAD", "main", "a" * 39, "A" * 40, "--help", "https://example.test/"]
)
def test_invalid_source_references_fail_without_running_git(
    reference: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *_args, **_kwargs: pytest.fail("Git must not run")
    )
    with pytest.raises(ValueError, match="full_commit_sha"):
        demo_pack.build_demo_pack(reference)


def test_standalone_pack_imports_no_services_reads_no_private_files_and_uses_no_network(
    tmp_path: Path,
) -> None:
    source = Path(__file__).resolve().parents[1] / "src"
    script = """
import json, pathlib, runpy, socket, subprocess, sys
original_open = pathlib.Path.open
def forbidden(*args, **kwargs):
    raise AssertionError('forbidden network, secret or subprocess operation')
def source_only(path, *args, **kwargs):
    assert path.name in {'strategy.py', 'demo.py', 'demo_pack.py'}
    assert path.parent.name == 'liquilens_trading_copilot'
    assert args == ('rb',) or (not args and kwargs.get('mode') == 'rb')
    return original_open(path, *args, **kwargs)
pathlib.Path.open = source_only
socket.socket = forbidden
socket.create_connection = forbidden
subprocess.run = forbidden
sys.argv = ['demo', '--all']
try:
    runpy.run_module('liquilens_trading_copilot.demo', run_name='__main__')
except SystemExit as result:
    assert result.code == 0
assert not any(name in sys.modules for name in (
    'httpx','alpaca','liquilens_trading_copilot.config',
    'liquilens_trading_copilot.runner','liquilens_trading_copilot.state',
    'liquilens_trading_copilot.scoped','liquilens_trading_copilot.evidence'))
"""
    result = subprocess.run(
        [sys.executable, "-S", "-B", "-c", script],
        env={
            "PATH": os.defpath,
            "PYTHONPATH": str(source),
            "ALPACA_PAPER_API_KEY": "DO_NOT_EXPORT",
        },
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == demo_pack.build_demo_pack()
    assert "DO_NOT_EXPORT" not in result.stdout
    assert result.stderr == ""
    assert list(tmp_path.iterdir()) == []


def test_standalone_all_and_dedicated_export_agree_and_reject_ambiguous_options(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from liquilens_trading_copilot import demo

    monkeypatch.setattr(sys, "argv", ["demo", "--all"])
    assert demo.main() == 0
    first = capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["demo_pack"])
    assert demo_pack.main() == 0
    assert capsys.readouterr().out == first
    for argv in (
        ["demo", "--all", "--scenario", "candidate"],
        ["demo", "--all", "--format", "markdown"],
        ["demo", "--source-ref", "a" * 40],
    ):
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as error:
            demo.main()
        assert error.value.code == 2
