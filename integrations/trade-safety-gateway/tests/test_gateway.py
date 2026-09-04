from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from liquilens_evidence.trade_safety import (
    TRADE_SAFETY_POLICY_SCHEMA,
    TRADE_SAFETY_REQUEST_SCHEMA,
    trade_safety_request_hash,
)

from trade_safety_gateway.app import (
    ISSUER_ENDPOINT,
    LIQUILENS_BASE_URL,
    MAX_REQUEST_BYTES,
    MCP_LEGACY_PROTOCOL_VERSION,
    MCP_PROTOCOL_VERSION,
    SEICHE_URL,
    SERVICE_REVISION,
    SERVICE_VERSION,
    UNDERTOW_REQUIRED_VENUES,
    UNDERTOW_URL,
    HttpxUpstreamTransport,
    RawUpstreamResponse,
    create_app,
)
from trade_safety_gateway.telemetry import (
    InMemoryTelemetrySink,
    TelemetryEmitter,
)
from trade_safety_gateway.x402_access import (
    LIQUILENS_EXTENSION,
    PAYMENT_REQUIRED_HEADER,
    PAYMENT_RESPONSE_HEADER,
    SQLiteSettlementJournal,
    X402AccessGate,
    X402Config,
)
from trade_safety_gateway.x402_runtime import X402Runtime

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
X402_RESOURCE = "https://api.liquilens.in/v1/x402/check"
X402_FACILITATOR = "https://facilitator.example.test/platform/v2/x402"
X402_NETWORK = "eip155:84532"
X402_AMOUNT = "10000"
X402_ASSET = "0x" + "1" * 40
X402_PAY_TO = "0x" + "2" * 40
X402_PAYER = "0x" + "3" * 40
X402_TRANSACTION = "0x" + "4" * 64


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


def _sealed(value: dict[str, Any], field: str) -> dict[str, Any]:
    unsigned = {key: item for key, item in value.items() if key != field}
    return {**unsigned, field: hashlib.sha256(_json_bytes(unsigned)).hexdigest()}


def _seiche_bytes(
    *,
    regime: str = "CALM",
    oldest_headline_asof: str = "2026-08-26",
    stress_index: Any = 20.0,
) -> bytes:
    evidence_at = datetime.fromisoformat(oldest_headline_asof).replace(tzinfo=UTC)
    evaluated_at = datetime(2026, 9, 2, 11, 59, 45, tzinfo=UTC)
    payload = {
        "ok": True,
        "schema": "seiche.risk-context.v1",
        "status": "available",
        "reason": None,
        "state": "context_only",
        "evidence_class": "derived",
        "rights_status": "metadata_only",
        "context_only": True,
        "executable": False,
        "executable_quote": False,
        "real_money_eligible": False,
        "can_authorize_order": False,
        "projection_mode": "cache_only",
        "request_time_collection": False,
        "request_time_model_fitting": False,
        "request_time_network": False,
        "request_time_notary": False,
        "request_time_broker": False,
        "attestation_state": "not_evaluated",
        "source_url": "https://api.seiche.info/api/trade-safety/risk-context",
        "source_snapshot_version": "0.12.0 fixture",
        "regime": regime,
        "stress_index": stress_index,
        "coverage_pct": 84.0,
        "fault_count": 1,
        "staleness": {
            "fresh": 3,
            "aging": 1,
            "stale": 1,
            "dead": 0,
            "unknown": 1,
            "total": 6,
        },
        "clocks": {
            "snapshot_generated_at": "2026-09-02T11:59:30Z",
            "evidence_as_of": evidence_at.isoformat().replace("+00:00", "Z"),
            "evaluated_at": "2026-09-02T11:59:45Z",
            "snapshot_age_seconds": 15,
            "evidence_age_seconds": int((evaluated_at - evidence_at).total_seconds()),
            "basis": "oldest valid public provenance observation clock",
        },
        "attestation": {
            "status": "not_evaluated",
            "ed25519_status": "not_evaluated",
            "ots_status": "not_evaluated",
            "bitcoin_anchor_claimed": False,
            "ledger_read": False,
            "reason": "attestation_ledger_not_evaluated_by_this_projection",
            "disclosure": "Stream proof is separate and is not order authority.",
        },
        "limitations": [
            "public_metadata_context_only_not_licensed_for_real_money_execution",
            "not_order_bound_and_cannot_authorize_or_route_an_order",
            "stream_attestation_is_not_per_order_execution_authority",
            "projection_sha256_is_a_server_internal_change_detector_not_authentication",
        ],
        "disclaimer": "Research context only; not investment advice.",
        "canonicalization": ("python-json-sort-keys-utf8-no-nan-server-internal-v1"),
    }
    return _json_bytes(_sealed(payload, "projection_sha256"))


