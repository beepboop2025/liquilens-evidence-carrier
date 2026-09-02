from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from liquilens_evidence.trade_safety import (
    issue_trade_safety_receipt,
    trade_safety_request_hash,
    verify_trade_safety_receipt,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "trade-safety"
EVALUATED_AT = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def _load(name: str) -> dict[str, Any]:
    value = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_golden_paper_receipt_reissues_and_verifies_cross_language() -> None:
    request = _load("request.paper.json")
    expected = _load("receipt.paper.pass.json")

    assert trade_safety_request_hash(request) == expected["request_hash"]
    actual = issue_trade_safety_receipt(
        request=request,
        evidence=_load("evidence.paper.json"),
        policy=_load("policy.paper.json"),
        broker_preview=_load("broker-preview.paper.json"),
        issuer=_load("issuer.paper.json"),
        evaluated_at=EVALUATED_AT,
    )
    assert actual == expected

    verified = verify_trade_safety_receipt(
        expected,
        evaluated_at=datetime(2026, 9, 2, 12, 0, 30, tzinfo=UTC),
    )
    assert verified.outcome.value == "pass"
    assert verified.authenticated is False
    assert verified.receipt["authority"]["can_execute"] is False

    node = subprocess.run(
        [
            "node",
            str(ROOT / "protocol" / "verify_hash_tree_v1.mjs"),
            "--artifact",
            "trade-safety-receipt",
            str(EXAMPLES / "receipt.paper.pass.json"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert node.returncode == 0, node.stderr
    result = json.loads(node.stdout)
    assert result["ok"] is True
    assert result["digest"] == expected["record_hash"]
    assert result["id_matches"] is True
