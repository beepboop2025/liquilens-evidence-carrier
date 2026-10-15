"""Private, consent-bound decision/outcome snapshots for the paper agent host.

Only host-owned receipts and journals supply facts. Export never contacts a
broker, accepts an agent's claimed fill, or grants permission to execute.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from liquilens_evidence import trade_safety_request_hash, verify_trade_safety_receipt

from .config import strict_json
from .market import validate_order_observation

SCHEMA = "liquilens.agent-outcome-snapshot.v1"
GRANT_SCHEMA = "liquilens.outcome-consent.v1"
PAGE_SCHEMA = "liquilens.outcome-export.v1"
PURPOSE = "execution_quality_review"
_BINDING = ("tenant_id", "operator_id", "account_id", "agent_id")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}")
_HASH = re.compile(r"[0-9a-f]{64}")
_MAX_SEQUENCE = 2**63 - 1


class OutcomeError(RuntimeError):
    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code, self.status = code, status


def encoded(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def instant(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("invalid_time")
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("aware_time_required")
    return result.astimezone(UTC)


def utc(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("aware_time_required")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def initialize(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS agent_outcome_snapshots (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            assessment_id TEXT NOT NULL,
            snapshot_hash TEXT NOT NULL UNIQUE,
            assessed_at TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            record TEXT NOT NULL);
    """)


def _reference(key: bytes, kind: str, value: Any) -> str:
    return hmac.new(
        key,
        b"liquilens-outcome-reference-v1\0"
        + kind.encode()
        + b"\0"
        + encoded(value).encode(),
        hashlib.sha256,
    ).hexdigest()


def capture(service: Any, assessment_id: str) -> None:
    """Retain a changed snapshot; repeated status/export calls add no activity.

    Called on the service's serialized lane, including after a refused submit.
    A later status/reconcile repairs a snapshot missed by a process interruption.
    Historical receipt verification uses issuance time, never renews validity.
    """
    row = service.store.db.execute(
        "SELECT request,receipt FROM agent_assessments WHERE assessment_id=?",
        (assessment_id,),
    ).fetchone()
    if row is None:
        return
    request, receipt = (strict_json(item.encode()) for item in row)
    verify_trade_safety_receipt(
        receipt, evaluated_at=instant(receipt["evaluated_at"]), hmac_key=service.key
    )
    binding = asdict(service.binding)
    if (
        trade_safety_request_hash(request) != assessment_id
        or receipt["request"] != request
        or request["mode"] != "paper"
        or any(
            request["agent"][name] != binding[name]
            for name in (*_BINDING, "strategy_id", "runtime")
        )
        or receipt["policy_hash"] != binding["policy_hash"]
    ):
        raise OutcomeError("outcome_assessment_binding_invalid", 503)
    record = {
        "schema": SCHEMA,
        "mode": "paper",
        "assessment_id": assessment_id,
        "identity": {
            name.removesuffix("_id") + "_ref": _reference(
                service.key, name, [binding["tenant_id"], binding[name]]
            )
            for name in (*_BINDING, "strategy_id")
        },
        "attribution": "operator_bound_agent_identity",
        "ai_authorship_verified": False,
        "assessed_at": utc(instant(receipt["evaluated_at"])),
        "order": {
            "symbol": request["order"]["instrument"]["symbol"],
            "side": request["order"]["side"],
            "order_type": request["order"]["order_type"],
            "notional": request["order"]["notional"],
            "time_in_force": request["order"]["time_in_force"],
        },
        "assessment": {
            "receipt_id": receipt["receipt_id"],
            "record_hash": receipt["record_hash"],
            "policy_hash": receipt["policy_hash"],
            "expires_at": receipt["expires_at"],
            "source_policy_outcome": receipt["decision"]["outcome"],
            "evidence": {
                product: {
                    name: section[name]
                    for name in (
                        "state",
                        "source_sha256",
                        "as_of",
                        "knowledge_time",
                        "retrieved_at",
                        "expires_at",
                        "rights_status",
                        "real_money_eligible",
                        "executable_quote",
                    )
                }
                for product, section in receipt["evidence"].items()
            },
        },
        "submission": None,
        "broker_observation": None,
        "execution_authorized": False,
    }
    submission = service.journal.get(assessment_id)
    if submission is not None:
        if submission.receipt_id != receipt["receipt_id"]:
            raise OutcomeError("outcome_submission_binding_invalid", 503)
        record["submission"] = {
            "state": str(submission.state),
            "attempts": submission.submit_attempts,
            "broker_acknowledged": submission.broker_order_id is not None,
            "reconciliation_resolution": submission.reconciliation_resolution,
            "updated_at": submission.updated_at,
        }
    previous = service.store.db.execute(
        "SELECT observed_at,record FROM order_observations WHERE request_hash=?",
        (assessment_id,),
    ).fetchone()
    if previous is not None:
        observation = validate_order_observation(
            assessment_id, strict_json(previous[1].encode())
        )
        if (
            submission is None
            or observation["account_id"] != binding["account_id"]
            or observation["side"] != request["order"]["side"]
            or observation["symbol"] != request["order"]["instrument"]["symbol"]
            or observation["broker_order_id"] != submission.broker_order_id
        ):
            raise OutcomeError("outcome_broker_binding_invalid", 503)
        record["broker_observation"] = {
            name: observation[name]
            for name in ("status", "terminal", "filled_qty", "filled_avg_price")
        } | {
            "observed_at": utc(instant(previous[0])),
            "scope": "last_observed_cumulative_snapshot",
            "broker_event_time": None,
        }
    body = encoded(record)
    if len(body.encode()) > 16000:
        raise OutcomeError("outcome_record_too_large", 503)
    with service.store.db:
        service.store.db.execute(
            "INSERT OR IGNORE INTO agent_outcome_snapshots "
            "(assessment_id,snapshot_hash,assessed_at,recorded_at,record) "
            "VALUES(?,?,?,?,?)",
            (
                assessment_id,
                digest(record),
                record["assessed_at"],
                utc(service.now()),
                body,
            ),
        )


