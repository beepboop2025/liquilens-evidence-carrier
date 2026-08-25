"""Dependency-free MCP stdio server for local evidence carrier inspection.

The server is deliberately offline and read-only. It reads only explicit JSON
paths below a configured root, verifies them with the carrier contract, and
projects verified values through the package's existing rights-aware adapters.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .evidence_carrier import (
    EVIDENCE_CARRIER_MAX_BYTES,
    EvidenceCarrierError,
    VerifiedEvidenceCarrier,
    to_arrow_metadata,
    to_cloudevent,
    to_csl_json,
    to_fdc3_context,
    to_flat_row,
    to_jsonld,
    to_openlineage_facet,
    to_otel_log,
    verify_evidence_carrier,
)
from .fleet_brief import FLEET_BRIEF_MAX_BYTES, verify_fleet_brief
from .protocol_resources import protocol_path

MCP_PROTOCOL_VERSION = "2026-07-28"
MCP_LEGACY_PROTOCOL_VERSION = "2025-11-25"
MCP_SERVER_NAME = "io.github.beepboop2025/liquilens-evidence-carrier"
MCP_MAX_MESSAGE_BYTES = 2_097_152

_SERVER_INFO: dict[str, Any] = {
    "name": MCP_SERVER_NAME,
    "title": "LiquiLens Evidence Carrier",
    "version": __version__,
    "description": "Offline verification of local evidence carriers and fleet briefs.",
    "websiteUrl": "https://liquilens.in/protocol/",
}
_SERVER_CAPABILITIES: dict[str, Any] = {
    "resources": {"listChanged": False, "subscribe": False},
    "tools": {"listChanged": False},
}
_INSTRUCTIONS = (
    "Read-only and offline. Verify local carrier or fleet-brief JSON. "
    "A valid carrier may still be non-exportable: preserve export_disposition, "
    "reason_codes, rights, clocks, and the all-false authority boundary. This "
    "server does not fetch market data, combine product scores, recommend, rate "
    "credit, or execute trades."
)

_PROJECTION_FORMATS = (
    "arrow",
    "cloudevent",
    "csl",
    "fdc3",
    "flat",
    "jsonld",
    "openlineage",
    "otel",
)

_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "verify_carrier",
        "title": "Verify local evidence carrier",
        "description": (
            "Verify one local carrier JSON file under the configured root. "
            "Returns identity and rights-aware export disposition, never market advice."
        ),
        "inputSchema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Carrier JSON path, relative to the allowed root or absolute within it.",
                },
                "evaluated_at": {
                    "type": "string",
                    "description": (
                        "Optional policy evaluation clock as an RFC 3339 UTC timestamp ending in Z. "
                        "The current UTC time is recorded when omitted."
                    ),
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "project_carrier",
        "title": "Project verified evidence carrier",
        "description": (
            "Verify one local carrier and project it to a bounded transport format. "
            "Restricted, unsafe, or expired payloads remain blocked or redacted by policy."
        ),
        "inputSchema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Carrier JSON path, relative to the allowed root or absolute within it.",
                },
                "format": {"type": "string", "enum": list(_PROJECTION_FORMATS)},
                "evaluated_at": {
                    "type": "string",
                    "description": (
                        "Optional policy evaluation clock as an RFC 3339 UTC timestamp ending in Z. "
                        "The current UTC time is recorded when omitted."
                    ),
                },
            },
            "required": ["path", "format"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "verify_fleet_brief",
        "title": "Verify local LiquiLens fleet brief",
        "description": (
            "Verify one local, content-addressed fleet brief under the configured root. "
            "Replays the exact recorded evaluation clock without fetching or mutating data."
        ),
        "inputSchema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Fleet brief JSON path, relative to the allowed root or absolute within it.",
                },
                "evaluated_at": {
                    "type": "string",
                    "description": (
                        "Required RFC 3339 UTC clock ending in Z; it must match the brief."
                    ),
                },
            },
            "required": ["path", "evaluated_at"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
]

_RESOURCE_FILES: dict[str, tuple[str, str, str]] = {
    "liquilens-evidence://protocol/full-schema": (
        "Evidence Carrier full schema",
        "liquilens-evidence-carrier-v1.schema.json",
        "JSON Schema for fully disclosable evidence carriers.",
    ),
    "liquilens-evidence://protocol/reference-schema": (
        "Evidence Carrier reference schema",
        "liquilens-evidence-carrier-reference-v1.schema.json",
        "JSON Schema for rights-bounded metadata-only references.",
    ),
    "liquilens-evidence://protocol/fleet-brief-schema": (
        "LiquiLens Fleet Brief schema",
        "liquilens-fleet-brief-v1.schema.json",
        "JSON Schema for four-product, rights-aware fleet briefs.",
    ),
    "liquilens-evidence://protocol/catalog": (
        "Evidence Carrier protocol catalog",
        "catalog.json",
        "Version and SHA-256 catalog for the installed protocol artifacts.",
    ),
}


class MCPInputError(ValueError):
    """A safe, user-correctable local input failure."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MCPInputError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _evaluated_at(value: Any) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if not isinstance(value, str) or not value.endswith("Z"):
        raise MCPInputError("evaluated_at must be a UTC timestamp ending in Z")
    try:
        instant = datetime.fromisoformat(value)
    except ValueError as error:
        raise MCPInputError("evaluated_at is not a valid timestamp") from error
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise MCPInputError("evaluated_at must be timezone-aware")
    return instant.astimezone(UTC)


