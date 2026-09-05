from __future__ import annotations

import json
import uuid

import pytest

from trade_safety_gateway.telemetry import InMemoryTelemetrySink, TelemetryEmitter
from trade_safety_gateway.traction_report import PREFIX, parse_lines, report, timestamp


def event(
    day,
    *,
    client="first",
    outcome="pass",
    traffic="unattributed",
    name="assessment_outcome",
):
    sink = InMemoryTelemetrySink()
    emitter = TelemetryEmitter(
        service_version="0.2.0",
        source_revision="a" * 40,
        sink=sink,
        clock=lambda: timestamp(f"2026-09-{day:02d}T12:00:00Z"),
        identity_key=b"a" * 32,
    )
    props = (
        {"outcome": outcome}
        if name == "assessment_outcome"
        else {"operation": "tools_list", "outcome": "success"}
    )
    emitter.emit(name, transport="mcp", **props)
    value = json.loads(sink.lines[0])
    value["installation_key"] = (
        None if client is None else ("1" if client == "first" else "2") * 64
    )
    value["traffic_class"] = traffic
    return value


def score(
    rows,
    *,
    start="2026-09-01T00:00:00Z",
    end="2026-09-10T00:00:00Z",
    coverage="2026-09-01T00:00:00Z",
):
    return report(
        rows,
        start=timestamp(start),
        end=timestamp(end),
        coverage_start=timestamp(coverage),
    )


def test_assessments_not_discovery_or_probes_drive_activation_and_retention():
    rows = [
        event(1),
        event(2),
        event(8),
        event(1, client="second"),
        event(2, client=None, outcome="hold"),
        event(2, outcome="unavailable"),
        event(3, traffic="synthetic"),
        event(3, traffic="automation"),
        event(3, name="mcp_activation"),
    ]
    value = score([*rows, rows[0]])
    assert value["useful_assessments"] == 5
    assert value["unverified_active_installations"] == 2
    assert value["useful_assessments_without_installation_id"] == 1
    assert value["first_observed_installation_retention"] == {
        "d1": {"eligible": 2, "returned": 1},
        "d7": {"eligible": 2, "returned": 1},
    }
    assert value["duplicate_events_removed"] == 1
    assert value["excluded_event_counts"] == {"automation": 1, "synthetic": 1}
    assert value["verified_people"] is None
    assert value["verified_payers"] is None
    assert value["revenue"] is None


def test_immature_or_partial_day_cohorts_do_not_become_zero_retention():
    value = score([event(1), event(2)], end="2026-09-02T13:00:00Z")
    assert value["first_observed_installation_retention"]["d1"] == {
        "eligible": 0,
        "returned": 0,
    }
    value = score(
        [event(1), event(2)],
        coverage="2026-09-01T10:00:00Z",
        start="2026-09-01T10:00:00Z",
    )
    assert value["first_observed_installation_retention"]["d1"]["eligible"] == 0


def test_prior_observed_installation_is_not_a_new_cohort():
    value = score([event(1), event(3), event(4)], start="2026-09-03T00:00:00Z")
    assert value["unverified_active_installations"] == 1
    assert value["first_observed_installation_retention"]["d1"]["eligible"] == 0


def test_conflicting_duplicates_and_mixed_key_epochs_refuse_a_scorecard():
    one = event(1)
    conflict = dict(one, properties={"outcome": "hold"})
    with pytest.raises(ValueError, match="conflicting"):
        score([one, conflict])
    other = dict(event(2), identity_epoch="b" * 16)
    with pytest.raises(ValueError, match="rotation"):
        score([one, other])


def test_provider_wrapper_and_direct_jsonl_produce_same_record():
    row = event(1)
    encoded = json.dumps(row)
    result = list(
        parse_lines(
            [
                "Starting container\n",
                PREFIX + encoded,
                json.dumps({"message": PREFIX + encoded}),
                encoded,
            ]
        )
    )
    assert result == [row, row, row]
    with pytest.raises(ValueError):
        list(parse_lines([PREFIX + '{"broken":1}']))


@pytest.mark.parametrize(
    "patch",
    [
        {"request": "sensitive"},
        {"properties": {"outcome": "secret"}},
        {"installation_key": "raw-client"},
        {"event_id": str(uuid.uuid1())},
        {"traffic_class": "verified_human"},
        {"observed_at": "2026-09-01"},
    ],
)
def test_unknown_fields_and_unsafe_values_fail_closed(patch):
    with pytest.raises((ValueError, TypeError)):
        score([dict(event(1), **patch)])


def test_asgi_envelope_scopes_and_clears_installation_context():
    from fastapi.testclient import TestClient

    from trade_safety_gateway.app import create_app

    sink = InMemoryTelemetrySink()
    emitter = TelemetryEmitter(
        service_version="0.2.0",
        source_revision="a" * 40,
        identity_key=b"a" * 32,
        sink=sink,
    )
    client_id = str(uuid.uuid4())
    with TestClient(create_app(telemetry=emitter)) as client:
        response = client.post(
            "/v1/check",
            content=b"not-json",
            headers={
                "content-type": "application/json",
                "X-Liquilens-Client-Id": client_id,
                "X-Liquilens-Traffic-Class": "synthetic",
            },
        )
        assert response.status_code >= 400
    records = [json.loads(line) for line in sink.lines]
    assert records and all(r["traffic_class"] == "synthetic" for r in records)
    assert all(r["installation_key"] is not None for r in records)
    assert client_id not in b"".join(sink.lines).decode()
    emitter.emit("assessment_accepted", transport="rest")
    assert json.loads(sink.lines[-1])["installation_key"] is None