def _undertow_bytes(
    *,
    requested: float = 1_000.0,
    rung: float = 1_000.0,
    worst: float = 10.0,
    spread: float = 8.0,
    unable: list[str] | None = None,
    venue_costs: dict[str, Any] | None = None,
    best_venue: str = "binance",
    worst_venue: str = "bitfinex",
    request_hash: str | None = None,
    mode: str = "paper",
) -> bytes:
    binding = request_hash or trade_safety_request_hash(_request())
    costs = venue_costs or {
        "binance": 2.0,
        "bitfinex": worst,
        "coinbase": 4.0,
        "gemini": 6.0,
        "kraken": 3.0,
        "okx": 5.0,
    }
    conversions = {}
    depth = {}
    for venue in UNDERTOW_REQUIRED_VENUES:
        quote = "USDT" if venue in {"binance", "okx"} else "USD"
        price = 0.999 if quote == "USDT" else 1.0
        requested_quote = requested / price
        conversions[venue] = {
            "quote_currency": quote,
            "state": "bound_usdt_usd" if quote == "USDT" else "identity",
            "usd_per_quote": price,
            "requested_notional_quote": requested_quote,
        }
        depth[venue] = {
            "side": "bid",
            "required_band": "1pct" if requested_quote <= 200_000 else "2pct",
            "covers_required_band": True,
            "covers_1pct_bid": True,
            "covers_2pct_bid": True,
            "span_below": 0.03,
            "depth_1pct_bid_quote": 200_000.0,
            "depth_2pct_bid_quote": 2_000_000.0,
            "within_observed_depth": True,
        }
    costs_usd = {
        venue: round(requested * float(cost) / 10_000, 2)
        for venue, cost in costs.items()
        if isinstance(cost, (int, float)) and not isinstance(cost, bool)
    }
    request_projection = {
        "request_hash": binding,
        "mode": mode,
        "instrument": "BTC/USD",
        "side": "sell",
        "venue": None,
        "requested_size_usd": requested,
    }
    payload = {
        "schema": "undertow.trade-safety-exit-context.v1",
        "schema_url": (
            "https://liquilens-undertow.com/"
            "undertow-trade-safety-exit-context-v1.schema.json"
        ),
        "status": "available",
        "reason": None,
        "request_hash": binding,
        "request": request_projection,
        "evidence_class": "derived",
        "measurement": {
            "instrument": "BTC/USD",
            "asset": "BTC",
            "side": "sell",
            "venue": None,
            "requested_size_usd": requested,
            "published_rung_used_usd": rung,
            "estimator": "band_interpolation_v1",
            "quote_conversion_by_venue": conversions,
            "sell_cost_bps_by_venue": costs,
            "sell_cost_usd_by_venue": costs_usd,
            "best": {
                "venue": best_venue,
                "sell_cost_bps": costs.get(best_venue, 2.0),
                "sell_cost_usd": costs_usd.get(best_venue, 0.2),
            },
            "worst": {
                "venue": worst_venue,
                "sell_cost_bps": costs.get(worst_venue, worst),
                "sell_cost_usd": costs_usd.get(worst_venue, 1.0),
            },
            "venue_spread_bps": spread,
            "venue_spread_usd": round(requested * spread / 10_000, 2),
        },
        "coverage": {
            "state": "complete",
            "expected_venues": sorted(UNDERTOW_REQUIRED_VENUES),
            "priced_venues": sorted(UNDERTOW_REQUIRED_VENUES),
            "unreachable_venues": [],
            "unable_at_observed_depth": unable or [],
            "uncovered_at_required_band": [],
            "conversion_unavailable_venues": [],
            "missing_venues": [],
            "source_lower_bound_note": None,
            "depth_coverage_by_venue": depth,
        },
        "peg": {
            "state": "within_threshold",
            "pair": "USDT/USD",
            "source": "coinbase USDT-USD ticker",
            "price": 0.999,
            "deviation": 0.001,
            "warn_threshold": 0.005,
            "depeg_flag": False,
            "observation_at": "2026-09-02T11:59:05Z",
        },
        "source": {
            "url": UNDERTOW_URL,
            "pack": "crypto_desk.json",
            "source_schema": "undertow.crypto_desk.v2",
            "raw_sha256": "1" * 64,
            "canonical_sha256": "2" * 64,
            "pit_input_sha256": "3" * 64,
            "deployed_sha": "4" * 40,
        },
        "pit": {
            "state": "verified",
            "board_content_sha256": "5" * 64,
            "ledger": "data/_pit/board.jsonl",
            "key": "2026-09-02T11:59:20Z",
            "revision": 3,
            "record_hash": "6" * 64,
            "chain_verified": True,
            "head_verified": True,
        },
        "clocks": {
            "observation_at": "2026-09-02T11:59:05Z",
            "oldest_observation_at": "2026-09-02T11:59:00Z",
            "venue_observation_at_by_venue": {
                venue: f"2026-09-02T11:59:0{index}Z"
                for index, venue in enumerate(sorted(UNDERTOW_REQUIRED_VENUES))
            },
            "max_observation_skew_seconds": 5.0,
            "max_observation_skew_allowed_seconds": 300,
            "knowledge_at": "2026-09-02T11:59:20Z",
            "retrieved_at": "2026-09-02T11:59:40Z",
            "expires_at": "2026-09-02T13:59:00Z",
        },
        "rights": {
            "status": "approved",
            "manifest": "trade_safety_exit_rights.json",
            "manifest_schema": "undertow.trade-safety-exit-rights.v1",
            "manifest_version": "2026-09-02.review.1",
            "reviewed_by": "reviewer@example.invalid",
            "reviewed_at": "2026-09-02T11:00:00Z",
            "valid_from": "2026-09-02T00:00:00Z",
            "valid_until": "2026-12-01T00:00:00Z",
            "raw_sha256": "7" * 64,
            "canonical_sha256": "8" * 64,
            "pit_input_sha256": "9" * 64,
            "scope": "derived_metadata_only",
            "raw_order_books_included": False,
            "redistribution": "derived_metrics_only",
            "venue_states": {
                venue: "approved" for venue in sorted(UNDERTOW_REQUIRED_VENUES)
            },
            "venue_reviewed_at_by_venue": {
                venue: "2026-09-02T11:00:00Z"
                for venue in sorted(UNDERTOW_REQUIRED_VENUES)
            },
            "venue_proof_sha256_by_venue": {
                venue: hashlib.sha256(venue.encode()).hexdigest()
                for venue in sorted(UNDERTOW_REQUIRED_VENUES)
            },
        },
        "authority": {
            "state": "context_only",
            "mode": mode,
            "paper_only": True,
            "execution_authority": False,
            "can_authorize_order": False,
            "can_route_order": False,
            "can_place_order": False,
            "can_modify_order": False,
            "can_cancel_order": False,
            "can_clear_other_controls": False,
            "can_increase_risk": False,
            "executable_quote": False,
            "real_money_eligible": False,
        },
        "limitations": [
            "band_interpolated_estimate_not_a_book_walk",
            "not_an_executable_quote_or_broker_preview",
            "exact_published_rungs_only_no_nearest_floor_or_interpolation",
            "context_cannot_clear_another_trade_safety_control",
        ],
    }
    return _mcp_response("trade-safety-undertow-v1", _sealed(payload, "context_sha256"))


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


def _set_path(value: dict[str, Any], path: tuple[str, ...], replacement: Any) -> None:
    target = value
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement


def _mutated_seiche(path: tuple[str, ...], value: Any, *, reseal: bool = True) -> bytes:
    payload = json.loads(_seiche_bytes())
    _set_path(payload, path, value)
    if reseal:
        payload = _sealed(payload, "projection_sha256")
    return _json_bytes(payload)


def _mutated_undertow(
    path: tuple[str, ...],
    value: Any,
    *,
    request_hash: str | None = None,
    reseal: bool = True,
) -> bytes:
    envelope = json.loads(_undertow_bytes(request_hash=request_hash))
    payload = envelope["result"]["structuredContent"]
    _set_path(payload, path, value)
    if reseal:
        envelope["result"]["structuredContent"] = _sealed(payload, "context_sha256")
    return _json_bytes(envelope)


def _mutated_undertow_envelope(path: tuple[str, ...], value: Any) -> bytes:
    envelope = json.loads(_undertow_bytes())
    _set_path(envelope, path, value)
    return _json_bytes(envelope)


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


