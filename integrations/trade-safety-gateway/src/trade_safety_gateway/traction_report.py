"""Aggregate private gateway logs without treating transports as adoption."""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from .telemetry import (
    ASSESSMENT_OUTCOME,
    MAX_TELEMETRY_LINE_BYTES,
    TELEMETRY_CONTEXT_SCHEMA,
    TelemetryEmitter,
)

PREFIX = "TRADE_SAFETY_TRACTION "
FIELDS = frozenset(
    {
        "schema",
        "event",
        "observed_at",
        "service_version",
        "source_revision",
        "transport",
        "duration_bucket",
        "properties",
        "traffic_class",
        "installation_key",
        "identity_epoch",
        "event_id",
    }
)
BUCKETS = frozenset(
    {
        "unknown",
        "lt_10_ms",
        "10_to_49_ms",
        "50_to_249_ms",
        "250_to_999_ms",
        "1_to_4_s",
        "gte_5_s",
    }
)


def timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ):
        raise ValueError("expected whole-second UTC timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate(record: object) -> dict:
    if not isinstance(record, dict) or set(record) != FIELDS:
        raise ValueError("unexpected telemetry fields")
    if record["schema"] != TELEMETRY_CONTEXT_SCHEMA:
        raise ValueError("report requires v2 telemetry")
    if record["traffic_class"] not in {"unattributed", "synthetic", "automation"}:
        raise ValueError("invalid traffic class")
    if record["duration_bucket"] not in BUCKETS:
        raise ValueError("invalid duration bucket")
    for field, size in (("installation_key", 64), ("identity_epoch", 16)):
        value = record[field]
        if field == "installation_key" and value is None:
            continue
        if not isinstance(value, str) or not re.fullmatch(
            rf"[0-9a-f]{{{size}}}", value
        ):
            raise ValueError("invalid pseudonymous identity")
    event_id = uuid.UUID(record["event_id"])
    if event_id.version != 4 or str(event_id) != record["event_id"]:
        raise ValueError("invalid event identity")
    timestamp(record["observed_at"])
    if not isinstance(record["properties"], dict):
        raise ValueError("invalid properties")
    # Reuse the emitter's closed event/property vocabulary, with no sink.
    TelemetryEmitter(
        service_version=record["service_version"],
        source_revision=record["source_revision"],
    ).emit(record["event"], transport=record["transport"], **record["properties"])
    return record


def parse_lines(lines):
    """Accept raw v2 JSONL or Railway JSONL messages; ignore other log records."""
    for line in lines:
        if len(line.encode("utf-8")) > 16_384:
            raise ValueError("provider log line exceeds its bound")
        line = line.strip()
        if not line:
            continue
        if line.startswith(PREFIX):
            payload = line[len(PREFIX) :]
        else:
            try:
                envelope = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(envelope, dict)
                and envelope.get("schema") == TELEMETRY_CONTEXT_SCHEMA
            ):
                payload = line
            elif (
                isinstance(envelope, dict)
                and isinstance(envelope.get("message"), str)
                and envelope["message"].startswith(PREFIX)
            ):
                payload = envelope["message"][len(PREFIX) :]
            else:
                continue
        if len(payload.encode("utf-8")) + 1 > MAX_TELEMETRY_LINE_BYTES:
            raise ValueError("telemetry record exceeds its bound")
        yield validate(json.loads(payload))


def report(
    records, *, start: datetime, end: datetime, coverage_start: datetime
) -> dict:
    """D1/D7 are first-observed calendar-day cohorts within supplied log coverage."""
    for value in (start, end, coverage_start):
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("report boundaries must be UTC")
    if not coverage_start <= start < end:
        raise ValueError("invalid coverage or report interval")
    unique = {}
    duplicates = 0
    epochs = set()
    for raw in records:
        record = validate(raw)
        seen = timestamp(record["observed_at"])
        if seen < coverage_start or seen >= end:
            continue
        event_id = record["event_id"]
        if event_id in unique:
            if unique[event_id] != record:
                raise ValueError("conflicting duplicate event")
            duplicates += 1
            continue
        unique[event_id] = record
        epochs.add(record["identity_epoch"])
    if len(epochs) > 1:
        raise ValueError("identity key rotation requires separate cohort reports")
    assessments = Counter()
    excluded = Counter()
    operations = Counter()
    days = defaultdict(set)
    active = set()
    anonymous = 0
    for record in unique.values():
        seen = timestamp(record["observed_at"])
        in_window = start <= seen < end
        traffic = record["traffic_class"]
        if traffic != "unattributed":
            if in_window:
                excluded[traffic] += 1
            continue
        if in_window:
            operations[record["event"]] += 1
        if record["event"] != ASSESSMENT_OUTCOME:
            continue
        outcome = record["properties"]["outcome"]
        if in_window:
            assessments[outcome] += 1
        if outcome == "unavailable":
            continue
        installation = record["installation_key"]
        if installation is None:
            anonymous += int(in_window)
            continue
        days[installation].add(seen.date())
        if in_window:
            active.add(installation)
    retention = {}
    for offset in (1, 7):
        eligible = returned = 0
        for observed_days in days.values():
            first = min(observed_days)
            first_midnight = datetime.combine(first, datetime.min.time(), tzinfo=UTC)
            # The first partial coverage day cannot establish a new cohort.
            if not max(start, coverage_start) <= first_midnight < end:
                continue
            target = first + timedelta(days=offset)
            target_end = datetime.combine(
                target + timedelta(days=1), datetime.min.time(), tzinfo=UTC
            )
            if target_end > end:
                continue
            eligible += 1
            returned += int(target in observed_days)
        retention[f"d{offset}"] = {"eligible": eligible, "returned": returned}
    return {
        "schema": "liquilens.trade-safety-scorecard.v1",
        "start": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "coverage_start": coverage_start.isoformat(),
        "coverage": "operator_declared_log_export_not_proven_complete",
        "identity_epoch": next(iter(epochs), None),
        "unattributed_assessment_outcomes": dict(sorted(assessments.items())),
        "useful_assessments": sum(assessments[k] for k in ("pass", "limit", "hold")),
        "useful_assessments_without_installation_id": anonymous,
        "unverified_active_installations": len(active),
        "first_observed_installation_retention": retention,
        "unattributed_event_counts": dict(sorted(operations.items())),
        "excluded_event_counts": dict(sorted(excluded.items())),
        "duplicate_events_removed": duplicates,
        "verified_people": None,
        "verified_payers": None,
        "revenue": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=timestamp)
    parser.add_argument("--end", required=True, type=timestamp)
    parser.add_argument("--coverage-start", required=True, type=timestamp)
    args = parser.parse_args()
    try:
        result = report(
            parse_lines(sys.stdin),
            start=args.start,
            end=args.end,
            coverage_start=args.coverage_start,
        )
    except (ValueError, TypeError, KeyError) as exc:
        # Input may be sensitive; do not echo the rejected value.
        print(
            f"Invalid telemetry export ({type(exc).__name__}); no scorecard emitted",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
