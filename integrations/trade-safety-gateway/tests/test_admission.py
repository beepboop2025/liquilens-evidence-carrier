from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from trade_safety_gateway.admission import AdmissionLimits, RequestAdmission
from trade_safety_gateway.app import (
    SERVICE_REVISION,
    SERVICE_VERSION,
    SafetyEnvelopeMiddleware,
    create_app,
)
from trade_safety_gateway.telemetry import InMemoryTelemetrySink, TelemetryEmitter


def test_capacity_does_not_spend_a_token_and_time_refills_only_to_burst() -> None:
    now = [0.0]
    budget = RequestAdmission(
        AdmissionLimits(max_in_flight=1, requests_per_second=1, burst=2),
        clock=lambda: now[0],
    )
    assert budget.acquire() is None
    assert budget.acquire() == "capacity_exhausted"
    budget.release()
    assert budget.acquire() is None
    budget.release()
    assert budget.acquire() == "rate_limited"
    now[0] = 1000
    for _ in range(2):
        assert budget.acquire() is None
        budget.release()
    now[0] = 1  # A clock regression must not manufacture allowance.
    assert budget.acquire() == "rate_limited"


@pytest.mark.parametrize("value", ["0", "-1", "201", "inf", "20.5", "\uff12\uff10", ""])
def test_invalid_operator_budget_fails_at_startup(monkeypatch, value) -> None:
    monkeypatch.setenv("TRADE_SAFETY_REQUEST_BURST", value)
    with pytest.raises(ValueError):
        AdmissionLimits.from_env()


def test_rate_rejection_cannot_be_bypassed_with_client_headers() -> None:
    now = [0.0]
    budget = RequestAdmission(
        AdmissionLimits(requests_per_second=1, burst=1), clock=lambda: now[0]
    )
    sink = InMemoryTelemetrySink()
    telemetry = TelemetryEmitter(
        sink=sink, service_version=SERVICE_VERSION, source_revision=SERVICE_REVISION
    )
    with TestClient(create_app(admission=budget, telemetry=telemetry)) as client:
        assert client.post("/v1/check", json={}).status_code == 422
        for identity in ("a", "b"):
            response = client.post(
                "/v1/check",
                json={},
                headers={
                    "X-Forwarded-For": identity,
                    "X-LiquiLens-Installation-Id": identity,
                    "User-Agent": "synthetic-monitor-" + identity,
                },
            )
            assert response.status_code == 429
            assert response.headers["retry-after"] == "1"
            assert response.headers["cache-control"] == "no-store"
            assert response.json()["state"] == "rate_limited"
            assert response.headers["x-trade-safety-authority"] == "read-only"
        assert client.get("/healthz").status_code == 200
        limits = client.get("/v1/capabilities").json()["limits"]["admission"]
        assert limits["burst"] == 1
        assert limits["scope"] == "per_worker_shared_anonymous_budget"
        now[0] = 1
        assert client.post("/v1/check", json={}).status_code == 422


def _scope(method="POST", path="/v1/check"):
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(b"content-type", b"application/json")],
    }


def _middleware(app, budget):
    return SafetyEnvelopeMiddleware(
        app,
        telemetry=TelemetryEmitter(
            sink=InMemoryTelemetrySink(),
            service_version=SERVICE_VERSION,
            source_revision=SERVICE_REVISION,
        ),
        x402_enabled=False,
        admission=budget,
    )


async def _ready_receive():
    return {"type": "http.request", "body": b"{}", "more_body": False}


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"{}"})


def test_slow_body_is_bounded_health_remains_available_and_cancel_releases_slot():
    async def scenario():
        budget = RequestAdmission(AdmissionLimits(max_in_flight=1))
        middleware = _middleware(_ok_app, budget)
        body_started = asyncio.Event()

        async def slow_receive():
            body_started.set()
            await asyncio.Event().wait()

        messages = []

        async def send(message):
            messages.append(message)

        first = asyncio.create_task(middleware(_scope(), slow_receive, send))
        await body_started.wait()
        try:
            await middleware(_scope(path="/mcp"), _ready_receive, send)
            assert messages[0]["status"] == 503
            payload = json.loads(messages[1]["body"])
            assert payload["error"]["data"]["state"] == "capacity_exhausted"
            messages.clear()
            await middleware(
                _scope(method="GET", path="/healthz"), _ready_receive, send
            )
            assert messages[0]["status"] == 200
        finally:
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        messages.clear()
        await middleware(_scope(), _ready_receive, send)
        assert messages[0]["status"] == 200

    asyncio.run(scenario())


def test_deadline_releases_slot_and_does_not_rewrite_started_response():
    async def scenario():
        budget = RequestAdmission(
            AdmissionLimits(max_in_flight=1, request_timeout_seconds=0.02)
        )
        messages = []

        async def send(message):
            messages.append(message)

        async def never_receive():
            await asyncio.Event().wait()

        middleware = _middleware(_ok_app, budget)
        await middleware(_scope(), never_receive, send)
        assert messages[0]["status"] == 503
        assert json.loads(messages[1]["body"])["state"] == "request_deadline"
        messages.clear()
        await middleware(_scope(), _ready_receive, send)
        assert messages[0]["status"] == 200

        async def partial_response(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await asyncio.Event().wait()

        messages.clear()
        middleware = _middleware(partial_response, budget)
        with pytest.raises(TimeoutError):
            await middleware(_scope(), _ready_receive, send)
        assert len(messages) == 1
        assert messages[0]["status"] == 200
        assert budget.acquire() is None
        budget.release()

    asyncio.run(scenario())