def authorize(grant: Any, binding: Any, token_hash: str, now: datetime) -> dict:
    """The customer provisions this file, not an agent or an HTTP request."""
    try:
        if (
            not isinstance(grant, dict)
            or set(grant)
            != {
                "schema",
                "consent_id",
                "enabled",
                "binding",
                "recipient_id",
                "token_sha256",
                "purpose",
                "not_before",
                "expires_at",
                "records_from",
                "records_until",
                "traffic_class",
                "allow_training",
                "allow_cross_customer_aggregation",
            }
            or grant["schema"] != GRANT_SCHEMA
            or grant["enabled"] is not True
            or grant["purpose"] != PURPOSE
            or grant["allow_training"] is not False
            or grant["allow_cross_customer_aggregation"] is not False
            or grant["traffic_class"]
            not in {"operator", "synthetic", "customer", "unknown"}
            or grant["binding"] != {name: getattr(binding, name) for name in _BINDING}
            or any(
                not isinstance(grant[name], str) or _ID.fullmatch(grant[name]) is None
                for name in ("consent_id", "recipient_id")
            )
            or not isinstance(grant["token_sha256"], str)
            or _HASH.fullmatch(grant["token_sha256"]) is None
            or not hmac.compare_digest(grant["token_sha256"], token_hash)
        ):
            raise ValueError
        start, end, records_from, records_until = (
            instant(grant[name])
            for name in ("not_before", "expires_at", "records_from", "records_until")
        )
        if not (start <= now < end and start <= records_from < records_until <= end):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise OutcomeError("outcome_consent_not_active_for_identity", 403) from None
    return grant