def _project(format_name: str, verified: VerifiedEvidenceCarrier) -> Any:
    adapters: dict[str, Callable[[VerifiedEvidenceCarrier], Any]] = {
        "cloudevent": to_cloudevent,
        "csl": to_csl_json,
        "fdc3": to_fdc3_context,
        "flat": to_flat_row,
        "jsonld": to_jsonld,
        "openlineage": to_openlineage_facet,
        "otel": to_otel_log,
    }
    if format_name == "arrow":
        return {
            key.decode("utf-8"): value.decode("utf-8")
            for key, value in to_arrow_metadata(verified).items()
        }
    adapter = adapters.get(format_name)
    if adapter is None:
        raise MCPInputError(f"format must be one of: {', '.join(_PROJECTION_FORMATS)}")
    return adapter(verified)


class EvidenceCarrierMCPServer:
    """Small dual-era MCP server with no third-party runtime dependencies."""

    def __init__(self, allowed_root: Path) -> None:
        try:
            root = allowed_root.expanduser().resolve(strict=True)
        except OSError as error:
            raise MCPInputError(
                f"allowed root is unavailable: {allowed_root}"
            ) from error
        if not root.is_dir():
            raise MCPInputError(f"allowed root is not a directory: {root}")
        self.allowed_root = root
        self._legacy_initialized = False

    @staticmethod
    def _error(
        request_id: Any,
        code: int,
        message: str,
        data: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = dict(data)
        return {"jsonrpc": "2.0", "id": request_id, "error": error}

    @staticmethod
    def _modern_result(result: Mapping[str, Any]) -> dict[str, Any]:
        adapted = dict(result)
        adapted.setdefault("resultType", "complete")
        adapted["_meta"] = {"io.modelcontextprotocol/serverInfo": dict(_SERVER_INFO)}
        return adapted

    @staticmethod
    def _legacy_result(result: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in result.items()
            if key not in {"resultType", "cacheScope", "ttlMs", "_meta"}
        }

    def _success(
        self, request_id: Any, result: Mapping[str, Any], *, modern: bool
    ) -> dict[str, Any]:
        adapted = self._modern_result(result) if modern else self._legacy_result(result)
        return {"jsonrpc": "2.0", "id": request_id, "result": adapted}

    def _resolve_json(
        self,
        path_value: Any,
        *,
        max_bytes: int,
        artifact_name: str,
    ) -> tuple[Path, dict[str, Any], int]:
        if not isinstance(path_value, str) or not path_value.strip():
            raise MCPInputError("path must be a non-blank string")
        if "\x00" in path_value:
            raise MCPInputError("path contains a null byte")
        supplied = Path(path_value).expanduser()
        candidate = supplied if supplied.is_absolute() else self.allowed_root / supplied
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as error:
            raise MCPInputError(f"{artifact_name} path is unavailable") from error
        if not resolved.is_relative_to(self.allowed_root):
            raise MCPInputError(f"{artifact_name} path escapes the configured root")
        try:
            file_stat = resolved.stat()
        except OSError as error:
            raise MCPInputError(f"{artifact_name} path cannot be inspected") from error
        if not stat.S_ISREG(file_stat.st_mode):
            raise MCPInputError(f"{artifact_name} path is not a regular file")
        if file_stat.st_size > max_bytes:
            raise MCPInputError(f"{artifact_name} JSON exceeds its byte limit")
        try:
            raw = resolved.read_bytes()
        except OSError as error:
            raise MCPInputError(f"{artifact_name} JSON cannot be read") from error
        if len(raw) > max_bytes:
            raise MCPInputError(f"{artifact_name} JSON exceeds its byte limit")
        try:
            value = json.loads(raw, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise MCPInputError(
                f"{artifact_name} input is not valid unique-key UTF-8 JSON"
            ) from error
        if not isinstance(value, dict):
            raise MCPInputError(f"{artifact_name} JSON root must be an object")
        return resolved, value, len(raw)

    def _resolve_carrier(self, path_value: Any) -> tuple[Path, dict[str, Any], int]:
        return self._resolve_json(
            path_value,
            max_bytes=EVIDENCE_CARRIER_MAX_BYTES,
            artifact_name="carrier",
        )

    def _verify_tool(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"path", "evaluated_at"}
        extra = set(arguments) - allowed
        if extra:
            raise MCPInputError(
                f"verify_carrier has unsupported arguments: {', '.join(sorted(extra))}"
            )
        if "path" not in arguments:
            raise MCPInputError("verify_carrier requires path")
        path, carrier, byte_count = self._resolve_carrier(arguments["path"])
        evaluated_at = _evaluated_at(arguments.get("evaluated_at"))
        verified = verify_evidence_carrier(carrier, evaluated_at=evaluated_at)
        return {
            "ok": True,
            "carrier_id": verified.carrier["carrier_id"],
            "record_hash": verified.carrier["record_hash"],
            "export_disposition": verified.disposition.value,
            "reason_codes": list(verified.reason_codes),
            "policy_version": verified.policy_version,
            "evaluated_at": _utc_text(evaluated_at),
            "source_path": str(path),
            "source_bytes": byte_count,
            "authority": dict(verified.carrier["authority"]),
        }

    def _project_tool(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"path", "format", "evaluated_at"}
        extra = set(arguments) - allowed
        if extra:
            raise MCPInputError(
                f"project_carrier has unsupported arguments: {', '.join(sorted(extra))}"
            )
        if "path" not in arguments or "format" not in arguments:
            raise MCPInputError("project_carrier requires path and format")
        format_name = arguments["format"]
        if not isinstance(format_name, str):
            raise MCPInputError("format must be a string")
        path, carrier, _byte_count = self._resolve_carrier(arguments["path"])
        evaluated_at = _evaluated_at(arguments.get("evaluated_at"))
        verified = verify_evidence_carrier(carrier, evaluated_at=evaluated_at)
        return {
            "ok": True,
            "carrier_id": verified.carrier["carrier_id"],
            "record_hash": verified.carrier["record_hash"],
            "export_disposition": verified.disposition.value,
            "reason_codes": list(verified.reason_codes),
            "policy_version": verified.policy_version,
            "evaluated_at": _utc_text(evaluated_at),
            "source_path": str(path),
            "format": format_name,
            "projection": _project(format_name, verified),
            "authority": dict(verified.carrier["authority"]),
        }

    def _verify_fleet_brief_tool(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"path", "evaluated_at"}
        extra = set(arguments) - allowed
        if extra:
            raise MCPInputError(
                "verify_fleet_brief has unsupported arguments: "
                + ", ".join(sorted(extra))
            )
        if "path" not in arguments or "evaluated_at" not in arguments:
            raise MCPInputError("verify_fleet_brief requires path and evaluated_at")
        if not isinstance(arguments["evaluated_at"], str):
            raise MCPInputError("verify_fleet_brief evaluated_at must be explicit")
        path, brief, byte_count = self._resolve_json(
            arguments["path"],
            max_bytes=FLEET_BRIEF_MAX_BYTES,
            artifact_name="fleet brief",
        )
        evaluated_at = _evaluated_at(arguments["evaluated_at"])
        verified = verify_fleet_brief(brief, evaluated_at=evaluated_at)
        value = verified.brief
        return {
            "ok": True,
            "brief_id": value["brief_id"],
            "record_hash": value["record_hash"],
            "states": verified.states,
            "evaluated_at": value["evaluated_at"],
            "source_path": str(path),
            "source_bytes": byte_count,
            "authority": dict(value["authority"]),
        }

    def _tool_result(
        self, name: str, arguments: Mapping[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        handlers: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
            "project_carrier": self._project_tool,
            "verify_carrier": self._verify_tool,
            "verify_fleet_brief": self._verify_fleet_brief_tool,
        }
        handler = handlers.get(name)
        if handler is None:
            return None, {"tool": name}
        try:
            structured = handler(arguments)
        except (EvidenceCarrierError, MCPInputError, TypeError, ValueError) as error:
            message = str(error) or error.__class__.__name__
            return {
                "resultType": "complete",
                "content": [
                    {"type": "text", "text": f"Evidence operation failed: {message}"}
                ],
                "structuredContent": {
                    "ok": False,
                    "error": {"code": "carrier_input_rejected", "message": message},
                },
                "isError": True,
            }, None
        if name == "verify_fleet_brief":
            summary = f"Verified fleet brief {structured['brief_id']}."
        else:
            summary = (
                f"Verified {structured['carrier_id']}: "
                f"export disposition {structured['export_disposition']}."
            )
            if name == "project_carrier":
                summary += f" Projected as {structured['format']}."
        return {
            "resultType": "complete",
            "content": [{"type": "text", "text": summary}],
            "structuredContent": structured,
            "isError": False,
        }, None

    @staticmethod
    def _modern_meta(params: Any) -> tuple[bool, str | None]:
        if not isinstance(params, dict):
            return False, "params must be an object"
        meta = params.get("_meta")
        if not isinstance(meta, dict):
            return False, "params._meta must be an object"
        version = meta.get("io.modelcontextprotocol/protocolVersion")
        if not isinstance(version, str) or not version:
            return False, "protocolVersion metadata is required"
        capabilities = meta.get("io.modelcontextprotocol/clientCapabilities")
        if not isinstance(capabilities, dict):
            return False, "clientCapabilities metadata must be an object"
        client_info = meta.get("io.modelcontextprotocol/clientInfo")
        if client_info is not None:
            if not isinstance(client_info, dict):
                return False, "clientInfo metadata must be an object"
            if not isinstance(client_info.get("name"), str) or not isinstance(
                client_info.get("version"), str
            ):
                return False, "clientInfo metadata requires name and version"
        return True, version

    def _initialize(self, request_id: Any, params: Any) -> dict[str, Any]:
        if not isinstance(params, dict):
            return self._error(
                request_id, -32602, "initialize params must be an object"
            )
        if not isinstance(params.get("protocolVersion"), str):
            return self._error(request_id, -32602, "protocolVersion is required")
        if not isinstance(params.get("capabilities"), dict):
            return self._error(request_id, -32602, "capabilities must be an object")
        client_info = params.get("clientInfo")
        if (
            not isinstance(client_info, dict)
            or not isinstance(client_info.get("name"), str)
            or not isinstance(client_info.get("version"), str)
        ):
            return self._error(
                request_id, -32602, "clientInfo requires string name and version"
            )
        self._legacy_initialized = True
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": MCP_LEGACY_PROTOCOL_VERSION,
                "capabilities": dict(_SERVER_CAPABILITIES),
                "serverInfo": dict(_SERVER_INFO),
                "instructions": _INSTRUCTIONS,
            },
        }

    def _dispatch(
        self,
        request_id: Any,
        method: str,
        params: dict[str, Any],
        *,
        modern: bool,
    ) -> dict[str, Any]:
        if method == "server/discover":
            return self._success(
                request_id,
                {
                    "resultType": "complete",
                    "supportedVersions": [MCP_PROTOCOL_VERSION],
                    "capabilities": dict(_SERVER_CAPABILITIES),
                    "instructions": _INSTRUCTIONS,
                    "ttlMs": 3_600_000,
                    "cacheScope": "public",
                },
                modern=modern,
            )
        if method == "ping":
            return self._success(request_id, {}, modern=modern)
        if method in {"tools/list", "resources/list"} and params.get("cursor"):
            return self._error(
                request_id, -32602, "cursor is not valid for this static list"
            )
        if method == "tools/list":
            return self._success(
                request_id,
                {
                    "resultType": "complete",
                    "tools": list(_TOOL_DEFINITIONS),
                    "ttlMs": 3_600_000,
                    "cacheScope": "public",
                },
                modern=modern,
            )
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(name, str) or not isinstance(arguments, dict):
                return self._error(
                    request_id,
                    -32602,
                    "tools/call requires string name and object arguments",
                )
            result, missing = self._tool_result(name, arguments)
            if missing is not None:
                return self._error(request_id, -32602, "unknown tool", missing)
            if result is None:  # pragma: no cover - tuple invariant
                return self._error(request_id, -32603, "internal tool dispatch error")
            return self._success(request_id, result, modern=modern)
        if method == "resources/list":
            resources: list[dict[str, Any]] = []
            for uri, (name, filename, description) in _RESOURCE_FILES.items():
                path = protocol_path(filename)
                resources.append(
                    {
                        "uri": uri,
                        "name": name,
                        "title": name,
                        "description": description,
                        "mimeType": "application/schema+json"
                        if filename.endswith(".schema.json")
                        else "application/json",
                        "size": path.stat().st_size,
                    }
                )
            return self._success(
                request_id,
                {
                    "resultType": "complete",
                    "resources": resources,
                    "ttlMs": 3_600_000,
                    "cacheScope": "public",
                },
                modern=modern,
            )
        if method == "resources/read":
            resource_uri_value = params.get("uri")
            if not isinstance(resource_uri_value, str):
                return self._error(request_id, -32602, "resources/read requires uri")
            resource = _RESOURCE_FILES.get(resource_uri_value)
            if resource is None:
                return self._error(
                    request_id,
                    -32602,
                    "unknown resource",
                    {"uri": resource_uri_value},
                )
            _name, filename, _description = resource
            path = protocol_path(filename)
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                return self._error(request_id, -32603, "protocol resource unavailable")
            return self._success(
                request_id,
                {
                    "resultType": "complete",
                    "contents": [
                        {
                            "uri": resource_uri_value,
                            "mimeType": "application/schema+json"
                            if filename.endswith(".schema.json")
                            else "application/json",
                            "text": text,
                        }
                    ],
                    "ttlMs": 3_600_000,
                    "cacheScope": "public",
                },
                modern=modern,
            )
        return self._error(request_id, -32601, "method not found", {"method": method})

    def handle(self, message: Any) -> dict[str, Any] | None:
        """Handle one decoded JSON-RPC message."""

        if not isinstance(message, dict):
            return self._error(None, -32600, "invalid request")
        request_id = message.get("id")
        is_notification = "id" not in message
        if message.get("jsonrpc") != "2.0" or not isinstance(
            message.get("method"), str
        ):
            return (
                None
                if is_notification
                else self._error(request_id, -32600, "invalid request")
            )
        if not is_notification and (
            isinstance(request_id, bool) or not isinstance(request_id, (int, str))
        ):
            return self._error(None, -32600, "request id must be a string or integer")
        method = message["method"]
        params_value = message.get("params", {})
        if is_notification:
            if method == "notifications/initialized":
                return None
            if method == "notifications/cancelled":
                return None
            return None
        if method == "initialize":
            return self._initialize(request_id, params_value)

        valid_meta, version_or_error = self._modern_meta(params_value)
        if valid_meta:
            if version_or_error != MCP_PROTOCOL_VERSION:
                return self._error(
                    request_id,
                    -32022,
                    "Unsupported protocol version",
                    {
                        "supported": [MCP_PROTOCOL_VERSION],
                        "requested": version_or_error or "",
                    },
                )
            if not isinstance(params_value, dict):  # pragma: no cover - validated above
                return self._error(request_id, -32602, "params must be an object")
            return self._dispatch(request_id, method, params_value, modern=True)

        if not self._legacy_initialized:
            return self._error(
                request_id, -32602, version_or_error or "missing request metadata"
            )
        if not isinstance(params_value, dict):
            return self._error(request_id, -32602, "params must be an object")
        return self._dispatch(request_id, method, params_value, modern=False)

    def serve(self) -> int:
        """Serve newline-delimited JSON-RPC on stdin/stdout until EOF."""

        while True:
            raw = sys.stdin.buffer.readline(MCP_MAX_MESSAGE_BYTES + 1)
            if not raw:
                return 0
            response: dict[str, Any] | None
            if len(raw) > MCP_MAX_MESSAGE_BYTES:
                while raw and not raw.endswith(b"\n"):
                    raw = sys.stdin.buffer.readline(MCP_MAX_MESSAGE_BYTES + 1)
                response = self._error(None, -32700, "message exceeds byte limit")
            else:
                try:
                    message = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    response = self._error(None, -32700, "parse error")
                else:
                    response = self.handle(message)
            if response is None:
                continue
            try:
                encoded = json.dumps(
                    response,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                sys.stdout.write(encoded + "\n")
                sys.stdout.flush()
            except BrokenPipeError:
                return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="liquilens-evidence-mcp",
        description=(
            "Offline, read-only MCP server for local LiquiLens evidence carriers "
            "and fleet briefs."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(os.environ.get("LIQUILENS_EVIDENCE_ROOT", ".")),
        help="only read carrier JSON paths below this directory (default: current directory)",
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        server = EvidenceCarrierMCPServer(args.root)
    except MCPInputError as error:
        print(f"liquilens-evidence-mcp: {error}", file=sys.stderr)
        return 2
    return server.serve()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
