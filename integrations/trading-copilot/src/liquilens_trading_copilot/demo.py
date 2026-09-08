"""Credential-free synthetic research examples using the real pure strategy.

This module uses only the Python standard library and the strategy module. It
does not load operator configuration, inspect credentials, construct a broker,
read or create journals, request evidence, or issue authorization receipts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from .strategy import MarketBar, PortfolioSnapshot, StrategyConfig, propose

SCENARIOS = (
    "candidate",
    "reduction",
    "stale-bars",
    "loss-halt",
    "funding-stress",
    "small-residual",
)
_NOW = datetime(2000, 1, 2, 12, tzinfo=UTC)
_REASONS = {
    "positive_momentum": (
        "The synthetic fast average exceeds the slow average enough to propose "
        "an entry within the unchanged strategy limits."
    ),
    "negative_momentum": (
        "The synthetic negative-momentum signal proposes reducing an existing "
        "holding; it does not open a short."
    ),
    "paper_proposal_requires_evidence_and_broker_authorization": (
        "A strategy candidate carries no execution permission. Independent "
        "evidence, operator and broker checks remain required."
    ),
    "stale_bars": (
        "The fixture's latest complete bar exceeds the strategy's age ceiling. "
        "Fetching or formatting a report cannot refresh an observation."
    ),
    "daily_loss_stop": (
        "The fixture's loss meets the unchanged daily-loss halt. This freezes "
        "both entries and reductions; it does not liquidate the holding."
    ),
    "seiche_regime_blocks_new_exposure": (
        "The synthetic STRESS regime blocks new exposure. The label is a demo "
        "input, not a current Seiche observation."
    ),
    "residual_below_minimum_order_notional": (
        "The synthetic holding is below the permitted $1,000 order rung. "
        "The strategy neither rounds it up nor creates a smaller order."
    ),
}
_GATES = (
    (
        "Seiche",
        "Funding observations and their source clocks, rights and scope.",
    ),
    (
        "LiquiLens",
        "Current corporate research legs and their independent observation dates.",
    ),
    (
        "Undertow",
        "Rights-approved, unexpired liquidity evidence for the exact hypothetical "
        "SELL scenario associated with the candidate.",
    ),
    (
        "Operator and broker",
        "Private paper account identity, current account constraints, STOP state, "
        "durable attempt budget and authenticated exact-order authorization.",
    ),
)


def _fixtures(
    scenario: str,
) -> tuple[list[MarketBar], PortfolioSnapshot, str]:
    """Fixed invented inputs; no downloaded or historical observations."""
    descending = scenario in {"reduction", "small-residual"}
    trend = -0.01 if descending else 0.01
    bars = [
        MarketBar(
            at=_NOW - timedelta(hours=29 - index),
            close=30_000 * math.exp(trend * index + 0.001 * math.sin(index)),
        )
        for index in range(30)
    ]
    portfolio = PortfolioSnapshot(100_000, 100_000, 0, 0, 0)
    regime = "CALM"
    if scenario == "reduction":
        portfolio = replace(portfolio, cash_usd=98_500, btc_notional_usd=1_500)
    elif scenario == "small-residual":
        portfolio = replace(portfolio, cash_usd=99_500, btc_notional_usd=500)
    elif scenario == "stale-bars":
        bars = [replace(bar, at=bar.at - timedelta(hours=3)) for bar in bars]
    elif scenario == "loss-halt":
        portfolio = replace(
            portfolio,
            cash_usd=95_000,
            equity_usd=97_000,
            btc_notional_usd=2_000,
            daily_pnl_usd=-3_000,
        )
    elif scenario == "funding-stress":
        regime = "STRESS"
    return bars, portfolio, regime


def build_demo(scenario: str = "candidate") -> dict[str, Any]:
    """Explain an unchanged strategy decision; all execution gates stay closed."""
    if scenario not in SCENARIOS:
        raise ValueError("unknown_offline_demo_scenario")
    bars, portfolio, regime = _fixtures(scenario)
    config = StrategyConfig()
    decision = propose(bars, portfolio, config, _NOW, regime)
    decision_record = asdict(decision)
    decision_record["reasons"] = list(decision.reasons)
    inputs = {
        "synthetic_now": _NOW.isoformat(),
        "bars": [{"at": bar.at.isoformat(), "close": bar.close} for bar in bars],
        "portfolio": asdict(portfolio),
        "synthetic_regime": regime,
        "strategy_config": asdict(config),
    }
    canonical = json.dumps(
        inputs, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return {
        "schema": "liquilens.copilot-offline-demo.v1",
        "mode": "offline_demo",
        "synthetic": True,
        "scenario": scenario,
        "provenance": {
            "kind": "invented_in_memory_fixture",
            "fixture_id": f"synthetic://copilot-demo/v1/{scenario}",
            "inputs_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "strategy_function": "liquilens_trading_copilot.strategy.propose",
            "observation_claim": "No live or historical market observations.",
            "clock_claim": (
                "All clocks belong to the fixed synthetic scenario; they do not "
                "describe current markets or independently verified freshness."
            ),
        },
        "inputs": inputs,
        "strategy_decision": decision_record,
        "explanation": [
            {"code": reason, "detail": _REASONS.get(reason, reason)}
            for reason in decision.reasons
        ],
        "execution": {
            "status": "blocked_in_demo",
            "order_authorized": False,
            "real_money_eligible": False,
            "receipt_issued": False,
            "order_submitted": False,
            "blockers": [
                "synthetic_inputs_are_not_execution_evidence",
                "required_evidence_not_evaluated",
                "operator_and_broker_checks_not_evaluated",
                "demo_has_no_submission_path",
            ],
        },
        "required_gates": [
            {"product": product, "status": "not_evaluated", "verify": detail}
            for product, detail in _GATES
        ],
        "countercase": (
            "An invented rising or falling price path is not strategy validation. "
            "Actual observations can be stale, restricted, missing or inconsistent; "
            "account and evidence gates can block a candidate. A HOLD may leave "
            "existing exposure open. No return, fill or profitability is established."
        ),
        "next_steps": [
            {
                "action": "Compare another offline scenario using --scenario.",
                "choices": list(SCENARIOS),
            },
            {
                "action": (
                    "For separate public research, open Market Brief and inspect "
                    "source clocks, gaps and its AI research handoff. Public "
                    "research is not order permission. This demo opens no URL."
                ),
                "url": "https://beepboop2025.github.io/market-brief/",
            },
            {
                "action": (
                    "Read the source-checkout installation and private paper "
                    "workflow before considering a separately configured runner."
                ),
                "url": (
                    "https://github.com/beepboop2025/liquilens-evidence-carrier/"
                    "tree/main/integrations/trading-copilot#private-setup-and-commands"
                ),
            },
        ],
        "limitations": [
            "Synthetic educational research only; no personalized recommendations.",
            "Product evidence stays separate; there is no combined risk score.",
            "Riptide research is display-only and does not authorize orders.",
            "No configuration, credential, account or journal is read or written.",
            "No network request, broker call, order request or receipt is created.",
        ],
    }


def format_demo_markdown(report: dict[str, Any]) -> str:
    """Portable rendering of a report produced by ``build_demo``."""
    decision = report["strategy_decision"]
    amount = decision["notional_usd"]
    notional = f"${amount:,.2f} USD" if amount is not None else "not proposed"
    lines = [
        "# LiquiLens copilot — synthetic offline demo",
        "",
        "**SYNTHETIC INPUTS. NO ORDER PERMISSION. NO NETWORK OR BROKER CALLS.**",
        "",
        f"Scenario: `{report['scenario']}`",
        f"Fixed synthetic clock: `{report['inputs']['synthetic_now']}`",
        f"Strategy result: **{decision['action'].upper()}**",
        f"Candidate notional: {notional}",
        "Execution: **BLOCKED IN DEMO**; no authorization or receipt is issued.",
        "",
        "## Why the strategy reached this result",
        "",
    ]
    for item in report["explanation"]:
        lines.append(f"- `{item['code']}`: {item['detail']}")
    lines.extend(["", "## Required checks remain unevaluated", ""])
    for gate in report["required_gates"]:
        lines.append(f"- **{gate['product']}** — {gate['verify']}")
    lines.extend(["", "## Countercase and limits", "", report["countercase"], ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.extend(["", "## Continue researching", ""])
    for step in report["next_steps"]:
        lines.append(f"- {step['action']}")
        if "url" in step:
            lines.append(f"  {step['url']}")
        if "choices" in step:
            lines.append("  " + ", ".join(f"`{item}`" for item in step["choices"]))
    lines.extend(
        [
            "",
            "## Complete synthetic inputs and result",
            "",
            "This JSON is a demo report, not a trading request or safety receipt.",
            "",
            "```json",
            json.dumps(report, indent=2, sort_keys=True, allow_nan=False),
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    """A standard-library-only entry point for an uninstalled source checkout."""
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--scenario", choices=SCENARIOS)
    selection.add_argument(
        "--all", action="store_true", help="export all scenarios as a JSON pack"
    )
    parser.add_argument(
        "--source-ref",
        help="--all only: verify sources against a full local Git commit SHA",
    )
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()
    if args.all:
        if args.format != "json":
            parser.error("--all exports JSON only")
        from .demo_pack import build_demo_pack, serialize_demo_pack

        try:
            print(serialize_demo_pack(build_demo_pack(args.source_ref)), end="")
        except (OSError, ValueError) as error:
            parser.error(str(error))
        return 0
    if args.source_ref is not None:
        parser.error("--source-ref requires --all")
    report = build_demo(args.scenario or "candidate")
    if args.format == "markdown":
        print(format_demo_markdown(report), end="")
    else:
        print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
