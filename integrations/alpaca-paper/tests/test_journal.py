from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest

from liquilens_alpaca_paper import (
    AlpacaPaperSubmissionJournalError,
    AlpacaPaperSubmissionState,
    SQLiteAlpacaPaperSubmissionJournal,
    client_order_id_for_request_hash,
)

NOW = datetime(2026, 9, 5, 9, 0, tzinfo=UTC)
EXPIRY = (NOW + timedelta(minutes=5)).isoformat()
HASH_A = "a" * 64
HASH_B = "b" * 64
RECEIPT_A = "tsr_01a"
RECEIPT_B = "tsr_01b"


def _journal(
    path: Path,
    *,
    clock=lambda: NOW,
    max_entries: int = 100,
) -> SQLiteAlpacaPaperSubmissionJournal:
    return SQLiteAlpacaPaperSubmissionJournal(
        path,
        clock=clock,
        max_entries=max_entries,
    )


def _claim(
    journal: SQLiteAlpacaPaperSubmissionJournal,
    *,
    receipt_id: str = RECEIPT_A,
    request_hash: str = HASH_A,
) -> None:
    assert journal.consume(
        receipt_id=receipt_id,
        request_hash=request_hash,
        expires_at=EXPIRY,
    )


def _begin(
    journal: SQLiteAlpacaPaperSubmissionJournal,
    *,
    receipt_id: str = RECEIPT_A,
    request_hash: str = HASH_A,
) -> None:
    journal.begin_submission(
        receipt_id=receipt_id,
        request_hash=request_hash,
        client_order_id=client_order_id_for_request_hash(request_hash),
    )


def test_claim_is_private_durable_and_permanently_unique(tmp_path: Path) -> None:
    path = tmp_path / "submission.sqlite3"
    with _journal(path) as journal:
        _claim(journal)
        record = journal.get(HASH_A)
        assert record is not None
        assert record.submission_id == f"llts-submission-{HASH_A}"
        assert record.receipt_id == RECEIPT_A
        assert record.client_order_id == f"llts-{HASH_A}"
        assert record.state is AlpacaPaperSubmissionState.CLAIMED
        assert record.submit_attempts == 0
        assert journal.counts()[AlpacaPaperSubmissionState.CLAIMED] == 1

    assert os.stat(path).st_mode & 0o777 == 0o600
    with _journal(path) as reopened:
        assert reopened.get(HASH_A) == record
        assert not reopened.consume(
            receipt_id=RECEIPT_B,
            request_hash=HASH_A,
            expires_at=EXPIRY,
        )
        assert not reopened.consume(
            receipt_id=RECEIPT_A,
            request_hash=HASH_B,
            expires_at=EXPIRY,
        )


def test_state_machine_persists_submission_and_reconciliation(tmp_path: Path) -> None:
    path = tmp_path / "submission.sqlite3"
    with _journal(path) as journal:
        _claim(journal)
        _begin(journal)
        submitting = journal.get(HASH_A)
        assert submitting is not None
        assert submitting.state is AlpacaPaperSubmissionState.SUBMITTING
        assert submitting.submit_attempts == 1
        submitted = journal.mark_submitted(
            receipt_id=RECEIPT_A,
            request_hash=HASH_A,
            client_order_id=client_order_id_for_request_hash(HASH_A),
            broker_order_id="paper-order-001",
        )
        assert submitted.state is AlpacaPaperSubmissionState.SUBMITTED
        assert submitted.broker_order_id == "paper-order-001"

    with _journal(path) as reopened:
        reconciled = reopened.mark_reconciled(
            request_hash=HASH_A,
            resolution="broker_order_found",
            broker_order_id="paper-order-001",
        )
        assert reconciled.state is AlpacaPaperSubmissionState.RECONCILED
        assert reconciled.reconciliation_resolution == "broker_order_found"
        assert reconciled.submit_attempts == 1
        assert reopened.recovery_candidates() == ()


def test_uncertain_attempt_survives_restart_without_new_owner(tmp_path: Path) -> None:
    path = tmp_path / "submission.sqlite3"
    journal = _journal(path)
    _claim(journal)
    _begin(journal)
    uncertain = journal.mark_uncertain(
        receipt_id=RECEIPT_A,
        request_hash=HASH_A,
        client_order_id=client_order_id_for_request_hash(HASH_A),
        error_type="TimeoutError",
    )
    assert uncertain.state is AlpacaPaperSubmissionState.UNCERTAIN
    journal.close()

    with _journal(path) as reopened:
        assert [entry.request_hash for entry in reopened.recovery_candidates()] == [
            HASH_A
        ]
        assert not reopened.consume(
            receipt_id=RECEIPT_A,
            request_hash=HASH_A,
            expires_at=EXPIRY,
        )
        with pytest.raises(AlpacaPaperSubmissionJournalError) as caught:
            _begin(reopened)
        assert caught.value.reason_code == "journal_owner_missing"