class FakeX402Facilitator:
    def __init__(
        self,
        *,
        settle_error: Exception | None = None,
        settle_hook: Callable[[], None] | None = None,
        settle_response: Mapping[str, Any] | None = None,
    ) -> None:
        self.settle_error = settle_error
        self.settle_hook = settle_hook
        self.settle_response = settle_response or {
            "success": True,
            "payer": X402_PAYER,
            "transaction": X402_TRANSACTION,
            "network": X402_NETWORK,
            "amount": X402_AMOUNT,
        }
        self.verify_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.settle_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.closed = False

    async def verify(
        self,
        *,
        payment_payload: Mapping[str, Any],
        payment_requirements: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        self.verify_calls.append(
            (
                copy.deepcopy(dict(payment_payload)),
                copy.deepcopy(dict(payment_requirements)),
            )
        )
        return {"isValid": True, "payer": X402_PAYER}

    async def settle(
        self,
        *,
        payment_payload: Mapping[str, Any],
        payment_requirements: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        self.settle_calls.append(
            (
                copy.deepcopy(dict(payment_payload)),
                copy.deepcopy(dict(payment_requirements)),
            )
        )
        if self.settle_hook is not None:
            self.settle_hook()
        if self.settle_error is not None:
            raise self.settle_error
        return copy.deepcopy(dict(self.settle_response))

    async def aclose(self) -> None:
        self.closed = True


def _x402_runtime(
    tmp_path: Path,
    *,
    facilitator: FakeX402Facilitator | None = None,
    config_overrides: Mapping[str, Any] | None = None,
) -> tuple[X402Runtime, FakeX402Facilitator]:
    selected_facilitator = facilitator or FakeX402Facilitator()
    config_values: dict[str, Any] = {
        "resource_url": X402_RESOURCE,
        "facilitator_url": X402_FACILITATOR,
        "network": X402_NETWORK,
        "amount": X402_AMOUNT,
        "asset": X402_ASSET,
        "pay_to": X402_PAY_TO,
        "quote_binding_key": b"gateway-test-x402-quote-binding-key",
        "offer_extra": {"name": "USDC", "version": "2"},
        "required_extensions": {
            "bazaar": {
                "info": {"input": {"type": "http", "method": "POST"}},
                "schema": {"type": "object"},
            }
        },
    }
    config_values.update(dict(config_overrides or {}))
    config = X402Config(**config_values)
    journal = SQLiteSettlementJournal(tmp_path / "x402.sqlite3")
    gate = X402AccessGate(
        config,
        facilitator=selected_facilitator,
        journal=journal,
    )
    return X402Runtime(gate=gate, journal=journal), selected_facilitator


def _x402_payment_header(
    gate: X402AccessGate,
    body: bytes,
    *,
    nonce_hex: str = "b",
) -> str:
    required = gate.challenge(body, resource=X402_RESOURCE).payment_required
    payment = {
        "x402Version": 2,
        "resource": copy.deepcopy(required["resource"]),
        "accepted": copy.deepcopy(required["accepts"][0]),
        "payload": {
            "signature": "0x" + "a" * 130,
            "authorization": {
                "from": X402_PAYER,
                "to": X402_PAY_TO,
                "value": X402_AMOUNT,
                "validAfter": "0",
                "validBefore": "9999999999",
                "nonce": "0x" + nonce_hex * 64,
            },
        },
        "extensions": copy.deepcopy(required["extensions"]),
    }
    return base64.b64encode(_json_bytes(payment)).decode("ascii")


def _request(
    *,
    mode: str = "paper",
    amount: float = 1_000.0,
    symbol: str = "BTC/USD",
    currency: str = "USD",
    institution_slug: str | None = None,
    extensions: dict[str, Any] | None = None,
    venue: str | None = None,
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
            "venue": venue,
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


def _client(
    fake: FakeUpstream,
    *,
    telemetry: TelemetryEmitter | None = None,
    x402_runtime: X402Runtime | None = None,
    clock: Callable[[], datetime] = lambda: NOW,
) -> TestClient:
    return TestClient(
        create_app(
            upstream=fake,
            clock=clock,
            telemetry=telemetry,
            x402_runtime=x402_runtime,
        )
    )


def _modern_mcp_headers(method: str, *, name: str | None = None) -> dict[str, str]:
    headers = {
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        "Mcp-Method": method,
    }
    if name is not None:
        headers["Mcp-Name"] = name
    return headers


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
        assert health.json()["version"] == SERVICE_VERSION == "0.2.0"
        assert health.json()["source_revision"] == SERVICE_REVISION
        assert health.json()["telemetry"] == {
            "state": "disabled",
            "delivery_failures": 0,
        }
        assert health.headers["x-trade-safety-execution"] == "disabled"
        assert health.headers["x-trade-safety-authority"] == "read-only"
        capabilities = client.get("/v1/capabilities").json()
        assert capabilities["execution_tools"] == []
        assert capabilities["live_outcome"] == "unavailable"
        assert capabilities["telemetry"] == {
            "state": "disabled",
            "delivery_failures": 0,
        }
        assert capabilities["x402_access"]["state"] == "disabled"
        assert capabilities["x402_access"]["protected_path"] is None
        assert capabilities["x402_access"]["free_routes"] == [
            "/healthz",
            "/v1/capabilities",
            "/v1/check",
            "/mcp",
        ]
        assert capabilities["policy_admission"] == {
            "mode": "server_owned_safety_envelope",
            "required_products": ["seiche", "undertow"],
            "required_hold_regimes": ["STRESS"],
            "max_evidence_age_seconds": {
                "seiche": 691_200,
                "undertow": 86_400,
                "liquilens": 86_400,
            },
            "max_notional_usd": 100_000.0,
            "max_exit_cost_bps": 50.0,
            "max_venue_spread_bps": 20.0,
            "exact_policy_allowlist": False,
        }
        assert capabilities["version"] == SERVICE_VERSION
        assert capabilities["source_revision"] == SERVICE_REVISION
        assert capabilities["mcp_protocol_versions"] == [
            MCP_PROTOCOL_VERSION,
            MCP_LEGACY_PROTOCOL_VERSION,
        ]
        assert capabilities["limits"]["request_bytes"] == MAX_REQUEST_BYTES
        assert capabilities["upstreams"]["undertow"]["required_venues"] == sorted(
            UNDERTOW_REQUIRED_VENUES
        )
        openapi = client.get("/openapi.json").json()
        assert {"/healthz", "/v1/capabilities", "/v1/check", "/mcp"} <= set(
            openapi["paths"]
        )
    assert fake.calls == []


def test_upstream_transport_never_persists_response_cookies() -> None:
    cookies: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        cookies.append(request.headers.get("cookie"))
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "set-cookie": "cross_agent_state=forbidden; Path=/; Secure",
            },
            content=b"{}",
            request=request,
        )

    async def scenario() -> None:
        upstream = HttpxUpstreamTransport(transport=httpx.MockTransport(handler))
        try:
            await upstream.request("GET", SEICHE_URL)
            await upstream.request("GET", SEICHE_URL)
        finally:
            await upstream.aclose()

    asyncio.run(scenario())
    assert cookies == [None, None]


def test_x402_route_challenges_settles_and_replays_exact_receipt(
    tmp_path: Path,
) -> None:
    fake = FakeUpstream()
    runtime, facilitator = _x402_runtime(tmp_path)
    sink = InMemoryTelemetrySink()
    telemetry = TelemetryEmitter(
        service_version=SERVICE_VERSION,
        source_revision=SERVICE_REVISION,
        sink=sink,
        clock=lambda: NOW,
    )
    body = _json_bytes({"request": _request(), "policy": _policy()})
    try:
        with _client(fake, x402_runtime=runtime, telemetry=telemetry) as client:
            challenge = client.post(
                "/v1/x402/check",
                content=body,
                headers={"content-type": "application/json"},
            )
            assert challenge.status_code == 402
            required = json.loads(
                base64.b64decode(
                    challenge.headers[PAYMENT_REQUIRED_HEADER],
                    validate=True,
                )
            )
            assert required == challenge.json()
            assert set(required["extensions"]) == {
                "bazaar",
                LIQUILENS_EXTENSION,
            }
            assert fake.calls == []
            assert facilitator.verify_calls == []

            capabilities = client.get("/v1/capabilities").json()
            assert capabilities["x402_access"]["state"] == "configured"
            assert capabilities["x402_access"]["protected_path"] == ("/v1/x402/check")
            assert (
                capabilities["x402_access"]["payment_changes_safety_outcome"] is False
            )

            payment_header = _x402_payment_header(runtime.gate, body)
            paid = client.post(
                "/v1/x402/check",
                content=body,
                headers={
                    "content-type": "application/json",
                    "payment-signature": payment_header,
                },
            )
            assert paid.status_code == 200, paid.text
            assert PAYMENT_RESPONSE_HEADER in paid.headers
            assert paid.json()["decision"]["outcome"] == "pass"
            assert paid.json()["authority"]["can_execute"] is False
            assert X402_PAYER.encode() not in paid.content
            assert X402_TRANSACTION.encode() not in paid.content

            replay = client.post(
                "/v1/x402/check",
                content=body,
                headers={
                    "content-type": "application/json",
                    "payment-signature": payment_header,
                },
            )
            assert replay.status_code == 200
            assert replay.content == paid.content
            assert (
                replay.headers[PAYMENT_RESPONSE_HEADER]
                == paid.headers[PAYMENT_RESPONSE_HEADER]
            )
    finally:
        asyncio.run(runtime.aclose())

    assert len(fake.calls) == 2
    assert len(facilitator.verify_calls) == 1
    assert len(facilitator.settle_calls) == 1
    records = [json.loads(line) for line in sink.lines]
    assert [record["event"] for record in records] == [
        "x402_offered",
        "x402_verify_failed",
        "assessment_accepted",
        "assessment_outcome",
        "x402_settled",
        "x402_released",
        "x402_released",
    ]
    assert records[5]["properties"] == {
        "delivery": "initial",
        "outcome": "pass",
    }
    assert records[6]["properties"] == {
        "delivery": "replay",
        "outcome": "pass",
    }
    serialized = b"\n".join(sink.lines)
    assert X402_PAYER.encode() not in serialized
    assert X402_TRANSACTION.encode() not in serialized


def test_x402_policy_rejection_happens_before_payment_or_network(
    tmp_path: Path,
) -> None:
    fake = FakeUpstream()
    runtime, facilitator = _x402_runtime(tmp_path)
    permissive = _policy()
    permissive["hold_regimes"] = []
    body = _json_bytes({"request": _request(), "policy": permissive})
    payment_header = _x402_payment_header(runtime.gate, body)
    try:
        with _client(fake, x402_runtime=runtime) as client:
            response = client.post(
                "/v1/x402/check",
                content=body,
                headers={
                    "content-type": "application/json",
                    "payment-signature": payment_header,
                },
            )
    finally:
        asyncio.run(runtime.aclose())

    assert response.status_code == 422
    assert response.json()["detail"] == "policy_hold_regimes_too_permissive"
    assert fake.calls == []
    assert facilitator.verify_calls == []
    assert facilitator.settle_calls == []


def test_numeric_overflow_is_bounded_consistently_across_every_agent_transport(
    tmp_path: Path,
) -> None:
    fake = FakeUpstream()
    runtime, facilitator = _x402_runtime(tmp_path)
    sink = InMemoryTelemetrySink()
    telemetry = TelemetryEmitter(
        service_version=SERVICE_VERSION,
        source_revision=SERVICE_REVISION,
        sink=sink,
        clock=lambda: NOW,
    )
    policy = _policy()
    policy["max_notional_usd"] = 10**4000
    arguments = {"request": _request(), "policy": policy}
    body = _json_bytes(arguments)
    assert len(body) < MAX_REQUEST_BYTES
    try:
        with _client(
            fake,
            x402_runtime=runtime,
            telemetry=telemetry,
        ) as client:
            rest = client.post(
                "/v1/check",
                content=body,
                headers={"content-type": "application/json"},
            )
            paid = client.post(
                "/v1/x402/check",
                content=body,
                headers={"content-type": "application/json"},
            )
            mcp = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "assess_trade_safety",
                        "arguments": arguments,
                    },
                },
                headers={"MCP-Protocol-Version": MCP_LEGACY_PROTOCOL_VERSION},
            )
    finally:
        asyncio.run(runtime.aclose())

    assert rest.status_code == paid.status_code == 422
    assert (
        rest.json()
        == paid.json()
        == {
            "detail": "numeric input is outside the supported range",
            "state": "invalid_request",
        }
    )
    assert mcp.status_code == 200
    mcp_result = mcp.json()["result"]
    assert mcp_result["isError"] is True
    assert mcp_result["structuredContent"]["error"]["code"] == (
        "trade_safety_request_rejected"
    )
    for response in (rest, paid, mcp):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-trade-safety-mode"] == "sandbox"
        assert response.headers["x-trade-safety-authority"] == "read-only"
        assert response.headers["x-trade-safety-execution"] == "disabled"
    assert fake.calls == []
    assert facilitator.verify_calls == []
    assert facilitator.settle_calls == []
    assert [json.loads(line)["event"] for line in sink.lines] == [
        "assessment_rejected",
        "assessment_rejected",
        "assessment_rejected",
        "mcp_activation",
    ]


