"""Durable, fail-closed submission state for the Alpaca paper adapter."""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Protocol, cast

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TABLE = "alpaca_paper_submission_journal_v1"
_META_TABLE = "alpaca_paper_submission_journal_metadata_v1"
_EXPECTED_COLUMNS = frozenset(
    {
        "submission_id",
        "receipt_id",
        "request_hash",
        "client_order_id",
        "expires_at",
        "state",
        "owner_token",
        "submit_attempts",
        "claimed_at",
        "submitting_at",
        "submitted_at",
        "uncertain_at",
        "reconciled_at",
        "broker_order_id",
        "reconciliation_resolution",
        "last_error_type",
        "updated_at",
    }
)


class AlpacaPaperSubmissionJournalError(Exception):
    """The durable submission boundary could not prove a safe transition."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(f"{reason_code}: {message}")
        self.reason_code = reason_code


class AlpacaPaperSubmissionState(StrEnum):
    """Persisted states for one exact paper-order identity."""

    CLAIMED = "claimed"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    UNCERTAIN = "uncertain"
    RECONCILED = "reconciled"


@dataclass(frozen=True, slots=True)
class AlpacaPaperSubmissionRecord:
    """Non-secret durable state for one exact paper-order attempt."""

    submission_id: str
    receipt_id: str
    request_hash: str
    client_order_id: str
    expires_at: str
    state: AlpacaPaperSubmissionState
    submit_attempts: int
    claimed_at: str
    submitting_at: str | None
    submitted_at: str | None
    uncertain_at: str | None
    reconciled_at: str | None
    broker_order_id: str | None
    reconciliation_resolution: str | None
    last_error_type: str | None
    updated_at: str


class AlpacaPaperSubmissionJournal(Protocol):
    """Atomic receipt consumer plus durable paper-submission transitions."""

    def consume(
        self,
        *,
        receipt_id: str,
        request_hash: str,
        expires_at: str,
    ) -> bool: ...

    def begin_submission(
        self,
        *,
        receipt_id: str,
        request_hash: str,
        client_order_id: str,
    ) -> AlpacaPaperSubmissionRecord: ...

    def mark_submitted(
        self,
        *,
        receipt_id: str,
        request_hash: str,
        client_order_id: str,
        broker_order_id: str,
    ) -> AlpacaPaperSubmissionRecord: ...

    def mark_uncertain(
        self,
        *,
        receipt_id: str,
        request_hash: str,
        client_order_id: str,
        error_type: str,
    ) -> AlpacaPaperSubmissionRecord: ...

    def get(self, request_hash: str) -> AlpacaPaperSubmissionRecord | None: ...

    def recovery_candidates(
        self, *, limit: int = 100
    ) -> tuple[AlpacaPaperSubmissionRecord, ...]: ...

    def mark_reconciled(
        self,
        *,
        request_hash: str,
        resolution: str,
        broker_order_id: str | None = None,
    ) -> AlpacaPaperSubmissionRecord: ...


def _required_text(value: str, field_name: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AlpacaPaperSubmissionJournalError(
            "journal_identity_invalid", f"{field_name} must be non-blank text"
        )
    if len(value.encode("utf-8")) > maximum or "\x00" in value:
        raise AlpacaPaperSubmissionJournalError(
            "journal_identity_invalid", f"{field_name} exceeds its boundary"
        )
    return value


def _request_hash(value: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise AlpacaPaperSubmissionJournalError(
            "journal_identity_invalid",
            "request_hash must be a lowercase SHA-256 digest",
        )
    return value


def client_order_id_for_request_hash(request_hash: str) -> str:
    """Return the stable Alpaca idempotency key for one exact request."""

    return f"llts-{_request_hash(request_hash)}"


def _submission_id(request_hash: str) -> str:
    return f"llts-submission-{_request_hash(request_hash)}"


def _aware_datetime(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise AlpacaPaperSubmissionJournalError(
            "journal_clock_invalid", f"{field_name} is invalid"
        ) from error
    if parsed.tzinfo is None:
        raise AlpacaPaperSubmissionJournalError(
            "journal_clock_invalid", f"{field_name} must be timezone-aware"
        )
    return parsed.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _default_clock() -> datetime:
    return datetime.now(UTC)


class SQLiteAlpacaPaperSubmissionJournal:
    """SQLite receipt consumer and one-attempt paper submission state machine.

    A receipt, request hash, and deterministic Alpaca client order id are each
    permanently unique within the journal.  The only external submission is
    permitted after ``claimed -> submitting`` commits with ``synchronous=FULL``.
    A process death can therefore leave ``claimed`` (no broker call began) or
    ``submitting`` (acceptance is unknown); neither state is auto-resubmitted.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], datetime] = _default_clock,
        max_entries: int = 1_000_000,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        path_text = str(path)
        if not path_text or path_text == ":memory:" or "\x00" in path_text:
            raise AlpacaPaperSubmissionJournalError(
                "journal_path_invalid", "journal path must name a durable file"
            )
        if not callable(clock):
            raise AlpacaPaperSubmissionJournalError(
                "journal_clock_invalid", "journal clock must be callable"
            )
        if (
            isinstance(max_entries, bool)
            or not isinstance(max_entries, int)
            or not 1 <= max_entries <= 10_000_000
        ):
            raise AlpacaPaperSubmissionJournalError(
                "journal_configuration_invalid",
                "max_entries must be between 1 and 10000000",
            )
        if (
            isinstance(busy_timeout_ms, bool)
            or not isinstance(busy_timeout_ms, int)
            or not 1 <= busy_timeout_ms <= 60_000
        ):
            raise AlpacaPaperSubmissionJournalError(
                "journal_configuration_invalid",
                "busy_timeout_ms must be between 1 and 60000",
            )

        self._path = Path(path_text)
        self._clock = clock
        self._max_entries = max_entries
        self._lock = threading.RLock()
        self._owned_claims: dict[str, str] = {}
        self._closed = False

        parent = self._path.parent
        if not parent.is_dir() or parent.is_symlink():
            raise AlpacaPaperSubmissionJournalError(
                "journal_path_invalid",
                "journal parent must be an existing non-symlink directory",
            )
        preexisting_nonempty = self._prepare_private_file(self._path)
        try:
            connection = sqlite3.connect(
                path_text,
                timeout=busy_timeout_ms / 1000,
                isolation_level=None,
                check_same_thread=False,
            )
        except sqlite3.Error as error:
            raise AlpacaPaperSubmissionJournalError(
                "journal_unavailable", "journal database could not be opened"
            ) from error
        self._connection = connection
        self._connection.row_factory = sqlite3.Row
        try:
            self._connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
            if self._connection.execute("PRAGMA secure_delete = ON").fetchone()[0] != 1:
                raise AlpacaPaperSubmissionJournalError(
                    "journal_configuration_invalid",
                    "journal storage must support SQLite secure deletion",
                )
            mode = self._connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if not isinstance(mode, str) or mode.lower() != "wal":
                raise AlpacaPaperSubmissionJournalError(
                    "journal_configuration_invalid",
                    "journal storage must support SQLite WAL mode",
                )
            self._connection.execute("PRAGMA synchronous = FULL")
            self._initialize_schema(preexisting_nonempty=preexisting_nonempty)
            self._harden_sqlite_files()
        except AlpacaPaperSubmissionJournalError:
            self._connection.close()
            self._closed = True
            raise
        except sqlite3.Error as error:
            self._connection.close()
            self._closed = True
            raise AlpacaPaperSubmissionJournalError(
                "journal_migration_required",
                "submission journal is unreadable or incompatible",
            ) from error

    @staticmethod
    def _prepare_private_file(path: Path) -> bool:
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as error:
            raise AlpacaPaperSubmissionJournalError(
                "journal_path_invalid", "journal path must be a regular file"
            ) from error
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise AlpacaPaperSubmissionJournalError(
                    "journal_path_invalid", "journal path must be a regular file"
                )
            os.fchmod(descriptor, 0o600)
            return metadata.st_size > 0
        finally:
            os.close(descriptor)

    def _harden_sqlite_files(self) -> None:
        for candidate in (
            self._path,
            Path(f"{self._path}-wal"),
            Path(f"{self._path}-shm"),
        ):
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                descriptor = os.open(candidate, flags)
            except FileNotFoundError:
                continue
            except OSError as error:
                raise AlpacaPaperSubmissionJournalError(
                    "journal_permissions_invalid",
                    "journal storage permissions could not be secured",
                ) from error
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise AlpacaPaperSubmissionJournalError(
                        "journal_permissions_invalid",
                        "journal storage is not a regular file",
                    )
                os.fchmod(descriptor, 0o600)
            finally:
                os.close(descriptor)

    def _initialize_schema(self, *, preexisting_nonempty: bool) -> None:
        existing_tables = {
            row[0]
            for row in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if preexisting_nonempty and _TABLE not in existing_tables:
            raise AlpacaPaperSubmissionJournalError(
                "journal_migration_required",
                "existing database is not an Alpaca paper submission journal",
            )
        self._connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_TABLE} (
                submission_id TEXT PRIMARY KEY,
                receipt_id TEXT NOT NULL UNIQUE,
                request_hash TEXT NOT NULL UNIQUE,
                client_order_id TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                state TEXT NOT NULL CHECK (
                    state IN ('claimed', 'submitting', 'submitted',
                              'uncertain', 'reconciled')
                ),
                owner_token TEXT,
                submit_attempts INTEGER NOT NULL CHECK (
                    submit_attempts IN (0, 1)
                ),
                claimed_at TEXT NOT NULL,
                submitting_at TEXT,
                submitted_at TEXT,
                uncertain_at TEXT,
                reconciled_at TEXT,
                broker_order_id TEXT,
                reconciliation_resolution TEXT,
                last_error_type TEXT,
                updated_at TEXT NOT NULL
            ) WITHOUT ROWID
            """
        )
        columns = {
            row["name"]
            for row in self._connection.execute(f"PRAGMA table_info({_TABLE})")
        }
        if columns != _EXPECTED_COLUMNS:
            raise AlpacaPaperSubmissionJournalError(
                "journal_migration_required",
                "submission journal schema requires an explicit migration",
            )
        self._connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_META_TABLE} (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            ) WITHOUT ROWID
            """
        )
        self._connection.execute(
            f"""
            CREATE INDEX IF NOT EXISTS alpaca_paper_submission_state_v1
            ON {_TABLE} (state, updated_at, submission_id)
            """
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise AlpacaPaperSubmissionJournalError(
                "journal_closed", "submission journal is closed"
            )

    def _now(self) -> datetime:
        try:
            value = self._clock()
        except Exception as error:
            raise AlpacaPaperSubmissionJournalError(
                "journal_clock_invalid", "journal clock is unavailable"
            ) from error
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise AlpacaPaperSubmissionJournalError(
                "journal_clock_invalid",
                "journal clock must return a timezone-aware datetime",
            )
        return value.astimezone(UTC)

    def _record_clock(self, now: datetime) -> str:
        now_text = _timestamp(now)
        row = self._connection.execute(
            f"SELECT value FROM {_META_TABLE} WHERE key = 'last_clock'"
        ).fetchone()
        if row is not None and now < _aware_datetime(row["value"], "last_clock"):
            raise AlpacaPaperSubmissionJournalError(
                "journal_clock_rollback",
                "journal clock moved backwards; submission is fail-closed",
            )
        self._connection.execute(
            f"""
            INSERT INTO {_META_TABLE} (key, value) VALUES ('last_clock', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (now_text,),
        )
        return now_text

    def _begin(self) -> None:
        self._ensure_open()
        self._connection.execute("BEGIN IMMEDIATE")

    def _commit(self) -> None:
        self._connection.commit()
        self._harden_sqlite_files()

    def _rollback(self) -> None:
        if self._connection.in_transaction:
            self._connection.rollback()

    def _row_for_hash(self, request_hash: str) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            self._connection.execute(
                f"SELECT * FROM {_TABLE} WHERE request_hash = ?", (request_hash,)
            ).fetchone(),
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> AlpacaPaperSubmissionRecord:
        request_hash = _request_hash(row["request_hash"])
        receipt_id = _required_text(row["receipt_id"], "receipt_id")
        expected_client_id = client_order_id_for_request_hash(request_hash)
        expected_submission_id = _submission_id(request_hash)
        if (
            row["client_order_id"] != expected_client_id
            or row["submission_id"] != expected_submission_id
        ):
            raise AlpacaPaperSubmissionJournalError(
                "journal_corrupt", "journal identities are inconsistent"
            )
        try:
            state = AlpacaPaperSubmissionState(row["state"])
        except (TypeError, ValueError) as error:
            raise AlpacaPaperSubmissionJournalError(
                "journal_corrupt", "journal state is invalid"
            ) from error
        attempts = row["submit_attempts"]
        resolution = row["reconciliation_resolution"]
        broker_order_id = row["broker_order_id"]
        if type(attempts) is not int or attempts not in (0, 1):
            raise AlpacaPaperSubmissionJournalError(
                "journal_corrupt", "journal attempt count is invalid"
            )
        if state is AlpacaPaperSubmissionState.CLAIMED and attempts != 0:
            raise AlpacaPaperSubmissionJournalError(
                "journal_corrupt", "claimed journal entry has a submit attempt"
            )
        if (
            state
            in {
                AlpacaPaperSubmissionState.SUBMITTING,
                AlpacaPaperSubmissionState.SUBMITTED,
                AlpacaPaperSubmissionState.UNCERTAIN,
            }
            and attempts != 1
        ):
            raise AlpacaPaperSubmissionJournalError(
                "journal_corrupt", "submission state lacks its single attempt"
            )
        if state is AlpacaPaperSubmissionState.SUBMITTED and not broker_order_id:
            raise AlpacaPaperSubmissionJournalError(
                "journal_corrupt", "submitted journal entry lacks broker identity"
            )
        if state is AlpacaPaperSubmissionState.RECONCILED:
            if resolution not in {"broker_order_found", "not_submitted"}:
                raise AlpacaPaperSubmissionJournalError(
                    "journal_corrupt", "reconciled journal resolution is invalid"
                )
            if resolution == "broker_order_found" and not broker_order_id:
                raise AlpacaPaperSubmissionJournalError(
                    "journal_corrupt", "reconciled broker order lacks identity"
                )
        _aware_datetime(row["expires_at"], "expires_at")
        for field_name in ("claimed_at", "updated_at"):
            _aware_datetime(row[field_name], field_name)
        return AlpacaPaperSubmissionRecord(
            submission_id=expected_submission_id,
            receipt_id=receipt_id,
            request_hash=request_hash,
            client_order_id=expected_client_id,
            expires_at=row["expires_at"],
            state=state,
            submit_attempts=attempts,
            claimed_at=row["claimed_at"],
            submitting_at=row["submitting_at"],
            submitted_at=row["submitted_at"],
            uncertain_at=row["uncertain_at"],
            reconciled_at=row["reconciled_at"],
            broker_order_id=broker_order_id,
            reconciliation_resolution=resolution,
            last_error_type=row["last_error_type"],
            updated_at=row["updated_at"],
        )

    def consume(
        self,
        *,
        receipt_id: str,
        request_hash: str,
        expires_at: str,
    ) -> bool:
        """Atomically and permanently claim one receipt/request identity."""

        receipt_id = _required_text(receipt_id, "receipt_id")
        request_hash = _request_hash(request_hash)
        expiry = _aware_datetime(expires_at, "expires_at")
        owner_token = secrets.token_hex(32)
        with self._lock:
            try:
                self._begin()
                now = self._now()
                now_text = self._record_clock(now)
                if expiry <= now:
                    self._commit()
                    return False
                count = int(
                    self._connection.execute(
                        f"SELECT COUNT(*) FROM {_TABLE}"
                    ).fetchone()[0]
                )
                if count >= self._max_entries:
                    raise AlpacaPaperSubmissionJournalError(
                        "journal_capacity_reached",
                        "submission identity capacity requires operator archival",
                    )
                try:
                    self._connection.execute(
                        f"""
                        INSERT INTO {_TABLE} (
                            submission_id, receipt_id, request_hash,
                            client_order_id, expires_at, state, owner_token,
                            submit_attempts, claimed_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 'claimed', ?, 0, ?, ?)
                        """,
                        (
                            _submission_id(request_hash),
                            receipt_id,
                            request_hash,
                            client_order_id_for_request_hash(request_hash),
                            _timestamp(expiry),
                            owner_token,
                            now_text,
                            now_text,
                        ),
                    )
                except sqlite3.IntegrityError:
                    self._rollback()
                    return False
                self._commit()
                self._owned_claims[request_hash] = owner_token
                return True
            except Exception:
                self._rollback()
                raise

    def begin_submission(
        self,
        *,
        receipt_id: str,
        request_hash: str,
        client_order_id: str,
    ) -> AlpacaPaperSubmissionRecord:
        """Durably enter the one-attempt state before any broker call."""

        receipt_id = _required_text(receipt_id, "receipt_id")
        request_hash = _request_hash(request_hash)
        expected_client_id = client_order_id_for_request_hash(request_hash)
        if client_order_id != expected_client_id:
            raise AlpacaPaperSubmissionJournalError(
                "journal_identity_mismatch",
                "client_order_id differs from the request-bound identity",
            )
        owner_token = self._owned_claims.get(request_hash)
        if owner_token is None:
            raise AlpacaPaperSubmissionJournalError(
                "journal_owner_missing",
                "only the process that atomically claimed the receipt may submit",
            )
        with self._lock:
            try:
                self._begin()
                now_text = self._record_clock(self._now())
                changed = self._connection.execute(
                    f"""
                    UPDATE {_TABLE}
                    SET state = 'submitting', submit_attempts = 1,
                        submitting_at = ?, updated_at = ?
                    WHERE request_hash = ? AND receipt_id = ?
                      AND client_order_id = ? AND state = 'claimed'
                      AND submit_attempts = 0 AND owner_token = ?
                    """,
                    (
                        now_text,
                        now_text,
                        request_hash,
                        receipt_id,
                        client_order_id,
                        owner_token,
                    ),
                ).rowcount
                if changed != 1:
                    raise AlpacaPaperSubmissionJournalError(
                        "journal_transition_rejected",
                        "claimed submission could not enter its single attempt",
                    )
                row = self._row_for_hash(request_hash)
                assert row is not None
                record = self._record(row)
                self._commit()
                return record
            except Exception:
                self._rollback()
                raise

    def mark_submitted(
        self,
        *,
        receipt_id: str,
        request_hash: str,
        client_order_id: str,
        broker_order_id: str,
    ) -> AlpacaPaperSubmissionRecord:
        """Persist broker acceptance after the sole submission attempt."""

        broker_order_id = _required_text(broker_order_id, "broker_order_id")
        return self._finish_owned_attempt(
            receipt_id=receipt_id,
            request_hash=request_hash,
            client_order_id=client_order_id,
            target=AlpacaPaperSubmissionState.SUBMITTED,
            broker_order_id=broker_order_id,
            error_type=None,
        )

    def mark_uncertain(
        self,
        *,
        receipt_id: str,
        request_hash: str,
        client_order_id: str,
        error_type: str,
    ) -> AlpacaPaperSubmissionRecord:
        """Persist an unknown post-attempt broker outcome without retrying."""

        error_type = _required_text(error_type, "error_type", maximum=128)
        return self._finish_owned_attempt(
            receipt_id=receipt_id,
            request_hash=request_hash,
            client_order_id=client_order_id,
            target=AlpacaPaperSubmissionState.UNCERTAIN,
            broker_order_id=None,
            error_type=error_type,
        )

    def _finish_owned_attempt(
        self,
        *,
        receipt_id: str,
        request_hash: str,
        client_order_id: str,
        target: AlpacaPaperSubmissionState,
        broker_order_id: str | None,
        error_type: str | None,
    ) -> AlpacaPaperSubmissionRecord:
        receipt_id = _required_text(receipt_id, "receipt_id")
        request_hash = _request_hash(request_hash)
        if client_order_id != client_order_id_for_request_hash(request_hash):
            raise AlpacaPaperSubmissionJournalError(
                "journal_identity_mismatch",
                "client_order_id differs from the request-bound identity",
            )
        owner_token = self._owned_claims.get(request_hash)
        if owner_token is None:
            raise AlpacaPaperSubmissionJournalError(
                "journal_owner_missing", "submission attempt ownership is unavailable"
            )
        timestamp_column = (
            "submitted_at"
            if target is AlpacaPaperSubmissionState.SUBMITTED
            else "uncertain_at"
        )
        with self._lock:
            try:
                self._begin()
                now_text = self._record_clock(self._now())
                changed = self._connection.execute(
                    f"""
                    UPDATE {_TABLE}
                    SET state = ?, {timestamp_column} = ?, broker_order_id = ?,
                        last_error_type = ?, owner_token = NULL, updated_at = ?
                    WHERE request_hash = ? AND receipt_id = ?
                      AND client_order_id = ? AND state = 'submitting'
                      AND submit_attempts = 1 AND owner_token = ?
                    """,
                    (
                        target.value,
                        now_text,
                        broker_order_id,
                        error_type,
                        now_text,
                        request_hash,
                        receipt_id,
                        client_order_id,
                        owner_token,
                    ),
                ).rowcount
                if changed != 1:
                    row = self._row_for_hash(request_hash)
                    if (
                        row is not None
                        and row["state"] == "reconciled"
                        and broker_order_id is not None
                        and row["broker_order_id"] == broker_order_id
                    ):
                        record = self._record(row)
                        self._commit()
                        self._owned_claims.pop(request_hash, None)
                        return record
                    raise AlpacaPaperSubmissionJournalError(
                        "journal_transition_rejected",
                        "submission attempt could not enter its terminal state",
                    )
                row = self._row_for_hash(request_hash)
                assert row is not None
                record = self._record(row)
                self._commit()
                self._owned_claims.pop(request_hash, None)
                return record
            except Exception:
                self._rollback()
                raise

    def get(self, request_hash: str) -> AlpacaPaperSubmissionRecord | None:
        """Read one exact submission record without changing it."""

        request_hash = _request_hash(request_hash)
        with self._lock:
            self._ensure_open()
            row = self._row_for_hash(request_hash)
            return None if row is None else self._record(row)

    def recovery_candidates(
        self, *, limit: int = 100
    ) -> tuple[AlpacaPaperSubmissionRecord, ...]:
        """Return sticky crash/unknown states; this never submits or mutates."""

        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 1000
        ):
            raise AlpacaPaperSubmissionJournalError(
                "journal_configuration_invalid", "limit must be between 1 and 1000"
            )
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                f"""
                SELECT * FROM {_TABLE}
                WHERE state IN ('claimed', 'submitting', 'uncertain')
                ORDER BY updated_at ASC, submission_id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return tuple(self._record(row) for row in rows)

    def mark_reconciled(
        self,
        *,
        request_hash: str,
        resolution: str,
        broker_order_id: str | None = None,
    ) -> AlpacaPaperSubmissionRecord:
        """Record a read-only reconciliation result across process restarts."""

        request_hash = _request_hash(request_hash)
        if resolution not in {"broker_order_found", "not_submitted"}:
            raise AlpacaPaperSubmissionJournalError(
                "journal_reconciliation_invalid", "reconciliation resolution is invalid"
            )
        if resolution == "broker_order_found":
            if broker_order_id is None:
                raise AlpacaPaperSubmissionJournalError(
                    "journal_reconciliation_invalid",
                    "broker_order_found requires broker_order_id",
                )
            broker_order_id = _required_text(broker_order_id, "broker_order_id")
            eligible_states: tuple[str, ...] = (
                "submitting",
                "uncertain",
                "submitted",
            )
        else:
            if broker_order_id is not None:
                raise AlpacaPaperSubmissionJournalError(
                    "journal_reconciliation_invalid",
                    "not_submitted cannot retain a broker order id",
                )
            eligible_states = ("claimed",)
        placeholders = ", ".join("?" for _ in eligible_states)
        with self._lock:
            try:
                self._begin()
                now_text = self._record_clock(self._now())
                changed = self._connection.execute(
                    f"""
                    UPDATE {_TABLE}
                    SET state = 'reconciled', reconciled_at = ?,
                        broker_order_id = ?, reconciliation_resolution = ?,
                        owner_token = NULL, updated_at = ?
                    WHERE request_hash = ? AND state IN ({placeholders})
                    """,
                    (
                        now_text,
                        broker_order_id,
                        resolution,
                        now_text,
                        request_hash,
                        *eligible_states,
                    ),
                ).rowcount
                if changed != 1:
                    row = self._row_for_hash(request_hash)
                    if (
                        row is not None
                        and row["state"] == "reconciled"
                        and row["reconciliation_resolution"] == resolution
                        and row["broker_order_id"] == broker_order_id
                    ):
                        record = self._record(row)
                        self._commit()
                        return record
                    raise AlpacaPaperSubmissionJournalError(
                        "journal_transition_rejected",
                        "submission state cannot accept this reconciliation",
                    )
                row = self._row_for_hash(request_hash)
                assert row is not None
                record = self._record(row)
                self._commit()
                self._owned_claims.pop(request_hash, None)
                return record
            except Exception:
                self._rollback()
                raise

    def counts(self) -> dict[AlpacaPaperSubmissionState, int]:
        """Return state counts for operator health and backlog monitoring."""

        with self._lock:
            self._ensure_open()
            counts = {state: 0 for state in AlpacaPaperSubmissionState}
            for row in self._connection.execute(
                f"SELECT state, COUNT(*) AS count FROM {_TABLE} GROUP BY state"
            ):
                try:
                    state = AlpacaPaperSubmissionState(row["state"])
                except (TypeError, ValueError) as error:
                    raise AlpacaPaperSubmissionJournalError(
                        "journal_corrupt", "journal contains an invalid state"
                    ) from error
                counts[state] = int(row["count"])
            return counts

    def close(self) -> None:
        """Close this connection; durable identities remain on disk."""

        with self._lock:
            if self._closed:
                return
            self._owned_claims.clear()
            self._connection.close()
            self._closed = True
            self._harden_sqlite_files()

    def __enter__(self) -> SQLiteAlpacaPaperSubmissionJournal:
        self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()


__all__ = [
    "AlpacaPaperSubmissionJournal",
    "AlpacaPaperSubmissionJournalError",
    "AlpacaPaperSubmissionRecord",
    "AlpacaPaperSubmissionState",
    "SQLiteAlpacaPaperSubmissionJournal",
    "client_order_id_for_request_hash",
]
