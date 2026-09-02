from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

from liquilens_evidence.canonical import canonical_hash_bytes

ROOT = Path(__file__).resolve().parents[1]


def _receipt() -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema": "liquilens.trade-safety-receipt.v1",
        "canonicalization": "liquilens-hash-tree-v1",
        "receipt_id": "trade_safety_" + "0" * 24,
        "record_hash": "0" * 64,
        "request": {"request_id": "order-7", "notional": 25_000.0},
        "decision": {"outcome": "hold"},
        "integrity": {
            "profile": "hmac-sha256",
            "key_id": "tenant-key-1",
            "signature": "f" * 64,
        },
    }
    payload = copy.deepcopy(receipt)
    payload.pop("receipt_id")
    payload.pop("record_hash")
    assert isinstance(payload["integrity"], dict)
    payload["integrity"]["signature"] = None
    digest = hashlib.sha256(canonical_hash_bytes(payload)).hexdigest()
    receipt["receipt_id"] = f"trade_safety_{digest[:24]}"
    receipt["record_hash"] = digest
    return receipt


def _verify(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "node",
            str(ROOT / "protocol" / "verify_hash_tree_v1.mjs"),
            "--artifact",
            "trade-safety-receipt",
            str(path),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_javascript_verifier_matches_python_trade_safety_identity(
    tmp_path: Path,
) -> None:
    receipt = _receipt()
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")

    verified = _verify(path)

    assert verified.returncode == 0, verified.stderr
    result = json.loads(verified.stdout)
    assert result["ok"] is True
    assert result["digest"] == receipt["record_hash"]
    assert result["id_matches"] is True


def test_javascript_verifier_ignores_signature_but_rejects_content_tamper(
    tmp_path: Path,
) -> None:
    receipt = _receipt()
    assert isinstance(receipt["integrity"], dict)
    receipt["integrity"]["signature"] = "a" * 64
    signature_path = tmp_path / "signature.json"
    signature_path.write_text(json.dumps(receipt), encoding="utf-8")
    assert _verify(signature_path).returncode == 0

    assert isinstance(receipt["decision"], dict)
    receipt["decision"]["outcome"] = "pass"
    tampered_path = tmp_path / "tampered.json"
    tampered_path.write_text(json.dumps(receipt), encoding="utf-8")
    tampered = _verify(tampered_path)
    assert tampered.returncode == 1
    assert json.loads(tampered.stdout)["ok"] is False
