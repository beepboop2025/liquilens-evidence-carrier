#!/usr/bin/env python3
"""Capture or atomically switch an inactive, unconfigured paper installation."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path

ROOT = Path("/opt/liquilens-trading-copilot")
STATE = Path("/var/lib/liquilens-trading-copilot")
UNITS = (
    "liquilens-paper-copilot.service",
    "liquilens-paper-copilot.timer",
    "liquilens-agent-host.service",
)


class RolloutBlocked(ValueError):
    """A fixed diagnostic code, without configuration or secret contents."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise RolloutBlocked(reason)


def digest_tree(path: Path, *, allow_links: bool = False) -> str:
    """Fingerprint content and ownership; exclude access/modification clocks."""
    digest = hashlib.sha256()
    for entry in [path, *sorted(path.rglob("*"))]:
        metadata = entry.lstat()
        digest.update(
            json.dumps(
                [
                    str(entry.relative_to(path)),
                    metadata.st_mode,
                    metadata.st_uid,
                    metadata.st_gid,
                ],
                separators=(",", ":"),
            ).encode()
        )
        if stat.S_ISLNK(metadata.st_mode):
            require(allow_links, "private_state_contains_symlink")
            digest.update(os.readlink(entry).encode())
        elif stat.S_ISREG(metadata.st_mode):
            with entry.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
        else:
            require(stat.S_ISDIR(metadata.st_mode), "unsupported_file_type")
        digest.update(b"\0")
    return digest.hexdigest()


def unit_state(name: str) -> dict:
    properties = (
        "LoadState",
        "ActiveState",
        "SubState",
        "UnitFileState",
        "MainPID",
        "ExecMainPID",
        "FragmentPath",
        "DropInPaths",
        "ExecStart",
        "InvocationID",
    )
    command = ["systemctl", "show", "--no-pager", name]
    command.extend("--property=" + prop for prop in properties)
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    values = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    missing = values.get("LoadState") == "not-found"
    require(result.returncode == 0 or missing, "systemd_read_failed")
    require(missing or values.get("LoadState") == "loaded", "unit_not_loaded")
    require(not missing or name == UNITS[2], "paper_unit_missing")
    require(values.get("ActiveState") == "inactive", "unit_not_inactive")
    require(
        values.get("UnitFileState", "") in {"disabled", "static", "masked", ""},
        "unit_not_disabled",
    )
    require(values.get("MainPID", "0") == "0", "unit_has_process")
    files = [values.get("FragmentPath", ""), *values.get("DropInPaths", "").split()]
    values["definition_sha256"] = {
        file: hashlib.sha256(Path(file).read_bytes()).hexdigest()
        for file in files
        if file
    }
    return values


def snapshot() -> dict:
    require(ROOT.is_dir() and not ROOT.is_symlink(), "install_root_invalid")
    require(STATE.is_dir() and not STATE.is_symlink(), "private_state_invalid")
    require(stat.S_IMODE(STATE.stat().st_mode) == 0o700, "private_state_mode_invalid")
    for name in ("config.json", "paper.env"):
        metadata = (STATE / name).lstat()
        require(
            stat.S_ISREG(metadata.st_mode) and stat.S_IMODE(metadata.st_mode) == 0o600,
            "private_file_mode_invalid",
        )
        require(metadata.st_uid == STATE.stat().st_uid, "private_file_owner_invalid")
    config = json.loads((STATE / "config.json").read_text())
    require(config.get("enabled") is False, "paper_execution_enabled")
    require(config.get("account_id") in (None, ""), "paper_account_already_configured")
    require(config.get("mode") == "paper", "wrong_account_mode")
    values = {}
    for line in (STATE / "paper.env").read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        require(key not in values, "duplicate_secret_field")
        values[key] = value
    require(values.get("ALPACA_PAPER_API_KEY") == "", "paper_key_already_configured")
    require(
        values.get("ALPACA_PAPER_SECRET_KEY") == "", "paper_secret_already_configured"
    )
    require(bool(values.get("COPILOT_PAPER_HMAC_KEY")), "paper_hmac_missing")
    current = ROOT / "current"
    require(current.is_symlink(), "current_must_be_symlink")
    target = current.resolve(strict=True)
    require(target.parent == ROOT / "releases", "current_outside_releases")
    return {
        "schema": "liquilens.disabled-rollout-state.v1",
        "current_link": os.readlink(current),
        "current_resolved": str(target),
        "release_sha256": digest_tree(target, allow_links=True),
        "private_state_sha256": digest_tree(STATE),
        "units": {name: unit_state(name) for name in UNITS},
    }


