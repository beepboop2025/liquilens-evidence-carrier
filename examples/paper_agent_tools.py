"""Offline agent-tool walkthrough: synthetic evidence and an in-process broker.

Run with the locked alpaca-paper environment. No credentials or network calls.
The temporary journal is created below an explicitly supplied scratch directory.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from alpaca.common.enums import BaseURL
from liquilens_alpaca_paper import (
    AlpacaPaperAgentTools,
    SQLiteAlpacaPaperSubmissionJournal,
)

from liquilens_evidence import TradeSafetyExecutionBinding, issue_trade_safety_receipt

FIXTURES = Path(__file__).resolve().parent / "trade-safety"
CLOCK = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
SYNTHETIC_KEY = b"offline-agent-tool-demo-not-a-real-key"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class SyntheticBroker:
    _base_url = BaseURL.TRADING_PAPER
    _sandbox = True

    def __init__(self, *, timeout: bool) -> None:
        self.timeout = timeout
        self.submissions = 0

    def get_account(self) -> dict[str, str]:
        return {"id": "example-paper-account"}

    def submit_order(self, order_data: Any) -> dict[str, str]:
        self.submissions += 1
        if self.timeout:
            raise TimeoutError("synthetic timeout after possible acceptance")
        return self.get_order_by_client_id(order_data.client_order_id)

    def get_order_by_client_id(self, client_id: str) -> dict[str, str]:
        return {"id": "synthetic-paper-order", "client_order_id": client_id}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-dir", required=True, type=Path)
    parser.add_argument("--timeout", action="store_true")
    args = parser.parse_args()
    args.scratch_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    request = fixture("request.paper.json")
    receipt = issue_trade_safety_receipt(
        request=request,
        evidence=fixture("evidence.paper.json"),
        policy=fixture("policy.paper.json"),
        broker_preview=fixture("broker-preview.paper.json"),
        evaluated_at=CLOCK,
        issuer=fixture("issuer.paper.json"),
        ttl_seconds=60,
        hmac_key=SYNTHETIC_KEY,
        hmac_key_id="synthetic-demo-key",
    )
    binding = TradeSafetyExecutionBinding(
        **{
            key: request["agent"][key]
            for key in (
                "account_id",
                "tenant_id",
                "operator_id",
                "agent_id",
                "runtime",
                "strategy_id",
            )
        },
        policy_id=receipt["policy"]["policy_id"],
        policy_version=receipt["policy"]["version"],
        policy_hash=receipt["policy_hash"],
        issuer_name=receipt["issuer"]["name"],
        issuer_version=receipt["issuer"]["version"],
        issuer_endpoint=receipt["issuer"]["endpoint"],
        hmac_key_id="synthetic-demo-key",
    )
    broker = SyntheticBroker(timeout=args.timeout)
    steps = []
    with (
        TemporaryDirectory(
            dir=args.scratch_dir, prefix="paper-agent-tools-"
        ) as directory,
        SQLiteAlpacaPaperSubmissionJournal(
            Path(directory) / "journal.sqlite3",
            clock=lambda: CLOCK + timedelta(seconds=30),
        ) as journal,
    ):
        tools = AlpacaPaperAgentTools(
            binding=binding,
            submission_journal=journal,
            hmac_key=SYNTHETIC_KEY,
            clock=lambda: CLOCK + timedelta(seconds=30),
            execution_enabled=lambda: True,
            _client_factory=lambda **kwargs: broker,
        )
        steps.append({"operation": "tools/list", "result": tools.list_tools()})
        for operation, arguments in (
            ("submit_paper_order", {"request": request, "receipt": receipt}),
            ("submit_paper_order", {"request": request, "receipt": receipt}),
            ("paper_order_status", {"request": request}),
            ("reconcile_paper_order", {"request": request}),
        ):
            result = tools.call_tool(operation, arguments)
            steps.append({"operation": operation, "result": result})
    print(
        json.dumps(
            {
                "schema": "liquilens.paper-agent-tools-demo.v1",
                "verification_only": True,
                "broker_contacted": False,
                "evidence": "synthetic_fixtures",
                "synthetic_submissions": broker.submissions,
                "steps": steps,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
