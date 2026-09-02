from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any

from fastapi.testclient import TestClient
from liquilens_evidence.trade_safety import (
    TRADE_SAFETY_POLICY_SCHEMA,
    TRADE_SAFETY_REQUEST_SCHEMA,
    trade_safety_request_hash,
)

from trade_safety_gateway.app import (
    LIQUILENS_BASE_URL,
    MAX_REQUEST_BYTES,
    MCP_LEGACY_PROTOCOL_VERSION,
    MCP_PROTOCOL_VERSION,
    SEICHE_URL,
    SERVICE_REVISION,
    UNDERTOW_URL,
    RawUpstreamResponse,
    create_app,
)

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _mcp_response(request_id: str, structured: dict[str, Any]) -> bytes:
    return _json_bytes(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": "ignored by gateway"}],
                "structuredContent": structured,
                "isError": False,
            },
        }
    )


def _seiche_bytes(
    *, regime: str = "CALM", oldest_headline_asof: str = "2026-08-26"
) -> bytes:
    return _mcp_response(
        "trade-safety-seiche-v1",
        {
            "schema": "seiche.public.v2",
            "generated_at": "2026-09-02T11:59:30Z",
            "conclusion": {
                "regime": regime,
                "value": 20.0,
                "coverage_pct": 100.0,
            },
            "proof": {"withheld": True},
            "data_quality": {
                "schema": "seiche.data_quality.v1",
                "generated_at": "2026-09-02T11:59:30Z",
                "headline_ages": [
                    {
                        "series": "reserves_b",
                        "asof": oldest_headline_asof,
                        "age_days": (
                            date(2026, 9, 2)
                            - date.fromisoformat(oldest_headline_asof)
                        ).days,
                    },
                    {"series": "sofr_pct", "asof": "2026-09-01", "age_days": 1},
                ],
            },
        },
    )


def _undertow_bytes(
    *,
    requested: float = 1_000.0,
    rung: float = 1_000.0,
    worst: float = 10.0,
    spread: float = 8.0,
) -> bytes:
    return _mcp_response(
        "trade-safety-undertow-v1",
        {
            "asof": "2026-09-02",
            "generated_at": "2026-09-02T11:59:40Z",
            "asset": "BTC",
            "requested_size_usd": requested,
            "published_rung_used_usd": rung,
            "sell_cost_bp_by_venue": {"venue-a": 2.0, "venue-b": worst},
            "best": {"venue": "venue-a", "sell_bp": 2.0},
            "worst": {"venue": "venue-b", "sell_bp": worst},
            "venue_spread_bp": spread,
            "unable_at_observed_depth": [],
        },
    )


def _liquilens_bytes() -> bytes:
    return _json_bytes(
        {
            "historical_evidence": {
                "status": "PERIOD_END_PROXY_CONSTRUCTION_PIT",
                "validated_backtest_eligible": False,
                "real_money_eligible": False,
                "reason": "must not be copied into the receipt",
            },
            "trajectory": [
                {"period_end": "2026-03-31", "pd_12m": 0.02},
                {"period_end": "2025-12-31", "pd_12m": 0.01},
            ],
            "sensitive_to_contract": {"must_not": "be projected"},
        }
    )


class FakeUpstream:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.responses: dict[str, bytes | Exception] = {
            SEICHE_URL: _seiche_bytes(),
            UNDERTOW_URL: _undertow_bytes(),
        }
        self.closed = False

    async def request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> RawUpstreamResponse:
        self.calls.append((method, url, copy.deepcopy(json_body)))
        value = self.responses[url]
        if isinstance(value, Exception):
            raise value
        return RawUpstreamResponse(value)

    async def aclose(self) -> None:
        self.closed = True