def export_page(service: Any, grant: dict, payload: Any, token_hash: str) -> dict:
    grant = authorize(grant, service.binding, token_hash, service.now())
    if (
        not isinstance(payload, dict)
        or set(payload) != {"consent_id", "after", "through", "limit"}
        or payload["consent_id"] != grant["consent_id"]
        or type(payload["after"]) is not int
        or not 0 <= payload["after"] <= _MAX_SEQUENCE
        or type(payload["limit"]) is not int
        or not 1 <= payload["limit"] <= 25
        or (
            payload["through"] is not None
            and (
                type(payload["through"]) is not int
                or not payload["after"] <= payload["through"] <= _MAX_SEQUENCE
            )
        )
    ):
        raise OutcomeError("invalid_outcome_page_request", 422)
    high = service.store.db.execute(
        "SELECT COALESCE(MAX(sequence),0) FROM agent_outcome_snapshots"
    ).fetchone()[0]
    through = high if payload["through"] is None else payload["through"]
    if not payload["after"] <= through <= high:
        raise OutcomeError("outcome_cursor_unavailable", 409)
    rows = service.store.db.execute(
        "SELECT sequence,snapshot_hash,recorded_at,record "
        "FROM agent_outcome_snapshots WHERE sequence>? AND sequence<=? "
        "AND assessed_at>=? AND recorded_at>=? AND recorded_at<? "
        "ORDER BY sequence LIMIT ?",
        (
            payload["after"],
            through,
            utc(instant(grant["records_from"])),
            utc(instant(grant["records_from"])),
            utc(instant(grant["records_until"])),
            payload["limit"] + 1,
        ),
    ).fetchall()
    records = []
    for sequence, checksum, recorded_at, body in rows[: payload["limit"]]:
        if not isinstance(body, str) or len(body.encode()) > 16000:
            raise OutcomeError("outcome_record_integrity_failed", 503)
        value = strict_json(body.encode())
        if (
            digest(value) != checksum
            or value["schema"] != SCHEMA
            or value["mode"] != "paper"
        ):
            raise OutcomeError("outcome_record_integrity_failed", 503)
        records.append(
            {
                "sequence": sequence,
                "snapshot_hash": checksum,
                "recorded_at": recorded_at,
                "snapshot": value,
            }
        )
    more = len(rows) > payload["limit"]
    return {
        "schema": PAGE_SCHEMA,
        "mode": "paper",
        "consent": {
            name: grant[name]
            for name in (
                "consent_id",
                "recipient_id",
                "purpose",
                "expires_at",
                "records_from",
                "records_until",
                "traffic_class",
                "allow_training",
                "allow_cross_customer_aggregation",
            )
        }
        | {"grant_hash": digest(grant)},
        "after": payload["after"],
        "through": through,
        "next_after": records[-1]["sequence"] if more else through,
        "has_more": more,
        "records": records,
        "coverage_complete": False,
        "coverage": "snapshots_captured_by_this_host_within_consent_window",
        "broker_contacted": False,
        "execution_authorized": False,
        "independent_customer_verified": False,
    }


