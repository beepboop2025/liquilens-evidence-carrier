import io
import json
from types import SimpleNamespace

import pytest

from liquilens_trading_copilot.agent_mcp import PaperAgentMCP


class Client:
    def __init__(self):
        self.calls = []
        self.error = False

    def call(self, operation, payload):
        self.calls.append((operation, payload))
        return {
            "http_status": 200,
            "result": {"mode": "paper", "tool_error": self.error},
        }


def rpc(method, **params):
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


def server(*, submit=False):
    client = Client()
    bridge = PaperAgentMCP(client, allow_submit=submit)
    init = bridge.handle(
        rpc(
            "initialize",
            protocolVersion="2025-11-25",
            capabilities={},
            clientInfo={"name": "test", "version": "1"},
        )
    )
    assert init["result"]["serverInfo"]["name"] == "liquilens-private-paper-execution"
    return bridge, client


def test_catalog_and_runtime_both_hide_submission_by_default():
    bridge, client = server()
    names = {
        tool["name"] for tool in bridge.handle(rpc("tools/list"))["result"]["tools"]
    }
    assert names == {"paper_capabilities", "assess_paper_order", "paper_order_status"}
    result = bridge.handle(
        rpc(
            "tools/call",
            name="submit_paper_order",
            arguments={"assessment_id": "a" * 64},
        )
    )
    assert result["error"]["code"] == -32602 and client.calls == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"account_id": "other"},
        {"assessment_id": "bad"},
        {"assessment_id": "a" * 64, "enable": True},
    ],
)
def test_arbitrary_controls_cannot_reach_host(arguments):
    bridge, client = server(submit=True)
    result = bridge.handle(
        rpc("tools/call", name="submit_paper_order", arguments=arguments)
    )
    assert result["error"]["code"] == -32602 and client.calls == []


def test_execution_is_exact_and_host_error_is_not_success():
    bridge, client = server(submit=True)
    client.error = True
    result = bridge.handle(
        rpc(
            "tools/call",
            name="submit_paper_order",
            arguments={"assessment_id": "a" * 64},
        )
    )["result"]
    assert result["isError"] is True
    assert client.calls == [("submit", {"assessment_id": "a" * 64})]
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]


def test_notifications_never_execute_and_modern_metadata_is_private():
    bridge, client = server(submit=True)
    message = rpc(
        "tools/call", name="submit_paper_order", arguments={"assessment_id": "a" * 64}
    )
    del message["id"]
    assert bridge.handle(message) is None and client.calls == []
    result = bridge.handle(
        rpc(
            "server/discover",
            _meta={
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        )
    )["result"]
    assert result["cacheScope"] == "private" and result["ttlMs"] == 0
    assert (
        result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"]
        == "liquilens-private-paper-execution"
    )


def test_ambiguous_json_cannot_execute(monkeypatch):
    bridge, client = server(submit=True)
    output = io.StringIO()
    raw = (
        b'{"jsonrpc":"2.0","id":1,"method":"tools/call",'
        b'"params":{"name":"submit_paper_order","arguments":'
        b'{"assessment_id":"x","assessment_id":"y"}}}\n'
    )
    monkeypatch.setattr("sys.stdin", SimpleNamespace(buffer=io.BytesIO(raw)))
    monkeypatch.setattr("sys.stdout", output)
    assert bridge.serve() == 0
    assert json.loads(output.getvalue())["error"]["code"] == -32700
    assert client.calls == []
