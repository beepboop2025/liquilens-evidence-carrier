"""Cross-language canonical hashing for LiquiLens protocol objects."""

from __future__ import annotations

import json
import struct
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from typing import Any


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _hash_tree(value: Any) -> list[Any]:
    """Build the typed tree defined by ``liquilens-hash-tree-v1``."""

    if isinstance(value, StrEnum):
        return ["string", value.value]
    if isinstance(value, datetime):
        return ["string", _utc_text(value)]
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["boolean", value]
    if isinstance(value, int):
        return ["integer", str(value)]
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("protocol objects cannot contain NaN or infinity")
        normalized = 0.0 if value == 0.0 else value
        return ["float64", struct.pack(">d", normalized).hex()]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, Mapping):
        converted: dict[str, Any] = {}
        for key, item in value.items():
            canonical_key = str(key)
            if canonical_key in converted:
                raise ValueError(f"duplicate canonical object key: {canonical_key!r}")
            converted[canonical_key] = item
        return [
            "object",
            [[key, _hash_tree(converted[key])] for key in sorted(converted)],
        ]
    if isinstance(value, (tuple, list)):
        return ["array", [_hash_tree(item) for item in value]]
    raise TypeError(f"unsupported protocol value: {type(value).__name__}")


def canonical_hash_bytes(value: Any) -> bytes:
    """Return the portable bytes used by LiquiLens SHA-256 identities."""

    return json.dumps(
        _hash_tree(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


__all__ = ["canonical_hash_bytes"]
