"""Deterministic synthetic reports for a read-only public browser experience.

Only this package's Python sources are read. An explicit source reference also
permits local Git blob reads with credential/config inheritance and lazy network
fetching disabled. No operator, evidence-service, account or broker is imported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path
from typing import Any

from .demo import SCENARIOS, build_demo
from .strategy import StrategyConfig

PACK_SCHEMA = "liquilens.copilot-demo-pack.v1"
_REPOSITORY = "https://github.com/beepboop2025/liquilens-evidence-carrier"
_SOURCE_PREFIX = "integrations/trading-copilot/src/liquilens_trading_copilot"
_SOURCE_NAMES = ("strategy.py", "demo.py", "demo_pack.py")
_MAX_PACK_BYTES = 256 * 1024
_CANONICALIZATION = (
    "UTF-8 Python json.dumps(sort_keys=True,separators=(',',':'),"
    "ensure_ascii=True,allow_nan=False), without trailing newline"
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _sources() -> dict[str, bytes]:
    """Read an explicit package-source allowlist, including from a wheel."""
    package = files("liquilens_trading_copilot")
    result = {}
    for name in _SOURCE_NAMES:
        raw = package.joinpath(name).read_bytes()
        if not raw or len(raw) > 1_048_576:
            raise ValueError("demo_pack_source_size_invalid")
        result[name] = raw
    return result


def _verify_source_ref(source_ref: str, sources: dict[str, bytes]) -> None:
    if not isinstance(source_ref, str) or not re.fullmatch(r"[0-9a-f]{40}", source_ref):
        raise ValueError("demo_pack_source_ref_requires_full_commit_sha")
    root = Path(__file__).resolve().parents[4]
    if not (root / ".git").exists():
        raise ValueError("demo_pack_source_verification_requires_source_checkout")
    # Never inherit Git credentials, custom SSH commands, injected configuration
    # or replacement objects. Missing partial-clone blobs must fail without fetch.
    environment = {
        "PATH": os.defpath,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }
    for name, raw in sources.items():
        try:
            result = subprocess.run(
                [
                    "git",
                    "--no-pager",
                    "--literal-pathspecs",
                    "-c",
                    "protocol.allow=never",
                    "-C",
                    str(root),
                    "show",
                    "--no-textconv",
                    f"{source_ref}:{_SOURCE_PREFIX}/{name}",
                ],
                env=environment,
                capture_output=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError("demo_pack_source_verification_unavailable") from error
        if result.returncode != 0 or result.stdout != raw:
            raise ValueError("demo_pack_sources_do_not_match_commit")


def build_demo_pack(source_ref: str | None = None) -> dict[str, Any]:
    """Build all six reports; a supplied revision must match actual source bytes.

    Without a revision the pack is explicitly unverified and unsuitable for a
    publisher requiring pinned source provenance. No wall clock or environment
    value contributes to any report or hash.
    """
    source_bytes = _sources()
    if source_ref is not None:
        _verify_source_ref(source_ref, source_bytes)
    source_hashes = {
        name: hashlib.sha256(raw).hexdigest() for name, raw in source_bytes.items()
    }
    scenarios = [build_demo(scenario) for scenario in SCENARIOS]
    dataset = {report["scenario"]: report["inputs"] for report in scenarios}
    generator = {name: source_hashes[name] for name in ("demo.py", "demo_pack.py")}
    pack = {
        "schema": PACK_SCHEMA,
        "mode": "offline_demo",
        "synthetic": True,
        "scenario_count": len(SCENARIOS),
        "scenario_order": list(SCENARIOS),
        "scenarios": scenarios,
        "provenance": {
            "source_ref": source_ref,
            "source_verified": source_ref is not None,
            "source_url": (
                f"{_REPOSITORY}/blob/{source_ref}/{_SOURCE_PREFIX}/strategy.py"
                if source_ref is not None
                else None
            ),
            "source_verification": (
                "actual_package_sources_match_local_git_commit"
                if source_ref is not None
                else "unversioned_local_sources"
            ),
            "source_verification_limit": (
                "Local source-byte verification only; no remote host, signature "
                "or deployment was checked."
            ),
            "strategy_sha256": source_hashes["strategy.py"],
            "dataset_sha256": hashlib.sha256(_canonical(dataset)).hexdigest(),
            "generator_sha256": hashlib.sha256(_canonical(generator)).hexdigest(),
            "source_files": {
                f"{_SOURCE_PREFIX}/{name}": digest
                for name, digest in source_hashes.items()
            },
            "hash_algorithm": "sha256",
            "canonicalization": _CANONICALIZATION,
            "dataset_hash_scope": "Object mapping each scenario ID to its inputs.",
            "generator_hash_scope": (
                "Object mapping demo.py and demo_pack.py to their source SHA-256."
            ),
            "dataset_kind": "invented_synthetic_inputs_not_market_observations",
            "browser_integrity": (
                "Publish and verify an independent SHA-256 of the complete pack "
                "file bytes; do not reserialize numeric JSON in the browser."
            ),
        },
        "policy_limits": {
            "strategy_config": asdict(StrategyConfig()),
            "execution_policy_evaluated": False,
            "description": (
                "Unchanged strategy defaults only. Source, receipt and broker "
                "policies remain unevaluated."
            ),
        },
        "execution": {
            "order_authorized": False,
            "real_money_eligible": False,
            "receipt_issued": False,
            "order_submitted": False,
        },
    }
    if len(_canonical(pack)) + 1 > _MAX_PACK_BYTES:
        raise ValueError("demo_pack_exceeds_256_kib_limit")
    return pack


def serialize_demo_pack(pack: dict[str, Any]) -> str:
    """Stable UTF-8 JSON with one trailing newline for file/hash portability."""
    encoded = _canonical(pack)
    if len(encoded) + 1 > _MAX_PACK_BYTES:
        raise ValueError("demo_pack_exceeds_256_kib_limit")
    return encoded.decode("utf-8") + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-ref",
        help="full local Git commit SHA; must match the actual package sources",
    )
    args = parser.parse_args()
    try:
        print(serialize_demo_pack(build_demo_pack(args.source_ref)), end="")
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