def test_upstream_numeric_overflow_is_evidence_unavailable_not_caller_rejection() -> (
    None
):
    fake = FakeUpstream()
    fake.responses[SEICHE_URL] = _seiche_bytes(stress_index=10**4000)
    sink = InMemoryTelemetrySink()
    telemetry = TelemetryEmitter(
        service_version=SERVICE_VERSION,
        source_revision=SERVICE_REVISION,
        sink=sink,
        clock=lambda: NOW,
    )

    with _client(fake, telemetry=telemetry) as client:
        response = _post_check(client, _request())

    assert response.status_code == 200
    assert response.json()["decision"]["outcome"] == "unavailable"
    assert response.json()["evidence"]["seiche"]["state"] == "unavailable"
    assert [json.loads(line)["event"] for line in sink.lines] == [
        "assessment_accepted",
        "assessment_outcome",
    ]
    assert json.loads(sink.lines[1])["properties"]["outcome"] == "unavailable"


def test_x402_uncertain_settlement_is_sticky_and_never_retried(
    tmp_path: Path,
) -> None:
    fake = FakeUpstream()
    failing_facilitator = FakeX402Facilitator(
        settle_error=TimeoutError("unknown settlement result")
    )
    runtime, facilitator = _x402_runtime(
        tmp_path,
        facilitator=failing_facilitator,
    )
    body = _json_bytes({"request": _request(), "policy": _policy()})
    payment_header = _x402_payment_header(runtime.gate, body)
    try:
        with _client(fake, x402_runtime=runtime) as client:
            first = client.post(
                "/v1/x402/check",
                content=body,
                headers={
                    "content-type": "application/json",
                    "payment-signature": payment_header,
                },
            )
            retry = client.post(
                "/v1/x402/check",
                content=body,
                headers={
                    "content-type": "application/json",
                    "payment-signature": payment_header,
                },
            )
    finally:
        asyncio.run(runtime.aclose())

    assert first.status_code == retry.status_code == 503
    assert first.json()["state"] == "settlement_uncertain"
    assert retry.json()["state"] == "settlement_uncertain"
    assert len(fake.calls) == 2
    assert len(facilitator.verify_calls) == 1
    assert len(facilitator.settle_calls) == 1


def test_x402_terminal_settlement_failure_returns_protocol_header_and_replays(
    tmp_path: Path,
) -> None:
    fake = FakeUpstream()
    sink = InMemoryTelemetrySink()
    telemetry = TelemetryEmitter(
        service_version=SERVICE_VERSION,
        source_revision=SERVICE_REVISION,
        sink=sink,
        clock=lambda: NOW,
    )
    failure = {
        "success": False,
        "errorReason": "insufficient_funds",
        "payer": X402_PAYER,
        "transaction": "",
        "network": X402_NETWORK,
    }
    runtime, facilitator = _x402_runtime(
        tmp_path,
        facilitator=FakeX402Facilitator(settle_response=failure),
    )
    body = _json_bytes({"request": _request(), "policy": _policy()})
    payment_header = _x402_payment_header(runtime.gate, body)
    try:
        with _client(fake, x402_runtime=runtime, telemetry=telemetry) as client:
            first = client.post(
                "/v1/x402/check",
                content=body,
                headers={
                    "content-type": "application/json",
                    "payment-signature": payment_header,
                },
            )
            replay = client.post(
                "/v1/x402/check",
                content=body,
                headers={
                    "content-type": "application/json",
                    "payment-signature": payment_header,
                },
            )
    finally:
        asyncio.run(runtime.aclose())

    assert first.status_code == replay.status_code == 402
    assert first.content == replay.content == b"{}"
    assert PAYMENT_REQUIRED_HEADER not in first.headers
    assert PAYMENT_REQUIRED_HEADER not in replay.headers
    assert (
        first.headers[PAYMENT_RESPONSE_HEADER]
        == replay.headers[PAYMENT_RESPONSE_HEADER]
    )
    assert (
        json.loads(
            base64.b64decode(first.headers[PAYMENT_RESPONSE_HEADER], validate=True)
        )
        == failure
    )
    assert len(fake.calls) == 2
    assert len(facilitator.verify_calls) == 1
    assert len(facilitator.settle_calls) == 1
    assert [json.loads(line)["event"] for line in sink.lines] == [
        "assessment_accepted",
        "assessment_outcome",
        "x402_settle_failed",
    ]


def test_x402_malformed_payment_header_is_http_400_without_new_offer(
    tmp_path: Path,
) -> None:
    fake = FakeUpstream()
    runtime, facilitator = _x402_runtime(tmp_path)
    body = _json_bytes({"request": _request(), "policy": _policy()})
    try:
        with _client(fake, x402_runtime=runtime) as client:
            response = client.post(
                "/v1/x402/check",
                content=body,
                headers={
                    "content-type": "application/json",
                    "payment-signature": "not+canonical=base64",
                },
            )
            payment = json.loads(
                base64.b64decode(
                    _x402_payment_header(runtime.gate, body),
                    validate=True,
                )
            )
            payment["x402Version"] = 1
            invalid_payload = client.post(
                "/v1/x402/check",
                content=body,
                headers={
                    "content-type": "application/json",
                    "payment-signature": base64.b64encode(_json_bytes(payment)).decode(
                        "ascii"
                    ),
                },
            )
    finally:
        asyncio.run(runtime.aclose())

    assert response.status_code == 400
    assert response.json()["state"] == "payment_malformed"
    assert PAYMENT_REQUIRED_HEADER not in response.headers
    assert PAYMENT_RESPONSE_HEADER not in response.headers
    assert invalid_payload.status_code == 400
    assert invalid_payload.json()["state"] == "payment_malformed"
    assert PAYMENT_REQUIRED_HEADER not in invalid_payload.headers
    assert PAYMENT_RESPONSE_HEADER not in invalid_payload.headers
    assert fake.calls == []
    assert facilitator.verify_calls == []
    assert facilitator.settle_calls == []


