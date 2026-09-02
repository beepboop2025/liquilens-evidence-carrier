"""Release smoke tests bind to the same immutable carrier identity."""

import tomllib
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


def test_container_context_carries_every_root_package_input():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    data_files = project["tool"]["setuptools"]["data-files"]
    root_package_data = {
        pattern
        for patterns in data_files.values()
        for pattern in patterns
        if "/" not in pattern
    }
    required = {
        "LICENSE",
        "NOTICE",
        "README.md",
        "pyproject.toml",
        *root_package_data,
    }

    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    allowed = {
        line.removeprefix("!")
        for line in dockerignore.splitlines()
        if line.startswith("!")
    }
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    copied_to_source = {
        source
        for line in dockerfile.splitlines()
        if line.startswith("COPY ") and line.endswith(" ./")
        for source in line.removeprefix("COPY ").removesuffix(" ./").split()
    }

    assert required <= allowed
    assert required <= copied_to_source


def test_ci_and_release_replay_the_declared_mcpb_bytes():
    for relative in (".github/workflows/ci.yml", ".github/workflows/release.yml"):
        workflow = (ROOT / relative).read_text(encoding="utf-8")
        assert workflow.count("scripts/build_mcpb.py") >= 2
        assert "--output \"$artifact\"" in workflow
        assert "--output \"$replay\"" in workflow
        assert "cmp --silent \"$artifact\" \"$replay\"" in workflow


def test_release_publishes_and_attests_trade_safety_assets():
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    build_step = workflow.split(
        "- name: Test and build release artifacts", maxsplit=1
    )[1].split("- name: Attest runnable", maxsplit=1)[0]
    attest_step = workflow.split("- name: Attest runnable", maxsplit=1)[1].split(
        "- name: Publish public release", maxsplit=1
    )[0]

    loose_assets = (
        "protocol/liquilens-trade-safety-request-v1.schema.json",
        "protocol/liquilens-trade-safety-policy-v1.schema.json",
        "protocol/liquilens-broker-preview-reference-v1.schema.json",
        "protocol/liquilens-trade-safety-receipt-v1.schema.json",
        "protocol/verify_hash_tree_v1.mjs",
        "integrations/fdc3/com.liquilens.trade-safety-receipt.schema.json",
        "integrations/fdc3/trade-safety-intents.json",
    )
    for asset in loose_assets:
        assert f"cp {asset} dist/" in build_step

    assert "uv build --project integrations/trade-safety-gateway --out-dir dist" in (
        build_step.replace("\\\n            ", "")
    )
    assert "find . -maxdepth 1 -type f" in build_step
    assert "xargs -0 sha256sum > SHA256SUMS" in build_step
    for subject in (
        "dist/*.whl",
        "dist/liquilens_trade_safety_gateway-*.tar.gz",
        "dist/*.schema.json",
        "dist/trade-safety-intents.json",
        "dist/verify_hash_tree_v1.mjs",
    ):
        assert subject in attest_step

    publish_step = workflow.split("- name: Publish public release", maxsplit=1)[1]
    assert 'gh release create "$GITHUB_REF_NAME" dist/*' in publish_step
