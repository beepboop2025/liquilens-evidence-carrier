#!/usr/bin/env python3
"""Attach private agent access to an inactive, bound paper state without reset.

Run as the existing state owner using the qualified integration environment.
This helper never starts services, initializes journals, or contacts any network.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import sqlite3
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path

from liquilens_trading_copilot.agent_host import BearerAuthority
from liquilens_trading_copilot.config import (
    SCOPED_PROFILE,
    PaperCredentials,
    load_config,
    load_secret_file,
    strict_json,
)

ORIGINALS = ("config.json", "paper.env", "audit.sqlite3", "operator.lock")
# Auth is last: a partial publication cannot pass host startup authentication.
ADDITIONS = ("agent-read.token", "agent-execution.token", "agent-auth.json")
UNITS = (
    "liquilens-paper-copilot.service",
    "liquilens-paper-copilot.timer",
    "liquilens-agent-host.service",
)
SCHEMA = "liquilens.paper-host-attach.v1"


class AttachBlocked(ValueError):
    """A fixed diagnostic code containing no configuration or token values."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise AttachBlocked(code)


def private_directory(path: Path) -> os.stat_result:
    require(path.is_absolute(), "absolute_path_required")
    require(
        not any(p.is_symlink() for p in (path, *path.parents)),
        "symlink_path_refused",
    )
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode), "private_directory_required")
    require(
        info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
        "private_directory_owner_or_mode_invalid",
    )
    return info


def file_record(path: Path, *, original: bool = False) -> dict:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_size <= (16 * 1024 * 1024 if original else 65536),
            "private_file_owner_mode_or_size_invalid",
        )
        require(not original or info.st_nlink == 1, "original_hardlink_refused")
        digest = hashlib.sha256()
        with os.fdopen(fd, "rb", closefd=False) as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                digest.update(chunk)

        def stable(metadata):
            return (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
                metadata.st_uid,
                metadata.st_gid,
                metadata.st_nlink,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            )

        require(stable(os.fstat(fd)) == stable(info), "file_changed_while_reading")
        require(stable(path.lstat()) == stable(info), "file_replaced_while_reading")
        result = {
            "sha256": digest.hexdigest(),
            "device": info.st_dev,
            "inode": info.st_ino,
            "uid": info.st_uid,
            "gid": info.st_gid,
            "mode": stat.S_IMODE(info.st_mode),
            "size": info.st_size,
        }
        if original:
            result.update(mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns)
        return result
    finally:
        os.close(fd)


def units_inactive() -> None:
    for name in UNITS:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                "--no-pager",
                name,
                "--property=LoadState,ActiveState,UnitFileState,MainPID",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        values = dict(
            line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
        )
        missing = values.get("LoadState") == "not-found"
        require(result.returncode == 0 or missing, "systemd_read_failed")
        require(
            values.get("LoadState") in {"loaded", "masked", "not-found"}
            and (not missing or name == UNITS[-1]),
            "unit_load_state_invalid",
        )
        require(
            values.get("ActiveState") == "inactive"
            and values.get("MainPID", "0") == "0"
            and values.get("UnitFileState", "") in {"disabled", "static", "masked", ""},
            "execution_unit_must_be_inactive_and_disabled",
        )


