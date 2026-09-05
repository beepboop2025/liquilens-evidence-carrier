from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest

from trade_safety_gateway.telemetry import (
    ASSESSMENT_ACCEPTED,
    ASSESSMENT_OUTCOME,
    ASSESSMENT_REJECTED,
    ASSESSMENT_REJECTION_REASONS,
    MAX_TELEMETRY_LINE_BYTES,
    MCP_ACTIVATION,
    TELEMETRY_PATH_ENV,
    TELEMETRY_SCHEMA,
    X402_DELIVERIES,
    X402_OFFERED,
    X402_RELEASE_FAILED,
    X402_RELEASE_FAILURE_REASONS,
    X402_RELEASED,
    X402_SETTLE_FAILED,
    X402_SETTLE_FAILURE_REASONS,
    X402_SETTLED,
    X402_VERIFY_FAILED,
    X402_VERIFY_FAILURE_REASONS,
    AppendOnlyJsonlSink,
    InMemoryTelemetrySink,
    TelemetryEmitter,
    TelemetrySchemaError,
    telemetry_from_env,
)

NOW = datetime(
    2026,
    9,
    4,
    12,
    34,
    56,
    987654,
    tzinfo=timezone(timedelta(hours=5, minutes=30)),
)
REVISION = "a" * 40


def _emitter(sink: InMemoryTelemetrySink) -> TelemetryEmitter:
    return TelemetryEmitter(
        service_version="0.1.3",
        source_revision=REVISION,
        sink=sink,
        clock=lambda: NOW,
    )


def _record(sink: InMemoryTelemetrySink) -> dict[str, object]:
    assert len(sink.lines) == 1
    return json.loads(sink.lines[0])


def test_default_emitter_is_disabled_but_still_validates_event_schema() -> None:
    emitter = TelemetryEmitter(
        service_version="0.1.3",
        source_revision="source-checkout",
        clock=lambda: (_ for _ in ()).throw(AssertionError("clock was called")),
    )

    assert emitter.enabled is False
    assert emitter.emit(ASSESSMENT_ACCEPTED, transport="rest") is False
    with pytest.raises(TelemetrySchemaError, match="event name"):
        emitter.emit(
            "not_an_event",
            transport="not_a_transport",
            request="must never be reflected",
        )


def test_env_factory_is_disabled_when_the_only_opt_in_is_unset(monkeypatch) -> None:
    monkeypatch.delenv(TELEMETRY_PATH_ENV, raising=False)
    # Similar-looking variables must not activate collection.
    monkeypatch.setenv("TRADE_SAFETY_TELEMETRY_ENABLED", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://example.invalid")

    emitter = telemetry_from_env("0.1.3", REVISION)

    assert emitter.enabled is False
    assert emitter.emit(ASSESSMENT_ACCEPTED, transport="rest") is False


def test_env_factory_enables_only_the_explicit_append_sink(
    monkeypatch, tmp_path
) -> None:
    path = tmp_path / "traction.jsonl"
    monkeypatch.setenv(TELEMETRY_PATH_ENV, os.fspath(path))

    emitter = telemetry_from_env("0.1.3", REVISION)

    assert emitter.enabled is True
    assert emitter.emit(ASSESSMENT_ACCEPTED, transport="rest")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_bytes())["event"] == ASSESSMENT_ACCEPTED


@pytest.mark.parametrize("configured_path", ["", ".", "traction.jsonl"])
def test_env_factory_fails_fast_for_non_absolute_path(
    monkeypatch, configured_path: str
) -> None:
    monkeypatch.setenv(TELEMETRY_PATH_ENV, configured_path)

    with pytest.raises(ValueError, match="absolute"):
        telemetry_from_env("0.1.3", REVISION)


def test_env_factory_fails_fast_when_parent_is_missing(monkeypatch, tmp_path) -> None:
    path = tmp_path / "missing" / "traction.jsonl"
    monkeypatch.setenv(TELEMETRY_PATH_ENV, os.fspath(path))

    with pytest.raises(FileNotFoundError, match="parent directory"):
        telemetry_from_env("0.1.3", REVISION)


