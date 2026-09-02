"""Create and push a release tag only after a matching protected preflight."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from verify_release_candidate import (
    SIGNER_PATH,
    PreflightError,
    verify_release_candidate,
)

GITHUB_REPOSITORY = "beepboop2025/liquilens-evidence-carrier"
GITHUB_HTTPS_REPOSITORY = f"https://github.com/{GITHUB_REPOSITORY}.git"
GITHUB_SSH_REPOSITORY = f"git@github.com:{GITHUB_REPOSITORY}.git"
IMMUTABLE_TAG_RULESET_ID = 21288366
TAG_CREATION_RULESET_ID = 22065439
TAG_PUSH_DEPLOY_KEY_ID = 162020600
RUN_ID_PATTERN = re.compile(r"[1-9][0-9]*")
TAG_PUSH_PUBLIC_KEY_PATH = (
    "ops/release/liquilens-evidence-carrier.tag_push.pub"
)


def _command(
    arguments: Sequence[str],
    *,
    repository: Path,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(arguments),
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise PreflightError(f"{' '.join(arguments)} failed: {detail}")
    return completed


def validate_preflight_run(
    run: dict[str, Any], *, version: str, commit: str, protected_head: str
) -> dict[str, str]:
    """Bind a successful protected-main workflow run to version and commit."""

    expected = {
        "name": "Release preflight",
        "display_title": f"Release preflight v{version} @ {commit}",
        "event": "workflow_dispatch",
        "head_branch": "main",
        "path": ".github/workflows/release-preflight.yml",
        "conclusion": "success",
    }
    for field, expected_value in expected.items():
        actual = run.get(field)
        if actual != expected_value:
            raise PreflightError(
                f"preflight run {field} is {actual!r}; expected {expected_value!r}"
            )
    head_sha = run.get("head_sha")
    url = run.get("html_url")
    if not isinstance(head_sha, str) or re.fullmatch(r"[0-9a-f]{40}", head_sha) is None:
        raise PreflightError("preflight run head_sha is not an exact commit SHA")
    if head_sha != protected_head:
        raise PreflightError(
            "preflight controller commit does not match current protected main"
        )
    if not isinstance(url, str) or not url.startswith(
        f"https://github.com/{GITHUB_REPOSITORY}/actions/runs/"
    ):
        raise PreflightError("preflight run URL is not from the release repository")
    return {"controller_commit": head_sha, "url": url}


def validate_remote_tag_refs(
    remote_refs: Sequence[str], *, tag: str, tag_object: str, commit: str
) -> None:
    """Require exact remote annotated-tag and peeled-commit identities."""

    parsed = {
        line.split("\t", maxsplit=1)[1]: line.split("\t", maxsplit=1)[0]
        for line in remote_refs
        if "\t" in line
    }
    if parsed.get(f"refs/tags/{tag}") != tag_object:
        raise PreflightError("remote annotated tag object differs from local tag")
    if parsed.get(f"refs/tags/{tag}^{{}}") != commit:
        raise PreflightError("pushed annotated tag does not peel to the candidate commit")


def _github_api(repository: Path, path: str) -> Any:
    completed = _command(
        ["gh", "api", path],
        repository=repository,
    )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise PreflightError(f"GitHub API response is invalid JSON: {exc}") from exc
    return value


def _require_canonical_origin(repository: Path) -> None:
    allowed = {GITHUB_HTTPS_REPOSITORY, GITHUB_SSH_REPOSITORY}
    fetch_url = _command(
        ["git", "remote", "get-url", "origin"], repository=repository
    ).stdout.strip()
    push_url = _command(
        ["git", "remote", "get-url", "--push", "origin"],
        repository=repository,
    ).stdout.strip()
    if fetch_url not in allowed or push_url not in allowed:
        raise PreflightError(
            "origin fetch and push URLs must resolve exactly to the canonical "
            "release repository"
        )


def _github_run(repository: Path, run_id: str) -> dict[str, Any]:
    if RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise PreflightError("preflight run ID must contain decimal digits")
    value = _github_api(
        repository,
        f"repos/{GITHUB_REPOSITORY}/actions/runs/{run_id}",
    )
    if not isinstance(value, dict):
        raise PreflightError("GitHub preflight response must be an object")
    return value


def _public_key_identity(value: str, *, label: str) -> tuple[str, str]:
    fields = value.strip().split()
    if len(fields) < 2 or fields[0] != "ssh-ed25519":
        raise PreflightError(f"{label} must contain one Ed25519 public key")
    return fields[0], fields[1]


def validate_repository_tag_policy(
    *,
    creation: dict[str, Any],
    immutable: dict[str, Any],
    deploy_key: dict[str, Any],
    deploy_keys: list[Any],
    expected_public_key: str,
) -> dict[str, Any]:
    """Require the live creation, immutability, and credential boundaries."""

    expected_creation = {
        "id": TAG_CREATION_RULESET_ID,
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
    expected_immutable = {
        "id": IMMUTABLE_TAG_RULESET_ID,
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
    for label, actual, expected in (
        ("creation ruleset", creation, expected_creation),
        ("immutable ruleset", immutable, expected_immutable),
    ):
        for field, expected_value in expected.items():
            if actual.get(field) != expected_value:
                raise PreflightError(
                    f"{label} {field} is {actual.get(field)!r}; "
                    f"expected {expected_value!r}"
                )

    expected_key_fields = {
        "id": TAG_PUSH_DEPLOY_KEY_ID,
        "title": "LiquiLens controlled version tag push 2026-09",
        "verified": True,
        "read_only": False,
    }
    for field, expected_value in expected_key_fields.items():
        if deploy_key.get(field) != expected_value:
            raise PreflightError(
                f"tag-push deploy key {field} is {deploy_key.get(field)!r}; "
                f"expected {expected_value!r}"
            )
    key_text = deploy_key.get("key")
    if not isinstance(key_text, str) or _public_key_identity(
        key_text, label="GitHub tag-push key"
    ) != _public_key_identity(expected_public_key, label="protected tag-push key"):
        raise PreflightError("GitHub deploy key differs from protected source")

    write_key_ids = sorted(
        key.get("id")
        for key in deploy_keys
        if isinstance(key, dict) and key.get("read_only") is False
    )
    if write_key_ids != [TAG_PUSH_DEPLOY_KEY_ID]:
        raise PreflightError(
            f"write-enabled deploy key IDs are {write_key_ids!r}; "
            f"expected only {[TAG_PUSH_DEPLOY_KEY_ID]!r}"
        )
    return {
        "creation_ruleset_id": TAG_CREATION_RULESET_ID,
        "immutable_ruleset_id": IMMUTABLE_TAG_RULESET_ID,
        "tag_push_deploy_key_id": TAG_PUSH_DEPLOY_KEY_ID,
        "account_bypass": "never",
    }


def _repository_tag_policy(
    repository: Path, *, protected_head: str
) -> tuple[dict[str, Any], str]:
    expected_public = _command(
        ["git", "show", f"{protected_head}:{TAG_PUSH_PUBLIC_KEY_PATH}"],
        repository=repository,
    ).stdout
    prefix = f"repos/{GITHUB_REPOSITORY}"
    creation = _github_api(
        repository, f"{prefix}/rulesets/{TAG_CREATION_RULESET_ID}"
    )
    immutable = _github_api(
        repository, f"{prefix}/rulesets/{IMMUTABLE_TAG_RULESET_ID}"
    )
    deploy_key = _github_api(
        repository, f"{prefix}/keys/{TAG_PUSH_DEPLOY_KEY_ID}"
    )
    deploy_keys = _github_api(repository, f"{prefix}/keys?per_page=100")
    if not all(isinstance(value, dict) for value in (creation, immutable, deploy_key)):
        raise PreflightError("GitHub rulesets and deploy key must be objects")
    if not isinstance(deploy_keys, list):
        raise PreflightError("GitHub deploy-key inventory must be a list")
    policy = validate_repository_tag_policy(
        creation=creation,
        immutable=immutable,
        deploy_key=deploy_key,
        deploy_keys=deploy_keys,
        expected_public_key=expected_public,
    )
    return policy, expected_public


def _tag_push_environment(
    repository: Path, *, private_key: Path, expected_public: str
) -> dict[str, str]:
    private_key = private_key.expanduser().resolve()
    if not private_key.is_file():
        raise PreflightError("tag-push private key file does not exist")
    actual_public = _command(
        ["ssh-keygen", "-y", "-f", str(private_key)],
        repository=repository,
    ).stdout
    if _public_key_identity(actual_public, label="derived tag-push key") != (
        _public_key_identity(expected_public, label="protected tag-push key")
    ):
        raise PreflightError("tag-push private key does not match protected source")

    environment = dict(os.environ)
    environment["GIT_SSH_COMMAND"] = (
        f"ssh -i {shlex.quote(str(private_key))} "
        "-o IdentitiesOnly=yes -o BatchMode=yes"
    )
    return environment


def create_release_tag(
    repository: Path,
    *,
    commit: str,
    version: str,
    preflight_run_id: str,
    push: bool,
    push_key: Path | None,
) -> dict[str, Any]:
    """Recheck identity, bind preflight, then sign and optionally push a tag."""

    repository = repository.resolve()
    _require_canonical_origin(repository)
    receipt = verify_release_candidate(
        repository,
        commit=commit,
        version=version,
        remote="origin",
        branch="main",
    )
    run = _github_run(repository, preflight_run_id)
    run_receipt = validate_preflight_run(
        run,
        version=version,
        commit=commit,
        protected_head=receipt["protected_head"],
    )
    policy_receipt, expected_push_public = _repository_tag_policy(
        repository,
        protected_head=receipt["protected_head"],
    )
    if not push:
        return {
            **receipt,
            "preflight_run": run_receipt,
            "tag_policy": policy_receipt,
            "tag_created": False,
            "tag_pushed": False,
        }

    if push_key is None:
        raise PreflightError("--push requires --push-key for the controlled deploy key")
    push_environment = _tag_push_environment(
        repository,
        private_key=push_key,
        expected_public=expected_push_public,
    )

    tag = receipt["tag"]
    message = (
        f"LiquiLens Evidence Carrier {tag}\n\n"
        f"Candidate: {commit}\n"
        f"Release preflight: {run_receipt['url']}"
    )
    _command(
        ["git", "tag", "-s", "-a", tag, commit, "-m", message],
        repository=repository,
    )

    protected_head = receipt["protected_head"]
    signer_text = _command(
        ["git", "show", f"{protected_head}:{SIGNER_PATH}"],
        repository=repository,
    ).stdout
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as signer_file:
        signer_file.write(signer_text)
        signer_file.flush()
        _command(
            [
                "git",
                "-c",
                "gpg.format=ssh",
                "-c",
                f"gpg.ssh.allowedSignersFile={signer_file.name}",
                "verify-tag",
                "--raw",
                tag,
            ],
            repository=repository,
        )

    local_peeled = _command(
        ["git", "rev-parse", f"refs/tags/{tag}^{{commit}}"],
        repository=repository,
    ).stdout.strip()
    if local_peeled != commit:
        raise PreflightError("signed tag does not peel to the candidate before push")
    tag_object = _command(
        ["git", "rev-parse", f"refs/tags/{tag}"], repository=repository
    ).stdout.strip()
    _command(
        [
            "git",
            "push",
            GITHUB_SSH_REPOSITORY,
            f"refs/tags/{tag}:refs/tags/{tag}",
        ],
        repository=repository,
        environment=push_environment,
    )
    remote_refs = _command(
        [
            "git",
            "ls-remote",
            "--tags",
            GITHUB_HTTPS_REPOSITORY,
            f"refs/tags/{tag}",
            f"refs/tags/{tag}^{{}}",
        ],
        repository=repository,
    ).stdout.splitlines()
    validate_remote_tag_refs(
        remote_refs,
        tag=tag,
        tag_object=tag_object,
        commit=commit,
    )
    return {
        **receipt,
        "preflight_run": run_receipt,
        "tag_policy": policy_receipt,
        "tag_object": tag_object,
        "tag_created": True,
        "tag_pushed": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a signed release tag after a successful preflight."
    )
    parser.add_argument("--commit", required=True, help="Exact candidate commit SHA")
    parser.add_argument("--version", required=True, help="Release version without v")
    parser.add_argument("--preflight-run-id", required=True, help="Successful run ID")
    parser.add_argument("--repository", default=".", help="Git repository path")
    parser.add_argument(
        "--push",
        action="store_true",
        help="Create and push the signed tag; otherwise only validate",
    )
    parser.add_argument(
        "--push-key",
        type=Path,
        help="Private deploy key matching protected tag-push public key",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        receipt = create_release_tag(
            Path(arguments.repository),
            commit=arguments.commit,
            version=arguments.version,
            preflight_run_id=arguments.preflight_run_id,
            push=arguments.push,
            push_key=arguments.push_key,
        )
    except PreflightError as exc:
        print(f"release tag creation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