def test_x402_settled_replay_never_releases_an_expired_safety_receipt(
    tmp_path: Path,
) -> None:
    fake = FakeUpstream()
    runtime, facilitator = _x402_runtime(tmp_path)
    current = [NOW]
    body = _json_bytes({"request": _request(), "policy": _policy()})
    payment_header = _x402_payment_header(runtime.gate, body)
    try:
        with _client(
            fake,
            x402_runtime=runtime,
            clock=lambda: current[0],
        ) as client:
            paid = client.post(
                "/v1/x402/check",
                content=body,
                headers={
                    "content-type": "application/json",
                    "payment-signature": payment_header,
                },
            )
            assert paid.status_code == 200

            current[0] = NOW + timedelta(seconds=60)
            expired = client.post(
                "/v1/x402/check",
                content=body,
                headers={
                    "content-type": "application/json",
                    "payment-signature": payment_header,
                },
            )
    finally:
        asyncio.run(runtime.aclose())

    assert expired.status_code == 409
    assert expired.json()["state"] == "settled_response_expired"
    assert (
        expired.headers[PAYMENT_RESPONSE_HEADER]
        == paid.headers[PAYMENT_RESPONSE_HEADER]
    )
    assert len(fake.calls) == 2
    assert len(facilitator.verify_calls) == 1
    assert len(facilitator.settle_calls) == 1


def test_x402_slow_success_returns_settlement_proof_without_stale_receipt(
    tmp_path: Path,
) -> None:
    fake = FakeUpstream()
    current = [NOW]

    def advance_past_receipt_expiry() -> None:
        current[0] = NOW + timedelta(seconds=60)

    facilitator = FakeX402Facilitator(
        settle_hook=advance_past_receipt_expiry,
    )
    runtime, facilitator = _x402_runtime(tmp_path, facilitator=facilitator)
    sink = InMemoryTelemetrySink()
    telemetry = TelemetryEmitter(
        service_version=SERVICE_VERSION,
        source_revision=SERVICE_REVISION,
        sink=sink,
        clock=lambda: current[0],
    )
    body = _json_bytes({"request": _request(), "policy": _policy()})
    payment_header = _x402_payment_header(runtime.gate, body)
    try:
        with _client(
            fake,
            x402_runtime=runtime,
            telemetry=telemetry,
            clock=lambda: current[0],
        ) as client:
            first = client.post(
                "/v1/x402/check",
                content=body,
                headers={
                    "content-type": "application/json",
                    "payment-signature": payment_header,
                },
            )
            replay = client.post(
                "/v1/x402/check",
                content=body,
                headers={
                    "content-type": "application/json",
                    "payment-signature": payment_header,
                },
            )
    finally:
        asyncio.run(runtime.aclose())

    assert first.status_code == replay.status_code == 409
    assert first.json()["state"] == "settled_response_expired"
    assert replay.json()["state"] == "settled_response_expired"
    assert first.headers[PAYMENT_RESPONSE_HEADER]
    assert (
        replay.headers[PAYMENT_RESPONSE_HEADER]
        == first.headers[PAYMENT_RESPONSE_HEADER]
    )
    assert len(fake.calls) == 2
    assert len(facilitator.verify_calls) == 1
    assert len(facilitator.settle_calls) == 1
    records = [json.loads(line) for line in sink.lines]
    assert [record["event"] for record in records] == [
        "assessment_accepted",
        "assessment_outcome",
        "x402_settled",
        "x402_release_failed",
        "x402_release_failed",
    ]
    assert records[3]["properties"] == {
        "delivery": "initial",
        "reason": "response_expired",
    }
    assert records[4]["properties"] == {
        "delivery": "replay",
        "reason": "response_expired",
    }


def test_x402_local_cache_limit_aborts_claim_before_settlement(
    tmp_path: Path,
) -> None:
    fake = FakeUpstream()
    runtime, facilitator = _x402_runtime(
        tmp_path,
        config_overrides={"max_cached_response_bytes": 1},
    )
    body = _json_bytes({"request": _request(), "policy": _policy()})
    payment_header = _x402_payment_header(runtime.gate, body)
    try:
        with _client(fake, x402_runtime=runtime) as client:
            responses = [
                client.post(
                    "/v1/x402/check",
                    content=body,
                    headers={
                        "content-type": "application/json",
                        "payment-signature": payment_header,
                    },
                )
                for _ in range(2)
            ]
    finally:
        asyncio.run(runtime.aclose())

    assert [response.status_code for response in responses] == [502, 502]
    assert all(
        response.json()["state"] == "response_too_large" for response in responses
    )
    assert len(fake.calls) == 4
    assert len(facilitator.verify_calls) == 2
    assert facilitator.settle_calls == []


def test_x402_payment_for_body_a_cannot_authorize_body_b(
    tmp_path: Path,
) -> None:
    fake = FakeUpstream()
    runtime, facilitator = _x402_runtime(tmp_path)
    body_a = _json_bytes({"request": _request(), "policy": _policy()})
    changed_request = _request()
    changed_request["request_id"] = "gateway-paper-different"
    body_b = _json_bytes({"request": changed_request, "policy": _policy()})
    payment_header = _x402_payment_header(runtime.gate, body_a)
    try:
        with _client(fake, x402_runtime=runtime) as client:
            response = client.post(
                "/v1/x402/check",
                content=body_b,
                headers={
                    "content-type": "application/json",
                    "payment-signature": payment_header,
                },
            )
    finally:
        asyncio.run(runtime.aclose())

    assert response.status_code == 400
    assert response.json()["state"] == "offer_mismatch"
    assert PAYMENT_REQUIRED_HEADER not in response.headers
    assert fake.calls == []
    assert facilitator.verify_calls == []
    assert facilitator.settle_calls == []


def test_x402_valid_response_from_another_paid_row_is_never_released(
    tmp_path: Path,
) -> None:
    fake = FakeUpstream()
    runtime, facilitator = _x402_runtime(tmp_path)
    request_a = _request()
    request_b = _request()
    request_b["request_id"] = "gateway-paper-row-b"
    body_a = _json_bytes({"request": request_a, "policy": _policy()})
    body_b = _json_bytes({"request": request_b, "policy": _policy()})
    payment_a = _x402_payment_header(runtime.gate, body_a, nonce_hex="b")
    payment_b = _x402_payment_header(runtime.gate, body_b, nonce_hex="c")

    try:
        with _client(fake, x402_runtime=runtime) as client:
            paid_a = client.post(
                "/v1/x402/check",
                content=body_a,
                headers={
                    "content-type": "application/json",
                    "payment-signature": payment_a,
                },
            )
            assert paid_a.status_code == 200

            fake.responses[UNDERTOW_URL] = _undertow_bytes(
                request_hash=trade_safety_request_hash(request_b)
            )
            paid_b = client.post(
                "/v1/x402/check",
                content=body_b,
                headers={
                    "content-type": "application/json",
                    "payment-signature": payment_b,
                },
            )
            assert paid_b.status_code == 200
            assert paid_b.content != paid_a.content

            table = runtime.journal._TABLE
            runtime.journal._connection.execute(
                f"""
                UPDATE {table}
                SET response_body = ?, response_sha256 = ?
                WHERE body_sha256 = ?
                """,
                (
                    paid_b.content,
                    hashlib.sha256(paid_b.content).hexdigest(),
                    hashlib.sha256(body_a).hexdigest(),
                ),
            )

            substituted = client.post(
                "/v1/x402/check",
                content=body_a,
                headers={
                    "content-type": "application/json",
                    "payment-signature": payment_a,
                },
            )
    finally:
        asyncio.run(runtime.aclose())

    assert substituted.status_code == 503
    assert substituted.json()["state"] == "settled_response_invalid"
    assert substituted.content != paid_b.content
    assert substituted.headers[PAYMENT_RESPONSE_HEADER]
    assert len(facilitator.verify_calls) == 2
    assert len(facilitator.settle_calls) == 2


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
        assert passed["evidence"]["undertow"]["source_schema"] == (
            "undertow.trade-safety-exit-context.v1"
        )

        limited_policy = _policy()
        limited_policy["max_exit_cost_bps"] = 5.0
        limited = _post_check(client, request, limited_policy).json()
        assert limited["decision"]["outcome"] == "limit"
        assert "max_exit_cost_bps_exceeded" in limited["decision"]["reason_codes"]