def test_env_factory_fails_fast_for_insecure_existing_path(
    monkeypatch, tmp_path
) -> None:
    path = tmp_path / "traction.jsonl"
    path.write_bytes(b"")
    path.chmod(0o644)
    monkeypatch.setenv(TELEMETRY_PATH_ENV, os.fspath(path))

    with pytest.raises(PermissionError, match="permissions"):
        telemetry_from_env("0.1.3", REVISION)


def test_env_factory_fails_fast_for_read_only_existing_path(
    monkeypatch, tmp_path
) -> None:
    path = tmp_path / "traction.jsonl"
    path.write_bytes(b"")
    path.chmod(0o400)
    monkeypatch.setenv(TELEMETRY_PATH_ENV, os.fspath(path))

    with pytest.raises(PermissionError, match="owner-writable"):
        telemetry_from_env("0.1.3", REVISION)


def test_env_factory_fails_fast_for_symlink(monkeypatch, tmp_path) -> None:
    target = tmp_path / "target.jsonl"
    target.write_bytes(b"")
    target.chmod(0o600)
    link = tmp_path / "traction.jsonl"
    link.symlink_to(target)
    monkeypatch.setenv(TELEMETRY_PATH_ENV, os.fspath(link))

    with pytest.raises(PermissionError, match="regular file"):
        telemetry_from_env("0.1.3", REVISION)

    assert target.read_bytes() == b""


def test_emitter_writes_one_minimized_utc_json_record() -> None:
    sink = InMemoryTelemetrySink()

    assert _emitter(sink).emit(
        ASSESSMENT_OUTCOME,
        transport="mcp",
        duration_ms=63.2,
        outcome="hold",
    )

    assert _record(sink) == {
        "duration_bucket": "50_to_249_ms",
        "event": "assessment_outcome",
        "observed_at": "2026-09-04T07:04:56Z",
        "properties": {"outcome": "hold"},
        "schema": TELEMETRY_SCHEMA,
        "service_version": "0.1.3",
        "source_revision": REVISION,
        "transport": "mcp",
    }
    assert len(sink.lines[0]) + 1 <= MAX_TELEMETRY_LINE_BYTES


@pytest.mark.parametrize(
    ("event", "transport", "properties"),
    [
        (ASSESSMENT_ACCEPTED, "rest", {}),
        (ASSESSMENT_REJECTED, "mcp", {"reason": "policy_not_admitted"}),
        (ASSESSMENT_OUTCOME, "x402", {"outcome": "pass"}),
        (
            MCP_ACTIVATION,
            "mcp",
            {"operation": "assess_trade_safety", "outcome": "success"},
        ),
        (X402_OFFERED, "x402", {}),
        (X402_VERIFY_FAILED, "x402", {"reason": "payment_malformed"}),
        (X402_SETTLE_FAILED, "x402", {"reason": "settlement_uncertain"}),
        (X402_SETTLED, "x402", {}),
        (
            X402_RELEASE_FAILED,
            "x402",
            {"delivery": "initial", "reason": "response_expired"},
        ),
        (
            X402_RELEASED,
            "x402",
            {"delivery": "replay", "outcome": "unavailable"},
        ),
    ],
)
def test_every_event_has_an_explicit_stage_schema(
    event: str, transport: str, properties: dict[str, str]
) -> None:
    sink = InMemoryTelemetrySink()

    assert _emitter(sink).emit(event, transport=transport, **properties)
    assert _record(sink)["properties"] == properties


@pytest.mark.parametrize(
    ("event", "reasons"),
    [
        (ASSESSMENT_REJECTED, ASSESSMENT_REJECTION_REASONS),
        (X402_VERIFY_FAILED, X402_VERIFY_FAILURE_REASONS),
        (X402_SETTLE_FAILED, X402_SETTLE_FAILURE_REASONS),
    ],
)
def test_every_published_reason_is_finite_and_emit_ready(
    event: str, reasons: frozenset[str]
) -> None:
    sink = InMemoryTelemetrySink()
    emitter = _emitter(sink)

    for reason in reasons:
        assert emitter.emit(event, transport="x402", reason=reason)

    assert {json.loads(line)["properties"]["reason"] for line in sink.lines} == set(
        reasons
    )