def save(path: Path, value: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    # Persist the entry as well as its bytes, including a newly made receipt
    # directory. Recovery intent must survive before current can be replaced.
    for parent in (path.parent, *path.parent.parents):
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def read_receipt(path: Path) -> dict:
    metadata = path.lstat()
    require(
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) == 0o600,
        "receipt_permissions_invalid",
    )
    return json.loads(path.read_text())


def switch(expected: dict, target: Path) -> dict:
    require(snapshot() == expected, "installation_changed_since_receipt")
    require(
        target.is_dir()
        and target.resolve() == target
        and target.parent == ROOT / "releases",
        "target_release_invalid",
    )
    target_hash = digest_tree(target, allow_links=True)
    temporary = ROOT / (".current-" + str(os.getpid()))
    require(not os.path.lexists(temporary), "temporary_link_exists")
    os.symlink(target, temporary)
    try:
        # Recheck after potentially expensive file hashing, immediately before replace.
        require(snapshot() == expected, "installation_changed_before_switch")
        os.replace(temporary, ROOT / "current")
        directory = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
    observed = snapshot()
    require(observed["release_sha256"] == target_hash, "target_changed_during_switch")
    require(
        observed["private_state_sha256"] == expected["private_state_sha256"]
        and observed["units"] == expected["units"],
        "state_changed_during_switch",
    )
    return {"before": expected, "after": observed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("capture", "check", "switch", "rollback"))
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--source-sha")
    args = parser.parse_args()
    try:
        require(os.getuid() == 0, "root_required")
        lock = os.open(
            ROOT / ".disabled-rollout.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(lock, "w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.command == "capture":
                save(args.receipt, snapshot())
            else:
                receipt = read_receipt(args.receipt)
                if args.command == "check":
                    require(snapshot() == receipt, "installation_changed_since_receipt")
                else:
                    require(
                        args.result is not None and not os.path.lexists(args.result),
                        "new_result_receipt_required",
                    )
                    if args.command == "rollback":
                        target = Path(receipt["before"]["current_resolved"])
                        require(
                            digest_tree(target, allow_links=True)
                            == receipt["before"]["release_sha256"],
                            "previous_release_changed",
                        )
                        expected = receipt["after"]
                    else:
                        require(
                            bool(re.fullmatch(r"[0-9a-f]{40}", args.source_sha or "")),
                            "exact_source_sha_required",
                        )
                        target = ROOT / "releases" / args.source_sha
                        head = subprocess.check_output(
                            ["git", "-C", str(target), "rev-parse", "HEAD"], text=True
                        ).strip()
                        require(head == args.source_sha, "source_head_mismatch")
                        dirty = subprocess.check_output(
                            [
                                "git",
                                "-C",
                                str(target),
                                "status",
                                "--porcelain",
                                "--untracked-files=all",
                            ],
                            text=True,
                        )
                        require(not dirty, "source_checkout_dirty")
                        require(
                            (
                                target / "integrations/trading-copilot/.venv/bin/"
                                "liquilens-trading-copilot"
                            ).is_file(),
                            "locked_install_missing",
                        )
                        expected = receipt
                    # Retain recovery identity before the atomic side effect. If a
                    # later verification fails, do not hide it by reverting state.
                    save(
                        args.result.with_name(args.result.name + ".intent"),
                        {
                            "operation": args.command,
                            "before": expected,
                            "target": str(target),
                        },
                    )
                    result = switch(expected, target)
                    save(args.result, result)
        print(
            json.dumps(
                {
                    "status": "verified",
                    "operation": args.command,
                    "service_started": False,
                    "execution_activated": False,
                }
            )
        )
        return 0
    except Exception as error:
        # File or process errors can contain private paths or environment contents.
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_type": type(error).__name__,
                    "reason": str(error)
                    if isinstance(error, RolloutBlocked)
                    else "unexpected_failure",
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
