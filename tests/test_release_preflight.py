"""Pre-tag checks prevent burning another immutable release identity."""

import runpy
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = runpy.run_path(str(ROOT / "scripts/verify_release_candidate.py"))
PreflightError = PREFLIGHT["PreflightError"]
validate_candidate_metadata = PREFLIGHT["validate_candidate_metadata"]
verify_release_candidate = PREFLIGHT["verify_release_candidate"]
sys.path.insert(0, str(ROOT / "scripts"))
TAG_CONTROLLER = runpy.run_path(str(ROOT / "scripts/create_release_tag.py"))
TagPreflightError = TAG_CONTROLLER["PreflightError"]
validate_preflight_run = TAG_CONTROLLER["validate_preflight_run"]
validate_preflight_workflow = TAG_CONTROLLER["validate_preflight_workflow"]
validate_remote_tag_refs = TAG_CONTROLLER["validate_remote_tag_refs"]
validate_repository_tag_policy = TAG_CONTROLLER["validate_repository_tag_policy"]


def _working_tree_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_current_candidate_metadata_passes_preflight_validation():
    metadata = validate_candidate_metadata(_working_tree_text, "0.18.0")
    assert metadata["mcpb_sha256"] == (
        "f57ce3fb488b693e633d8bc66f980b616af09a8080722a11c50507496f39a2bb"
    )


def test_version_mismatch_fails_before_tag_creation():
    def mismatched(path: str) -> str:
        if path == "VERSION":
            return "0.17.0\n"
        return _working_tree_text(path)

    with pytest.raises(PreflightError, match="VERSION"):
        validate_candidate_metadata(mismatched, "0.18.0")


def test_placeholder_mcpb_digest_fails_before_tag_creation():
    def placeholder(path: str) -> str:
        text = _working_tree_text(path)
        if path == "server.json":
            return text.replace(
                "f57ce3fb488b693e633d8bc66f980b616af09a8080722a11c50507496f39a2bb",
                "0" * 64,
            )
        return text

    with pytest.raises(PreflightError, match="placeholder"):
        validate_candidate_metadata(placeholder, "0.18.0")


def test_manual_preflight_workflow_has_no_tag_write_authority():
    workflow = (ROOT / ".github/workflows/release-preflight.yml").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch:" in workflow
    assert "contents: read" in workflow
    assert "contents: write" not in workflow
    assert "scripts/verify_release_candidate.py" in workflow
    assert "--commit \"$CANDIDATE_SHA\"" in workflow
    assert "--version \"$RELEASE_VERSION\"" in workflow
    assert "ref: main" in workflow
    assert 'test "$GITHUB_REF" = "refs/heads/main"' in workflow
    assert workflow.count("scripts/build_mcpb.py") == 2
    assert workflow.count("--check-registry-metadata") == 2
    assert 'cmp --silent "$artifact" "$replay"' in workflow


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("commit", "HEAD", "exact 40-character"),
        ("version", "0.17", "MAJOR.MINOR.PATCH"),
        ("remote", "--upload-pack=bad", "named Git remote"),
        ("branch", "--help", "option prefix"),
    ),
)
def test_git_like_inputs_fail_before_any_network_call(field, value, message):
    arguments = {
        "commit": "a" * 40,
        "version": "0.18.0",
        "remote": "origin",
        "branch": "main",
    }
    arguments[field] = value
    with pytest.raises(PreflightError, match=message):
        verify_release_candidate(ROOT, **arguments)


def _successful_run() -> dict[str, Any]:
    commit = "a" * 40
    return {
        "name": f"Release preflight v0.18.0 @ {commit}",
        "display_title": f"Release preflight v0.18.0 @ {commit}",
        "event": "workflow_dispatch",
        "head_branch": "main",
        "head_sha": "b" * 40,
        "workflow_id": 348081695,
        "path": ".github/workflows/release-preflight.yml",
        "conclusion": "success",
        "html_url": (
            "https://github.com/beepboop2025/liquilens-evidence-carrier/"
            "actions/runs/123"
        ),
    }


def test_tag_controller_binds_successful_run_to_exact_candidate():
    commit = "a" * 40
    receipt = validate_preflight_run(
        _successful_run(),
        version="0.18.0",
        commit=commit,
        protected_head="b" * 40,
    )
    assert receipt["controller_commit"] == "b" * 40
    assert receipt["workflow_id"] == 348081695


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("conclusion", "failure"),
        ("head_branch", "feature"),
        ("path", ".github/workflows/other.yml"),
        ("name", "Release preflight"),
        ("display_title", "Release preflight v0.18.0 @ " + "c" * 40),
    ),
)
def test_tag_controller_rejects_unbound_or_failed_run(field, value):
    run = _successful_run()
    run[field] = value
    with pytest.raises(TagPreflightError, match=field):
        validate_preflight_run(
            run,
            version="0.18.0",
            commit="a" * 40,
            protected_head="b" * 40,
        )