def test_every_release_failure_reason_and_delivery_is_emit_ready() -> None:
    sink = InMemoryTelemetrySink()
    emitter = _emitter(sink)

    for delivery in X402_DELIVERIES:
        for reason in X402_RELEASE_FAILURE_REASONS:
            assert emitter.emit(
                X402_RELEASE_FAILED,
                transport="x402",
                delivery=delivery,
                reason=reason,
            )

    observed = {
        (
            json.loads(line)["properties"]["delivery"],
            json.loads(line)["properties"]["reason"],
        )
        for line in sink.lines
    }
    assert observed == {
        (delivery, reason)
        for delivery in X402_DELIVERIES
        for reason in X402_RELEASE_FAILURE_REASONS
    }


@pytest.mark.parametrize(
    ("duration_ms", "expected"),
    [
        (None, "unknown"),
        (0, "lt_10_ms"),
        (9.999, "lt_10_ms"),
        (10, "10_to_49_ms"),
        (49.999, "10_to_49_ms"),
        (50, "50_to_249_ms"),
        (249.999, "50_to_249_ms"),
        (250, "250_to_999_ms"),
        (999.999, "250_to_999_ms"),
        (1_000, "1_to_4_s"),
        (4_999.999, "1_to_4_s"),
        (5_000, "gte_5_s"),
    ],
)
def test_duration_is_stored_only_as_a_bucket(
    duration_ms: int | float | None, expected: str
) -> None:
    sink = InMemoryTelemetrySink()

    _emitter(sink).emit(ASSESSMENT_ACCEPTED, transport="rest", duration_ms=duration_ms)

    record = _record(sink)
    assert record["duration_bucket"] == expected
    assert "duration_ms" not in record


@pytest.mark.parametrize("duration_ms", [-1, True, float("inf"), float("nan"), "1"])
def test_invalid_durations_are_rejected_without_a_record(duration_ms: object) -> None:
    sink = InMemoryTelemetrySink()

    with pytest.raises(TelemetrySchemaError, match="finite non-negative"):
        _emitter(sink).emit(
            ASSESSMENT_ACCEPTED,
            transport="rest",
            duration_ms=duration_ms,  # type: ignore[arg-type]
        )

    assert sink.lines == []


@pytest.mark.parametrize(
    "forbidden",
    [
        "request",
        "order",
        "account_id",
        "tenant_id",
        "agent_id",
        "ip",
        "wallet",
        "payment",
        "tx_hash",
        "url",
        "evidence",
        "exception",
    ],
)
def test_sensitive_or_unknown_properties_cannot_enter_any_record(
    forbidden: str,
) -> None:
    sink = InMemoryTelemetrySink()

    with pytest.raises(TelemetrySchemaError, match="properties do not match"):
        _emitter(sink).emit(
            ASSESSMENT_ACCEPTED,
            transport="rest",
            **{forbidden: "secret-value"},
        )

    assert sink.lines == []


def test_free_text_cannot_enter_reason_or_outcome() -> None:
    sink = InMemoryTelemetrySink()
    emitter = _emitter(sink)

    with pytest.raises(TelemetrySchemaError, match="property value") as error:
        emitter.emit(
            X402_VERIFY_FAILED,
            transport="x402",
            reason="facilitator said wallet 0xsecret at https://internal.invalid",
        )
    with pytest.raises(TelemetrySchemaError, match="property value"):
        emitter.emit(
            ASSESSMENT_OUTCOME,
            transport="rest",
            outcome="exception: raw evidence follows",
        )

    assert "0xsecret" not in str(error.value)
    assert sink.lines == []


@pytest.mark.parametrize(
    ("event", "transport", "properties"),
    [
        ("assessment_started", "rest", {}),
        (ASSESSMENT_ACCEPTED, "websocket", {}),
        (MCP_ACTIVATION, "rest", {"operation": "initialize", "outcome": "success"}),
        (MCP_ACTIVATION, "x402", {"operation": "initialize", "outcome": "success"}),
        (X402_SETTLED, "mcp", {}),
        (ASSESSMENT_REJECTED, "rest", {}),
        (X402_OFFERED, "x402", {"reason": "payment_missing"}),
        (X402_RELEASED, "x402", {"outcome": "pass"}),
        (
            X402_RELEASE_FAILED,
            "x402",
            {"delivery": "initial", "reason": "facilitator secret"},
        ),
    ],
)
def test_unknown_events_transports_and_stage_properties_are_rejected(
    event: str, transport: str, properties: dict[str, str]
) -> None:
    sink = InMemoryTelemetrySink()

    with pytest.raises(TelemetrySchemaError):
        _emitter(sink).emit(event, transport=transport, **properties)

    assert sink.lines == []


