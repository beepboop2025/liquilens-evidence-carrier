from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from liquilens_evidence.evidence_carrier import issue_evidence_carrier
from liquilens_evidence.fleet_brief import issue_fleet_brief
from liquilens_evidence.mcp_server import (
    MCP_LEGACY_PROTOCOL_VERSION,
    MCP_PROTOCOL_VERSION,
    MCP_SERVER_NAME,
    EvidenceCarrierMCPServer,
)

ROOT = Path(__file__).resolve().parents[1]
EVALUATED_AT = "2026-08-25T00:00:00Z"


def _carrier_file(directory: Path, name: str = "carrier.json") -> Path:
    descriptor = json.loads((ROOT / "examples/descriptor.json").read_text())
    carrier = issue_evidence_carrier(**descriptor)
    path = directory / name
    path.write_text(json.dumps(carrier), encoding="utf-8")
    return path


def _brief_file(directory: Path, name: str = "fleet-brief.json") -> Path:
    carrier_path = _carrier_file(directory, "fleet-brief-input.carrier.json")
    carrier = json.loads(carrier_path.read_text())
    brief = issue_fleet_brief(
        carriers={"liquilens": carrier},
        evaluated_at=datetime.fromisoformat(EVALUATED_AT),
    )
    path = directory / name
    path.write_text(json.dumps(brief), encoding="utf-8")
    return path


def _modern_request(
    request_id: int | str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    version: str = MCP_PROTOCOL_VERSION,
) -> dict[str, Any]:
    request_params = dict(params or {})
    request_params["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {
            "name": "carrier-test-client",
            "version": "1.0.0",
        },
    }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": request_params,
    }


def _result(response: dict[str, Any] | None) -> dict[str, Any]:
    assert response is not None
    assert "error" not in response
    result = response["result"]
    assert isinstance(result, dict)
    return result


def test_modern_discover_list_call_and_resources(tmp_path: Path) -> None:
    carrier_path = _carrier_file(tmp_path)
    brief_path = _brief_file(tmp_path)
    server = EvidenceCarrierMCPServer(tmp_path)

    discovered = _result(server.handle(_modern_request(1, "server/discover")))
    assert discovered["supportedVersions"] == [MCP_PROTOCOL_VERSION]
    assert discovered["resultType"] == "complete"
    assert discovered["capabilities"] == {
        "resources": {"listChanged": False, "subscribe": False},
        "tools": {"listChanged": False},
    }
    assert discovered["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == (
        MCP_SERVER_NAME
    )

    listed = _result(server.handle(_modern_request(2, "tools/list")))
    assert [tool["name"] for tool in listed["tools"]] == [
        "verify_carrier",
        "project_carrier",
        "verify_fleet_brief",
        "verify_trade_safety_receipt",
    ]
    assert all(tool["annotations"]["readOnlyHint"] for tool in listed["tools"])
    assert all(not tool["annotations"]["openWorldHint"] for tool in listed["tools"])

    verified = _result(
        server.handle(
            _modern_request(
                3,
                "tools/call",
                {
                    "name": "verify_carrier",
                    "arguments": {
                        "path": carrier_path.name,
                        "evaluated_at": EVALUATED_AT,
                    },
                },
            )
        )
    )
    assert verified["isError"] is False
    assert verified["structuredContent"]["ok"] is True
    assert verified["structuredContent"]["export_disposition"] == "full"
    assert verified["structuredContent"]["authority"] == {
        "financial_authority": "none",
        "can_execute": False,
        "can_recommend": False,
        "is_credit_rating": False,
    }

    projected = _result(
        server.handle(
            _modern_request(
                4,
                "tools/call",
                {
                    "name": "project_carrier",
                    "arguments": {
                        "path": carrier_path.name,
                        "format": "flat",
                        "evaluated_at": EVALUATED_AT,
                    },
                },
            )
        )
    )
    assert projected["isError"] is False
    projection = projected["structuredContent"]["projection"]
    assert projection["evidence_schema_url"].endswith(
        "liquilens-evidence-carrier-v1.schema.json"
    )
    assert projection["export_disposition"] == "full"

    brief_verified = _result(
        server.handle(
            _modern_request(
                5,
                "tools/call",
                {
                    "name": "verify_fleet_brief",
                    "arguments": {
                        "path": brief_path.name,
                        "evaluated_at": EVALUATED_AT,
                    },
                },
            )
        )
    )
    assert brief_verified["isError"] is False
    assert brief_verified["structuredContent"]["states"] == {
        "liquilens": "full",
        "seiche": "missing",
        "undertow": "missing",
        "palimpsest": "missing",
    }
    assert brief_verified["structuredContent"]["authority"]["can_execute"] is False

    resources = _result(server.handle(_modern_request(6, "resources/list")))
    assert len(resources["resources"]) == 8
    uri = resources["resources"][0]["uri"]
    read = _result(server.handle(_modern_request(7, "resources/read", {"uri": uri})))
    assert json.loads(read["contents"][0]["text"])["$schema"].startswith(
        "https://json-schema.org/"
    )


