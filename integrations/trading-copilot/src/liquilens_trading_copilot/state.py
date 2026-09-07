"""Private audit state, a single-process lock and durable intent limits."""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .market import InputUnavailable, validate_order_observation


class StateError(RuntimeError):
    pass


def prepare_state(path: Path) -> None:
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise StateError("state_path_must_be_absolute_without_symlinks")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir() or path.stat().st_uid != os.getuid():
        raise StateError("state_directory_ownership_invalid")
    path.chmod(0o700)


@contextmanager
def operator_lock(path: Path) -> Iterator[None]:
    prepare_state(path)
    fd = os.open(path / "operator.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StateError("another_copilot_process_owns_this_state") from exc
        yield
    finally:
        os.close(fd)


class CycleStore:
    def __init__(self, state_dir: Path) -> None:
        prepare_state(state_dir)
        path = state_dir / "audit.sqlite3"
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(path, timeout=5)
        # DELETE journaling keeps the private-directory boundary simple. The
        # submission adapter separately maintains its own durable WAL journal.
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL,
            kind TEXT NOT NULL, record TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS intents (
            intent_key TEXT PRIMARY KEY, day TEXT NOT NULL,
            request_hash TEXT NOT NULL UNIQUE, amount REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS intent_directions (
            intent_key TEXT PRIMARY KEY,
            side TEXT NOT NULL CHECK (side IN ('buy','sell','unknown')));
          CREATE TABLE IF NOT EXISTS order_observations (
            request_hash TEXT PRIMARY KEY, observed_at TEXT NOT NULL,
            status TEXT NOT NULL, terminal INTEGER NOT NULL CHECK (terminal IN (0,1)),
            record TEXT NOT NULL);
        """)
        self.db.commit()

    def event(self, kind: str, record: Mapping[str, Any], now: datetime) -> None:
        body = json.dumps(
            record, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        if len(body.encode()) > 524288:
            raise StateError("audit_record_too_large")
        with self.db:
            self.db.execute(
                "INSERT INTO events(observed_at,kind,record) VALUES (?,?,?)",
                (now.isoformat(), kind, body),
            )

    def reserve(
        self,
        *,
        intent_key: str,
        request_hash: str,
        amount: float,
        now: datetime,
        max_daily_attempts: int,
        side: str = "unknown",
        reserved_daily_exit_attempts: int = 1,
    ) -> bool:
        day = self._budget_day(now, max_daily_attempts, reserved_daily_exit_attempts)
        if side not in {"buy", "sell", "unknown"}:
            raise StateError("invalid_intent_direction")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            budget = self._daily_budget(
                day, max_daily_attempts, reserved_daily_exit_attempts
            )
            if budget["remaining_total"] == 0 or (
                side != "sell" and budget["remaining_entries"] == 0
            ):
                self.db.rollback()
                return False
            self.db.execute(
                "INSERT INTO intents VALUES (?,?,?,?)",
                (intent_key, day, request_hash, amount),
            )
            self.db.execute(
                "INSERT INTO intent_directions(intent_key,side) VALUES (?,?)",
                (intent_key, side),
            )
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            self.db.rollback()
            return False
        except BaseException:
            self.db.rollback()
            raise

    @staticmethod
    def _budget_day(now: datetime, maximum: int, reserved: int) -> str:
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise StateError("aware_reservation_clock_required")
        if (
            type(maximum) is not int
            or maximum < 1
            or type(reserved) is not int
            or not 0 <= reserved <= maximum
        ):
            raise StateError("invalid_reservation_budget")
        return now.astimezone(UTC).date().isoformat()

    def _daily_budget(self, day: str, maximum: int, reserved: int) -> dict[str, Any]:
        # Missing sidecar rows are old reservations, not free capacity. Charge
        # every unknown direction against entry capacity; preserve old intents.
        total, entries = self.db.execute(
            """SELECT count(*), coalesce(sum(CASE
                 WHEN coalesce(d.side,'unknown') != 'sell' THEN 1 ELSE 0 END),0)
               FROM intents i LEFT JOIN intent_directions d USING(intent_key)
               WHERE i.day=?""",
            (day,),
        ).fetchone()
        remaining = max(0, maximum - total)
        return {
            "utc_day": day,
            "maximum_total": maximum,
            "reserved_exit_attempts": reserved,
            "used_total": total,
            "used_entries_or_unknown": entries,
            "remaining_total": remaining,
            "remaining_entries": min(remaining, max(0, maximum - reserved - entries)),
        }

    def daily_budget(
        self,
        *,
        now: datetime,
        max_daily_attempts: int,
        reserved_daily_exit_attempts: int = 1,
    ) -> dict[str, Any]:
        day = self._budget_day(now, max_daily_attempts, reserved_daily_exit_attempts)
        return self._daily_budget(day, max_daily_attempts, reserved_daily_exit_attempts)

    def pending_intents(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        """Known intents lacking a terminal broker observation, oldest first.

        This includes intents whose broker submission may never have occurred.
        A failed lookup must stay unresolved; callers may not silently remove it.
        """
        if type(limit) is not int or not 1 <= limit <= 100:
            raise StateError("pending_intent_limit_invalid")
        rows = self.db.execute(
            """SELECT i.intent_key,i.day,i.request_hash,i.amount
               FROM intents AS i LEFT JOIN order_observations AS o
               ON i.request_hash=o.request_hash
               WHERE o.request_hash IS NULL OR o.terminal=0
               ORDER BY i.day,i.rowid LIMIT ?""",
            (limit,),
        ).fetchall()
        return tuple(
            {
                "intent_key": row[0],
                "day": row[1],
                "request_hash": row[2],
                "amount": row[3],
            }
            for row in rows
        )

    def order_observation(
        self, *, request_hash: str, observation: Mapping[str, Any], now: datetime
    ) -> None:
        """Persist a read-only outcome without changing attempts or receipts.

        Terminal state and cumulative fills cannot regress. The original
        account/strategy/bar intent remains reserved even after cancellation.
        """
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise StateError("order_observation_clock_invalid")
        try:
            validated = validate_order_observation(request_hash, dict(observation))
        except (InputUnavailable, TypeError, ValueError) as error:
            raise StateError("order_observation_invalid") from error
        encoded = json.dumps(validated, sort_keys=True, separators=(",", ":"))
        self.db.execute("BEGIN IMMEDIATE")
        try:
            intent = self.db.execute(
                "SELECT intent_key FROM intents WHERE request_hash=?", (request_hash,)
            ).fetchone()
            if intent is None:
                raise StateError("order_observation_intent_unknown")
            if intent[0].split("|", 1)[0] != validated["account_id"]:
                raise StateError("order_observation_account_mismatch")
            previous = self.db.execute(
                "SELECT observed_at,record FROM order_observations "
                "WHERE request_hash=?",
                (request_hash,),
            ).fetchone()
            if previous is not None:
                old = validate_order_observation(request_hash, json.loads(previous[1]))
                old_at = datetime.fromisoformat(previous[0])
                if now < old_at:
                    raise StateError("order_observation_clock_regressed")
                if old["broker_order_id"] != validated["broker_order_id"]:
                    raise StateError("order_observation_broker_identity_changed")
                if (
                    old["side"] != validated["side"]
                    or old["symbol"] != validated["symbol"]
                ):
                    raise StateError("order_observation_order_semantics_changed")
                if old["terminal"] and old != validated:
                    raise StateError("order_observation_terminal_state_changed")
                if Decimal(validated["filled_qty"]) < Decimal(old["filled_qty"]):
                    raise StateError("order_observation_fill_regressed")
            self.db.execute(
                """INSERT INTO order_observations VALUES (?,?,?,?,?)
                   ON CONFLICT(request_hash) DO UPDATE SET
                   observed_at=excluded.observed_at,status=excluded.status,
                   terminal=excluded.terminal,record=excluded.record""",
                (
                    request_hash,
                    now.isoformat(),
                    validated["status"],
                    int(validated["terminal"]),
                    encoded,
                ),
            )
            self.db.execute(
                "INSERT INTO events(observed_at,kind,record) VALUES (?,?,?)",
                (now.isoformat(), "order_observation", encoded),
            )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def status(self) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT observed_at,kind,record FROM events ORDER BY id DESC LIMIT 1",
        ).fetchone()
        return {
            "intent_count": self.db.execute("SELECT count(*) FROM intents").fetchone()[
                0
            ],
            "event_count": self.db.execute("SELECT count(*) FROM events").fetchone()[0],
            "order_observation_count": self.db.execute(
                "SELECT count(*) FROM order_observations"
            ).fetchone()[0],
            "filled_order_count": self.db.execute(
                "SELECT count(*) FROM order_observations WHERE status='filled'"
            ).fetchone()[0],
            "pending_intent_count": self.db.execute(
                """SELECT count(*) FROM intents AS i
                   LEFT JOIN order_observations AS o ON i.request_hash=o.request_hash
                   WHERE o.request_hash IS NULL OR o.terminal=0"""
            ).fetchone()[0],
            "latest": None
            if row is None
            else {
                "observed_at": row[0],
                "kind": row[1],
                "record": json.loads(row[2]),
            },
        }

    def close(self) -> None:
        self.db.close()
