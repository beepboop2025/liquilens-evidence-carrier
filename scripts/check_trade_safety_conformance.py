"""Check corpus reproducibility and the dependency-free Node consumer."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(arguments: list[str]) -> None:
    completed = subprocess.run(
        arguments,
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        sys.stdout.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        raise SystemExit(completed.returncode)


def main() -> int:
    _run([sys.executable, "scripts/generate_trade_safety_conformance.py", "--check"])
    _run(["node", "--test", "integrations/typescript/test/conformance.test.mjs"])
    print("trade-safety conformance: reproducible corpus and Node guard passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