def test_legacy_initialize_list_and_call(tmp_path: Path) -> None:
    carrier_path = _carrier_file(tmp_path)
    server = EvidenceCarrierMCPServer(tmp_path)
    initialized = _result(
        server.handle(
            {
                "jsonrpc": "2.0",
                "id": "init",
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_LEGACY_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "legacy-test", "version": "1.0.0"},
                },
            }
        )
    )
    assert initialized["protocolVersion"] == MCP_LEGACY_PROTOCOL_VERSION
    assert initialized["serverInfo"]["name"] == MCP_SERVER_NAME
    assert (
        server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    )

    listed = _result(
        server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    )
    assert "resultType" not in listed
    assert len(listed["tools"]) == 4

    called = _result(
        server.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "verify_carrier",
                    "arguments": {
                        "path": carrier_path.name,
                        "evaluated_at": EVALUATED_AT,
                    },
                },
            }
        )
    )
    assert "resultType" not in called
    assert called["structuredContent"]["ok"] is True


def test_fail_closed_path_protocol_and_tool_errors(tmp_path: Path) -> None:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    outside = _carrier_file(tmp_path, "outside.json")
    server = EvidenceCarrierMCPServer(allowed_root)

    outside_result = _result(
        server.handle(
            _modern_request(
                1,
                "tools/call",
                {
                    "name": "verify_carrier",
                    "arguments": {"path": str(outside), "evaluated_at": EVALUATED_AT},
                },
            )
        )
    )
    assert outside_result["isError"] is True
    assert "escapes the configured root" in outside_result["content"][0]["text"]

    unsupported = server.handle(_modern_request(2, "tools/list", version="1900-01-01"))
    assert unsupported is not None
    assert unsupported["error"] == {
        "code": -32022,
        "message": "Unsupported protocol version",
        "data": {
            "supported": [MCP_PROTOCOL_VERSION],
            "requested": "1900-01-01",
        },
    }

    missing_meta = EvidenceCarrierMCPServer(allowed_root).handle(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}
    )
    assert missing_meta is not None
    assert missing_meta["error"]["code"] == -32602

    unknown = server.handle(
        _modern_request(4, "tools/call", {"name": "place_trade", "arguments": {}})
    )
    assert unknown is not None
    assert unknown["error"]["code"] == -32602

    brief_path = _brief_file(allowed_root)
    implicit_clock = _result(
        server.handle(
            _modern_request(
                5,
                "tools/call",
                {
                    "name": "verify_fleet_brief",
                    "arguments": {
                        "path": brief_path.name,
                        "evaluated_at": None,
                    },
                },
            )
        )
    )
    assert implicit_clock["isError"] is True
    assert "must be explicit" in implicit_clock["content"][0]["text"]


