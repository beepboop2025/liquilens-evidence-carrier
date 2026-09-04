from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REVISION = "5f46ff09288a8ee1024715db75615ab5882465fa"
OCI_IMAGE = (
    "ghcr.io/beepboop2025/liquilens-trade-safety-gateway@"
    "sha256:2d741addefa972e25d65f2617ce75f639321345ffe74dd02d5f3b4f668154762"
)


def test_registry_metadata_is_remote_read_only_and_exact() -> None:
    metadata = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))

    assert metadata == {
        "$schema": (
            "https://static.modelcontextprotocol.io/"
            "schemas/2025-12-11/server.schema.json"
        ),
        "name": "io.github.beepboop2025/liquilens-trade-safety-gateway",
        "title": "LiquiLens Trade Safety Gateway",
        "description": (
            "Read-only checks for proposed orders against evidence and policy; "
            "no trading or payment authority."
        ),
        "version": "0.2.0",
        "repository": {
            "url": "https://github.com/beepboop2025/liquilens-evidence-carrier",
            "source": "github",
            "id": "1344895874",
            "subfolder": "integrations/trade-safety-gateway",
        },
        "websiteUrl": "https://liquilens.in/protocol/trade-safety/",
        "remotes": [
            {
                "type": "streamable-http",
                "url": "https://trade-safety.liquilens.in/mcp",
            }
        ],
        "_meta": {
            "io.modelcontextprotocol.registry/publisher-provided": {
                "protocolVersions": ["2026-07-28", "2025-11-25"],
                "mode": "read-only-sandbox",
                "financialAuthority": "none",
                "executionAuthority": False,
                "brokerCredentials": False,
                "orderSubmission": False,
                "x402Access": "disabled",
                "sourceRevision": SOURCE_REVISION,
                "ociImage": OCI_IMAGE,
            }
        },
    }
    assert "packages" not in metadata


def test_registry_publication_is_manual_exact_and_live_proof_gated() -> None:
    workflow = (
        ROOT.parents[1] / ".github/workflows/trade-safety-gateway-mcp-registry.yml"
    ).read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "id-token: write" in workflow
    assert 'test "$GITHUB_REF" = refs/heads/main' in workflow
    assert 'assert branch["protected"] is True' in workflow
    assert 'assert branch["commit"]["sha"] == expected' in workflow
    proof = workflow.index("Prove the exact public read-only gateway")
    publish = workflow.index("Publish to the official MCP Registry")
    assert proof < publish
    workflow_lines = set(workflow.splitlines())
    assert "          BASE_URL: https://trade-safety.liquilens.in" in workflow_lines
    assert SOURCE_REVISION in workflow
    assert 'capabilities["x402_access"]["state"] == "disabled"' in workflow
    assert 'capabilities["execution_tools"] == []' in workflow
    assert 'all(value is False for value in capabilities["authority"].values())' in (
        workflow
    )
    assert "mcp-publisher login github-oidc" in workflow
    assert "integrations/trade-safety-gateway/server.json" in workflow
    assert "Require official Registry convergence" in workflow
