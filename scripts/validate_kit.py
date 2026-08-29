#!/usr/bin/env python3
"""Validate version, identities, references, and immutable artifact hashes."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLISHED_VERSION = "0.15.0"
PUBLISHED_REVISION = "0d852c06b1a4b0be566c8b4586c9c4c8b8f8f31c"


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _package_version() -> str:
    source = ROOT / "src/liquilens_evidence/__init__.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    versions = [
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    assert len(versions) == 1, "expected one literal __version__ assignment"
    return versions[0]


def main() -> int:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == version
    assert _package_version() == version

    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    local_packages = [
        package
        for package in lock["package"]
        if package.get("name") == "liquilens-evidence"
        and package.get("source") == {"editable": "."}
    ]
    assert len(local_packages) == 1
    assert local_packages[0]["version"] == version

    manifest = _json(ROOT / "mcpb/manifest.json")
    registry = _json(ROOT / "server.json")
    plugin = _json(ROOT / "plugins/liquilens-evidence/.codex-plugin/plugin.json")
    assert manifest["version"] == version
    assert registry["version"] == version
    assert plugin["version"] == version
    expected_mcpb = f"liquilens-evidence-carrier-mcp-{version}.mcpb"
    package = registry["packages"][0]
    assert package["identifier"] == (
        "https://github.com/beepboop2025/liquilens-evidence-carrier/"
        f"releases/download/v{version}/{expected_mcpb}"
    )
    assert re.fullmatch(r"[0-9a-f]{64}", package["fileSha256"])
    assert registry["_meta"][
        "io.modelcontextprotocol.registry/publisher-provided"
    ] == {
        "protocolVersions": ["2026-07-28", "2025-11-25"],
        "networkAccess": False,
        "financialAuthority": "none",
    }
    assert any(tool.get("name") == "verify_fleet_brief" for tool in manifest["tools"])
    assert "fleet-brief" in manifest["keywords"]
    assert "mcpServers" not in plugin
    assert "fleet-brief" in plugin["keywords"]
    assert "Fleet Brief verification" in plugin["interface"]["capabilities"]
    assert any(
        "Fleet Brief" in prompt for prompt in plugin["interface"]["defaultPrompt"]
    )
    assert (
        ROOT / "skills/liquilens-evidence/SKILL.md"
    ).read_bytes() == (
        ROOT / "plugins/liquilens-evidence/skills/liquilens-evidence/SKILL.md"
    ).read_bytes()

    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    assert re.search(rf"^version: {re.escape(version)}$", citation, re.MULTILINE)
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert dockerfile.startswith(f'ARG VERSION="{version}"\n')
    assert 'ARG REVISION="source-checkout"' in dockerfile
    assert 'ARG CREATED="1970-01-01T00:00:00Z"' in dockerfile
    devcontainer = _json(ROOT / ".devcontainer/devcontainer.json")
    assert devcontainer["build"]["args"] == {"VERSION": version}

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
    published_wheel = (
        f"releases/download/v{PUBLISHED_VERSION}/"
        f"liquilens_evidence-{PUBLISHED_VERSION}-py3-none-any.whl"
    )
    assert published_wheel in readme
    assert f"This source tree is versioned for `v{version}`" in readme
    assert "a source version alone is not\npublication proof" in readme
    assert PUBLISHED_REVISION in readme
    if version != PUBLISHED_VERSION:
        candidate_wheel = (
            f"releases/download/v{version}/"
            f"liquilens_evidence-{version}-py3-none-any.whl"
        )
        assert candidate_wheel not in readme
    assert "liquilens.fleet-brief.v1" in readme
    assert "liquilens-evidence issue-brief" in readme
    distribution = (ROOT / "DISTRIBUTION.md").read_text(encoding="utf-8")
    assert f"immutable public implementation release is `v{PUBLISHED_VERSION}`" in (
        distribution
    )
    assert f"source is preparing `v{version}`" in distribution
    assert PUBLISHED_REVISION in distribution
    assert (ROOT / "CHANGELOG.md").is_file()
    assert project["tool"]["setuptools"]["data-files"][
        "share/liquilens_evidence/docs"
    ] == ["CHANGELOG.md", "docs/*.md"]
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
    assert "cmp --silent" in release_workflow
    assert "LICENSE NOTICE README.md CHANGELOG.md server.json" in release_workflow
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