def summarize_pages(pages: list[dict]) -> dict:
    """Validate a complete export from cursor zero, deduplicate retry pages.

    Count latest cumulative observations once per assessment. These are local
    paper workflow counts, never inferred customers, turnover, PnL or retention.
    """
    if not pages:
        raise OutcomeError("outcome_pages_required", 422)
    latest: dict[str, dict] = {}
    accepted: dict[int, str] = {}
    after = 0
    first = pages[0]
    done = False
    try:
        for page in pages:
            if (
                page["schema"] != PAGE_SCHEMA
                or page["mode"] != "paper"
                or page["consent"] != first["consent"]
                or page["through"] != first["through"]
                or type(page["after"]) is not int
                or type(page["through"]) is not int
                or type(page["next_after"]) is not int
                or not 0 <= page["after"] <= page["next_after"] <= page["through"]
                or page["consent"]["purpose"] != PURPOSE
                or page["consent"]["allow_training"] is not False
                or page["consent"]["allow_cross_customer_aggregation"] is not False
                or not isinstance(page["records"], list)
                or len(page["records"]) > 25
            ):
                raise ValueError
            fingerprint = digest(page)
            if page["after"] in accepted:
                if accepted[page["after"]] != fingerprint:
                    raise ValueError
                continue
            if done or page["after"] != after:
                raise ValueError
            previous = after
            for item in page["records"]:
                record = item["snapshot"]
                if (
                    type(item["sequence"]) is not int
                    or not previous < item["sequence"] <= page["through"]
                    or record["schema"] != SCHEMA
                    or record["mode"] != "paper"
                    or digest(record) != item["snapshot_hash"]
                ):
                    raise ValueError
                previous = item["sequence"]
                latest[record["assessment_id"]] = record
            after = page["next_after"]
            if (
                type(page["has_more"]) is not bool
                or (page["has_more"] and (not page["records"] or after != previous))
                or (not page["has_more"] and after != page["through"])
            ):
                raise ValueError
            accepted[page["after"]] = fingerprint
            done = not page["has_more"]
        if not done:
            raise ValueError
        counts = dict.fromkeys(
            (
                "assessed",
                "source_policy_passed",
                "source_policy_not_passed",
                "submission_attempted",
                "broker_acknowledged",
                "fill_observed",
                "fully_filled",
                "partial_fill_observed",
                "terminal_without_fill",
                "no_broker_observation",
            ),
            0,
        )
        for record in latest.values():
            counts["assessed"] += 1
            counts[
                "source_policy_passed"
                if record["assessment"]["source_policy_outcome"] == "pass"
                else "source_policy_not_passed"
            ] += 1
            submitted, observation = record["submission"], record["broker_observation"]
            if submitted:
                counts["submission_attempted"] += int(submitted["attempts"] > 0)
                counts["broker_acknowledged"] += int(submitted["broker_acknowledged"])
            if observation is None:
                counts["no_broker_observation"] += 1
            else:
                filled = Decimal(observation["filled_qty"]) > 0
                counts["fill_observed"] += int(filled)
                counts["fully_filled"] += int(observation["status"] == "filled")
                counts["partial_fill_observed"] += int(
                    filled and observation["status"] != "filled"
                )
                counts["terminal_without_fill"] += int(
                    observation["terminal"] and not filled
                )
    except (ValueError, TypeError, KeyError, ArithmeticError):
        raise OutcomeError("outcome_pages_invalid_or_incomplete", 422) from None
    return {
        "schema": "liquilens.outcome-summary.v1",
        "mode": "paper",
        "consent": first["consent"],
        "through": first["through"],
        "counts": counts,
        "export_pages_complete": True,
        "coverage_complete": False,
        "independent_customers": None,
        "ai_authorship_verified": False,
        "live_trades": None,
        "turnover_usd": None,
        "pnl_usd": None,
        "authority": "customer_host_report_not_independent_broker_attestation",
    }


def main() -> int:
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(
        description="Summarize saved consented paper outcome pages offline."
    )
    parser.add_argument("pages", nargs="+", type=Path)
    args = parser.parse_args()
    try:
        pages = []
        for path in args.pages:
            with path.open("rb") as handle:
                raw = handle.read(524289)
            if len(raw) > 524288 or len(pages) >= 10000:
                raise ValueError
            page = strict_json(raw)
            # The reference REST client's CLI wraps successful pages.
            if isinstance(page, dict) and set(page) == {"http_status", "result"}:
                if page["http_status"] != 200:
                    raise ValueError
                page = page["result"]
            pages.append(page)
        print(encoded(summarize_pages(pages)))
        return 0
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OutcomeError):
        print(
            encoded({"error": "outcome_pages_invalid_or_incomplete", "mode": "paper"})
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
