#!/usr/bin/env python3
"""Root-only, disabled-paper-host snapshots and isolated encrypted restore checks.

No broker or source API is called. Recovery only resumes the same previously
active, unchanged, disabled host; it never initializes an account or enables a unit.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import hmac
import json
import os
import pwd
import re
import secrets
import shlex
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import ExitStack, closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

HOST = "liquilens-agent-host.service"
UNITS = (
    HOST,
    "liquilens-execution-observer.service",
    "liquilens-execution-observer.timer",
    "liquilens-source-access-renew.service",
    "liquilens-source-access-renew.timer",
    "undertow-mcp.service",
    "liquilens-paper-backup.service",
    "liquilens-paper-backup.timer",
    "liquilens-paper-backup-recover.service",
)
RETIRED = ("liquilens-paper-copilot.service", "liquilens-paper-copilot.timer")
STATE = "/var/lib/liquilens-trading-copilot"
TOKENS = "/etc/liquilens-source-access"
REGISTRY = "/etc/undertow-mcp/service-identities.json"
WORK = "/var/lib/liquilens-paper-backup"
PASSPHRASE = "/etc/liquilens-paper-backup/passphrase"
SCHEMA = "liquilens.disabled-paper-backup.v1"
ROOT_UID = 0
MAX_FILE = 128 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_CAPTURE_BYTES = MAX_TOTAL - 16 * 1024 * 1024
ENV_KEYS = ("TYPE", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY", "ENDPOINT", "REGION")


class BackupRefused(ValueError):
    """Fixed diagnostic codes only; secret-bearing subprocess output is hidden."""


def require(value, code: str) -> None:
    if not value:
        raise BackupRefused(code)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=pairs)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def real_path(path: Path) -> None:
    require(path.is_absolute() and ".." not in path.parts, "unsafe_path")
    for parent in path.parents:
        require(stat.S_ISDIR(parent.lstat().st_mode), "symlink_ancestor")


def private_dir(path: Path, owner: int | None = None) -> None:
    owner = ROOT_UID if owner is None else owner
    real_path(path)
    info = path.lstat()
    require(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == owner
        and stat.S_IMODE(info.st_mode) == 0o700,
        "private_directory_required",
    )


def read_file(
    path: Path,
    *,
    owner: int | None = None,
    modes: tuple[int, ...] = (0o600,),
    links: tuple[int, ...] = (1,),
):
    real_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_nlink in links
            and stat.S_IMODE(info.st_mode) in modes
            and info.st_size <= MAX_FILE
            and (owner is None or info.st_uid == owner),
            "unsafe_source_file",
        )
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(MAX_FILE + 1)
        after = os.fstat(fd)

        def stable(value):
            return (
                value.st_dev,
                value.st_ino,
                value.st_uid,
                value.st_gid,
                value.st_mode,
                value.st_nlink,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
            )

        require(
            stable(info) == stable(after) == stable(path.lstat())
            and len(raw) == info.st_size,
            "source_changed_during_read",
        )
        return raw, {
            "uid": info.st_uid,
            "gid": info.st_gid,
            "mode": stat.S_IMODE(info.st_mode),
            "mtime_ns": info.st_mtime_ns,
        }
    finally:
        os.close(fd)


def durable_json(path: Path, value: dict) -> None:
    private_dir(path.parent)
    temporary = path.with_name(".write-" + secrets.token_hex(12))
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write((json.dumps(value, sort_keys=True) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def existing_lock(path: Path, owner: int):
    read_file(path, owner=owner)
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        require(os.fstat(fd) == path.lstat(), "lock_inode_changed")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(os.fstat(fd) == path.lstat(), "lock_inode_changed")
        yield
    finally:
        os.close(fd)


def command(args: list[str], *, env: dict | None = None, output: Path | None = None):
    child_env = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        **(env or {}),
    }
    if output is None:
        result = subprocess.run(
            args, env=child_env, capture_output=True, timeout=900, check=False
        )
    else:
        with output.open("xb") as stream:
            result = subprocess.run(
                args,
                env=child_env,
                stdout=stream,
                stderr=subprocess.PIPE,
                timeout=900,
                check=False,
            )
    require(result.returncode == 0, "command_failed")
    return result.stdout or b""


def unit_info(name: str) -> dict:
    raw = command(
        [
            "systemctl",
            "show",
            name,
            "--no-pager",
            "--property=ActiveState,UnitFileState,FragmentPath,DropInPaths,ExecStart,MainPID",
        ]
    )
    return dict(line.split("=", 1) for line in raw.decode().splitlines() if "=" in line)


def database_info(path: Path) -> dict:
    # The isolated file has no WAL and no writers. immutable avoids creating SHM.
    with closing(
        sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    ) as db:
        require(
            db.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
            "sqlite_integrity_failed",
        )
        schema = db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        ).fetchall()
        counts = {}
        for kind, name, _table, _sql in schema:
            if kind == "table":
                quoted = '"' + name.replace('"', '""') + '"'
                counts[name] = db.execute(f"SELECT count(*) FROM {quoted}").fetchone()[
                    0
                ]
        return {"schema_sha256": sha(json.dumps(schema).encode()), "rows": counts}


def validate_account(snapshot: Path, captured_at: int) -> dict:
    config = strict_json((snapshot / "state/config.json").read_bytes())
    require(
        config.get("enabled") is False
        and config.get("mode") == "paper"
        and config.get("state_dir") == STATE
        and bool(config.get("agent_id")),
        "disabled_paper_configuration_required",
    )
    auth = strict_json((snapshot / "state/agent-auth.json").read_bytes())
    require(
        auth.get("schema") == "liquilens.agent-host-auth.v1"
        and auth.get("agent_id") == config["agent_id"],
        "auth_identity_mismatch",
    )
    expected_scopes = ({"read", "assess"}, {"read", "assess", "submit", "reconcile"})
    for filename, scopes in zip(
        ("agent-read.token", "agent-execution.token"), expected_scopes, strict=True
    ):
        token = (snapshot / "state" / filename).read_bytes().strip()
        require(
            any(
                row.get("sha256") == sha(token) and set(row.get("scopes", [])) == scopes
                for row in auth["tokens"]
            ),
            "auth_token_mismatch",
        )
    registry = strict_json((snapshot / "source/service-identities.json").read_bytes())
    require(
        registry.get("schema") == "undertow.operator-service-identities.v1",
        "source_registry_invalid",
    )
    values = {}
    for line in (snapshot / "source/undertow-mcp.env").read_text().splitlines():
        if line.strip().startswith("UNDERTOW_WEB_SECRET="):
            require("secret" not in values, "duplicate_signing_secret")
            fields = shlex.split(line.split("=", 1)[1])
            require(len(fields) == 1, "invalid_signing_secret")
            values["secret"] = fields[0]
    require(bool(values.get("secret")), "missing_signing_secret")
    for service, filename in (
        ("liquilens-execution-observer", "observer.token"),
        ("liquilens-paper-host", "paper-host.token"),
    ):
        token = (snapshot / "source" / filename).read_text().strip()
        part, signature = token.split(".")
        claims = strict_json(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        row = registry["services"][service]
        expected = hmac.new(
            values["secret"].encode(), part.encode(), hashlib.sha256
        ).hexdigest()
        require(
            hmac.compare_digest(signature, expected)
            and row["token_sha256"] == sha(token.encode())
            and claims["sub"] == "service:" + service
            and claims["name"] == service
            and claims["aud"] == "mcp"
            and claims["iat"] == row["issued_at"]
            and claims["exp"] == row["expires_at"]
            and row["issued_at"] <= captured_at < row["expires_at"]
            and row["state"] == "active"
            and row["scopes"] == ["trade_safety_exit_context"]
            and row["traffic_class"] == "operator_verification",
            "source_binding_invalid",
        )
    return {
        "disabled_paper": True,
        "auth_bound": True,
        "source_bindings_valid_at_capture": True,
    }


class HostBackup:
    def __init__(self, root: Path = Path("/"), *, service_uid: int | None = None):
        self.root = root
        self.owner = (
            service_uid
            if service_uid is not None
            else pwd.getpwnam("liquilens-copilot").pw_uid
        )
        self.work = self.path(WORK)
        self.intent_path = self.work / "intent.json"
        self.backup_release = Path(__file__).resolve().parents[3]

    def path(self, value: str) -> Path:
        return self.root / value.lstrip("/")

    @contextmanager
    def locks(self):
        private_dir(self.work)
        with ExitStack() as stack:
            for path in (
                self.path(TOKENS) / ".renew.lock",
                self.path(REGISTRY).parent / ".service-identities.lock",
            ):
                stack.enter_context(existing_lock(path, ROOT_UID))
            yield

    def guards(self) -> dict:
        private_dir(self.path(STATE), self.owner)
        raw, _ = read_file(self.path(STATE) / "config.json", owner=self.owner)
        config = strict_json(raw)
        require(
            config.get("enabled") is False
            and config.get("mode") == "paper"
            and config.get("state_dir") == STATE,
            "disabled_paper_configuration_required",
        )
        unit = unit_info(HOST)
        require(
            re.search(r"\bliquilens-agent-host serve ", unit["ExecStart"])
            and re.search(r" --require-disabled(?:\s|;|$)", unit["ExecStart"]),
            "disabled_unit_required",
        )
        files = [unit["FragmentPath"], *unit["DropInPaths"].split()]
        require(
            files and files[0] == "/etc/systemd/system/" + HOST, "unexpected_host_unit"
        )
        digest = {}
        for path in files:
            body, _ = read_file(self.path(path), owner=ROOT_UID, modes=(0o644, 0o600))
            digest[path] = sha(body)
        halt = self.path(STATE) / "STOP"
        halt_digest = (
            sha(read_file(halt, owner=self.owner)[0])
            if halt.exists() or halt.is_symlink()
            else None
        )
        for name in RETIRED:
            path = self.path("/etc/systemd/system/" + name)
            require(
                path.is_symlink() and os.readlink(path) == "/dev/null",
                "retired_unit_not_masked",
            )
        return {
            "config_sha256": sha(raw),
            "unit_files": digest,
            "halt_sha256": halt_digest,
        }

    def read_intent(self) -> dict | None:
        if not self.intent_path.exists():
            return None
        value = strict_json(read_file(self.intent_path, owner=ROOT_UID)[0])
        require(
            set(value) == {"schema", "run_id", "previously_active", "guards", "phase"}
            and value["schema"] == SCHEMA
            and re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[a-f0-9]{16}", value["run_id"])
            and type(value["previously_active"]) is bool
            and value["phase"] in {"quiescing", "resumed", "complete"},
            "invalid_recovery_intent",
        )
        return value

    def resume(self, intent: dict) -> None:
        if intent["phase"] != "quiescing":
            return
        require(self.guards() == intent["guards"], "recovery_guard_changed")
        state = unit_info(HOST)["ActiveState"]
        require(state in {"active", "inactive"}, "host_lifecycle_unstable")
        if intent["previously_active"] and state == "inactive":
            command(["systemctl", "start", HOST])
        if intent["previously_active"]:
            self.wait_ready(intent["guards"])
        intent["phase"] = "resumed"
        durable_json(self.intent_path, intent)

    def wait_ready(self, guards: dict) -> None:
        """Prove lifespan startup locally; Type=simple active is insufficient."""
        config = strict_json(
            read_file(self.path(STATE) / "config.json", owner=self.owner)[0]
        )
        token = read_file(
            self.path(STATE) / "agent-read.token", owner=self.owner, links=(1, 2)
        )[0].strip()
        require(
            re.fullmatch(rb"[A-Za-z0-9_-]{32,512}", token), "invalid_readiness_token"
        )
        ports = re.findall(r" --port ([0-9]+)(?=\s|;|$)", unit_info(HOST)["ExecStart"])
        require(len(ports) <= 1, "unexpected_host_port")
        port = int(ports[0]) if ports else 8766
        require(1024 <= port <= 65535, "unexpected_host_port")
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect()
        )
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/capabilities",
            headers={"Authorization": "Bearer " + token.decode("ascii")},
            method="GET",
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            require(self.guards() == guards, "recovery_guard_changed")
            before = unit_info(HOST)
            require(before["ActiveState"] == "active", "host_resume_unverified")
            try:
                with opener.open(request, timeout=1) as response:
                    require(response.status == 200, "host_resume_unverified")
                    body = strict_json(response.read(65537))
            except (OSError, ValueError):
                time.sleep(0.25)
                continue
            require(
                isinstance(body, dict)
                and body.get("schema") == "liquilens.agent-host-capabilities.v1"
                and body.get("agent_id") == config["agent_id"]
                and body.get("mode") == "paper"
                and body.get("execution_enabled") is False
                and body.get("live_execution_supported") is False,
                "host_readiness_identity_mismatch",
            )
            after = unit_info(HOST)
            require(
                after["ActiveState"] == "active"
                and before["MainPID"] == after["MainPID"]
                and int(after["MainPID"]) > 0
                and self.guards() == guards,
                "host_resume_unverified",
            )
            return
        raise BackupRefused("host_resume_unverified")

    def recover(self) -> dict:
        with self.locks():
            intent = self.read_intent()
            if intent:
                self.resume(intent)
                if intent["phase"] == "resumed":
                    run = self.work / intent["run_id"]
                    if run.exists():
                        private_dir(run)
                        shutil.rmtree(run)
                    intent["phase"] = "complete"
                    durable_json(self.intent_path, intent)
        return {"status": "reconciled", "execution_enabled": False}

    def capture(self, snapshot: Path) -> dict:
        captured_at = int(time.time())
        manifest = {
            "schema": SCHEMA,
            "captured_at": captured_at,
            "files": {},
            "databases": {},
            "units": {},
            "retired_masks": dict.fromkeys(RETIRED, "/dev/null"),
            "releases": {},
            "release_links": {},
        }

        def put(label, raw, metadata):
            require(
                len(manifest["files"]) < 9999
                and sum(row["bytes"] for row in manifest["files"].values()) + len(raw)
                <= MAX_CAPTURE_BYTES,
                "snapshot_limits_exceeded",
            )
            target = snapshot / label
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with target.open("xb") as output:
                output.write(raw)
            target.chmod(0o600)
            manifest["files"][label] = {
                **metadata,
                "sha256": sha(raw),
                "bytes": len(raw),
            }

        for entry in sorted(self.path(STATE).iterdir()):
            if entry.name.endswith(("-wal", "-shm")):
                base = entry.name.removesuffix("-wal").removesuffix("-shm")
                require(
                    base.endswith(".sqlite3") and (entry.parent / base).is_file(),
                    "unexpected_sqlite_sidecar",
                )
                # The staged SQLite backup includes committed WAL data. These
                # original coordination files remain untouched and are never
                # archived as restored database content.
                if entry.exists():
                    info = entry.lstat()
                    require(
                        stat.S_ISREG(info.st_mode)
                        and info.st_uid == self.owner
                        and stat.S_IMODE(info.st_mode) == 0o600
                        and info.st_nlink == 1,
                        "unsafe_sqlite_sidecar",
                    )
                continue
            require(
                entry.is_file() and not entry.is_symlink(), "unexpected_state_entry"
            )
            raw, metadata = read_file(entry, owner=self.owner, links=(1, 2))
            if entry.name.endswith(".sqlite3"):
                target = snapshot / "state" / entry.name
                target.parent.mkdir(mode=0o700, exist_ok=True)
                # Even a read-only SQLite connection can create WAL/SHM files.
                # Copy the settled files while holding the account lock; SQLite
                # may recover/checkpoint only these disposable private copies.
                with tempfile.TemporaryDirectory(dir=snapshot) as staging:
                    copied = Path(staging) / entry.name
                    copied.write_bytes(raw)
                    copied.chmod(0o600)
                    for suffix in ("-wal", "-shm"):
                        sidecar = Path(str(entry) + suffix)
                        if sidecar.exists() or sidecar.is_symlink():
                            contents, _ = read_file(sidecar, owner=self.owner)
                            staged = Path(str(copied) + suffix)
                            staged.write_bytes(contents)
                            staged.chmod(0o600)
                    with (
                        closing(sqlite3.connect(copied)) as source,
                        closing(sqlite3.connect(target)) as dest,
                    ):
                        deadline = time.monotonic() + 30

                        def progress(_status, _remaining, _total, *, until=deadline):
                            require(time.monotonic() < until, "sqlite_snapshot_timeout")

                        source.backup(dest, pages=128, progress=progress, sleep=0.01)
                require(target.stat().st_size <= MAX_FILE, "source_file_too_large")
                raw = target.read_bytes()
                target.unlink()
                put("state/" + entry.name, raw, metadata)
                manifest["databases"]["state/" + entry.name] = database_info(target)
            else:
                put("state/" + entry.name, raw, metadata)
        require(
            {"state/audit.sqlite3", "state/alpaca-submissions.sqlite3"}
            <= manifest["databases"].keys(),
            "required_journals_missing",
        )
        for entry in self.path(TOKENS).iterdir():
            require(
                entry.name in {".renew.lock", "observer.token", "paper-host.token"},
                "source_renewal_not_settled",
            )
        sources = {
            "observer.token": self.path(TOKENS) / "observer.token",
            "paper-host.token": self.path(TOKENS) / "paper-host.token",
            "service-identities.json": self.path(REGISTRY),
            "undertow-mcp.env": self.path("/etc/undertow-mcp.env"),
        }
        for name, source in sources.items():
            raw, metadata = read_file(source, owner=ROOT_UID, modes=(0o600, 0o640))
            put("source/" + name, raw, metadata)
        attach = self.path("/var/lib/liquilens-agent-attach")
        private_dir(attach, self.owner)
        for directory, dirs, files in os.walk(attach, followlinks=False):
            private_dir(Path(directory), self.owner)
            for name in dirs:
                require(
                    not (Path(directory) / name).is_symlink(), "unsafe_attach_directory"
                )
            for name in files:
                path = Path(directory) / name
                raw, metadata = read_file(path, owner=self.owner, links=(1, 2))
                put("attach/" + str(path.relative_to(attach)), raw, metadata)
        receipts = self.path("/opt/liquilens-agent-host/receipts")
        private_dir(receipts)
        for directory, dirs, files in os.walk(receipts, followlinks=False):
            private_dir(Path(directory))
            for name in dirs:
                require(
                    not (Path(directory) / name).is_symlink(),
                    "unsafe_receipt_directory",
                )
            for name in files:
                path = Path(directory) / name
                raw, metadata = read_file(path, owner=ROOT_UID, modes=(0o600, 0o644))
                put("receipts/" + str(path.relative_to(receipts)), raw, metadata)
        wrapper = self.path("/usr/local/bin/liquilens-paper-mcp")
        raw, metadata = read_file(wrapper, owner=ROOT_UID, modes=(0o755,))
        put("bin/liquilens-paper-mcp", raw, metadata)
        for name, source in (
            ("renew.lock", self.path(TOKENS) / ".renew.lock"),
            ("registry.lock", self.path(REGISTRY).parent / ".service-identities.lock"),
        ):
            raw, metadata = read_file(source, owner=ROOT_UID)
            put("source/" + name, raw, metadata)
        for unit in UNITS:
            info = unit_info(unit)
            manifest["units"][unit] = info
            for name in [info["FragmentPath"], *info["DropInPaths"].split()]:
                require(name.startswith("/etc/systemd/system/"), "unexpected_unit_file")
                label = "units/" + name.removeprefix("/etc/systemd/system/")
                if label not in manifest["files"]:
                    raw, metadata = read_file(
                        self.path(name), owner=ROOT_UID, modes=(0o644, 0o600)
                    )
                    put(label, raw, metadata)
        host_command = manifest["units"][HOST]["ExecStart"]
        match = re.search(
            r"path=([^ ;]+?)/integrations/trading-copilot/"
            r"\.venv/bin/liquilens-agent-host",
            host_command,
        )
        require(match is not None, "unexpected_host_release")
        releases = {
            "carrier": self.path(match[1]),
            "undertow": self.path("/opt/liquilens-undertow"),
            "backup": self.backup_release,
        }
        for link_name in (
            "/opt/liquilens-agent-host/current",
            "/opt/liquilens-execution-observer/current",
        ):
            link = self.path(link_name)
            require(link.is_symlink(), "release_link_missing")
            target = os.readlink(link)
            require(
                re.fullmatch(
                    r"/opt/liquilens-execution-observer/releases/[a-f0-9]{40}", target
                ),
                "release_link_invalid",
            )
            manifest["release_links"][link_name] = target
            if link_name.endswith("observer/current"):
                releases["observer"] = self.path(target)
        for role, release in releases.items():
            release_path = (
                str(release)
                if role == "backup"
                else "/" + str(release.relative_to(self.root))
            )
            commit = (
                command(["git", "-C", str(release), "rev-parse", "HEAD"])
                .decode()
                .strip()
            )
            require(re.fullmatch(r"[a-f0-9]{40}", commit), "invalid_release_identity")
            require(
                not command(
                    [
                        "git",
                        "-C",
                        str(release),
                        "status",
                        "--porcelain",
                        "--untracked-files=no",
                    ]
                ),
                "release_has_tracked_edits",
            )
            archive = command(
                ["git", "-C", str(release), "archive", "--format=tar", commit]
            )
            require(len(archive) <= MAX_FILE, "source_archive_too_large")
            put(
                "releases/" + role + ".tar",
                archive,
                {"mode": 0o600, "uid": ROOT_UID, "gid": 0},
            )
            manifest["releases"][role] = {
                "commit": commit,
                "original_path": release_path,
            }
        manifest["offline_validation"] = validate_account(snapshot, captured_at)
        require(
            sum(row["bytes"] for row in manifest["files"].values())
            + len(json.dumps(manifest).encode())
            + 1
            <= MAX_TOTAL,
            "snapshot_limits_exceeded",
        )
        durable_json(snapshot / "MANIFEST.json", manifest)
        return manifest

    def snapshot(self, snapshot: Path, run_id: str) -> dict:
        with self.locks():
            old = self.read_intent()
            if old:
                self.resume(old)
            guards = self.guards()
            state = unit_info(HOST)["ActiveState"]
            require(state in {"active", "inactive"}, "host_lifecycle_unstable")
            intent = {
                "schema": SCHEMA,
                "run_id": run_id,
                "previously_active": state == "active",
                "guards": guards,
                "phase": "quiescing",
            }
            durable_json(self.intent_path, intent)
            try:
                if intent["previously_active"]:
                    command(["systemctl", "stop", HOST])
                require(
                    unit_info(HOST)["ActiveState"] == "inactive", "host_not_quiescent"
                )
                with existing_lock(self.path(STATE) / "operator.lock", self.owner):
                    require(self.guards() == guards, "capture_guard_changed")
                    manifest = self.capture(snapshot)
                    require(self.guards() == guards, "capture_guard_changed")
                return manifest
            finally:
                self.resume(intent)


def verify_snapshot(snapshot: Path) -> dict:
    manifest = strict_json((snapshot / "MANIFEST.json").read_bytes())
    require(
        manifest["schema"] == SCHEMA
        and manifest["retired_masks"] == dict.fromkeys(RETIRED, "/dev/null"),
        "invalid_snapshot_manifest",
    )
    actual = {str(p.relative_to(snapshot)) for p in snapshot.rglob("*") if p.is_file()}
    require(
        not any(p.is_symlink() for p in snapshot.rglob("*")), "snapshot_symlink_refused"
    )
    require(
        actual == set(manifest["files"]) | {"MANIFEST.json"},
        "snapshot_file_set_mismatch",
    )
    for name, record in manifest["files"].items():
        require(
            set(record)
            in (
                {"uid", "gid", "mode", "sha256", "bytes"},
                {"uid", "gid", "mode", "sha256", "bytes", "mtime_ns"},
            )
            and all(
                type(record[k]) is int and record[k] >= 0
                for k in ("uid", "gid", "mode", "bytes")
            )
            and record["mode"] in {0o600, 0o640, 0o644, 0o755}
            and re.fullmatch(r"[a-f0-9]{64}", record["sha256"]),
            "invalid_portable_metadata",
        )
        path = PurePosixPath(name)
        require(
            not path.is_absolute() and ".." not in path.parts, "unsafe_archive_member"
        )
        raw, _ = read_file(snapshot / name, owner=ROOT_UID)
        require(
            sha(raw) == record["sha256"] and len(raw) == record["bytes"],
            "restored_content_mismatch",
        )
    for name, expected in manifest["databases"].items():
        require(
            name in manifest["files"] and name.endswith(".sqlite3"),
            "invalid_database_manifest",
        )
        require(
            database_info(snapshot / name) == expected, "restored_database_mismatch"
        )
    validate_account(snapshot, manifest["captured_at"])
    return {
        "verified": True,
        "files": len(manifest["files"]),
        "databases": len(manifest["databases"]),
        "execution_enabled": False,
        "broker_contacted": False,
    }


def extract_archive(archive: Path, destination: Path) -> None:
    private_dir(destination)
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        require(
            len(members) <= 10000 and sum(m.size for m in members) <= MAX_TOTAL,
            "archive_limits_exceeded",
        )
        seen = set()
        for member in members:
            path = PurePosixPath(member.name)
            require(
                member.isfile()
                and not path.is_absolute()
                and ".." not in path.parts
                and str(path) == member.name
                and member.name not in seen
                and member.size <= MAX_FILE,
                "unsafe_archive_member",
            )
            seen.add(member.name)
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with target.open("xb") as output, tar.extractfile(member) as source:
                shutil.copyfileobj(source, output)
            target.chmod(0o600)


def storage_environment() -> dict:
    env = {
        "RCLONE_CONFIG_ANCHOR_" + key: os.environ.get("RCLONE_CONFIG_ANCHOR_" + key, "")
        for key in ENV_KEYS
    }
    require(
        all(env.values()) and env["RCLONE_CONFIG_ANCHOR_TYPE"] == "s3",
        "anchor_configuration_missing",
    )
    require(
        re.fullmatch(r"https://[a-z0-9.-]+", env["RCLONE_CONFIG_ANCHOR_ENDPOINT"])
        and re.fullmatch(r"[A-Za-z0-9_-]+", env["RCLONE_CONFIG_ANCHOR_REGION"]),
        "invalid_anchor_endpoint",
    )
    # Pin private, S3-compatible behavior; do not inherit optional rclone policy.
    env["RCLONE_CONFIG_ANCHOR_PROVIDER"] = "Other"
    env["RCLONE_CONFIG_ANCHOR_ACL"] = "private"
    return env


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, _req, _fp, _code, _msg, _headers, _newurl):
        raise BackupRefused("storage_redirect_refused")


def s3_request(
    env: dict, bucket: str, key: str = "", *, bucket_lock=False
) -> tuple[dict, bytes]:
    """Bounded read-only SigV4 HEAD/GET; credentials never enter argv or logs."""
    endpoint = env["RCLONE_CONFIG_ANCHOR_ENDPOINT"].removeprefix("https://")
    region = env["RCLONE_CONFIG_ANCHOR_REGION"]
    host = bucket + "." + endpoint
    method, query = ("GET", "object-lock=") if bucket_lock else ("HEAD", "")
    path = "/" + urllib.parse.quote(key, safe="/-_.~")
    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    day = stamp[:8]
    payload = sha(b"")
    headers = {"host": host, "x-amz-content-sha256": payload, "x-amz-date": stamp}
    names = ";".join(sorted(headers))
    canonical = "\n".join(
        (
            method,
            path,
            query,
            "".join(k + ":" + headers[k] + "\n" for k in sorted(headers)),
            names,
            payload,
        )
    )
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(("AWS4-HMAC-SHA256", stamp, scope, sha(canonical.encode())))
    signing = ("AWS4" + env["RCLONE_CONFIG_ANCHOR_SECRET_ACCESS_KEY"]).encode()
    for value in (day, region, "s3", "aws4_request"):
        signing = hmac.new(signing, value.encode(), hashlib.sha256).digest()
    signature = hmac.new(signing, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = (
        "AWS4-HMAC-SHA256 Credential="
        + env["RCLONE_CONFIG_ANCHOR_ACCESS_KEY_ID"]
        + "/"
        + scope
        + ", SignedHeaders="
        + names
        + ", Signature="
        + signature
    )
    request = urllib.request.Request(
        "https://" + host + path + ("?" + query if query else ""),
        headers=headers,
        method=method,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=30) as response:
        require(response.status == 200, "storage_verification_failed")
        return {k.lower(): v for k, v in response.headers.items()}, response.read(65537)


def check_retention(headers: dict) -> None:
    require(
        headers.get("x-amz-object-lock-mode") == "COMPLIANCE", "object_not_immutable"
    )
    until = datetime.fromisoformat(
        headers.get("x-amz-object-lock-retain-until-date", "").replace("Z", "+00:00")
    )
    require(
        until >= datetime.now(UTC) + timedelta(days=89), "object_retention_too_short"
    )


def backup(host: HostBackup) -> dict:
    host.recover()
    private_dir(host.work)
    require(
        shutil.disk_usage(host.work).free >= 7 * MAX_TOTAL, "backup_space_insufficient"
    )
    env = storage_environment()
    bucket = os.environ.get("PAPER_BACKUP_BUCKET", "")
    require(
        re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket),
        "invalid_backup_bucket",
    )
    passphrase = host.path(PASSPHRASE)
    raw, _ = read_file(passphrase, owner=ROOT_UID, modes=(0o400, 0o600))
    require(
        raw.endswith(b"\n")
        and raw.count(b"\n") == 1
        and 32 <= len(raw) - 1 <= 4096
        and b"\0" not in raw
        and b"\r" not in raw,
        "invalid_backup_passphrase",
    )
    _, policy = s3_request(env, bucket, bucket_lock=True)
    document = ET.fromstring(policy)
    require(
        document.findtext(".//{*}ObjectLockEnabled") == "Enabled"
        and document.findtext(".//{*}Mode") == "COMPLIANCE"
        and int(document.findtext(".//{*}Days", "0")) >= 90,
        "bucket_retention_unqualified",
    )
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(8)
    run = host.work / run_id
    run.mkdir(mode=0o700)
    snapshot = run / "snapshot"
    snapshot.mkdir(mode=0o700)
    archive = run / "snapshot.tar.gz"
    encrypted = run / "snapshot.tar.gz.gpg"
    downloaded = run / "downloaded.gpg"
    decrypted = run / "downloaded.tar.gz"
    gnupg = run / "gnupg"
    gnupg.mkdir(mode=0o700)
    restore = run / "restored"
    restore.mkdir(mode=0o700)
    completed = False
    try:
        manifest = host.snapshot(snapshot, run_id)
        with tarfile.open(archive, "w:gz") as tar:
            for path in sorted(snapshot.rglob("*")):
                if path.is_file():
                    tar.add(
                        path, arcname=str(path.relative_to(snapshot)), recursive=False
                    )
        gpg = [
            "gpg",
            "--batch",
            "--yes",
            "--pinentry-mode",
            "loopback",
            "--passphrase-file",
            str(passphrase),
        ]
        command(
            [
                *gpg,
                "--symmetric",
                "--cipher-algo",
                "AES256",
                "--compress-algo",
                "none",
                "--output",
                str(encrypted),
                str(archive),
            ],
            env={"GNUPGHOME": str(gnupg)},
        )
        digest = sha(encrypted.read_bytes())
        key = "liquilens-paper-host/v1/snapshots/" + run_id + "/snapshot.tar.gz.gpg"
        remote = "anchor:" + bucket + "/" + key
        flags = [
            "--config=/dev/null",
            "--s3-no-check-bucket",
            "--transfers=1",
            "--checkers=2",
            "--retries=3",
        ]
        command(
            ["rclone", "copyto", str(encrypted), remote, "--immutable", *flags], env=env
        )
        check_retention(s3_request(env, bucket, key)[0])
        command(["rclone", "copyto", remote, str(downloaded), *flags], env=env)
        require(sha(downloaded.read_bytes()) == digest, "downloaded_archive_mismatch")
        command(
            [*gpg, "--decrypt", "--output", str(decrypted), str(downloaded)],
            env={"GNUPGHOME": str(gnupg)},
        )
        extract_archive(decrypted, restore)
        verification = verify_snapshot(restore)
        receipt = {
            "schema": SCHEMA,
            "status": "verified",
            "run_id": run_id,
            "verified_at": datetime.now(UTC).isoformat(),
            "archive_sha256": digest,
            "archive_bytes": encrypted.stat().st_size,
            "object_key": key,
            "source_commits": {k: v["commit"] for k, v in manifest["releases"].items()},
            "verification": verification,
            "retention": {"mode": "COMPLIANCE", "minimum_days": 90},
        }
        durable_json(run / "RECEIPT.json", receipt)
        receipt_key = key.rsplit("/", 1)[0] + "/RECEIPT.json"
        command(
            [
                "rclone",
                "copyto",
                str(run / "RECEIPT.json"),
                "anchor:" + bucket + "/" + receipt_key,
                "--immutable",
                *flags,
            ],
            env=env,
        )
        check_retention(s3_request(env, bucket, receipt_key)[0])
        command(
            [
                "rclone",
                "copyto",
                "anchor:" + bucket + "/" + receipt_key,
                str(run / "receipt.downloaded"),
                *flags,
            ],
            env=env,
        )
        require(
            (run / "receipt.downloaded").read_bytes()
            == (run / "RECEIPT.json").read_bytes(),
            "remote_receipt_mismatch",
        )
        durable_json(host.work / "status.json", receipt)
        completed = True
        return {
            "status": "verified",
            "run_id": run_id,
            "restore_verified": True,
            "execution_enabled": False,
            "broker_contacted": False,
        }
    finally:
        # An interrupted quiesce intent must survive for recover/ExecStopPost.
        intent = host.read_intent()
        if intent and intent["run_id"] == run_id and intent["phase"] == "resumed":
            shutil.rmtree(run)
            intent["phase"] = "complete"
            durable_json(host.intent_path, intent)
        if not completed:
            durable_json(
                host.work / "last-failure.json",
                {
                    "schema": SCHEMA,
                    "run_id": run_id,
                    "status": "failed",
                    "observed_at": datetime.now(UTC).isoformat(),
                },
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup", "recover", "verify"))
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    try:
        require(os.geteuid() == ROOT_UID, "root_required")
        os.umask(0o077)
        host = HostBackup()
        if args.action == "verify":
            require(args.snapshot is not None, "snapshot_required")
            result = verify_snapshot(args.snapshot)
        else:
            require(args.snapshot is None, "unexpected_snapshot_argument")
            with existing_lock(host.work / "backup.lock", ROOT_UID):
                result = host.recover() if args.action == "recover" else backup(host)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        sqlite3.Error,
        subprocess.SubprocessError,
        tarfile.TarError,
        ET.ParseError,
    ) as error:
        print(
            json.dumps(
                {
                    "status": "backup_requires_review",
                    "reason": str(error)
                    if isinstance(error, BackupRefused)
                    else type(error).__name__,
                    "execution_enabled": False,
                    "broker_contacted": False,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