def test_caller_cannot_remove_server_safety_policy_before_network_io() -> None:
    fake = FakeUpstream()
    permissive = _policy()
    permissive["hold_regimes"] = []
    permissive["max_notional_usd"] = 1_000_000_000.0
    permissive["max_exit_cost_bps"] = None
    permissive["max_venue_spread_bps"] = None
    permissive["max_evidence_age_seconds"] = {
        "seiche": 10 * 365 * 86_400,
        "undertow": 10 * 365 * 86_400,
        "liquilens": 10 * 365 * 86_400,
    }

    with _client(fake) as client:
        response = _post_check(client, _request(), permissive)

    assert response.status_code == 422
    assert response.json() == {
        "detail": "policy_hold_regimes_too_permissive",
        "state": "invalid_request",
    }
    assert fake.calls == []


def test_gateway_telemetry_reports_funnel_without_request_identity() -> None:
    fake = FakeUpstream()
    sink = InMemoryTelemetrySink()
    telemetry = TelemetryEmitter(
        service_version=SERVICE_VERSION,
        source_revision=SERVICE_REVISION,
        sink=sink,
        clock=lambda: NOW,
    )
    permissive = _policy()
    permissive["hold_regimes"] = []

    with _client(fake, telemetry=telemetry) as client:
        passed = _post_check(client, _request())
        rejected = _post_check(client, _request(), permissive)
        initialized = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": "telemetry-init",
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_LEGACY_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1"},
                },
            },
        )

    assert passed.status_code == 200
    assert rejected.status_code == 422
    assert initialized.status_code == 200
    records = [json.loads(line) for line in sink.lines]
    assert [record["event"] for record in records] == [
        "assessment_accepted",
        "assessment_outcome",
        "assessment_rejected",
        "mcp_activation",
    ]
    assert records[1]["properties"] == {"outcome": "pass"}
    assert records[2]["properties"] == {"reason": "policy_not_admitted"}
    assert records[3]["properties"] == {
        "operation": "initialize",
        "outcome": "success",
    }
    serialized = b"\n".join(sink.lines)
    for forbidden in (
        b"copilot-test",
        b"operator-test",
        b"tenant-test",
        b"sandbox-account",
        b"gateway-paper-1000",
        b"BTC/USD",
    ):
        assert forbidden not in serialized


def test_gateway_exposes_degraded_telemetry_without_changing_assessment() -> None:
    class BrokenSink:
        def write(self, _line: bytes, /) -> None:
            raise OSError("private path and diagnostics must not escape")

    fake = FakeUpstream()
    telemetry = TelemetryEmitter(
        service_version=SERVICE_VERSION,
        source_revision=SERVICE_REVISION,
        sink=BrokenSink(),
        clock=lambda: NOW,
    )

    with _client(fake, telemetry=telemetry) as client:
        response = _post_check(client, _request())
        health = client.get("/healthz")
        advertised = client.get("/v1/capabilities")

    assert response.status_code == 200
    assert response.json()["decision"]["outcome"] == "pass"
    expected_status = {"state": "degraded", "delivery_failures": 2}
    assert health.json()["telemetry"] == expected_status
    assert advertised.json()["telemetry"] == expected_status
    assert "private path" not in health.text
    assert "diagnostics" not in advertised.text


def test_early_http_rejections_use_neutral_telemetry_operations() -> None:
    fake = FakeUpstream()
    sink = InMemoryTelemetrySink()
    telemetry = TelemetryEmitter(
        service_version=SERVICE_VERSION,
        source_revision=SERVICE_REVISION,
        sink=sink,
        clock=lambda: NOW,
    )

    with _client(fake, telemetry=telemetry) as client:
        malformed = client.post(
            "/v1/check",
            content=b"{",
            headers={"content-type": "application/json"},
        )
        wrong_media_type = client.post(
            "/v1/check",
            content=b"{}",
            headers={"content-type": "text/plain"},
        )
        malformed_mcp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "params": {}},
        )

    assert malformed.status_code == 400
    assert wrong_media_type.status_code == 415
    assert malformed_mcp.status_code == 422
    records = [json.loads(line) for line in sink.lines]
    assert [record["event"] for record in records] == [
        "assessment_rejected",
        "assessment_rejected",
        "mcp_activation",
    ]
    assert records[0]["properties"] == {"reason": "invalid_request"}
    assert records[1]["properties"] == {"reason": "invalid_request"}
    assert records[2]["properties"] == {
        "operation": "transport",
        "outcome": "error",
    }
    assert fake.calls == []


def test_disabled_x402_probe_does_not_emit_paid_funnel_event() -> None:
    fake = FakeUpstream()
    sink = InMemoryTelemetrySink()
    telemetry = TelemetryEmitter(
        service_version=SERVICE_VERSION,
        source_revision=SERVICE_REVISION,
        sink=sink,
        clock=lambda: NOW,
    )

    with _client(fake, telemetry=telemetry) as client:
        response = client.post(
            "/v1/x402/check",
            content=b"{}",
            headers={"content-type": "text/plain"},
        )

    assert response.status_code == 415
    assert sink.lines == []
    assert fake.calls == []


def test_unexpected_x402_backend_error_is_bounded_json_with_authority_headers(
    tmp_path: Path,
) -> None:
    fake = FakeUpstream()
    runtime, facilitator = _x402_runtime(tmp_path)
    body = _json_bytes({"request": _request(), "policy": _policy()})
    payment_header = _x402_payment_header(runtime.gate, body)
    runtime.journal.close()
    app = create_app(upstream=fake, clock=lambda: NOW, x402_runtime=runtime)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/v1/x402/check",
                content=body,
                headers={
                    "content-type": "application/json",
                    "payment-signature": payment_header,
                },
            )
    finally:
        asyncio.run(runtime.aclose())

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "detail": "gateway unavailable",
        "state": "unavailable",
    }
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-trade-safety-mode"] == "sandbox"
    assert response.headers["x-trade-safety-authority"] == "read-only"
    assert response.headers["x-trade-safety-execution"] == "disabled"
    assert PAYMENT_REQUIRED_HEADER not in response.headers
    assert PAYMENT_RESPONSE_HEADER not in response.headers
    assert fake.calls == []
    assert facilitator.verify_calls == []
    assert facilitator.settle_calls == []


def test_native_contract_calls_bind_and_retain_proof_rights_and_clocks() -> None:
    fake = FakeUpstream()
    request = _request()
    request_hash = trade_safety_request_hash(request)
    with _client(fake) as client:
        receipt = _post_check(client, request).json()

    assert fake.calls[0] == ("GET", SEICHE_URL, None)
    undertow_call = next(call for call in fake.calls if call[1] == UNDERTOW_URL)
    assert undertow_call == (
        "POST",
        UNDERTOW_URL,
        {
            "jsonrpc": "2.0",
            "id": "trade-safety-undertow-v1",
            "method": "tools/call",
            "params": {
                "name": "trade_safety_exit_context",
                "arguments": {
                    "request_hash": request_hash,
                    "mode": "paper",
                    "instrument": "BTC/USD",
                    "side": "sell",
                    "venue": None,
                    "requested_size_usd": 1_000.0,
                },
            },
        },
    )
    seiche = receipt["evidence"]["seiche"]
    undertow = receipt["evidence"]["undertow"]
    assert seiche["facts"]["gateway_binding"]["request_hash"] == request_hash
    assert seiche["facts"]["clocks"]["evidence_as_of"] == seiche["as_of"]
    assert seiche["facts"]["attestation"]["ledger_read"] is False
    assert seiche["facts"]["staleness"]["total"] == 6
    assert undertow["facts"]["gateway_binding"]["request_hash"] == request_hash
    assert undertow["facts"]["native_request"]["request_hash"] == request_hash
    assert undertow["facts"]["source"] == {
        "url": UNDERTOW_URL,
        "pack": "crypto_desk.json",
        "source_schema": "undertow.crypto_desk.v2",
        "raw_sha256": "1" * 64,
        "canonical_sha256": "2" * 64,
        "pit_input_sha256": "3" * 64,
        "deployed_sha": "4" * 40,
    }
    assert undertow["facts"]["pit"]["chain_verified"] is True
    assert undertow["facts"]["pit"]["head_verified"] is True
    assert undertow["facts"]["rights"]["status"] == "approved"
    assert undertow["facts"]["rights"]["scope"] == "derived_metadata_only"
    assert undertow["facts"]["clocks"]["expires_at"] == ("2026-09-02T13:59:00Z")
    assert set(undertow["facts"]["authority"].values()) <= {
        False,
        True,
        "context_only",
        "paper",
    }
    assert receipt["request_hash"] == request_hash
    assert receipt["decision"]["outcome"] == "pass"

    for section in (seiche, undertow):
        binding = dict(section["facts"]["gateway_binding"])
        binding_sha256 = binding.pop("binding_sha256")
        assert binding_sha256 == hashlib.sha256(_json_bytes(binding)).hexdigest()


