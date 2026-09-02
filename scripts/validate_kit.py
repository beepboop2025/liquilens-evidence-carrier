#!/usr/bin/env python3
"""Validate version, identities, references, and immutable artifact hashes."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_VERSION = "0.19.0"
SOURCE_MCPB_SHA256 = (
    "692f19b3b202fe9a6a8601532e0728f36e406665dfddd09643a1d737d2b5ef74"
)
GATEWAY_VERSION = "0.1.2"
PUBLISHED_VERSION = "0.18.0"
PUBLISHED_REVISION = "906ca033a96ea862ab813c64db2a6b01c5ce8c4f"
PUBLISHED_TREE = "0065206e14a21bb01ce25caed60bf14c9570d12f"
PUBLISHED_TAG_OBJECT = "42dd412ef27b470841b71b8bc73c0ed63a5e4a6b"
PUBLISHED_PREFLIGHT = "33593756967"
PUBLISHED_WORKFLOW = "33593840364"
PUBLISHED_CONTAINER_WORKFLOW = "33593840346"
PUBLISHED_SHA256SUMS = (
    "71c2c884d16fd3315a21c263ec8254b0f9578c8150f4a424c296228668d89953"
)
PUBLISHED_WHEEL_SHA256 = (
    "9fbc7ee50f658e2a8d1d880f8f76d73dca8b07ef6f0747df33a7b9fc346495ef"
)
PUBLISHED_MCPB_SHA256 = (
    "f57ce3fb488b693e633d8bc66f980b616af09a8080722a11c50507496f39a2bb"
)
PUBLISHED_GATEWAY_WHEEL_SHA256 = (
    "103cde79c006074eaabe5083fec212ba237fcf3a42f01b0600e0faf0328a05a8"
)
PUBLISHED_OCI_DIGEST = (
    "293a9ec61ad43f9bac22775936271b19651b486115ab53acbe7928cb177f8c4e"
)
PUBLISHED_README_SHA256 = (
    "3cc9705a2c1aa0471342199f54509b2aa66a02a2c84d89287732a89cd026018a"
)
PUBLISHED_RELEASE_ATTESTATION = "44605007"
PUBLISHED_CONTAINER_ATTESTATION = "44605376"
PREVIOUS_VERSION = "0.17.1"
PREVIOUS_REVISION = "a74274236e177404c2d254541e6a4110a4ce8a0d"
PREVIOUS_TAG_OBJECT = "8844ee4556d59472a587cb9ceb412112c23543db"
PREVIOUS_RELEASE_RECORD_SHA256 = (
    "c089ee719ac4e6eba99936f4daf50681b045694b63aba7effea307aa48e93dd8"
)
PREVIOUS_README_SHA256 = (
    "8422e21dc715443c22c8d18e1991fa8427136292a06ee45068db4a1a26029c9e"
)
CANONICAL_SITE_REVISION = "3ec660175c81c5b282715ee400eea2f771dc2610"
CANONICAL_SITE_WORKFLOW = "33592149926"


def _json(path: Path) -> dict[str, Any]:
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
    assert version == SOURCE_VERSION
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
    assert package["fileSha256"] == SOURCE_MCPB_SHA256
    assert registry["_meta"][
        "io.modelcontextprotocol.registry/publisher-provided"
    ] == {
        "protocolVersions": ["2026-07-28", "2025-11-25"],
        "networkAccess": False,
        "financialAuthority": "none",
    }
    assert any(tool.get("name") == "verify_fleet_brief" for tool in manifest["tools"])
    assert any(
        tool.get("name") == "verify_trade_safety_receipt"
        for tool in manifest["tools"]
    )
    assert "fleet-brief" in manifest["keywords"]
    assert "trade-safety" in manifest["keywords"]
    assert "mcpServers" not in plugin
    assert "fleet-brief" in plugin["keywords"]
    assert "Fleet Brief verification" in plugin["interface"]["capabilities"]
    assert "Trade Safety Receipt verification" in plugin["interface"][
        "capabilities"
    ]
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
        assert artifact["path"].endswith(".schema.json"), artifact["path"]
        path = (ROOT / "protocol" / artifact["path"]).resolve()
        assert path.is_relative_to(ROOT), artifact["path"]
        assert path.is_file(), path
        assert _sha256(path) == artifact["sha256"], path
        contract = _json(path)
        assert contract["$id"] == artifact["canonical_url"], path
        assert contract["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        canonical_urls.add(artifact["canonical_url"])

    assert len(catalog["conformance"]) == 1
    conformance_entry = catalog["conformance"][0]
    assert conformance_entry["kind"] == "conformance-corpus"
    conformance_path = (ROOT / "protocol" / conformance_entry["path"]).resolve()
    assert conformance_path.is_relative_to(ROOT)
    assert _sha256(conformance_path) == conformance_entry["sha256"]
    conformance = _json(conformance_path)
    assert conformance["schema"] == "liquilens.trade-safety-conformance.v1"
    assert conformance["protocol_schema"] == "liquilens.trade-safety-receipt.v1"
    assert conformance["canonicalization"] == "liquilens-hash-tree-v1"
    assert len(conformance["cases"]) >= 12

    full_url = "https://liquilens.in/protocol/liquilens-evidence-carrier-v1.schema.json"
    reference_url = (
        "https://liquilens.in/protocol/"
        "liquilens-evidence-carrier-reference-v1.schema.json"
    )
    fleet_brief_url = (
        "https://liquilens.in/protocol/liquilens-fleet-brief-v1.schema.json"
    )
    trade_safety_urls = {
        "https://liquilens.in/protocol/liquilens-trade-safety-request-v1.schema.json",
        "https://liquilens.in/protocol/liquilens-trade-safety-policy-v1.schema.json",
        "https://liquilens.in/protocol/liquilens-broker-preview-reference-v1.schema.json",
        "https://liquilens.in/protocol/liquilens-trade-safety-receipt-v1.schema.json",
        "https://liquilens.in/protocol/fdc3/com.liquilens.trade-safety-receipt.schema.json",
    }
    assert {full_url, reference_url, fleet_brief_url, *trade_safety_urls} <= (
        canonical_urls
    )

    for relative in (
        "integrations/fdc3/com.liquilens.evidence.schema.json",
        "integrations/openlineage/liquilens-evidence-facet.schema.json",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert full_url in text
        assert reference_url in text

    trade_safety_fdc3 = (
        ROOT / "integrations/fdc3/com.liquilens.trade-safety-receipt.schema.json"
    ).read_text(encoding="utf-8")
    assert (
        "https://liquilens.in/protocol/liquilens-trade-safety-receipt-v1.schema.json"
        in trade_safety_fdc3
    )
    trade_safety_intents = _json(
        ROOT / "integrations/fdc3/trade-safety-intents.json"
    )
    assert trade_safety_intents["context_schemas"][
        "com.liquilens.trade-safety-receipt"
    ].endswith("com.liquilens.trade-safety-receipt.schema.json")
    provider_intents = trade_safety_intents["provider_interop"]["intents"][
        "listensFor"
    ]
    assert provider_intents["liquilens.EvaluateTradeSafety"]["contexts"] == [
        "fdc3.order"
    ]
    assert (
        provider_intents["liquilens.EvaluateTradeSafety"]["resultType"]
        == "com.liquilens.trade-safety-receipt"
    )
    assert trade_safety_intents["safety_contract"]["execution_side_effects"] is False

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
    normalized_readme = " ".join(readme.split())
    assert "https://pypi.org/project/liquilens-evidence/" not in readme
    published_wheel = (
        f"releases/download/v{PUBLISHED_VERSION}/"
        f"liquilens_evidence-{PUBLISHED_VERSION}-py3-none-any.whl"
    )
    assert published_wheel in readme
    assert (
        f"current signed and published core release is `v{PUBLISHED_VERSION}`"
        in readme
    )
    assert f"source checkpoint prepares `v{SOURCE_VERSION}`" in readme
    assert "not yet tagged, published, or registered" in normalized_readme
    assert PUBLISHED_REVISION in readme
    assert PUBLISHED_TREE in readme
    assert PUBLISHED_TAG_OBJECT in readme
    assert PUBLISHED_PREFLIGHT in readme
    assert PUBLISHED_WORKFLOW in readme
    assert PUBLISHED_WHEEL_SHA256 in readme
    assert PUBLISHED_MCPB_SHA256 in readme
    assert PUBLISHED_SHA256SUMS in readme
    assert PUBLISHED_OCI_DIGEST in readme
    assert "immutable: false" in readme
    assert "21288366" in readme
    assert (
        f"registry.modelcontextprotocol.io/v0.1/servers/"
        "io.github.beepboop2025%2Fliquilens-evidence-carrier/versions/"
        f"{PUBLISHED_VERSION}"
    ) in readme
    assert CANONICAL_SITE_REVISION in readme
    assert CANONICAL_SITE_WORKFLOW in readme
    assert SOURCE_MCPB_SHA256 in readme
    assert "liquilens.fleet-brief.v1" in readme
    assert "liquilens-evidence issue-brief" in readme
    assert "liquilens.trade-safety-receipt.v1" in readme
    assert "liquilens-evidence issue-trade-safety" in readme
    assert f"Published release `v{PUBLISHED_VERSION}` provides" in readme
    distribution = (ROOT / "DISTRIBUTION.md").read_text(encoding="utf-8")
    assert f"current core implementation release is `v{PUBLISHED_VERSION}`" in (
        distribution
    )
    assert f"source now prepares core `v{SOURCE_VERSION}`" in distribution
    assert CANONICAL_SITE_REVISION in distribution
    assert CANONICAL_SITE_WORKFLOW in distribution
    assert SOURCE_MCPB_SHA256 in distribution
    assert PUBLISHED_REVISION in distribution
    assert PUBLISHED_TAG_OBJECT in distribution
    assert PUBLISHED_PREFLIGHT in distribution
    assert PUBLISHED_WORKFLOW in distribution
    assert PUBLISHED_CONTAINER_WORKFLOW in distribution
    assert PUBLISHED_WHEEL_SHA256 in distribution
    assert PUBLISHED_MCPB_SHA256 in distribution
    assert PUBLISHED_GATEWAY_WHEEL_SHA256 in distribution
    assert PUBLISHED_SHA256SUMS in distribution
    assert PUBLISHED_OCI_DIGEST in distribution
    assert PUBLISHED_RELEASE_ATTESTATION in distribution
    assert PUBLISHED_CONTAINER_ATTESTATION in distribution
    assert "immutable: false" in distribution
    assert f"versions/{PUBLISHED_VERSION}" in distribution
    release_record = (ROOT / f"docs/RELEASE-{PUBLISHED_VERSION}.md").read_text(
        encoding="utf-8"
    )
    assert "signed, published, attested, and active" in release_record
    assert PUBLISHED_REVISION in release_record
    assert PUBLISHED_TREE in release_record
    assert PUBLISHED_TAG_OBJECT in release_record
    assert PUBLISHED_PREFLIGHT in release_record
    assert PUBLISHED_WORKFLOW in release_record
    assert PUBLISHED_CONTAINER_WORKFLOW in release_record
    assert PUBLISHED_SHA256SUMS in release_record
    assert PUBLISHED_WHEEL_SHA256 in release_record
    assert PUBLISHED_MCPB_SHA256 in release_record
    assert PUBLISHED_GATEWAY_WHEEL_SHA256 in release_record
    assert PUBLISHED_OCI_DIGEST in release_record
    assert PUBLISHED_RELEASE_ATTESTATION in release_record
    assert PUBLISHED_CONTAINER_ATTESTATION in release_record
    assert "immutable: false" in release_record
    assert "not tagged, published, or registered" not in release_record
    release_readme = ROOT / "mcpb/release-readmes" / f"{PUBLISHED_VERSION}.md"
    assert _sha256(release_readme) == PUBLISHED_README_SHA256
    assert release_readme.read_bytes() != (ROOT / "README.md").read_bytes()
    current_release_readme = ROOT / "mcpb/release-readmes" / f"{version}.md"
    assert current_release_readme.is_file()
    assert current_release_readme.read_bytes() != (ROOT / "README.md").read_bytes()
    candidate_readme = current_release_readme.read_text(encoding="utf-8")
    normalized_candidate_readme = " ".join(candidate_readme.split())
    assert f"bytes prepared for the v{SOURCE_VERSION} MCPB candidate" in (
        normalized_candidate_readme
    )
    assert "No such publication receipt is asserted" in candidate_readme
    candidate_record = (ROOT / f"docs/RELEASE-{SOURCE_VERSION}.md").read_text(
        encoding="utf-8"
    )
    assert "prepared source; not tagged, published, registered, or" in (
        candidate_record
    )
    assert "There is no v0.19.0 tag object" in candidate_record
    assert SOURCE_MCPB_SHA256 in candidate_record
    previous_record = ROOT / f"docs/RELEASE-{PREVIOUS_VERSION}.md"
    assert _sha256(previous_record) == PREVIOUS_RELEASE_RECORD_SHA256
    previous_text = previous_record.read_text(encoding="utf-8")
    assert PREVIOUS_REVISION in previous_text
    assert PREVIOUS_TAG_OBJECT in previous_text
    previous_readme = ROOT / "mcpb/release-readmes" / f"{PREVIOUS_VERSION}.md"
    assert _sha256(previous_readme) == PREVIOUS_README_SHA256
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{SOURCE_VERSION}] - 2026-09-02" in changelog
    assert "No `v0.19.0` tag, GitHub release" in changelog
    assert SOURCE_MCPB_SHA256 in changelog
    assert f"## [{PUBLISHED_VERSION}] - 2026-09-02" in changelog
    assert PUBLISHED_REVISION in changelog
    assert PUBLISHED_TAG_OBJECT in changelog
    assert PUBLISHED_PREFLIGHT in changelog
    assert PUBLISHED_WORKFLOW in changelog
    assert PUBLISHED_CONTAINER_WORKFLOW in changelog
    assert PUBLISHED_WHEEL_SHA256 in changelog
    assert PUBLISHED_MCPB_SHA256 in changelog
    assert PUBLISHED_GATEWAY_WHEEL_SHA256 in changelog
    assert PUBLISHED_SHA256SUMS in changelog
    assert PUBLISHED_OCI_DIGEST in changelog
    assert PUBLISHED_RELEASE_ATTESTATION in changelog
    assert PUBLISHED_CONTAINER_ATTESTATION in changelog
    assert project["tool"]["setuptools"]["data-files"][
        "share/liquilens_evidence/docs"
    ] == ["CHANGELOG.md", "docs/*.md"]
    mcp_source = (ROOT / "src/liquilens_evidence/mcp_server.py").read_text(
        encoding="utf-8"
    )
    assert 'MCP_PROTOCOL_VERSION = "2026-07-28"' in mcp_source
    assert 'MCP_LEGACY_PROTOCOL_VERSION = "2025-11-25"' in mcp_source
    assert "to_openfigi_mapping_jobs" not in mcp_source
    assert '"verify_trade_safety_receipt"' in mcp_source
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
    for asset in (
        "liquilens-trade-safety-request-v1.schema.json",
        "liquilens-trade-safety-policy-v1.schema.json",
        "liquilens-broker-preview-reference-v1.schema.json",
        "liquilens-trade-safety-receipt-v1.schema.json",
        "com.liquilens.trade-safety-receipt.schema.json",
        "trade-safety-intents.json",
        "liquilens-trade-safety-conformance-v1.json",
        "liquilens-trade-safety-0.1.0.tgz",
    ):
        assert asset in release_workflow
    gateway_project = tomllib.loads(
        (ROOT / "integrations/trade-safety-gateway/pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    assert f"liquilens-evidence=={version}" in gateway_project["project"][
        "dependencies"
    ]
    assert gateway_project["project"]["version"] == GATEWAY_VERSION
    assert "fastapi>=0.141.1,<0.142" in gateway_project["project"]["dependencies"]
    assert "starlette>=1.3.1,<2" in gateway_project["project"]["dependencies"]
    assert "pytest>=9.0.3,<10" in gateway_project["project"][
        "optional-dependencies"
    ]["test"]
    gateway_lock = tomllib.loads(
        (ROOT / "integrations/trade-safety-gateway/uv.lock").read_text(
            encoding="utf-8"
        )
    )
    assert any(
        package.get("name") == "liquilens-evidence"
        and package.get("version") == version
        for package in gateway_lock["package"]
    )
    gateway_versions = {
        package.get("name"): package.get("version")
        for package in gateway_lock["package"]
        if package.get("name")
        in {
            "fastapi",
            "liquilens-trade-safety-gateway",
            "pytest",
            "starlette",
        }
    }
    assert gateway_versions == {
        "fastapi": "0.141.1",
        "liquilens-trade-safety-gateway": GATEWAY_VERSION,
        "pytest": "9.1.1",
        "starlette": "1.6.0",
    }
    gateway_init = (
        ROOT
        / "integrations/trade-safety-gateway/src/trade_safety_gateway/__init__.py"
    ).read_text(encoding="utf-8")
    gateway_app = (
        ROOT / "integrations/trade-safety-gateway/src/trade_safety_gateway/app.py"
    ).read_text(encoding="utf-8")
    gateway_dockerfile = (
        ROOT / "integrations/trade-safety-gateway/Dockerfile"
    ).read_text(encoding="utf-8")
    assert f'__version__ = "{GATEWAY_VERSION}"' in gateway_init
    assert f'SERVICE_VERSION = "{GATEWAY_VERSION}"' in gateway_app
    assert (
        f'org.opencontainers.image.version="{GATEWAY_VERSION}"'
        in gateway_dockerfile
    )
    golden = _json(ROOT / "examples/trade-safety/receipt.paper.pass.json")
    assert golden["schema"] == "liquilens.trade-safety-receipt.v1"
    assert golden["decision"]["outcome"] == "pass"
    assert golden["authority"]["can_execute"] is False
    verifier = (ROOT / "protocol/verify_hash_tree_v1.mjs").read_text(
        encoding="utf-8"
    )
    assert 'artifactKind === "trade-safety-receipt"' in verifier
    typescript = _json(ROOT / "integrations/typescript/package.json")
    assert typescript["name"] == "@liquilens/trade-safety"
    assert typescript["version"] == "0.1.0"
    assert "dependencies" not in typescript
    assert "scripts/check_trade_safety_conformance.py" in release_workflow
    openbb = tomllib.loads(
        (ROOT / "integrations/openbb/pyproject.toml").read_text(encoding="utf-8")
    )
    assert openbb["project"]["version"] == "0.2.0"
    assert any(
        "v0.18.0/liquilens_evidence-0.18.0" in dependency
        for dependency in openbb["project"]["dependencies"]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