@pytest.mark.parametrize(
    ("service_version", "source_revision"),
    [
        ("latest", REVISION),
        ("0.1.3", "main"),
        ("0.1.3 customer@example.invalid", REVISION),
        ("0.1.3", "b" * 39),
    ],
)
def test_release_identity_is_bounded_and_validated(
    service_version: str, source_revision: str
) -> None:
    with pytest.raises(TelemetrySchemaError):
        TelemetryEmitter(
            service_version=service_version,
            source_revision=source_revision,
        )


def test_sink_failure_cannot_change_the_caller_result() -> None:
    class BrokenSink:
        def write(self, _line: bytes, /) -> None:
            raise OSError("secret path and diagnostics")

    emitter = TelemetryEmitter(
        service_version="0.1.3",
        source_revision=REVISION,
        sink=BrokenSink(),
        clock=lambda: NOW,
    )

    assert emitter.status == {"state": "ready", "delivery_failures": 0}
    assert emitter.emit(ASSESSMENT_ACCEPTED, transport="rest") is False
    assert emitter.status == {"state": "degraded", "delivery_failures": 1}


def test_append_sink_creates_0600_jsonl_and_appends_complete_lines(tmp_path) -> None:
    path = tmp_path / "traction.jsonl"
    sink = AppendOnlyJsonlSink(path)
    emitter = TelemetryEmitter(
        service_version="0.1.3",
        source_revision=REVISION,
        sink=sink,
        clock=lambda: NOW,
    )

    assert emitter.emit(ASSESSMENT_ACCEPTED, transport="rest")
    assert emitter.emit(ASSESSMENT_REJECTED, transport="rest", reason="invalid_request")

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    lines = path.read_bytes().splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["event"] for line in lines] == [
        ASSESSMENT_ACCEPTED,
        ASSESSMENT_REJECTED,
    ]


def test_append_sink_rejects_insecure_existing_file(tmp_path) -> None:
    path = tmp_path / "traction.jsonl"
    path.write_bytes(b"")
    path.chmod(0o644)

    with pytest.raises(PermissionError, match="permissions"):
        AppendOnlyJsonlSink(path)

    assert path.read_bytes() == b""


def test_append_sink_rejects_symlink_destination(tmp_path) -> None:
    if not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("platform has no O_NOFOLLOW")
    target = tmp_path / "target.jsonl"
    target.write_bytes(b"")
    target.chmod(0o600)
    link = tmp_path / "traction.jsonl"
    link.symlink_to(target)

    with pytest.raises(PermissionError, match="regular file"):
        AppendOnlyJsonlSink(link)

    assert target.read_bytes() == b""


