"""Riptide research cannot silently acquire paper-order authority."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from test_scoped_pipeline import SyntheticSDK, bars, scoped_pipeline


def research_context(band: str = "ACUTE") -> dict:
    return {
        "schema": "liquilens.riptide-research-context.v1",
        "financial_authority": "none",
        "influences_order_decision": False,
        "risk": {"state": "available", "band": band},
        "events": {"state": "unavailable", "reason": "quarantined"},
    }


@pytest.mark.parametrize("band", ["CALM", "ACUTE", "unavailable"])
def test_research_does_not_become_btc_signal(tmp_path: Path, band: str) -> None:
    async def run() -> None:
        sdk = SyntheticSDK()
        context = research_context(band)
        async with scoped_pipeline(tmp_path / "operator", sdk) as pipeline:
            result = await pipeline.runner.cycle(
                bars("buy"), seiche_regime="CALM", riptide_context=context
            )
            assert result["status"] == "submitted"
            assert result["decision"]["action"] == "buy"
            assert len(sdk.orders) == 1
            assert result["riptide_research"] == context
            assert set(result["receipt"]["evidence"]) == {
                "seiche",
                "liquilens",
                "undertow",
            }
            context["risk"]["band"] = "changed_after_capture"
            assert result["riptide_research"]["risk"]["band"] == band

    asyncio.run(run())


def test_calm_research_does_not_clear_undertow_refusal(tmp_path: Path) -> None:
    async def run() -> None:
        sdk = SyntheticSDK()
        async with scoped_pipeline(
            tmp_path / "operator", sdk, native_fault="rights_manifest_not_approved"
        ) as pipeline:
            result = await pipeline.runner.cycle(
                bars("buy"),
                seiche_regime="CALM",
                riptide_context=research_context("CALM"),
            )
            assert result["status"] == "blocked"
            assert result["receipt"]["decision"]["outcome"] == "unavailable"
            assert sdk.orders == []

    asyncio.run(run())


@pytest.mark.parametrize(
    ("field", "value"),
    [("financial_authority", "trading"), ("influences_order_decision", True)],
)
def test_research_authority_mismatch_never_reaches_broker(
    tmp_path: Path, field: str, value: object
) -> None:
    async def run() -> None:
        sdk = SyntheticSDK()
        context = research_context()
        context[field] = value
        async with scoped_pipeline(tmp_path / "operator", sdk) as pipeline:
            with pytest.raises(ValueError, match="riptide_research_authority_invalid"):
                await pipeline.runner.cycle(
                    bars("buy"), seiche_regime="CALM", riptide_context=context
                )
            assert sdk.orders == []

    asyncio.run(run())


def test_research_cli_needs_no_broker_or_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from liquilens_trading_copilot import cli, riptide

    async def captured_context() -> dict:
        return research_context()

    def forbidden_credentials(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("research must not request paper credentials")

    monkeypatch.setattr(riptide, "collect_riptide_context", captured_context)
    monkeypatch.setattr(cli.PaperCredentials, "from_environment", forbidden_credentials)
    monkeypatch.setattr("sys.argv", ["liquilens-trading-copilot", "research"])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out) == research_context()