@contextmanager
def existing_lock(state: Path):
    private_directory(state)
    before = file_record(state / "operator.lock", original=True)
    fd = os.open(state / "operator.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(
            (info.st_dev, info.st_ino) == (before["device"], before["inode"]),
            "operator_lock_replaced",
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise AttachBlocked("operator_lock_busy") from error
        require(
            file_record(state / "operator.lock", original=True) == before,
            "operator_lock_changed",
        )
        yield
    finally:
        os.close(fd)


def audit_counts(state: Path) -> dict:
    # immutable=1 avoids journal creation; exact state inventory excludes WAL.
    connection = sqlite3.connect(
        (state / "audit.sqlite3").as_uri() + "?mode=ro&immutable=1", uri=True
    )
    try:
        connection.execute("PRAGMA trusted_schema=OFF")
        require(
            connection.execute("PRAGMA quick_check").fetchall() == [("ok",)],
            "audit_integrity_failed",
        )
        tables = connection.execute(
            "SELECT name,type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        require(
            set(tables)
            == {
                ("events", "table"),
                ("intents", "table"),
                ("intent_directions", "table"),
                ("order_observations", "table"),
            },
            "audit_schema_not_eligible",
        )
        counts = {
            name: connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            for name in ("events", "intents", "intent_directions", "order_observations")
        }
        require(
            all(counts[name] == 0 for name in counts if name != "events"),
            "existing_execution_history_refused",
        )
        require(
            connection.execute(
                "SELECT count(*) FROM events "
                "WHERE kind NOT IN ('blocked','configuration_blocked')"
            ).fetchone()[0]
            == 0,
            "unreviewed_audit_event_refused",
        )
        return counts
    finally:
        connection.close()


def snapshot(state: Path, *, allowed_additions: tuple[str, ...] = ()) -> dict:
    info = private_directory(state)
    require(
        {p.name for p in state.iterdir()} == set(ORIGINALS) | set(allowed_additions),
        "unexpected_state_files",
    )
    files = {name: file_record(state / name, original=True) for name in ORIGINALS}
    require(
        all(record["gid"] == info.st_gid for record in files.values()),
        "private_file_group_mismatch",
    )
    config = load_config(state / "config.json")
    require(Path(config.state_dir) == state, "configured_state_directory_mismatch")
    require(
        config.enabled is False and config.mode == "paper", "disabled_paper_required"
    )
    require(config.evidence_profile == SCOPED_PROFILE, "scoped_paper_profile_required")
    require(bool(config.account_id), "bound_paper_account_required")
    config.binding()
    PaperCredentials.from_environment(load_secret_file(state / "paper.env"))
    counts = audit_counts(state)
    require(
        files == {name: file_record(state / name, original=True) for name in ORIGINALS},
        "state_changed_during_validation",
    )
    return {
        "state_dir": str(state),
        "directory": {
            "device": info.st_dev,
            "inode": info.st_ino,
            "uid": info.st_uid,
            "gid": info.st_gid,
            "mode": 0o700,
        },
        "files": files,
        "audit_counts": counts,
    }


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_private(path: Path, content: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def encoded(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def check(state: Path) -> dict:
    with existing_lock(state):
        units_inactive()
        baseline = snapshot(state)
        return {
            "status": "eligible_disabled",
            "baseline": baseline,
            "execution_enabled": False,
            "network_contacted": False,
        }


def prepare(state: Path, plan: Path) -> dict:
    parent = private_directory(plan.parent)
    require(plan.is_absolute() and plan.name not in {".", ".."}, "invalid_plan_path")
    require(plan != state and state not in plan.parents, "plan_must_be_outside_state")
    with existing_lock(state):
        units_inactive()
        baseline = snapshot(state)
        require(
            parent.st_dev == baseline["directory"]["device"], "plan_filesystem_mismatch"
        )
        # Exclusive reservation is intentionally retained after a prepare crash.
        # With no complete manifest it is never resumable and has not touched state.
        plan.mkdir(mode=0o700)
        sync_directory(plan.parent)
        config = load_config(state / "config.json")
        tokens = (secrets.token_urlsafe(32), secrets.token_urlsafe(32))
        authority = {
            "schema": "liquilens.agent-host-auth.v1",
            "agent_id": config.agent_id,
            "tokens": [
                {"sha256": hashlib.sha256(token.encode()).hexdigest(), "scopes": scopes}
                for token, scopes in zip(
                    tokens,
                    (["read", "assess"], ["read", "assess", "submit", "reconcile"]),
                    strict=True,
                )
            ],
        }
        BearerAuthority(authority, agent_id=config.agent_id)
        for name, token in zip(ADDITIONS[:2], tokens, strict=True):
            write_private(plan / name, (token + "\n").encode())
        write_private(plan / ADDITIONS[-1], encoded(authority))
        manifest = {
            "schema": SCHEMA,
            "baseline": baseline,
            "additions": {name: file_record(plan / name) for name in ADDITIONS},
        }
        require(snapshot(state) == baseline, "state_changed_before_plan_commit")
        write_private(plan / "manifest.json", encoded(manifest))
        sync_directory(plan)
        return {
            "status": "prepared_disabled",
            "plan_dir": str(plan),
            "manifest_sha256": file_record(plan / "manifest.json")["sha256"],
            "execution_enabled": False,
            "network_contacted": False,
        }


def apply(state: Path, plan: Path, manifest_sha256: str) -> dict:
    private_directory(plan)
    require(
        {p.name for p in plan.iterdir()} == set(ADDITIONS) | {"manifest.json"},
        "unexpected_plan_files",
    )
    require(
        file_record(plan / "manifest.json")["sha256"] == manifest_sha256,
        "plan_manifest_digest_mismatch",
    )
    manifest = strict_json((plan / "manifest.json").read_bytes())
    require(
        isinstance(manifest, dict)
        and set(manifest) == {"schema", "baseline", "additions"}
        and manifest["schema"] == SCHEMA,
        "plan_manifest_invalid",
    )
    require(set(manifest["additions"]) == set(ADDITIONS), "plan_additions_invalid")
    with existing_lock(state):
        units_inactive()
        for name in ADDITIONS:
            require(
                file_record(plan / name) == manifest["additions"][name],
                "plan_file_changed",
            )
        tokens = [(plan / name).read_text().strip() for name in ADDITIONS[:2]]
        config = load_config(state / "config.json")
        authority = BearerAuthority(
            strict_json((plan / "agent-auth.json").read_bytes()),
            agent_id=config.agent_id,
        )
        for token, scopes in zip(
            tokens,
            (
                frozenset({"read", "assess"}),
                frozenset({"read", "assess", "submit", "reconcile"}),
            ),
            strict=True,
        ):
            require(
                authority.authenticate(["Bearer " + token]) == scopes,
                "plan_token_scope_mismatch",
            )

        def validate() -> list[str]:
            published = [name for name in ADDITIONS if os.path.lexists(state / name)]
            for name in published:
                require(
                    file_record(state / name) == manifest["additions"][name],
                    "unowned_existing_auth_file",
                )
            require(
                snapshot(state, allowed_additions=tuple(published))
                == manifest["baseline"],
                "state_changed_since_prepare",
            )
            return published

        published = validate()
        for name in ADDITIONS:
            if name not in published:
                units_inactive()
                validate()
                # link() is atomic and refuses every existing destination.
                os.link(plan / name, state / name, follow_symlinks=False)
                sync_directory(state)
        validate()
        return {
            "status": "attached_disabled",
            "plan_dir": str(plan),
            "manifest_sha256": manifest_sha256,
            "added_files": list(ADDITIONS),
            "original_state_preserved": True,
            "execution_enabled": False,
            "network_contacted": False,
            "journals_initialized": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "prepare", "apply"))
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--plan-dir", type=Path)
    parser.add_argument("--manifest-sha256")
    args = parser.parse_args()
    try:
        if args.command == "check":
            result = check(args.state_dir)
        elif args.command == "prepare":
            require(args.plan_dir is not None, "plan_directory_required")
            result = prepare(args.state_dir, args.plan_dir)
        else:
            require(
                args.plan_dir is not None and args.manifest_sha256 is not None,
                "prepared_manifest_digest_required",
            )
            result = apply(args.state_dir, args.plan_dir, args.manifest_sha256)
        print(json.dumps(result))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(error)
                    if isinstance(error, AttachBlocked)
                    else "attach_validation_or_io_failed",
                    "execution_enabled": False,
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