def test_append_sink_rejects_fifo_without_blocking(tmp_path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("platform has no FIFO support")
    path = tmp_path / "traction.jsonl"
    os.mkfifo(path, mode=0o600)

    with pytest.raises(PermissionError, match="regular file"):
        AppendOnlyJsonlSink(path)


def test_append_sink_enforces_single_bounded_line(tmp_path) -> None:
    path = tmp_path / "traction.jsonl"
    sink = AppendOnlyJsonlSink(path, max_line_bytes=8)

    with pytest.raises(ValueError, match="one record"):
        sink.write(b"{}\n{}")
    with pytest.raises(ValueError, match="byte bound"):
        sink.write(b"12345678")

    # Configuration probes the destination eagerly so a broken opt-in cannot
    # masquerade as enabled collection before the first request arrives.
    assert path.read_bytes() == b""


def test_provider_sink_is_explicit_and_does_not_reveal_client_identity(
    monkeypatch, capsys
):
    from trade_safety_gateway.telemetry import (
        TELEMETRY_IDENTITY_ENV,
        TELEMETRY_STDOUT_ENV,
    )

    monkeypatch.delenv(TELEMETRY_PATH_ENV, raising=False)
    monkeypatch.setenv(TELEMETRY_STDOUT_ENV, "1")
    monkeypatch.setenv(TELEMETRY_IDENTITY_ENV, "ab" * 32)
    emitter = telemetry_from_env("0.2.0", REVISION)
    client = b"8cd0e592-2eb7-44b8-999f-abfb8cc0fa3e"
    with emitter.request_context(
        [(b"x-liquilens-client-id", client), (b"authorization", b"secret-token")]
    ):
        emitter.emit(ASSESSMENT_OUTCOME, transport="rest", outcome="hold")
    output = capsys.readouterr().out
    record = json.loads(output.removeprefix("TRADE_SAFETY_TRACTION "))
    assert record["schema"] == "liquilens.trade-safety-traction.v2"
    assert len(record["installation_key"]) == 64
    assert record["traffic_class"] == "unattributed"
    assert (
        client.decode() not in output
        and "secret-token" not in output
        and "ab" * 32 not in output
    )
    assert emitter.status == {"state": "ready", "delivery_failures": 0}


@pytest.mark.parametrize("configured", ["", "yes", "0"])
def test_stdout_opt_in_rejects_ambiguous_values(monkeypatch, configured):
    monkeypatch.setenv("TRADE_SAFETY_TELEMETRY_STDOUT", configured)
    with pytest.raises(ValueError):
        telemetry_from_env("0.2.0", REVISION)


def test_provider_sink_rejects_dual_sinks_and_invalid_identity_key(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("TRADE_SAFETY_TELEMETRY_STDOUT", "1")
    monkeypatch.setenv(TELEMETRY_PATH_ENV, str(tmp_path / "events.jsonl"))
    with pytest.raises(ValueError, match="exactly one"):
        telemetry_from_env("0.2.0", REVISION)
    monkeypatch.delenv(TELEMETRY_PATH_ENV)
    monkeypatch.setenv("TRADE_SAFETY_TELEMETRY_IDENTITY_KEY", "secret-invalid")
    with pytest.raises(ValueError) as error:
        telemetry_from_env("0.2.0", REVISION)
    assert "secret-invalid" not in str(error.value)


def test_request_context_is_stable_classified_and_isolated_across_tasks():
    import asyncio

    sink = InMemoryTelemetrySink()
    emitter = TelemetryEmitter(
        service_version="0.2.0",
        source_revision=REVISION,
        sink=sink,
        identity_key=b"k" * 32,
    )
    clients = [
        b"8cd0e592-2eb7-44b8-999f-abfb8cc0fa3e",
        b"93183944-859a-44b6-8ec5-9bb3e4da46df",
    ]

    async def request(client, headers):
        with emitter.request_context([(b"x-liquilens-client-id", client), *headers]):
            await asyncio.sleep(0)
            emitter.emit(ASSESSMENT_ACCEPTED, transport="rest")
            await asyncio.sleep(0)
            emitter.emit(ASSESSMENT_OUTCOME, transport="rest", outcome="hold")

    async def together():
        await asyncio.gather(
            request(clients[0], [(b"x-liquilens-traffic-class", b"synthetic")]),
            request(clients[1], [(b"user-agent", b"MCPBeat/1")]),
        )

    asyncio.run(together())
    records = [json.loads(line) for line in sink.lines]
    groups = {
        kind: [r for r in records if r["traffic_class"] == kind]
        for kind in ["synthetic", "automation"]
    }
    assert all(len(group) == 2 for group in groups.values())
    assert len({r["installation_key"] for r in records}) == 2
    assert len({r["installation_key"] for r in groups["synthetic"]}) == 1
    emitter.emit(ASSESSMENT_ACCEPTED, transport="rest")
    assert json.loads(sink.lines[-1])["installation_key"] is None
    assert json.loads(sink.lines[-1])["traffic_class"] == "unattributed"
    for headers in [
        [(b"x-liquilens-client-id", b"private@example.test")],
        [
            (b"x-liquilens-client-id", clients[0]),
            (b"x-liquilens-client-id", clients[1]),
        ],
    ]:
        with emitter.request_context(headers):
            emitter.emit(ASSESSMENT_ACCEPTED, transport="rest")
        assert json.loads(sink.lines[-1])["installation_key"] is None