def test_crash_during_submitting_is_sticky_and_reconcilable(tmp_path: Path) -> None:
    path = tmp_path / "submission.sqlite3"
    journal = _journal(path)
    _claim(journal)
    _begin(journal)
    journal.close()  # Simulate loss after durable pre-call transition.

    with _journal(path) as restarted:
        candidates = restarted.recovery_candidates()
        assert len(candidates) == 1
        assert candidates[0].state is AlpacaPaperSubmissionState.SUBMITTING
        assert candidates[0].submit_attempts == 1
        assert not restarted.consume(
            receipt_id=RECEIPT_A,
            request_hash=HASH_A,
            expires_at=EXPIRY,
        )
        reconciled = restarted.mark_reconciled(
            request_hash=HASH_A,
            resolution="broker_order_found",
            broker_order_id="paper-order-after-crash",
        )
        assert reconciled.state is AlpacaPaperSubmissionState.RECONCILED


def test_crash_after_claim_can_only_reconcile_as_not_submitted(tmp_path: Path) -> None:
    path = tmp_path / "submission.sqlite3"
    journal = _journal(path)
    _claim(journal)
    journal.close()  # No durable submitting transition means no broker call began.

    with _journal(path) as restarted:
        reconciled = restarted.mark_reconciled(
            request_hash=HASH_A,
            resolution="not_submitted",
        )
        assert reconciled.state is AlpacaPaperSubmissionState.RECONCILED
        assert reconciled.reconciliation_resolution == "not_submitted"
        assert reconciled.submit_attempts == 0
        with pytest.raises(AlpacaPaperSubmissionJournalError):
            restarted.mark_reconciled(
                request_hash=HASH_A,
                resolution="broker_order_found",
                broker_order_id="must-not-appear",
            )


def test_concurrent_connections_grant_exactly_one_claim(tmp_path: Path) -> None:
    path = tmp_path / "submission.sqlite3"
    first = _journal(path)
    second = _journal(path)
    barrier = Barrier(2)

    def consume(journal: SQLiteAlpacaPaperSubmissionJournal) -> bool:
        barrier.wait()
        return journal.consume(
            receipt_id=RECEIPT_A,
            request_hash=HASH_A,
            expires_at=EXPIRY,
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(consume, (first, second)))
        assert sorted(results) == [False, True]
        assert sum(first.counts().values()) == 1
    finally:
        first.close()
        second.close()


def test_capacity_and_clock_rollback_fail_closed(tmp_path: Path) -> None:
    now = [NOW]
    path = tmp_path / "submission.sqlite3"
    with _journal(path, clock=lambda: now[0], max_entries=1) as journal:
        _claim(journal)
        with pytest.raises(AlpacaPaperSubmissionJournalError) as capacity:
            journal.consume(
                receipt_id=RECEIPT_B,
                request_hash=HASH_B,
                expires_at=EXPIRY,
            )
        assert capacity.value.reason_code == "journal_capacity_reached"

    now[0] -= timedelta(seconds=1)
    with _journal(path, clock=lambda: now[0], max_entries=2) as restarted:
        with pytest.raises(AlpacaPaperSubmissionJournalError) as rollback:
            restarted.consume(
                receipt_id=RECEIPT_B,
                request_hash=HASH_B,
                expires_at=EXPIRY,
            )
        assert rollback.value.reason_code == "journal_clock_rollback"


def test_invalid_or_ephemeral_journal_paths_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(AlpacaPaperSubmissionJournalError) as memory:
        _journal(Path(":memory:"))
    assert memory.value.reason_code == "journal_path_invalid"

    foreign = tmp_path / "foreign.sqlite3"
    foreign.write_text("not a journal", encoding="utf-8")
    with pytest.raises(AlpacaPaperSubmissionJournalError) as incompatible:
        _journal(foreign)
    assert incompatible.value.reason_code == "journal_migration_required"

    target = tmp_path / "target.sqlite3"
    target.touch()
    link = tmp_path / "link.sqlite3"
    link.symlink_to(target)
    with pytest.raises(AlpacaPaperSubmissionJournalError) as symlink:
        _journal(link)
    assert symlink.value.reason_code == "journal_path_invalid"
