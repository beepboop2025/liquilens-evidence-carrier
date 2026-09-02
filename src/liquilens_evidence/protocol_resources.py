"""Installed access to the versioned JSON protocol artifacts."""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, files
from pathlib import Path, PurePosixPath
from typing import Any

_DISTRIBUTION = "liquilens-evidence"
_ALLOWED_SUFFIXES = (".schema.json", ".json", ".mjs")


def protocol_path(name: str) -> Path:
    """Locate one packaged protocol artifact without accepting path traversal."""

    relative = PurePosixPath(name)
    if (
        relative.is_absolute()
        or "\\" in name
        or any(part in {"", ".", ".."} for part in relative.parts)
        or not name.endswith(_ALLOWED_SUFFIXES)
    ):
        raise ValueError("protocol resource name is invalid")
    # A source checkout may coexist with an older installed distribution. Prefer
    # the protocol beside the imported source module so code and schema versions
    # cannot be silently mixed during development or replay validation.
    source_fallback = (
        Path(__file__).resolve().parents[2] / "protocol" / Path(*relative.parts)
    )
    if source_fallback.is_file():
        return source_fallback
    try:
        distribution_files = files(_DISTRIBUTION)
    except PackageNotFoundError:
        distribution_files = None
    if distribution_files is not None:
        for resource in distribution_files:
            normalized = str(resource).replace("\\", "/")
            if normalized.endswith(f"share/liquilens_evidence/protocol/{name}"):
                return Path(resource.locate())
    raise FileNotFoundError(f"protocol resource is not installed: {name}")


def load_protocol_json(name: str) -> dict[str, Any]:
    """Load a packaged schema or vector as a JSON object."""

    value = json.loads(protocol_path(name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"protocol resource is not a JSON object: {name}")
    return value
