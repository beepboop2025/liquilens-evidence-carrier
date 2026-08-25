#!/usr/bin/env python3
"""Validate version, identities, references, and immutable artifact hashes."""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == version

    catalog = _json(ROOT / "protocol/catalog.json")
    assert catalog["release"] == version
    canonical_urls: set[str] = set()
    for artifact in catalog["artifacts"]:
        path = (ROOT / "protocol" / artifact["path"]).resolve()
        assert path.is_relative_to(ROOT), artifact["path"]
        assert path.is_file(), path
        assert _sha256(path) == artifact["sha256"], path
        contract = _json(path)
        assert contract["$id"] == artifact["canonical_url"], path
        assert contract["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        canonical_urls.add(artifact["canonical_url"])

    full_url = "https://liquilens.in/protocol/liquilens-evidence-carrier-v1.schema.json"
    reference_url = (
        "https://liquilens.in/protocol/"
        "liquilens-evidence-carrier-reference-v1.schema.json"
    )
    fleet_brief_url = (
        "https://liquilens.in/protocol/liquilens-fleet-brief-v1.schema.json"
    )
    assert {full_url, reference_url, fleet_brief_url} <= canonical_urls

    for relative in (
        "integrations/fdc3/com.liquilens.evidence.schema.json",
        "integrations/openlineage/liquilens-evidence-facet.schema.json",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert full_url in text
        assert reference_url in text

    assert "NotImplementedError" not in (
        ROOT / "examples/evidence_delivery_policy.py"
    ).read_text(encoding="utf-8")
    assert (ROOT / "dbt_project.yml").read_bytes() == (
        ROOT / "integrations/dbt/dbt_project.yml"
    ).read_bytes()
    assert (ROOT / "macros/test_liquilens_evidence_contract.sql").read_bytes() == (
        ROOT / "integrations/dbt/macros/test_liquilens_evidence_contract.sql"
    ).read_bytes()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "https://pypi.org/project/liquilens-evidence/" not in readme
    assert (
        f"releases/download/v{version}/liquilens_evidence-{version}-py3-none-any.whl"
    ) in readme
    assert "liquilens.fleet-brief.v1" in readme
    assert "liquilens-evidence issue-brief" in readme
    mcp_source = (ROOT / "src/liquilens_evidence/mcp_server.py").read_text(
        encoding="utf-8"
    )
    assert 'MCP_PROTOCOL_VERSION = "2026-07-28"' in mcp_source
    assert 'MCP_LEGACY_PROTOCOL_VERSION = "2025-11-25"' in mcp_source
    assert "to_openfigi_mapping_jobs" not in mcp_source
    assert "requests" not in project["project"]["dependencies"]
    action = (ROOT / "action.yml").read_text(encoding="utf-8")
    assert "using: composite" in action
    assert "scripts/action_verify.py" in action
    hooks = (ROOT / ".pre-commit-hooks.yaml").read_text(encoding="utf-8")
    assert "liquilens-evidence verify-files" in hooks
    assert r"\.(evidence|carrier)\.json$" in hooks
    release_workflow = (ROOT / ".github/workflows/release.yml").read_text(
        encoding="utf-8"
    )
    assert "! -name '.*' ! -name SHA256SUMS" in release_workflow
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
