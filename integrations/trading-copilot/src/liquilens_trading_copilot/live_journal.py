"""Bounded, credential-independent inspection of an existing live journal.

No directory, lock, database, recovery file or network client is created here.
A shared existing operator lock makes immutable SQLite reads consistent with the
writer. A leftover recovery file requires operator recovery, never silent repair.
"""

from __future__ import annotations

import fcntl
import os
import re
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import strict_json
from .live_connector import ORDER_STATES, TERMINAL, LiveExecutionBlocked, number

_HASH = re.compile(r"[0-9a-f]{64}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_COLUMNS = {
    "identity": {"id", "binding"},
    "orders": {
        "request_hash",
        "request_id",
        "receipt_id",
        "day",
        "attempted_at",
        "state",
        "request",
        "observation",
        "cancel_requested",
    },
}
_SELECT = "request_hash,state,observation,cancel_requested"


def private_directory(path: Path) -> None:
    """Validate existing state without chmod, mkdir or symlink traversal."""
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise LiveExecutionBlocked("local_state_path_invalid")
    try:
        info = path.stat()
    except OSError:
        raise LiveExecutionBlocked("local_state_unavailable") from None
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise LiveExecutionBlocked("local_state_not_private")


def _private_fd(path: Path) -> int:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise LiveExecutionBlocked("local_journal_unavailable") from None
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        os.close(fd)
        raise LiveExecutionBlocked("local_journal_not_private")
    return fd


@contextmanager
def _reader(state_dir: Path) -> Iterator[sqlite3.Connection]:
    private_directory(state_dir)
    lock = _private_fd(state_dir / "operator.lock")
    db = None
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LiveExecutionBlocked("local_journal_busy") from None
        path = state_dir / "live-orders.sqlite3"
        fd = _private_fd(path)
        os.close(fd)
        if any(
            candidate.exists() or candidate.is_symlink()
            for suffix in ("-journal", "-wal", "-shm")
            for candidate in (Path(str(path) + suffix),)
        ):
            raise LiveExecutionBlocked("local_journal_recovery_required")
        db = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        # Keep malformed/unexpected databases from causing unbounded SQL work.
        remaining = [200]

        def progress():
            remaining[0] -= 1
            return remaining[0] < 0

        db.set_progress_handler(progress, 1000)
        for table, columns in _COLUMNS.items():
            definition = db.execute(
                "SELECT type FROM sqlite_schema WHERE name=?", (table,)
            ).fetchone()
            if not definition or definition[0] != "table":
                raise LiveExecutionBlocked("local_journal_schema_unsupported")
            if {row[1] for row in db.execute(f"PRAGMA table_info({table})")} != columns:
                raise LiveExecutionBlocked("local_journal_schema_unsupported")
        yield db
    except sqlite3.Error:
        raise LiveExecutionBlocked("local_journal_invalid") from None
    finally:
        if db is not None:
            db.close()
        os.close(lock)


def _counts(db: sqlite3.Connection) -> dict[str, int]:
    counts = {"uncertain": 0, "pending": 0, "terminal": 0}
    for state, count in db.execute("SELECT state,COUNT(*) FROM orders GROUP BY state"):
        if state not in counts:
            raise LiveExecutionBlocked("local_journal_record_invalid")
        counts[state] = count
    if sum(counts.values()) > 10000:
        raise LiveExecutionBlocked("local_journal_capacity_exceeded")
    return counts


def _record(row: sqlite3.Row) -> dict[str, Any]:
    digest = row["request_hash"]
    if (
        not isinstance(digest, str)
        or _HASH.fullmatch(digest) is None
        or row["state"] not in {"uncertain", "pending", "terminal"}
        or row["cancel_requested"] not in {0, 1}
    ):
        raise LiveExecutionBlocked("local_journal_record_invalid")
    observed = None
    if row["observation"] is not None:
        try:
            raw = row["observation"]
            if not isinstance(raw, str) or len(raw) > 8192:
                raise ValueError
            value = strict_json(raw.encode())
            status = value["status"]
            qty = number(value["filled_qty"])
            price = value["filled_avg_price"]
            observed_at = value["broker_observed_at"]
            if (
                status not in ORDER_STATES
                or _UUID.fullmatch(value["broker_order_id"]) is None
                or value["client_order_id"] != "ll-live-" + digest[:40]
                or type(value["terminal"]) is not bool
                or value["terminal"] != (status in TERMINAL)
                or (row["state"] == "terminal") != value["terminal"]
                or qty < 0
                or len(str(qty)) > 64
                or (price is not None and (number(price) <= 0 or len(str(price)) > 64))
                or (qty > 0 and price is None)
                or not isinstance(observed_at, str)
                or len(observed_at) > 64
                or datetime.fromisoformat(observed_at).tzinfo is None
            ):
                raise ValueError
            # Allowlist metadata; never echo arbitrary persisted provider fields.
            observed = {
                "broker_order_id": value["broker_order_id"],
                "client_order_id": value["client_order_id"],
                "status": status,
                "terminal": value["terminal"],
                "filled_qty": str(qty),
                "filled_avg_price": str(number(price)) if price is not None else None,
                "broker_observed_at": observed_at,
                "observation_scope": "last_observed",
            }
        except Exception:
            raise LiveExecutionBlocked("local_journal_record_invalid") from None
    elif row["state"] != "uncertain":
        raise LiveExecutionBlocked("local_journal_record_invalid")
    return {
        "mode": "live",
        "request_hash": digest,
        "state": row["state"],
        "client_order_id": "ll-live-" + digest[:40],
        "observation": observed,
        "cancel_requested": bool(row["cancel_requested"]),
        "resubmit_allowed": False,
    }


def local_status(state_dir: Path, digest: str) -> dict[str, Any]:
    if not isinstance(digest, str) or _HASH.fullmatch(digest) is None:
        raise LiveExecutionBlocked("invalid_request_hash")
    with _reader(state_dir) as db:
        _counts(db)
        row = db.execute(
            f"SELECT {_SELECT} FROM orders WHERE request_hash=?", (digest,)
        ).fetchone()
        if row is None:
            raise LiveExecutionBlocked("unknown_order")
        return _record(row)


def local_orders(
    state_dir: Path,
    *,
    limit: int = 50,
    unresolved_only: bool = False,
    after: str | None = None,
) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise LiveExecutionBlocked("invalid_journal_limit")
    if type(unresolved_only) is not bool or (
        after is not None
        and (not isinstance(after, str) or _HASH.fullmatch(after) is None)
    ):
        raise LiveExecutionBlocked("invalid_journal_filter")
    with _reader(state_dir) as db:
        counts = _counts(db)
        rows = db.execute(
            f"SELECT {_SELECT} FROM orders WHERE request_hash>? "
            "AND (?=0 OR state!='terminal') ORDER BY request_hash LIMIT ?",
            (after or "", int(unresolved_only), limit + 1),
        ).fetchall()
        records = [_record(row) for row in rows[:limit]]
        return {
            "schema": "liquilens.customer-live-journal.v1",
            "mode": "live",
            "scope": "local_last_observed_metadata",
            "network_accessed": False,
            "broker_connected": False,
            "order_submitted": False,
            "resubmit_allowed": False,
            "counts": counts,
            "unresolved_count": counts["uncertain"] + counts["pending"],
            "orders": records,
            "truncated": len(rows) > limit,
            "next_after": records[-1]["request_hash"] if len(rows) > limit else None,
        }