def test_stdio_is_newline_delimited_json_only(tmp_path: Path) -> None:
    carrier_path = _carrier_file(tmp_path)
    messages = [
        _modern_request(1, "server/discover"),
        _modern_request(2, "tools/list"),
        _modern_request(
            3,
            "tools/call",
            {
                "name": "verify_carrier",
                "arguments": {
                    "path": carrier_path.name,
                    "evaluated_at": EVALUATED_AT,
                },
            },
        ),
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}},
    ]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "liquilens_evidence.mcp_server",
            "--root",
            str(tmp_path),
        ],
        cwd=ROOT,
        env=environment,
        input="".join(json.dumps(message) + "\n" for message in messages),
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    lines = completed.stdout.splitlines()
    assert len(lines) == 3
    responses = [json.loads(line) for line in lines]
    assert [response["id"] for response in responses] == [1, 2, 3]
    assert responses[2]["result"]["structuredContent"]["ok"] is True
    assert all("\n" not in line for line in lines)


def test_deterministic_mcpb_runs_from_extracted_bundle(tmp_path: Path) -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    first = tmp_path / f"liquilens-evidence-carrier-mcp-{version}.mcpb"
    second = tmp_path / "second.mcpb"
    for output in (first, second):
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/build_mcpb.py",
                "--output",
                str(output),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        assert completed.returncode == 0, completed.stderr
    assert first.read_bytes() == second.read_bytes()

    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(first) as archive:
        names = set(archive.namelist())
        offline_adoption_assets = {
            "protocol/verify_hash_tree_v1.mjs",
            "integrations/fdc3/com.liquilens.trade-safety-receipt.schema.json",
            "integrations/fdc3/trade-safety-intents.json",
        }
        assert {
            "manifest.json",
            "src/liquilens_evidence/mcp_server.py",
            *offline_adoption_assets,
        } <= names
        assert not any(
            name.startswith("integrations/trade-safety-gateway/") for name in names
        )
        for asset in offline_adoption_assets:
            assert archive.read(asset) == (ROOT / asset).read_bytes()
        assert json.loads(archive.read("manifest.json"))["version"] == version
        embedded_readme = archive.read("README.md").decode()
        assert f"bytes prepared for the v{version} MCPB" in embedded_readme
        assert "is not publication proof" in embedded_readme
        assert "not embedded in or started by this offline MCPB" in embedded_readme
        archive.extractall(extracted)
    carrier_path = _carrier_file(tmp_path, "bundle-carrier.json")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(extracted / "src")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "liquilens_evidence.mcp_server",
            "--root",
            str(tmp_path),
        ],
        cwd=extracted,
        env=environment,
        input=json.dumps(
            _modern_request(
                1,
                "tools/call",
                {
                    "name": "verify_carrier",
                    "arguments": {
                        "path": carrier_path.name,
                        "evaluated_at": EVALUATED_AT,
                    },
                },
            )
        )
        + "\n",
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    response = json.loads(completed.stdout)
    assert response["result"]["structuredContent"]["ok"] is True


@pytest.mark.skipif(
    "MCP_PROTOCOL_SCHEMA" not in os.environ,
    reason="official protocol schema is injected by CI",
)
def test_modern_responses_validate_against_official_schema(tmp_path: Path) -> None:
    schema = json.loads(Path(os.environ["MCP_PROTOCOL_SCHEMA"]).read_text())
    server = EvidenceCarrierMCPServer(tmp_path)
    carrier_path = _carrier_file(tmp_path)
    cases = [
        (
            "DiscoverResultResponse",
            server.handle(_modern_request(1, "server/discover")),
        ),
        ("ListToolsResultResponse", server.handle(_modern_request(2, "tools/list"))),
        (
            "CallToolResultResponse",
            server.handle(
                _modern_request(
                    3,
                    "tools/call",
                    {
                        "name": "verify_carrier",
                        "arguments": {
                            "path": carrier_path.name,
                            "evaluated_at": EVALUATED_AT,
                        },
                    },
                )
            ),
        ),
        (
            "ListResourcesResultResponse",
            server.handle(_modern_request(4, "resources/list")),
        ),
        (
            "ReadResourceResultResponse",
            server.handle(
                _modern_request(
                    5,
                    "resources/read",
                    {"uri": "liquilens-evidence://protocol/catalog"},
                )
            ),
        ),
    ]
    for definition, response in cases:
        assert response is not None
        validation_schema = dict(schema)
        validation_schema["$ref"] = f"#/$defs/{definition}"
        jsonschema.Draft202012Validator(validation_schema).validate(response)
