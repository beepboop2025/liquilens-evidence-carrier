"""Release smoke tests bind to the same immutable carrier identity."""

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_DIGEST = "293a9ec61ad43f9bac22775936271b19651b486115ab53acbe7928cb177f8c4e"
RELEASE_REVISION = "906ca033a96ea862ab813c64db2a6b01c5ce8c4f"


def test_published_mcp_smoke_receives_release_identity_explicitly():
    workflow = (ROOT / ".github/workflows/mcp-container.yml").read_text(
        encoding="utf-8"
    )
    smoke_step = workflow.split("- name: Smoke the published MCP digest", maxsplit=1)[
        1
    ].split("- name: Record immutable MCP receipt", maxsplit=1)[0]
    assert "LIQUILENS_CLI_IMAGE: ${{ steps.release.outputs.base_image }}" in smoke_step
    assert (
        "LIQUILENS_EXPECTED_BASE_DIGEST: "
        "${{ steps.release.outputs.base_digest }}" in smoke_step
    )
    assert (
        "LIQUILENS_EXPECTED_RELEASE_REVISION: "
        "${{ steps.release.outputs.release_revision }}" in smoke_step
    )


def test_mcp_defaults_match_the_released_v018_base():
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


def test_mcp_container_targets_v019_and_resolves_exact_attested_core_base():
    workflow = (ROOT / ".github/workflows/mcp-container.yml").read_text(
        encoding="utf-8"
    )
    assert "RELEASE_TAG: v0.19.0" in workflow
    assert 'base_tag="ghcr.io/${GITHUB_REPOSITORY}:${version}"' in workflow
    assert "docker buildx imagetools inspect" in workflow
    assert 'gh attestation verify "oci://${base_tag}@${base_digest}"' in workflow
    assert "BASE_IMAGE=${{ steps.release.outputs.base_image }}" in workflow
    assert "BASE_DIGEST=${{ steps.release.outputs.base_digest }}" in workflow


def test_gateway_container_is_attested_multiarch_distribution_not_authority():
    workflow = (ROOT / ".github/workflows/gateway-container.yml").read_text(
        encoding="utf-8"
    )
    dockerfile = (ROOT / "integrations/trade-safety-gateway/Dockerfile").read_text(
        encoding="utf-8"
    )
    for token in (
        "integrations/trade-safety-gateway/Dockerfile",
        "linux/amd64,linux/arm64",
        "liquilens-trade-safety-gateway",
        "actions/attest-build-provenance",
        "provenance: mode=max",
        "sbom: true",
        "read-only-hash-only-sandbox",
        "no broker submission or order authorization",
        "--network none --read-only",
        "/v1/capabilities",
    ):
        assert token in workflow
    assert 'org.opencontainers.image.version="0.1.3"' in dockerfile
    assert 'io.liquilens.evidence.core.version="${CORE_VERSION}"' in dockerfile
    assert 'io.liquilens.trade-safety.authority="read-only-hash-only-sandbox"' in (
        dockerfile
    )


def test_gateway_pr_multiarch_build_installs_the_pinned_qemu_before_buildx():
    workflow = (ROOT / ".github/workflows/gateway-container.yml").read_text(
        encoding="utf-8"
    )
    smoke = workflow.split("  smoke:", maxsplit=1)[1].split(
        "  publish:", maxsplit=1
    )[0]
    qemu = (
        "docker/setup-qemu-action@"
        "96fe6ef7f33517b61c61be40b68a1882f3264fb8"
    )
    buildx = (
        "docker/setup-buildx-action@"
        "37fe631027851001ddb9b187196cc803df7f5f0e"
    )
    assert smoke.count(qemu) == 1
    assert smoke.index(qemu) < smoke.index(buildx)


def test_gateway_only_release_tag_is_signed_main_bound_and_immutable():
    workflow = (ROOT / ".github/workflows/gateway-container.yml").read_text(
        encoding="utf-8"
    )
    assert '"trade-safety-gateway-v[0-9]*"' in workflow
    assert 'test "$(git cat-file -t "$tag_name")" = "tag"' in workflow
    assert 'verify-tag --raw "$tag_name"' in workflow
    assert 'verify-commit --raw "$release_commit"' in workflow
    assert 'git merge-base --is-ancestor "$release_commit" origin/main' in workflow
    assert (
        "^trade-safety-gateway-v([0-9]+\\.[0-9]+\\.[0-9]+)$" in workflow
    )
    assert 'test "$source_gateway_version" = "$tag_gateway_version"' in workflow
    assert 'EXPECTED_GATEWAY_VERSION: "0.1.3"' in workflow
    assert 'EXPECTED_GATEWAY_CORE_VERSION: "0.19.0"' in workflow
    assert 'test "$source_core_version" = "$EXPECTED_GATEWAY_CORE_VERSION"' in (
        workflow
    )
    assert "Reject pre-existing immutable gateway artifact tags" in workflow
    assert "immutable gateway tag already exists" in workflow
    assert "${IMAGE}:signed-tag-${release_tag_object}" in workflow
    assert "provenance: mode=max" in workflow
    assert "sbom: true" in workflow
    assert "actions/attest-build-provenance" in workflow

    gateway_case = workflow.split("trade-safety-gateway-v*)", maxsplit=1)[1].split(
        ";;", maxsplit=1
    )[0]
    assert "${IMAGE}:latest" not in gateway_case
    assert "${IMAGE}:core-${core_version}" not in gateway_case