def test_tag_controller_binds_run_to_static_active_workflow():
    workflow = {
        "id": 348081695,
        "name": "Release preflight",
        "path": ".github/workflows/release-preflight.yml",
        "state": "active",
        "html_url": (
            "https://github.com/beepboop2025/liquilens-evidence-carrier/"
            "blob/main/.github/workflows/release-preflight.yml"
        ),
    }
    receipt = validate_preflight_workflow(workflow, workflow_id=348081695)
    assert receipt["name"] == "Release preflight"

    for field, value in (
        ("id", 7),
        ("name", "Other workflow"),
        ("path", ".github/workflows/other.yml"),
        ("state", "disabled_manually"),
        ("html_url", "https://example.invalid/workflow"),
    ):
        changed = dict(workflow)
        changed[field] = value
        with pytest.raises(TagPreflightError, match=field):
            validate_preflight_workflow(changed, workflow_id=348081695)


def test_tag_controller_rejects_invalid_run_workflow_id():
    run = _successful_run()
    run["workflow_id"] = 0
    with pytest.raises(TagPreflightError, match="workflow_id"):
        validate_preflight_run(
            run,
            version="0.18.0",
            commit="a" * 40,
            protected_head="b" * 40,
        )


def test_tag_controller_rejects_superseded_main_preflight():
    with pytest.raises(TagPreflightError, match="current protected main"):
        validate_preflight_run(
            _successful_run(),
            version="0.18.0",
            commit="a" * 40,
            protected_head="c" * 40,
        )


def test_remote_tag_receipt_binds_object_and_peeled_commit():
    tag_object = "c" * 40
    commit = "a" * 40
    refs = [
        f"{tag_object}\trefs/tags/v0.18.0",
        f"{commit}\trefs/tags/v0.18.0^{{}}",
    ]
    validate_remote_tag_refs(
        refs,
        tag="v0.18.0",
        tag_object=tag_object,
        commit=commit,
    )
    with pytest.raises(TagPreflightError, match="tag object differs"):
        validate_remote_tag_refs(
            refs,
            tag="v0.18.0",
            tag_object="d" * 40,
            commit=commit,
        )


def _repository_policy_fixtures():
    public_key = (
        "ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAIOl45/7HK3aaA1Mhs4nDqErMnDNjQ1VW4KFvQOo0z08B"
    )
    creation = {
        "id": 22065439,
        "name": "Controlled version tag creation",
        "target": "tag",
        "enforcement": "active",
        "bypass_actors": [
            {
                "actor_id": None,
                "actor_type": "DeployKey",
                "bypass_mode": "always",
            }
        ],
        "conditions": {
            "ref_name": {"exclude": [], "include": ["refs/tags/v*"]}
        },
        "rules": [{"type": "creation"}],
        "current_user_can_bypass": "never",
    }
    immutable = {
        "id": 21288366,
        "name": "Immutable version tags",
        "target": "tag",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {
            "ref_name": {"exclude": [], "include": ["refs/tags/v*"]}
        },
        "rules": [{"type": "update"}, {"type": "deletion"}],
        "current_user_can_bypass": "never",
    }
    deploy_key = {
        "id": 162020600,
        "title": "LiquiLens controlled version tag push 2026-09",
        "key": public_key,
        "verified": True,
        "read_only": False,
    }
    return creation, immutable, deploy_key, [deploy_key], public_key


def test_repository_tag_policy_matches_live_fail_closed_shape():
    creation, immutable, deploy_key, deploy_keys, public_key = (
        _repository_policy_fixtures()
    )
    receipt = validate_repository_tag_policy(
        creation=creation,
        immutable=immutable,
        deploy_key=deploy_key,
        deploy_keys=deploy_keys,
        expected_public_key=public_key,
    )
    assert receipt["creation_ruleset_id"] == 22065439
    assert receipt["account_bypass"] == "never"


@pytest.mark.parametrize("mutation", ("disabled", "bypass", "extra_write_key"))
def test_repository_tag_policy_rejects_weakened_live_state(mutation):
    creation, immutable, deploy_key, deploy_keys, public_key = (
        _repository_policy_fixtures()
    )
    if mutation == "disabled":
        creation["enforcement"] = "disabled"
    elif mutation == "bypass":
        immutable["bypass_actors"] = [
            {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
        ]
    else:
        deploy_keys.append({"id": 99, "read_only": False})
    with pytest.raises(TagPreflightError):
        validate_repository_tag_policy(
            creation=creation,
            immutable=immutable,
            deploy_key=deploy_key,
            deploy_keys=deploy_keys,
            expected_public_key=public_key,
        )