def test_observe_request_is_bound_without_becoming_an_enforcement_or_order() -> None:
    request = _request(mode="observe")
    request_hash = trade_safety_request_hash(request)
    fake = FakeUpstream()
    fake.responses[UNDERTOW_URL] = _undertow_bytes(
        request_hash=request_hash, mode="observe"
    )
    with _client(fake) as client:
        receipt = _post_check(client, request).json()
    assert receipt["decision"]["outcome"] == "pass"
    assert receipt["decision"]["enforced"] is False
    assert receipt["evidence"]["undertow"]["facts"]["authority"]["mode"] == ("observe")
    assert (
        receipt["evidence"]["undertow"]["facts"]["authority"]["can_place_order"]
        is False
    )


@pytest.mark.parametrize(
    ("path", "value", "reseal"),
    [
        (("unexpected",), "extension", True),
        (("can_authorize_order",), True, True),
        (("projection_sha256",), "f" * 64, False),
        (("clocks", "evidence_age_seconds"), 1, True),
        (("staleness", "total"), 999, True),
        (("status",), "unavailable", True),
    ],
)
def test_seiche_adversarial_native_context_fails_typed_unavailable(
    path: tuple[str, ...], value: Any, reseal: bool
) -> None:
    fake = FakeUpstream()
    fake.responses[SEICHE_URL] = _mutated_seiche(path, value, reseal=reseal)
    with _client(fake) as client:
        response = _post_check(client, _request())
    receipt = response.json()
    section = receipt["evidence"]["seiche"]
    assert response.status_code == 200
    assert receipt["decision"]["outcome"] == "unavailable"
    assert section["state"] == "unavailable"
    assert (
        section["source_sha256"]
        == hashlib.sha256(fake.responses[SEICHE_URL]).hexdigest()
    )
    assert "seiche_upstream_contract_unavailable_or_invalid" in section["limitations"]


@pytest.mark.parametrize(
    ("path", "value", "reseal"),
    [
        (("unexpected",), "extension", True),
        (("context_sha256",), "f" * 64, False),
        (("status",), "unavailable", True),
        (("request_hash",), "b" * 64, True),
        (("request", "request_hash"), "b" * 64, True),
        (("source", "deployed_sha"), "bad", True),
        (("pit", "chain_verified"), False, True),
        (("rights", "status"), "pending_owner_review", True),
        (("rights", "venue_proof_sha256_by_venue", "okx"), "bad", True),
        (("authority", "can_route_order"), True, True),
        (("coverage", "priced_venues"), ["binance"], True),
        (("clocks", "expires_at"), "2026-09-02T12:00:00Z", True),
    ],
)
def test_undertow_adversarial_native_context_fails_typed_unavailable(
    path: tuple[str, ...], value: Any, reseal: bool
) -> None:
    request = _request()
    request_hash = trade_safety_request_hash(request)
    fake = FakeUpstream()
    raw = _mutated_undertow(path, value, request_hash=request_hash, reseal=reseal)
    fake.responses[UNDERTOW_URL] = raw
    with _client(fake) as client:
        response = _post_check(client, request)
    receipt = response.json()
    section = receipt["evidence"]["undertow"]
    assert response.status_code == 200
    assert receipt["decision"]["outcome"] == "unavailable"
    assert section["state"] == "unavailable"
    assert section["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert (
        "undertow_trade_safety_context_unavailable_or_invalid" in section["limitations"]
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("result", "isError"), "false"),
        (("result", "unexpected"), "extension"),
        (("unexpected",), "extension"),
        (("result", "content"), {"type": "text"}),
    ],
)
def test_undertow_adversarial_mcp_envelope_fails_typed_unavailable(
    path: tuple[str, ...], value: Any
) -> None:
    fake = FakeUpstream()
    raw = _mutated_undertow_envelope(path, value)
    fake.responses[UNDERTOW_URL] = raw
    with _client(fake) as client:
        receipt = _post_check(client, _request()).json()
    section = receipt["evidence"]["undertow"]
    assert receipt["decision"]["outcome"] == "unavailable"
    assert section["state"] == "unavailable"
    assert section["facts"] == {}
    assert section["source_sha256"] == hashlib.sha256(raw).hexdigest()


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


def test_quantity_cannot_hide_economic_size_from_notional_policy() -> None:
    request = _request(amount=1_000.0)
    request["order"]["quantity"] = 1_000_000_000.0
    fake = FakeUpstream()

    with _client(fake) as client:
        response = _post_check(client, request)

    assert response.status_code == 200
    receipt = response.json()
    assert receipt["decision"]["outcome"] == "unavailable"
    assert receipt["evidence"]["undertow"]["limitations"] == [
        "quantity_requires_broker_normalized_economic_order_binding"
    ]
    assert not any(url == UNDERTOW_URL for _method, url, _body in fake.calls)


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


def test_unmeasured_depth_and_forged_spread_make_undertow_unavailable() -> None:
    unable_fake = FakeUpstream()
    unable_raw = _undertow_bytes(unable=["venue-c"])
    unable_fake.responses[UNDERTOW_URL] = unable_raw
    with _client(unable_fake) as client:
        receipt = _post_check(client, _request()).json()
    section = receipt["evidence"]["undertow"]
    assert receipt["decision"]["outcome"] == "unavailable"
    assert section["state"] == "unavailable"
    assert section["facts"] == {}
    assert section["source_sha256"] == hashlib.sha256(unable_raw).hexdigest()

    spread_fake = FakeUpstream()
    spread_raw = _undertow_bytes(spread=7.0)
    spread_fake.responses[UNDERTOW_URL] = spread_raw
    with _client(spread_fake) as client:
        receipt = _post_check(client, _request()).json()
    section = receipt["evidence"]["undertow"]
    assert receipt["decision"]["outcome"] == "unavailable"
    assert section["state"] == "unavailable"
    assert section["source_sha256"] == hashlib.sha256(spread_raw).hexdigest()


def test_partial_venue_map_cannot_claim_complete_undertow_coverage() -> None:
    fake = FakeUpstream()
    partial_raw = _undertow_bytes(
        venue_costs={"binance": 2.0},
        best_venue="binance",
        worst_venue="binance",
        worst=2.0,
        spread=0.0,
    )
    fake.responses[UNDERTOW_URL] = partial_raw
    with _client(fake) as client:
        receipt = _post_check(client, _request()).json()
    section = receipt["evidence"]["undertow"]
    assert receipt["decision"]["outcome"] == "unavailable"
    assert section["state"] == "unavailable"
    assert section["facts"] == {}
    assert section["source_sha256"] == hashlib.sha256(partial_raw).hexdigest()


def test_extra_venue_cannot_claim_declared_undertow_coverage() -> None:
    fake = FakeUpstream()
    venue_costs = {
        "binance": 2.0,
        "bitfinex": 10.0,
        "coinbase": 4.0,
        "gemini": 6.0,
        "kraken": 3.0,
        "okx": 5.0,
        "off-roster": 7.0,
    }
    extra_raw = _undertow_bytes(venue_costs=venue_costs)
    fake.responses[UNDERTOW_URL] = extra_raw
    with _client(fake) as client:
        receipt = _post_check(client, _request()).json()
    section = receipt["evidence"]["undertow"]
    assert receipt["decision"]["outcome"] == "unavailable"
    assert section["state"] == "unavailable"
    assert section["facts"] == {}
    assert section["source_sha256"] == hashlib.sha256(extra_raw).hexdigest()


