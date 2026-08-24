"""Run the verifier directly from a checked-out composite action."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


if __name__ == "__main__":
    cli = importlib.import_module("liquilens_evidence.evidence_cli")
    raise SystemExit(cli.main(["verify", *sys.argv[1:]]))