def _request(
    *,
    mode: str = "paper",
    amount: float = 1_000.0,
    symbol: str = "BTC/USD",
    currency: str = "USD",
    institution_slug: str | None = None,
    extensions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scopes = {
        "observe": ["evidence:read"],
        "paper": ["evidence:read", "orders:paper"],
        "live": ["evidence:read", "orders:live"],
    }
    return {
        "schema": TRADE_SAFETY_REQUEST_SCHEMA,
        "request_id": f"gateway-{mode}-{amount:g}",
        "created_at": "2026-09-02T11:59:00Z",
        "expires_at": "2026-09-02T12:10:00Z",
        "mode": mode,
        "agent": {
            "agent_id": "copilot-test",
            "operator_id": "operator-test",
            "tenant_id": "tenant-test",
            "account_id": "sandbox-account",
            "runtime": "pytest/1",
            "strategy_id": "fixture",
            "authorization_scope": scopes[mode],
        },
        "order": {
            "instrument": {
                "asset_class": "crypto",
                "symbol": symbol,
                "identifiers": (
                    {}
                    if institution_slug is None
                    else {"liquilens_institution_slug": institution_slug}
                ),
            },
            "side": "sell",
            "order_type": "market",
            "notional": {"amount": amount, "currency": currency},
            "quantity": None,
            "limit_price": None,
            "stop_price": None,
            "venue": None,
            "time_in_force": "IOC",
        },
        "policy_ref": {"policy_id": "sandbox-default", "version": "1.0.0"},
        "extensions": extensions or {},
    }


def _policy() -> dict[str, Any]:
    return {
        "schema": TRADE_SAFETY_POLICY_SCHEMA,
        "policy_id": "sandbox-default",
        "version": "1.0.0",
        "required_products": ["seiche", "undertow"],
        "max_evidence_age_seconds": {
            # The composite regime keeps the oldest reported headline input as
            # its semantic as-of; the sandbox does not relabel it as real time.
            "seiche": 8 * 86_400,
            # Undertow publishes its market as-of as an ISO date and its
            # generation/knowledge clock separately. Keep that coarse source
            # clock intact instead of pretending it has sub-day precision.
            "undertow": 86_400,
            "liquilens": 86_400,
        },
        "hold_regimes": ["STRESS"],
        "max_notional_usd": 100_000.0,
        "max_exit_cost_bps": 50.0,
        "max_venue_spread_bps": 20.0,
        "missing_evidence": "fail_closed",
        "live_requires_executable_quote": True,
        "live_requires_broker_preview": True,
        "auto_resize": False,
        "extensions": {},
    }


def _client(fake: FakeUpstream) -> TestClient:
    return TestClient(create_app(upstream=fake, clock=lambda: NOW))


def _post_check(
    client: TestClient,
    request: dict[str, Any],
    policy: dict[str, Any] | None = None,
):
    return client.post(
        "/v1/check", json={"request": request, "policy": policy or _policy()}
    )


def test_health_capabilities_openapi_and_sandbox_headers() -> None:
    fake = FakeUpstream()
    with _client(fake) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["state"] == "read_only_sandbox"
        assert health.json()["source_revision"] == SERVICE_REVISION
        assert health.headers["x-trade-safety-execution"] == "disabled"
        assert health.headers["x-trade-safety-authority"] == "read-only"
        capabilities = client.get("/v1/capabilities").json()
        assert capabilities["execution_tools"] == []
        assert capabilities["live_outcome"] == "unavailable"
        assert capabilities["source_revision"] == SERVICE_REVISION
        assert capabilities["mcp_protocol_versions"] == [
            MCP_PROTOCOL_VERSION,
            MCP_LEGACY_PROTOCOL_VERSION,
        ]
        assert capabilities["limits"]["request_bytes"] == MAX_REQUEST_BYTES
        openapi = client.get("/openapi.json").json()
        assert {"/healthz", "/v1/capabilities", "/v1/check", "/mcp"} <= set(
            openapi["paths"]
        )
    assert fake.calls == []


def test_paper_receipt_can_pass_and_limit_and_hashes_exact_source_bytes() -> None:
    fake = FakeUpstream()
    request = _request()
    with _client(fake) as client:
        passed_response = _post_check(client, request)
        assert passed_response.status_code == 200, passed_response.text
        passed = passed_response.json()
        assert passed["decision"]["outcome"] == "pass"
        assert passed["integrity"] == {
            "profile": "sha256",
            "key_id": None,
            "signature": None,
        }
        assert (
            passed["evidence"]["seiche"]["source_sha256"]
            == hashlib.sha256(_seiche_bytes()).hexdigest()
        )
        assert (
            passed["evidence"]["undertow"]["source_sha256"]
            == hashlib.sha256(_undertow_bytes()).hexdigest()
        )
        assert passed["evidence"]["seiche"]["state"] == "context_only"
        assert passed["evidence"]["seiche"]["as_of"] == "2026-08-26T00:00:00Z"
        assert passed["evidence"]["undertow"]["executable_quote"] is False
        assert passed["evidence"]["undertow"]["source_schema"] is None

        limited_policy = _policy()
        limited_policy["max_exit_cost_bps"] = 5.0
        limited = _post_check(client, request, limited_policy).json()
        assert limited["decision"]["outcome"] == "limit"
        assert "max_exit_cost_bps_exceeded" in limited["decision"]["reason_codes"]


def test_unsupported_and_invalid_size_never_calls_undertow() -> None:
    fake = FakeUpstream()
    with _client(fake) as client:
        unsupported = _post_check(client, _request(amount=5_000.0))
        assert unsupported.status_code == 200
        receipt = unsupported.json()
        assert receipt["decision"]["outcome"] == "unavailable"
        assert receipt["evidence"]["undertow"]["state"] == "unavailable"
        assert not any(url == UNDERTOW_URL for _method, url, _body in fake.calls)

    invalid_fake = FakeUpstream()
    with _client(invalid_fake) as client:
        invalid = _post_check(client, _request(amount=-1.0))
        assert invalid.status_code == 422
        assert invalid_fake.calls == []

    extension_fake = FakeUpstream()
    with _client(extension_fake) as client:
        unsupported_extension = _post_check(
            client, _request(extensions={"broker_url": "https://example.invalid"})
        )
        assert unsupported_extension.status_code == 422
        assert extension_fake.calls == []


def test_source_errors_and_nearest_rung_mismatch_fail_closed() -> None:
    fake = FakeUpstream()
    fake.responses[SEICHE_URL] = RuntimeError("secret transport detail")
    with _client(fake) as client:
        response = _post_check(client, _request())
        assert response.status_code == 200
        receipt = response.json()
        assert receipt["decision"]["outcome"] == "unavailable"
        assert receipt["evidence"]["seiche"]["state"] == "unavailable"
        assert "secret transport detail" not in response.text

    mismatch_fake = FakeUpstream()
    mismatch_raw = _undertow_bytes(requested=1_000.0, rung=10_000.0)
    mismatch_fake.responses[UNDERTOW_URL] = mismatch_raw
    with _client(mismatch_fake) as client:
        receipt = _post_check(client, _request()).json()
        section = receipt["evidence"]["undertow"]
        assert receipt["decision"]["outcome"] == "unavailable"
        assert section["state"] == "unavailable"
        assert section["facts"] == {}
        assert section["source_sha256"] == hashlib.sha256(mismatch_raw).hexdigest()


def test_fresh_seiche_wrapper_does_not_reset_stale_observation_clock() -> None:
    fake = FakeUpstream()
    fake.responses[SEICHE_URL] = _seiche_bytes(oldest_headline_asof="2026-08-20")
    with _client(fake) as client:
        receipt = _post_check(client, _request()).json()
    assert receipt["evidence"]["seiche"]["as_of"] == "2026-08-20T00:00:00Z"
    assert receipt["decision"]["outcome"] == "unavailable"
    assert "seiche_evidence_too_old" in receipt["decision"]["reason_codes"]


def test_every_section_and_broker_preview_bind_the_exact_order_hash() -> None:
    fake = FakeUpstream()
    first = _request()
    second = _request()
    second["request_id"] = "gateway-paper-order-two"
    second["order"]["side"] = "buy"
    with _client(fake) as client:
        first_receipt = _post_check(client, first).json()
        second_receipt = _post_check(client, second).json()
    assert first_receipt["request_hash"] == trade_safety_request_hash(first)
    assert second_receipt["request_hash"] == trade_safety_request_hash(second)
    assert first_receipt["request_hash"] != second_receipt["request_hash"]
    for receipt in (first_receipt, second_receipt):
        assert {
            section["request_hash"] for section in receipt["evidence"].values()
        } == {receipt["request_hash"]}
        assert receipt["broker_preview"]["request_hash"] == receipt["request_hash"]
        assert receipt["broker_preview"]["account_id"] == "sandbox-account"


def test_live_mode_is_deterministically_unavailable() -> None:
    fake = FakeUpstream()
    with _client(fake) as client:
        response = _post_check(client, _request(mode="live"))
        assert response.status_code == 200
        receipt = response.json()
    assert receipt["decision"]["outcome"] == "unavailable"
    assert receipt["integrity"]["profile"] == "sha256"
    assert receipt["broker_preview"]["state"] == "not_applicable"
    assert receipt["broker_preview"]["provider"] is None
    assert receipt["broker_preview"]["facts"] == {}
    assert "broker_preview_unavailable" in receipt["decision"]["reason_codes"]
    assert receipt["authority"]["can_execute"] is False


def test_liquilens_is_conditional_fixed_base_and_projects_only_allowed_facts() -> None:
    fake = FakeUpstream()
    url = LIQUILENS_BASE_URL + "indusind-bank"
    raw = _liquilens_bytes()
    fake.responses[url] = raw
    request = _request(institution_slug="indusind-bank")
    with _client(fake) as client:
        receipt = _post_check(client, request).json()
    assert any(
        method == "GET" and called_url == url for method, called_url, _ in fake.calls
    )
    section = receipt["evidence"]["liquilens"]
    assert section["state"] == "context_only"
    assert section["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert section["facts"] == {
        "period_end": "2026-03-31",
        "historical_evidence_status": "PERIOD_END_PROXY_CONSTRUCTION_PIT",
        "validated_backtest_eligible": False,
        "historical_real_money_eligible": False,
    }
    assert "pd_12m" not in json.dumps(section)


def test_mcp_lists_only_read_only_tools_and_calls_assessment() -> None:
    fake = FakeUpstream()
    with _client(fake) as client:
        initialized = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": "init",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1"},
                },
            },
        ).json()
        assert initialized["result"]["protocolVersion"] == (MCP_LEGACY_PROTOCOL_VERSION)

        meta = {
            "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": {
                "name": "pytest-modern",
                "version": "1",
            },
        }
        discovered = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": "discover",
                "method": "server/discover",
                "params": {"_meta": meta},
            },
        ).json()
        assert discovered["result"]["supportedVersions"] == [MCP_PROTOCOL_VERSION]
        assert discovered["result"]["resultType"] == "complete"

        listed = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        ).json()
        tools = {tool["name"]: tool for tool in listed["result"]["tools"]}
        assert set(tools) == {
            "assess_trade_safety",
            "trade_safety_capabilities",
        }
        assert all(tool["annotations"]["readOnlyHint"] for tool in tools.values())
        assert all(
            not tool["annotations"]["destructiveHint"] for tool in tools.values()
        )
        assert not any("execute" in name or "order" in name for name in tools)

        modern_listed = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": "modern-list",
                "method": "tools/list",
                "params": {"_meta": meta},
            },
        ).json()
        assert modern_listed["result"]["resultType"] == "complete"
        assert len(modern_listed["result"]["tools"]) == 2

        called = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "assess_trade_safety",
                    "arguments": {"request": _request(), "policy": _policy()},
                },
            },
        ).json()
        result = called["result"]
        assert result["isError"] is False
        assert result["structuredContent"]["decision"]["outcome"] == "pass"
        assert "no execution" in result["content"][0]["text"]


def test_body_limit_and_duplicate_json_keys_are_rejected_before_handlers() -> None:
    fake = FakeUpstream()
    with _client(fake) as client:
        oversized = client.post(
            "/v1/check",
            content=b"{" + b" " * MAX_REQUEST_BYTES + b"}",
            headers={"content-type": "application/json"},
        )
        assert oversized.status_code == 413
        duplicate = client.post(
            "/v1/check",
            content=b'{"request":{},"request":{},"policy":{}}',
            headers={"content-type": "application/json"},
        )
        assert duplicate.status_code == 400
    assert fake.calls == []