def test_unsupported_venue_and_malformed_sizes_never_reach_undertow() -> None:
    for request in (
        _request(venue="venue-a"),
        _request(amount=5_000.0),
        _request(amount=10**30),
    ):
        request["request_id"] = "gateway-paper-unsupported-undertow-input"
        fake = FakeUpstream()
        with _client(fake) as client:
            receipt = _post_check(client, request).json()
        assert receipt["decision"]["outcome"] == "unavailable"
        assert receipt["evidence"]["undertow"]["state"] == "unavailable"
        assert not any(url == UNDERTOW_URL for _method, url, _body in fake.calls)

    for amount in (True, "1000", -1.0):
        fake = FakeUpstream()
        request = _request()
        request["order"]["notional"]["amount"] = amount
        with _client(fake) as client:
            response = _post_check(client, request)
        assert response.status_code == 422
        assert fake.calls == []

    fake = FakeUpstream()
    body = _json_bytes({"request": _request(), "policy": _policy()}).replace(
        b'"amount":1000.0', b'"amount":NaN'
    )
    with _client(fake) as client:
        response = client.post(
            "/v1/check", content=body, headers={"content-type": "application/json"}
        )
    assert response.status_code == 400
    assert fake.calls == []


def test_malformed_venue_cost_values_fail_closed() -> None:
    malformed = (
        {"venue-a": 2.0, "venue-b": True},
        {"venue-a": 2.0, "venue-b": "10"},
        {"venue-a": 2.0, "venue-b": -1.0},
    )
    for costs in malformed:
        fake = FakeUpstream()
        raw = _undertow_bytes(venue_costs=costs)
        fake.responses[UNDERTOW_URL] = raw
        with _client(fake) as client:
            receipt = _post_check(client, _request()).json()
        assert receipt["decision"]["outcome"] == "unavailable"
        assert receipt["evidence"]["undertow"]["state"] == "unavailable"

    fake = FakeUpstream()
    raw = _undertow_bytes().replace(b'"bitfinex":10.0', b'"bitfinex":NaN')
    fake.responses[UNDERTOW_URL] = raw
    with _client(fake) as client:
        receipt = _post_check(client, _request()).json()
    assert receipt["decision"]["outcome"] == "unavailable"


def test_fresh_seiche_wrapper_cannot_hide_stale_native_evidence_clock() -> None:
    fake = FakeUpstream()
    fake.responses[SEICHE_URL] = _seiche_bytes(oldest_headline_asof="2026-08-20")
    with _client(fake) as client:
        receipt = _post_check(client, _request()).json()
    assert receipt["evidence"]["seiche"]["state"] == "unavailable"
    assert receipt["evidence"]["seiche"]["as_of"] is None
    assert receipt["decision"]["outcome"] == "unavailable"
    assert "seiche_evidence_unavailable" in receipt["decision"]["reason_codes"]


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
    assert not any(url == UNDERTOW_URL for _method, url, _body in fake.calls)


def test_buy_order_never_reaches_sell_only_undertow_context() -> None:
    fake = FakeUpstream()
    request = _request()
    request["order"]["side"] = "buy"
    with _client(fake) as client:
        receipt = _post_check(client, request).json()
        authority = client.get("/v1/capabilities").json()["authority"]
    assert receipt["decision"]["outcome"] == "unavailable"
    assert receipt["evidence"]["undertow"]["limitations"] == [
        "undertow_trade_safety_context_supports_only_sell_orders"
    ]
    assert not any(url == UNDERTOW_URL for _method, url, _body in fake.calls)
    assert authority["can_execute"] is False
    assert authority["can_route_order"] is False
    assert authority["can_custody"] is False
    assert authority["can_settle"] is False


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
            headers=_modern_mcp_headers("server/discover"),
        ).json()
        assert discovered["result"]["supportedVersions"] == [MCP_PROTOCOL_VERSION]
        assert discovered["result"]["resultType"] == "complete"

        listed = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"MCP-Protocol-Version": MCP_LEGACY_PROTOCOL_VERSION},
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
            headers=_modern_mcp_headers("tools/list"),
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
            headers={"MCP-Protocol-Version": MCP_LEGACY_PROTOCOL_VERSION},
        ).json()
        result = called["result"]
        assert result["isError"] is False
        assert result["structuredContent"]["decision"]["outcome"] == "pass"
        assert "no execution" in result["content"][0]["text"]


def test_mcp_streamable_http_transport_contract() -> None:
    fake = FakeUpstream()
    issuer = httpx.URL(ISSUER_ENDPOINT)
    issuer_origin = f"{issuer.scheme}://{issuer.host}"
    if issuer.port is not None:
        issuer_origin = f"{issuer_origin}:{issuer.port}"
    meta = {
        "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    with _client(fake) as client:
        initialized = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
            headers={"MCP-Protocol-Version": MCP_LEGACY_PROTOCOL_VERSION},
        )
        unsupported = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"MCP-Protocol-Version": "1900-01-01"},
        )
        invalid_origin = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "ping"},
            headers={
                "MCP-Protocol-Version": MCP_LEGACY_PROTOCOL_VERSION,
                "Origin": "https://attacker.example",
            },
        )
        trusted_origin = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 3, "method": "ping"},
            headers={
                "MCP-Protocol-Version": MCP_LEGACY_PROTOCOL_VERSION,
                "Origin": issuer_origin,
            },
        )
        invalid_origin_get = client.get(
            "/mcp",
            headers={"Origin": "https://attacker.example"},
        )
        unknown_notification = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "notifications/future_extension",
                "params": {},
            },
            headers={"MCP-Protocol-Version": MCP_LEGACY_PROTOCOL_VERSION},
        )
        null_id_request = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": None, "method": "ping"},
            headers={"MCP-Protocol-Version": MCP_LEGACY_PROTOCOL_VERSION},
        )
        null_id_notification = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": None,
                "method": "notifications/initialized",
            },
            headers={"MCP-Protocol-Version": MCP_LEGACY_PROTOCOL_VERSION},
        )
        malformed_notification = client.post(
            "/mcp",
            json={"jsonrpc": "1.0", "method": "notifications/initialized"},
            headers={"MCP-Protocol-Version": MCP_LEGACY_PROTOCOL_VERSION},
        )
        headerless_legacy = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 6, "method": "tools/list"},
        )
        modern_without_headers = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/list",
                "params": {"_meta": meta},
            },
        )
        modern_name_mismatch = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "_meta": meta,
                    "name": "trade_safety_capabilities",
                    "arguments": {},
                },
            },
            headers=_modern_mcp_headers("tools/call", name="different_tool"),
        )

    assert initialized.status_code == 202
    assert initialized.content == b""
    assert unsupported.status_code == 400
    assert unsupported.json()["error"] == {
        "code": -32022,
        "message": "Unsupported protocol version",
        "data": {
            "supported": [MCP_PROTOCOL_VERSION, MCP_LEGACY_PROTOCOL_VERSION],
            "requested": "1900-01-01",
        },
    }
    assert invalid_origin.status_code == 403
    assert invalid_origin.json()["error"]["message"] == "Invalid Origin"
    assert invalid_origin_get.status_code == 403
    assert unknown_notification.status_code == 202
    assert unknown_notification.content == b""
    assert null_id_request.status_code == 400
    assert null_id_request.json()["error"] == {
        "code": -32600,
        "message": "JSON-RPC request id must be non-null",
    }
    assert null_id_notification.status_code == 400
    assert null_id_notification.json()["error"]["code"] == -32600
    assert malformed_notification.status_code == 400
    assert malformed_notification.json()["error"] == {
        "code": -32600,
        "message": "invalid JSON-RPC version",
    }
    assert headerless_legacy.status_code == 400
    assert headerless_legacy.json()["error"]["code"] == -32020
    assert trusted_origin.status_code == 200
    assert trusted_origin.json()["result"] == {}
    assert modern_without_headers.status_code == 400
    assert modern_without_headers.json()["error"]["code"] == -32020
    assert modern_name_mismatch.status_code == 400
    assert modern_name_mismatch.json()["error"]["code"] == -32020
    assert fake.calls == []


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
