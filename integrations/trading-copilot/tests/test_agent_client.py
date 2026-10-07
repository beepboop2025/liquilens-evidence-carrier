from pathlib import Path

import httpx
import pytest

from liquilens_trading_copilot.agent_client import AgentHostClient, read_agent_token

TOKEN = "synthetic-agent-client-token-0123456789abcdef"


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://remote.example",
        "https://user:password@private.example",
        "https://private.example?token=secret",
        "https://private.example/#fragment",
        "https://private.example/orders",
    ],
)
def test_client_rejects_unsafe_endpoints(endpoint: str) -> None:
    with pytest.raises(ValueError, match="endpoint_invalid"):
        AgentHostClient(endpoint, token=TOKEN)


@pytest.mark.parametrize("failure", ["redirect", "timeout", "oversize", "live"])
def test_client_does_not_retry_or_expose_failed_submission_body(failure: str) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer " + TOKEN
        if failure == "timeout":
            raise httpx.ReadTimeout("SECRET " + TOKEN, request=request)
        if failure == "redirect":
            return httpx.Response(307, headers={"Location": "https://attacker.example"})
        if failure == "oversize":
            return httpx.Response(200, json={"mode": "paper", "body": "x" * 524288})
        return httpx.Response(200, json={"mode": "live", "body": "SECRET"})

    client = AgentHostClient(
        "http://127.0.0.1:8766", token=TOKEN, transport=httpx.MockTransport(respond)
    )
    try:
        result = client.call("submit", {"assessment_id": "a" * 64})
    finally:
        client.close()
    assert len(calls) == 1
    assert result["result"]["submission_outcome"] == "unknown"
    assert result["result"]["resubmit_allowed"] is False
    assert "SECRET" not in str(result) and TOKEN not in str(result)


def test_token_file_must_be_private_and_cannot_be_a_symlink(tmp_path: Path) -> None:
    token_file = tmp_path / "agent.token"
    token_file.write_text(TOKEN)
    token_file.chmod(0o600)
    assert read_agent_token(token_file) == TOKEN
    token_file.chmod(0o644)
    with pytest.raises(ValueError, match="private_token_file_required"):
        read_agent_token(token_file)
    alias = tmp_path / "alias.token"
    alias.symlink_to(token_file)
    with pytest.raises(OSError):
        read_agent_token(alias)


def test_client_preserves_business_error_even_with_http_success() -> None:
    client = AgentHostClient(
        "https://private.example",
        token=TOKEN,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"mode": "paper", "tool_error": True, "status": "uncertain"}
            )
        ),
    )
    try:
        result = client.call("submit", {"assessment_id": "a" * 64})
        assert result["http_status"] == 200
        assert result["result"]["tool_error"] is True
        assert result["result"]["status"] == "uncertain"
    finally:
        client.close()
