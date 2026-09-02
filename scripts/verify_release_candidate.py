"""Fail closed before creating an immutable LiquiLens release tag."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
REMOTE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
SIGNER_PATH = "ops/release/liquilens-evidence-carrier.allowed_signers"


class PreflightError(RuntimeError):
    """A release candidate failed a pre-tag invariant."""


def _json(text: str, path: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PreflightError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise PreflightError(f"{path} must contain a JSON object")
    return value


def _toml(text: str, path: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise PreflightError(f"{path} is not valid TOML: {exc}") from exc


def _require_equal(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise PreflightError(f"{label} is {actual!r}; expected {expected!r}")


def _lock_version(lock: dict[str, Any], package_name: str, path: str) -> str:
    matches = [
        package
        for package in lock.get("package", [])
        if isinstance(package, dict) and package.get("name") == package_name
    ]
    if len(matches) != 1:
        raise PreflightError(
            f"{path} must contain exactly one {package_name!r} package entry"
        )
    version = matches[0].get("version")
    if not isinstance(version, str):
        raise PreflightError(f"{path} {package_name!r} version must be a string")
    return version


def validate_candidate_metadata(
    read_text: Callable[[str], str], expected_version: str
) -> dict[str, str]:
    """Validate version-bearing files read from one exact Git commit."""

    if VERSION_PATTERN.fullmatch(expected_version) is None:
        raise PreflightError("version must use MAJOR.MINOR.PATCH digits")

    project = _toml(read_text("pyproject.toml"), "pyproject.toml")
    server = _json(read_text("server.json"), "server.json")
    manifest = _json(read_text("mcpb/manifest.json"), "mcpb/manifest.json")
    plugin = _json(
        read_text("plugins/liquilens-evidence/.codex-plugin/plugin.json"),
        "plugins/liquilens-evidence/.codex-plugin/plugin.json",
    )
    catalog = _json(read_text("protocol/catalog.json"), "protocol/catalog.json")
    gateway = _toml(
        read_text("integrations/trade-safety-gateway/pyproject.toml"),
        "integrations/trade-safety-gateway/pyproject.toml",
    )
    openbb = _toml(
        read_text("integrations/openbb/pyproject.toml"),
        "integrations/openbb/pyproject.toml",
    )
    typescript = _json(
        read_text("integrations/typescript/package.json"),
        "integrations/typescript/package.json",
    )
    root_lock = _toml(read_text("uv.lock"), "uv.lock")
    gateway_lock = _toml(
        read_text("integrations/trade-safety-gateway/uv.lock"),
        "integrations/trade-safety-gateway/uv.lock",
    )

    packages = server.get("packages")
    if not isinstance(packages, list) or len(packages) != 1:
        raise PreflightError("server.json must declare exactly one package")
    package = packages[0]
    if not isinstance(package, dict):
        raise PreflightError("server.json package must be an object")
    expected_asset = (
        "https://github.com/beepboop2025/liquilens-evidence-carrier/releases/"
        f"download/v{expected_version}/liquilens-evidence-carrier-mcp-"
        f"{expected_version}.mcpb"
    )
    digest = package.get("fileSha256")
    if not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None:
        raise PreflightError("server.json fileSha256 must be 64 lowercase hex digits")
    if digest == "0" * 64:
        raise PreflightError("server.json fileSha256 must not be the placeholder")

    init_text = read_text("src/liquilens_evidence/__init__.py")
    init_match = re.search(r'^__version__ = "([^"]+)"$', init_text, re.MULTILINE)
    if init_match is None:
        raise PreflightError("package __version__ declaration is missing")

    citation = read_text("CITATION.cff")
    citation_match = re.search(r"^version:\s*([^\s#]+)\s*$", citation, re.MULTILINE)
    if citation_match is None:
        raise PreflightError("CITATION.cff version declaration is missing")

    gateway_dependencies = gateway.get("project", {}).get("dependencies", [])
    expected_gateway_dependency = f"liquilens-evidence=={expected_version}"
    versions = {
        "VERSION": read_text("VERSION").strip(),
        "pyproject.toml": project.get("project", {}).get("version"),
        "server.json": server.get("version"),
        "mcpb/manifest.json": manifest.get("version"),
        "plugin.json": plugin.get("version"),
        "protocol/catalog.json": catalog.get("release"),
        "package __version__": init_match.group(1),
        "CITATION.cff": citation_match.group(1),
        "uv.lock": _lock_version(root_lock, "liquilens-evidence", "uv.lock"),
        "gateway uv.lock": _lock_version(
            gateway_lock,
            "liquilens-evidence",
            "integrations/trade-safety-gateway/uv.lock",
        ),
    }
    for label, actual in versions.items():
        _require_equal(label, actual, expected_version)
    _require_equal("server.json package URL", package.get("identifier"), expected_asset)
    if expected_gateway_dependency not in gateway_dependencies:
        raise PreflightError(
            "gateway dependency does not pin the exact candidate core version"
        )
    _require_equal(
        "gateway package version",
        gateway.get("project", {}).get("version"),
        "0.1.3",
    )
    _require_equal(
        "OpenBB package version",
        openbb.get("project", {}).get("version"),
        "0.2.0",
    )
    expected_openbb_pin = (
        "liquilens-evidence @ https://github.com/beepboop2025/"
        "liquilens-evidence-carrier/releases/download/v0.18.0/"
        "liquilens_evidence-0.18.0-py3-none-any.whl#sha256="
        "9fbc7ee50f658e2a8d1d880f8f76d73dca8b07ef6f0747df33a7b9fc346495ef"
    )
    if expected_openbb_pin not in openbb.get("project", {}).get("dependencies", []):
        raise PreflightError("OpenBB dependency must pin the released v0.18.0 wheel")
    _require_equal("TypeScript package name", typescript.get("name"), "@liquilens/trade-safety")
    _require_equal("TypeScript package version", typescript.get("version"), "0.1.0")
    if "dependencies" in typescript:
        raise PreflightError("TypeScript runtime must not declare dependencies")
    schema_entries = catalog.get("artifacts", [])
    if not isinstance(schema_entries, list) or not schema_entries:
        raise PreflightError("catalog artifacts must be a non-empty schema list")
    if any(
        not isinstance(artifact, dict)
        or not str(artifact.get("path", "")).endswith(".schema.json")
        for artifact in schema_entries
    ):
        raise PreflightError("catalog artifacts must remain schema-only")
    corpus_entries = [
        artifact
        for artifact in catalog.get("conformance", [])
        if isinstance(artifact, dict)
        and artifact.get("kind") == "conformance-corpus"
    ]
    if len(corpus_entries) != 1:
        raise PreflightError("catalog must declare exactly one conformance corpus")
    corpus_text = read_text("protocol/conformance/trade-safety-v1/corpus.json")
    corpus_digest = hashlib.sha256(corpus_text.encode("utf-8")).hexdigest()
    _require_equal(
        "conformance corpus SHA-256",
        corpus_entries[0].get("sha256"),
        corpus_digest,
    )

    return {
        **versions,
        "gateway package": "0.1.3",
        "OpenBB package": "0.2.0",
        "TypeScript package": "0.1.0",
        "conformance_sha256": corpus_digest,
        "mcpb_sha256": digest,
    }


def _git(
    repository: Path,
    arguments: Sequence[str],
    *,
    accepted: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode not in accepted:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise PreflightError(f"git {' '.join(arguments)} failed: {detail}")
    return completed


def verify_release_candidate(
    repository: Path,
    *,
    commit: str,
    version: str,
    remote: str,
    branch: str,
) -> dict[str, Any]:
    """Verify a candidate before an immutable version tag is created."""

    repository = repository.resolve()
    if COMMIT_PATTERN.fullmatch(commit) is None:
        raise PreflightError("commit must be an exact 40-character lowercase SHA")
    if VERSION_PATTERN.fullmatch(version) is None:
        raise PreflightError("version must use MAJOR.MINOR.PATCH digits")
    if REMOTE_PATTERN.fullmatch(remote) is None:
        raise PreflightError("remote must be a named Git remote, not an option or URL")
    if branch.startswith("-"):
        raise PreflightError("branch must not begin with an option prefix")
    _git(repository, ["check-ref-format", "--branch", branch])
    candidate = _git(
        repository, ["rev-parse", "--verify", f"{commit}^{{commit}}"]
    ).stdout.strip()
    tag = f"v{version}"

    _git(repository, ["fetch", remote, branch, "--no-tags"])
    protected_ref = f"refs/remotes/{remote}/{branch}"
    protected_head = _git(
        repository, ["rev-parse", "--verify", f"{protected_ref}^{{commit}}"]
    ).stdout.strip()
    _git(repository, ["merge-base", "--is-ancestor", candidate, protected_ref])

    signer_bytes = _git(
        repository, ["show", f"{protected_head}:{SIGNER_PATH}"]
    ).stdout
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as signer_file:
        signer_file.write(signer_bytes)
        signer_file.flush()
        _git(
            repository,
            [
                "-c",
                "gpg.format=ssh",
                "-c",
                f"gpg.ssh.allowedSignersFile={signer_file.name}",
                "verify-commit",
                "--raw",
                candidate,
            ],
        )

    def read_blob(path: str) -> str:
        return _git(repository, ["show", f"{candidate}:{path}"]).stdout

    metadata = validate_candidate_metadata(read_blob, version)

    local_tag = _git(
        repository,
        ["show-ref", "--verify", "--quiet", f"refs/tags/{tag}"],
        accepted=(0, 1),
    )
    if local_tag.returncode == 0:
        raise PreflightError(f"local tag {tag} already exists")
    remote_tag = _git(
        repository,
        ["ls-remote", "--exit-code", "--tags", remote, f"refs/tags/{tag}"],
        accepted=(0, 2),
    )
    if remote_tag.returncode == 0:
        raise PreflightError(f"remote tag {tag} already exists")

    return {
        "ok": True,
        "version": version,
        "tag": tag,
        "candidate_commit": candidate,
        "protected_ref": protected_ref,
        "protected_head": protected_head,
        "commit_signature": "allowlisted_ssh",
        "protected_main_ancestor": True,
        "local_tag_absent": True,
        "remote_tag_absent": True,
        "metadata": metadata,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify release identity before creating an immutable tag."
    )
    parser.add_argument("--commit", required=True, help="Exact candidate commit SHA")
    parser.add_argument("--version", required=True, help="Release version without v")
    parser.add_argument("--repository", default=".", help="Git repository path")
    parser.add_argument("--remote", default="origin", help="Protected Git remote")
    parser.add_argument("--branch", default="main", help="Protected branch name")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        receipt = verify_release_candidate(
            Path(arguments.repository),
            commit=arguments.commit,
            version=arguments.version,
            remote=arguments.remote,
            branch=arguments.branch,
        )
    except PreflightError as exc:
        print(f"release preflight failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
