#!/usr/bin/env python3
"""Retain bounded source-only observations; never load trading state or keys."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import selectors
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = "liquilens.execution-observation-run.v1"
REPORT_SCHEMA = "liquilens.execution-observatory.v1"
AUTHORITY = (
    "ready_for_order",
    "receipt_issued",
    "order_authorized",
    "order_submitted",
    "state_modified",
)
RUN_NAME = re.compile(r"run-\d{8}T\d{12}Z-[0-9a-f]{32}\.json\Z")
MAX_OUTPUT = 1048576
TEMP_NAME = re.compile(r"\.observation-[a-zA-Z0-9_-]{8}\Z")


class CaptureError(Exception):
    """A fixed diagnostic without upstream content or credentials."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise CaptureError(reason)


def private(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    require(
        kind(info.st_mode)
        and info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600)
        and (directory or info.st_nlink == 1),
        "private_path_invalid",
    )


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def encode(value: dict) -> bytes:
    return (
        json.dumps(value, sort_keys=True, allow_nan=False, indent=2) + "\n"
    ).encode()


def write_new(path: Path, raw: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=".observation-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # Link is exclusive and publishes only the complete, fsynced bytes.
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def write_latest(path: Path, raw: bytes) -> None:
    if path.exists() or path.is_symlink():
        private(path)
        require(
            json.loads(path.read_bytes()).get("schema") == SCHEMA, "unknown_latest_file"
        )
    fd, name = tempfile.mkstemp(prefix=".observation-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def validate_report(raw: bytes) -> dict:
    require(len(raw) <= MAX_OUTPUT, "report_too_large")

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    def constant(_value):
        raise CaptureError("nonfinite_json_value")

    report = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    require(
        isinstance(report, dict) and report.get("schema") == REPORT_SCHEMA,
        "report_schema_invalid",
    )
    require(
        all(report.get(key) is False for key in AUTHORITY), "report_authority_invalid"
    )
    require(type(report.get("source_checks_passed")) is bool, "source_status_invalid")
    sources = report.get("sources")
    require(
        isinstance(sources, dict)
        and set(sources) == {"seiche", "liquilens", "undertow"},
        "source_set_invalid",
    )
    require(
        all(
            isinstance(row, dict) and type(row.get("admitted")) is bool
            for row in sources.values()
        ),
        "source_row_invalid",
    )
    require(
        report["source_checks_passed"]
        == all(row["admitted"] for row in sources.values()),
        "source_summary_inconsistent",
    )
    return report


def run_bounded(command: list[str], env: dict[str, str], timeout: float = 45):
    output = bytearray()
    stderr_size = 0
    deadline = time.monotonic() + timeout
    with subprocess.Popen(
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0
    ) as process:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise CaptureError("observer_timed_out")
                    for key, _mask in selector.select(min(remaining, 0.5)):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        elif key.data == "stdout":
                            require(
                                len(output) + len(chunk) <= MAX_OUTPUT,
                                "report_too_large",
                            )
                            output.extend(chunk)
                        else:
                            stderr_size += len(chunk)
                            require(stderr_size <= 4096, "observer_stderr_too_large")
            remaining = deadline - time.monotonic()
            require(remaining > 0, "observer_timed_out")
            return process.wait(timeout=remaining), bytes(output)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def observe(undertow_token_file: Path | None = None) -> tuple[dict | None, str | None]:
    # No inherited credentials/proxies, config path, account flag or shell.
    command = [
        sys.executable,
        "-B",
        "-m",
        "liquilens_trading_copilot.cli",
        "observe",
        "--format",
        "json",
    ]
    if undertow_token_file is not None:
        command.extend(["--undertow-token-file", str(undertow_token_file)])
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        code, raw = run_bounded(command, env)
        require(code in (0, 2), "observer_process_failed")
        return validate_report(raw), None
    except subprocess.TimeoutExpired:
        return None, "observer_timed_out"
    except CaptureError as exc:
        return None, str(exc)
    except (ValueError, TypeError, OSError):
        return None, "observer_output_invalid"


def prune(history: Path, keep: int) -> None:
    candidates = sorted(
        path for path in history.iterdir() if RUN_NAME.fullmatch(path.name)
    )
    for path in candidates[: max(0, len(candidates) - keep)]:
        private(path)
        require(
            json.loads(path.read_bytes()).get("schema") == SCHEMA,
            "unknown_history_file",
        )
        path.unlink()
    sync_directory(history)


def recover_temporary(directory: Path) -> None:
    for path in directory.iterdir():
        if not TEMP_NAME.fullmatch(path.name):
            continue
        info = path.lstat()
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_nlink in (1, 2),
            "unknown_temporary_file",
        )
        # Only this private capture namespace; never an execution journal.
        path.unlink()
    sync_directory(directory)


def capture(
    state: Path,
    source_sha: str,
    keep: int = 288,
    *,
    undertow_token_file: Path | None = None,
) -> dict:
    require(re.fullmatch(r"[0-9a-f]{40}", source_sha) is not None, "source_sha_invalid")
    require(type(keep) is int and 1 <= keep <= 2048, "retention_invalid")
    state = state.resolve(strict=True)
    private(state, directory=True)
    lock = state / ".observer.lock"
    fd = os.open(lock, os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        private(lock)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        history = state / "history"
        history.mkdir(mode=0o700, exist_ok=True)
        private(history, directory=True)
        started = datetime.now(UTC)
        record = {
            "schema": SCHEMA,
            "source_commit": source_sha,
            "capture_implementation_sha256": hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
            "started_at": started.isoformat(),
            "completed_at": None,
            "capture_status": "in_progress",
            "error_code": None,
            "report": None,
            "broker_account_checked": False,
            "execution_authorized": False,
            "attribution": "owner_source_verification",
        }
        recover_temporary(state)
        recover_temporary(history)
        # Persist non-passing state before collection, even if the process dies.
        write_latest(state / "latest.json", encode(record))
        # Prune before publishing a new history file: corruption cannot grow
        # the history on each failed attempt. Unknown data requires review.
        prune(history, keep - 1)
        report, error = (
            observe() if undertow_token_file is None else observe(undertow_token_file)
        )
        completed = datetime.now(UTC)
        record.update(
            completed_at=completed.isoformat(),
            capture_status="complete" if error is None else "failed",
            error_code=error,
            report=report,
        )
        raw = encode(record)
        name = (
            "run-"
            + completed.strftime("%Y%m%dT%H%M%S%fZ")
            + "-"
            + uuid.uuid4().hex
            + ".json"
        )
        write_new(history / name, raw)
        # Failures replace latest too; an old passing observation is never current.
        write_latest(state / "latest.json", raw)
        return record
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--keep", type=int, default=288)
    parser.add_argument("--undertow-token-file", type=Path)
    args = parser.parse_args()
    try:
        record = capture(
            args.state_dir,
            args.source_sha,
            args.keep,
            undertow_token_file=args.undertow_token_file,
        )
        print(
            json.dumps(
                {
                    "capture_status": record["capture_status"],
                    "error_code": record["error_code"],
                    "execution_authorized": False,
                }
            )
        )
        return 0 if record["capture_status"] == "complete" else 1
    except (CaptureError, OSError, ValueError, TypeError):
        print(
            json.dumps(
                {
                    "capture_status": "failed",
                    "error_code": "private_capture_failed",
                    "execution_authorized": False,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
