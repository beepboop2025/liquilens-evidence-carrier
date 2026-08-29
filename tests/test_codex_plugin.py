import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKETPLACE = ROOT / ".agents/plugins/marketplace.json"
PLUGIN = ROOT / "plugins/liquilens-evidence"


def test_codex_plugin_manifest_and_marketplace_are_consistent() -> None:
    manifest = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text())
    marketplace = json.loads(MARKETPLACE.read_text())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert manifest["name"] == "liquilens-evidence"
    assert manifest["version"] == project["project"]["version"]
    assert manifest["license"] == "Apache-2.0"
    assert manifest["skills"] == "./skills/"
    assert "mcpServers" not in manifest
    assert marketplace["name"] == "liquilens"
    assert marketplace["plugins"] == [
        {
            "name": "liquilens-evidence",
            "source": {
                "source": "local",
                "path": "./plugins/liquilens-evidence",
            },
            "policy": {
                "installation": "AVAILABLE",
                "authentication": "ON_INSTALL",
            },
            "category": "Developer Tools",
        }
    ]


def test_codex_plugin_discovers_fleet_briefs_without_starting_mcp() -> None:
    manifest = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text())

    assert "mcpServers" not in manifest
    assert "fleet-brief" in manifest["keywords"]
    assert "Fleet Brief verification" in manifest["interface"]["capabilities"]
    assert "Fleet Brief" in manifest["interface"]["longDescription"]
    assert any(
        "Fleet Brief" in prompt
        for prompt in manifest["interface"]["defaultPrompt"]
    )


def test_codex_plugin_reuses_the_canonical_skill_exactly() -> None:
    canonical = ROOT / "skills/liquilens-evidence/SKILL.md"
    packaged = PLUGIN / "skills/liquilens-evidence/SKILL.md"
    assert packaged.read_bytes() == canonical.read_bytes()
