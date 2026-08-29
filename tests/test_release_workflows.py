"""Release smoke tests bind to the same immutable carrier identity."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_DIGEST = "d92d7b31850f1788ae910d56035137e422e331f7e07516cce5b546674dbde00a"
RELEASE_REVISION = "0d852c06b1a4b0be566c8b4586c9c4c8b8f8f31c"


def test_published_mcp_smoke_receives_release_identity_explicitly():
    workflow = (ROOT / ".github/workflows/mcp-container.yml").read_text(
        encoding="utf-8"
    )
    smoke_step = workflow.split("- name: Smoke the published MCP digest", maxsplit=1)[
        1
    ].split("- name: Record immutable MCP receipt", maxsplit=1)[0]
    assert "LIQUILENS_CLI_IMAGE: ${{ env.BASE_IMAGE }}" in smoke_step
    assert "LIQUILENS_EXPECTED_BASE_DIGEST: ${{ env.BASE_DIGEST }}" in smoke_step
    assert (
        "LIQUILENS_EXPECTED_RELEASE_REVISION: "
        "${{ steps.release.outputs.release_revision }}" in smoke_step
    )


def test_mcp_defaults_match_the_released_v015_base():
    dockerfile = (ROOT / "Dockerfile.mcp").read_text(encoding="utf-8")
    smoke = (ROOT / "scripts/mcp_container_smoke.sh").read_text(encoding="utf-8")
    for content in (dockerfile, smoke):
        assert BASE_DIGEST in content
        assert RELEASE_REVISION in content
        assert (
            "9ec0646269357e971a67e88c8076c3c52c1561b094c1f2093ee19882a33294d1"
            not in content
        )


def test_candidate_container_smokes_take_version_from_source():
    for relative in (
        ".github/workflows/container.yml",
        ".github/workflows/mcp-container.yml",
    ):
        workflow = (ROOT / relative).read_text(encoding="utf-8")
        assert "echo \"version=$(cat VERSION)\"" in workflow
        assert "${{ steps.source.outputs.version }}" in workflow


def test_ci_and_release_replay_the_declared_mcpb_bytes():
    for relative in (".github/workflows/ci.yml", ".github/workflows/release.yml"):
        workflow = (ROOT / relative).read_text(encoding="utf-8")
        assert workflow.count("scripts/build_mcpb.py") >= 2
        assert "--output \"$artifact\"" in workflow
        assert "--output \"$replay\"" in workflow
        assert "cmp --silent \"$artifact\" \"$replay\"" in workflow
