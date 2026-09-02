#!/usr/bin/env python3
"""Build and verify the deterministic GitHub-release MCPB artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import tomllib
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXED_ZIP_TIME = (2026, 8, 24, 0, 0, 0)
_OFFLINE_ADOPTION_ASSETS = (
    Path("protocol/verify_hash_tree_v1.mjs"),
    Path("integrations/fdc3/com.liquilens.trade-safety-receipt.schema.json"),
    Path("integrations/fdc3/trade-safety-intents.json"),
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return value


def _version() -> str:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    if not isinstance(version, str):
        raise SystemExit("project.version must be a string")
    return version


def _archive_files(version: str) -> list[tuple[Path, str]]:
    # A release README is part of the immutable MCPB byte contract. Keep it
    # separate from the repository README so truthful post-release status
    # updates do not silently redefine an already-published bundle.
    release_readme = ROOT / "mcpb/release-readmes" / f"{version}.md"
    files: list[tuple[Path, str]] = [
        (ROOT / "mcpb/manifest.json", "manifest.json"),
        (ROOT / "LICENSE", "LICENSE"),
        (ROOT / "NOTICE", "NOTICE"),
        (release_readme, "README.md"),
    ]
    files.extend(
        (path, path.relative_to(ROOT).as_posix())
        for path in sorted((ROOT / "src/liquilens_evidence").glob("*.py"))
    )
    files.append(
        (
            ROOT / "src/liquilens_evidence/py.typed",
            "src/liquilens_evidence/py.typed",
        )
    )
    files.extend(
        (path, path.relative_to(ROOT).as_posix())
        for path in sorted((ROOT / "protocol").glob("*.json"))
    )
    # Keep offline validation and discovery self-contained while excluding the
    # separately deployed, network-capable gateway implementation.
    files.extend(
        (ROOT / relative_path, relative_path.as_posix())
        for relative_path in _OFFLINE_ADOPTION_ASSETS
    )
    missing = [str(path) for path, _name in files if not path.is_file()]
    if missing:
        raise SystemExit(f"MCPB source file missing: {', '.join(missing)}")
    return sorted(files, key=lambda item: item[1])


def build(output: Path) -> str:
    version = _version()
    manifest = _load_json(ROOT / "mcpb/manifest.json")
    if manifest.get("version") != version:
        raise SystemExit("MCPB manifest version differs from project version")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        output, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for source, archive_name in _archive_files(version):
            info = zipfile.ZipInfo(archive_name, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes(), compresslevel=9)
    return hashlib.sha256(output.read_bytes()).hexdigest()


def check_registry_metadata(output: Path, digest: str) -> None:
    version = _version()
    metadata = _load_json(ROOT / "server.json")
    if metadata.get("version") != version:
        raise SystemExit("server.json version differs from project version")
    packages = metadata.get("packages")
    if not isinstance(packages, list) or len(packages) != 1:
        raise SystemExit("server.json must contain exactly one MCPB package")
    package = packages[0]
    if not isinstance(package, dict) or package.get("registryType") != "mcpb":
        raise SystemExit("server.json package must use registryType mcpb")
    if "registryBaseUrl" in package:
        raise SystemExit("MCPB packages must not set registryBaseUrl")
    expected_name = f"liquilens-evidence-carrier-mcp-{version}.mcpb"
    if output.name != expected_name:
        raise SystemExit(f"MCPB filename must be {expected_name}")
    expected_url = (
        "https://github.com/beepboop2025/liquilens-evidence-carrier/"
        f"releases/download/v{version}/{expected_name}"
    )
    if package.get("identifier") != expected_url:
        raise SystemExit("server.json MCPB release URL is not exact")
    if package.get("fileSha256") != digest:
        raise SystemExit("server.json fileSha256 differs from deterministic MCPB")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-registry-metadata", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    digest = build(args.output)
    if args.check_registry_metadata:
        check_registry_metadata(args.output, digest)
    print(f"{digest}  {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