def test_gateway_release_documentation_preserves_core_tag_identity():
    guide = (
        ROOT / "integrations/trade-safety-gateway/README.md"
    ).read_text(encoding="utf-8")
    for token in (
        "trade-safety-gateway-v0.1.3",
        "immutable core `v0.19.0` tag",
        "lightweight or unsigned tag",
        "`origin/main`",
        "no floating",
        "provenance",
        "SBOM",
    ):
        assert token in guide


def test_typescript_tarball_manifest_carries_root_license_and_notice():
    package = json.loads(
        (ROOT / "integrations/typescript/package.json").read_text(encoding="utf-8")
    )
    assert {"LICENSE", "NOTICE"} <= set(package["files"])
    for name in ("LICENSE", "NOTICE"):
        assert (ROOT / "integrations/typescript" / name).read_bytes() == (
            ROOT / name
        ).read_bytes()


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

    assert "protocol/conformance/trade-safety-v1/corpus.json" in build_step
    assert "dist/liquilens-trade-safety-conformance-v1.json" in build_step
    assert "npm pack ./integrations/typescript --pack-destination dist" in build_step
    assert "uv build --project integrations/openbb --out-dir dist" in (
        build_step.replace("\\\n            ", "")
    )
    assert "test -f dist/openbb_liquilens_evidence-0.2.0.tar.gz" in build_step

    assert "uv build --project integrations/trade-safety-gateway --out-dir dist" in (
        build_step.replace("\\\n            ", "")
    )
    assert "find . -maxdepth 1 -type f" in build_step
    assert "xargs -0 sha256sum > SHA256SUMS" in build_step
    for subject in (
        "dist/*.whl",
        "dist/openbb_liquilens_evidence-*.tar.gz",
        "dist/liquilens_trade_safety_gateway-*.tar.gz",
        "dist/*.schema.json",
        "dist/trade-safety-intents.json",
        "dist/verify_hash_tree_v1.mjs",
        "dist/*.tgz",
        "dist/liquilens-trade-safety-conformance-v1.json",
    ):
        assert subject in attest_step

    publish_step = workflow.split("- name: Publish public release", maxsplit=1)[1]
    assert 'gh release create "$GITHUB_REF_NAME" dist/*' in publish_step


def test_openbb_reproducible_install_uses_the_candidate_release_tag():
    readme = (ROOT / "integrations/openbb/README.md").read_text(encoding="utf-8")
    assert (
        "liquilens-evidence-carrier.git@v0.19.0#subdirectory=integrations/openbb"
        in readme
    )
    assert "f4e9d6fb6bb20abbe6fc4625bd8c0f3279b48674" not in readme


def test_release_preflight_runs_before_any_immutable_tag_is_created():
    workflow = (ROOT / ".github/workflows/release-preflight.yml").read_text(
        encoding="utf-8"
    )
    script = (ROOT / "scripts/verify_release_candidate.py").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert "run-name: Release preflight v${{ inputs.version }}" in workflow
    assert "contents: read" in workflow
    assert "contents: write" not in workflow
    assert "verify_release_candidate.py" in workflow
    assert "ref: main" in workflow
    assert 'test "$GITHUB_REF" = "refs/heads/main"' in workflow
    assert workflow.count("scripts/build_mcpb.py") == 2
    assert workflow.count("--check-registry-metadata") == 2
    assert 'cmp --silent "$artifact" "$replay"' in workflow
    assert "scripts/check_trade_safety_conformance.py" in workflow
    assert "npm pack ./integrations/typescript" in workflow
    assert "uv build --project integrations/openbb" in workflow
    for invariant in (
        "verify-commit",
        "merge-base",
        "validate_candidate_metadata",
        "fileSha256",
        "ls-remote",
    ):
        assert invariant in script

    controller = (ROOT / "scripts/create_release_tag.py").read_text(
        encoding="utf-8"
    )
    for binding in (
        "preflight_run_id",
        "display_title",
        '"conclusion": "success"',
        '"head_branch": "main"',
        '"path": ".github/workflows/release-preflight.yml"',
        '"git", "tag", "-s"',
        'f"refs/tags/{tag}:refs/tags/{tag}"',
        "TAG_PUSH_PUBLIC_KEY_PATH",
        "GITHUB_HTTPS_REPOSITORY",
        "GITHUB_SSH_REPOSITORY",
        "TAG_CREATION_RULESET_ID",
        "IMMUTABLE_TAG_RULESET_ID",
        "TAG_PUSH_DEPLOY_KEY_ID",
        "validate_repository_tag_policy",
    ):
        assert binding in controller
