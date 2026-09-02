from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import pytest
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
    SERVICE_VERSION,
    UNDERTOW_REQUIRED_VENUES,
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


def _sealed(value: dict[str, Any], field: str) -> dict[str, Any]:
    unsigned = {key: item for key, item in value.items() if key != field}
    return {**unsigned, field: hashlib.sha256(_json_bytes(unsigned)).hexdigest()}


def _seiche_bytes(
    *, regime: str = "CALM", oldest_headline_asof: str = "2026-08-26"
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
        "stress_index": 20.0,
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
            "evidence_age_seconds": int(
                (evaluated_at - evidence_at).total_seconds()
            ),
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
        "canonicalization": (
            "python-json-sort-keys-utf8-no-nan-server-internal-v1"
        ),
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
    return _mcp_response(
        "trade-safety-undertow-v1", _sealed(payload, "context_sha256")
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


def _set_path(value: dict[str, Any], path: tuple[str, ...], replacement: Any) -> None:
    target = value
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement


def _mutated_seiche(
    path: tuple[str, ...], value: Any, *, reseal: bool = True
) -> bytes:
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
        envelope["result"]["structuredContent"] = _sealed(
            payload, "context_sha256"
        )
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
        assert health.json()["version"] == SERVICE_VERSION == "0.1.3"
        assert health.json()["source_revision"] == SERVICE_REVISION
        assert health.headers["x-trade-safety-execution"] == "disabled"
        assert health.headers["x-trade-safety-authority"] == "read-only"
        capabilities = client.get("/v1/capabilities").json()
        assert capabilities["execution_tools"] == []
        assert capabilities["live_outcome"] == "unavailable"
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
    assert undertow["facts"]["clocks"]["expires_at"] == (
        "2026-09-02T13:59:00Z"
    )
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
    assert receipt["evidence"]["undertow"]["facts"]["authority"]["mode"] == (
        "observe"
    )
    assert receipt["evidence"]["undertow"]["facts"]["authority"][
        "can_place_order"
    ] is False


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
    assert section["source_sha256"] == hashlib.sha256(
        fake.responses[SEICHE_URL]
    ).hexdigest()
    assert "seiche_upstream_contract_unavailable_or_invalid" in section[
        "limitations"
    ]


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
    raw = _mutated_undertow(
        path, value, request_hash=request_hash, reseal=reseal
    )
    fake.responses[UNDERTOW_URL] = raw
    with _client(fake) as client:
        response = _post_check(client, request)
    receipt = response.json()
    section = receipt["evidence"]["undertow"]
    assert response.status_code == 200
    assert receipt["decision"]["outcome"] == "unavailable"
    assert section["state"] == "unavailable"
    assert section["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert "undertow_trade_safety_context_unavailable_or_invalid" in section[
        "limitations"
    ]


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
