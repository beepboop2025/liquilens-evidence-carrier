"""Isolated fake host and loopback readiness tests; no broker/source calls."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import importlib.util
import io
import json
import os
import sqlite3
import tarfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "backup_agent_host", Path(__file__).with_name("backup_agent_host.py")
)
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)
RUN_ID = "20261008T110000Z-0123456789abcdef"
REVISION = "a" * 40


def write(path, value, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(value if isinstance(value, bytes) else value.encode())
    path.chmod(mode)


class FakeSystem:
    def __init__(self, host):
        self.host = host
        self.state = "active"
        self.calls = []
        self.fail_stop = False
        self.fail_start = False

    def info(self, name):
        release = "/opt/liquilens-execution-observer/releases/" + REVISION
        return {
            "ActiveState": self.state if name == backup.HOST else "active",
            "MainPID": "123" if self.state == "active" else "0",
            "UnitFileState": "enabled",
            "FragmentPath": "/etc/systemd/system/" + name,
            "DropInPaths": "",
            "ExecStart": "{ path="
            + release
            + "/integrations/trading-copilot/.venv/bin/liquilens-agent-host ; "
            "argv[]=liquilens-agent-host serve --state-dir "
            + backup.STATE
            + " --require-disabled ; }",
        }

    def command(self, args, **kwargs):
        self.calls.append(args)
        if args[:2] == ["systemctl", "stop"]:
            assert args[2] == backup.HOST
            if self.fail_stop:
                raise backup.BackupRefused("command_failed")
            self.state = "inactive"
        elif args[:2] == ["systemctl", "start"]:
            assert args[2] == backup.HOST
            if self.fail_start:
                raise backup.BackupRefused("command_failed")
            self.state = "active"
        elif args[0] == "git":
            if args[3] == "rev-parse":
                return (REVISION + "\n").encode()
            if args[3] == "archive":
                return b"immutable tracked source archive"
        else:
            raise AssertionError("unexpected external command: " + args[0])
        return b""


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(backup, "ROOT_UID", os.getuid())
    host = backup.HostBackup(root, service_uid=os.getuid())
    host.backup_release = root / "backup-release"
    for directory in (
        backup.WORK,
        backup.STATE,
        backup.TOKENS,
        "/var/lib/liquilens-agent-attach",
        "/opt/liquilens-agent-host/receipts",
    ):
        host.path(directory).mkdir(parents=True, mode=0o700)
    for lock in (
        host.work / "backup.lock",
        host.path(backup.STATE) / "operator.lock",
        host.path(backup.TOKENS) / ".renew.lock",
        host.path(backup.REGISTRY).parent / ".service-identities.lock",
    ):
        write(lock, b"")
    config = {
        "enabled": False,
        "mode": "paper",
        "state_dir": backup.STATE,
        "agent_id": "fixture-agent",
        "paper_account_id": "fixture-paper-account",
    }
    write(host.path(backup.STATE) / "config.json", json.dumps(config))
    write(host.path(backup.STATE) / "paper.env", "ALPACA_PAPER_API_KEY=fixture\n")
    auth = {
        "schema": "liquilens.agent-host-auth.v1",
        "agent_id": "fixture-agent",
        "tokens": [],
    }
    for name, scopes in (
        ("agent-read.token", ["read", "assess"]),
        ("agent-execution.token", ["read", "assess", "submit", "reconcile"]),
    ):
        token = name.replace(".", "_") * 3
        write(host.path(backup.STATE) / name, token + "\n")
        auth["tokens"].append({"sha256": backup.sha(token.encode()), "scopes": scopes})
    write(host.path(backup.STATE) / "agent-auth.json", json.dumps(auth))
    for name in ("audit.sqlite3", "alpaca-submissions.sqlite3"):
        path = host.path(backup.STATE) / name
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, state TEXT)")
            db.execute("INSERT INTO events VALUES (1, 'blocked')")
        path.chmod(0o600)
    secret = "fixture-signing-key-with-enough-entropy"
    write(host.path("/etc/undertow-mcp.env"), "UNDERTOW_WEB_SECRET=" + secret + "\n")
    registry = {"schema": "undertow.operator-service-identities.v1", "services": {}}
    now = int(time.time())
    for name, filename in (
        ("liquilens-paper-host", "paper-host.token"),
        ("liquilens-execution-observer", "observer.token"),
    ):
        claims = {
            "sub": "service:" + name,
            "name": name,
            "aud": "mcp",
            "iat": now - 60,
            "exp": now + 3600,
        }
        part = (
            base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        )
        token = (
            part
            + "."
            + hmac.new(secret.encode(), part.encode(), hashlib.sha256).hexdigest()
        )
        write(host.path(backup.TOKENS) / filename, token + "\n")
        registry["services"][name] = {
            "issued_at": now - 60,
            "expires_at": now + 3600,
            "state": "active",
            "token_sha256": backup.sha(token.encode()),
            "scopes": ["trade_safety_exit_context"],
            "traffic_class": "operator_verification",
        }
    write(host.path(backup.REGISTRY), json.dumps(registry), 0o640)
    for name in backup.UNITS:
        write(host.path("/etc/systemd/system/" + name), "[Service]\n# fixture\n", 0o644)
    for name in backup.RETIRED:
        host.path("/etc/systemd/system/" + name).symlink_to("/dev/null")
    for name in (
        "/opt/liquilens-agent-host/current",
        "/opt/liquilens-execution-observer/current",
    ):
        path = host.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to("/opt/liquilens-execution-observer/releases/" + REVISION)
    write(host.path("/usr/local/bin/liquilens-paper-mcp"), "#!/bin/sh\nexit 0\n", 0o755)
    system = FakeSystem(host)
    monkeypatch.setattr(backup, "unit_info", system.info)
    monkeypatch.setattr(backup, "command", system.command)
    monkeypatch.setattr(host, "wait_ready", lambda _guards: None)
    snapshot = root / "snapshot"
    snapshot.mkdir(mode=0o700)
    return host, system, snapshot


def test_snapshot_preserves_account_and_both_journals_then_resumes(fixture):
    host, system, snapshot = fixture
    before = {p.name: p.read_bytes() for p in host.path(backup.STATE).iterdir()}
    manifest = host.snapshot(snapshot, RUN_ID)
    assert system.state == "active"
    assert [call[1] for call in system.calls if call[0] == "systemctl"] == [
        "stop",
        "start",
    ]
    assert {p.name: p.read_bytes() for p in host.path(backup.STATE).iterdir()} == before
    assert manifest["databases"]["state/audit.sqlite3"]["rows"] == {"events": 1}
    assert backup.verify_snapshot(snapshot)["verified"] is True
    assert host.read_intent()["phase"] == "resumed"


def test_inactive_host_is_never_started(fixture):
    host, system, snapshot = fixture
    system.state = "inactive"
    host.snapshot(snapshot, RUN_ID)
    assert system.state == "inactive"
    assert not any(call[0] == "systemctl" for call in system.calls)
    host.recover()
    assert not any(call[0] == "systemctl" for call in system.calls)


def test_snapshot_failure_resumes_original_host_without_erasing_intent(
    fixture, monkeypatch
):
    host, system, snapshot = fixture
    monkeypatch.setattr(
        host,
        "capture",
        lambda _path: (_ for _ in ()).throw(
            backup.BackupRefused("test_capture_failure")
        ),
    )
    with pytest.raises(backup.BackupRefused, match="test_capture_failure"):
        host.snapshot(snapshot, RUN_ID)
    assert system.state == "active"
    assert host.read_intent()["phase"] == "resumed"


def test_failed_resume_leaves_durable_recovery_and_later_recover_starts_once(fixture):
    host, system, snapshot = fixture
    system.fail_start = True
    with pytest.raises(backup.BackupRefused):
        host.snapshot(snapshot, RUN_ID)
    assert host.read_intent()["phase"] == "quiescing"
    assert system.state == "inactive"
    system.fail_start = False
    host.recover()
    count = sum(call[:2] == ["systemctl", "start"] for call in system.calls)
    host.recover()
    assert sum(call[:2] == ["systemctl", "start"] for call in system.calls) == count
    assert system.state == "active"


@pytest.mark.parametrize("change", ["enabled", "halt", "unit"])
def test_interrupted_backup_refuses_restart_after_operator_change(fixture, change):
    host, system, _snapshot = fixture
    intent = {
        "schema": backup.SCHEMA,
        "run_id": RUN_ID,
        "previously_active": True,
        "phase": "quiescing",
        "guards": host.guards(),
    }
    backup.durable_json(host.intent_path, intent)
    system.state = "inactive"
    if change == "enabled":
        path = host.path(backup.STATE) / "config.json"
        config = json.loads(path.read_text())
        config["enabled"] = True
        write(path, json.dumps(config))
    elif change == "halt":
        write(host.path(backup.STATE) / "STOP", "operator halt\n")
    else:
        write(host.path("/etc/systemd/system/" + backup.HOST), "changed\n", 0o644)
    with pytest.raises(backup.BackupRefused):
        host.recover()
    assert system.state == "inactive"
    assert not system.calls
    assert host.read_intent()["phase"] == "quiescing"


@pytest.mark.parametrize("lock", ["renewal", "registry", "operator"])
def test_existing_lock_contention_never_replaces_lock_or_starts_stopped_host(
    fixture, lock
):
    host, system, snapshot = fixture
    system.state = "inactive"
    paths = {
        "renewal": host.path(backup.TOKENS) / ".renew.lock",
        "registry": host.path(backup.REGISTRY).parent / ".service-identities.lock",
        "operator": host.path(backup.STATE) / "operator.lock",
    }
    path = paths[lock]
    before = path.stat().st_ino
    with path.open("rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            host.snapshot(snapshot, RUN_ID)
    assert path.stat().st_ino == before and system.state == "inactive"
    assert not system.calls


def test_pending_rotation_refused_and_preserved(fixture):
    host, system, snapshot = fixture
    pending = host.path(backup.TOKENS) / "paper-host.token.pending.json"
    write(pending, '{"pending":"preserve"}')
    with pytest.raises(backup.BackupRefused, match="source_renewal_not_settled"):
        host.snapshot(snapshot, RUN_ID)
    assert pending.read_text() == '{"pending":"preserve"}' and system.state == "active"


def test_auth_source_or_database_corruption_fails_isolated_verification(fixture):
    host, _system, snapshot = fixture
    host.snapshot(snapshot, RUN_ID)
    target = snapshot / "state/audit.sqlite3"
    with sqlite3.connect(target) as db:
        db.execute("DELETE FROM events")
    with pytest.raises(backup.BackupRefused, match="restored_content_mismatch"):
        backup.verify_snapshot(snapshot)


def test_signing_secret_must_match_both_saved_source_tokens(fixture):
    host, system, snapshot = fixture
    write(host.path("/etc/undertow-mcp.env"), "UNDERTOW_WEB_SECRET=wrong-key\n")
    with pytest.raises(backup.BackupRefused, match="source_binding_invalid"):
        host.snapshot(snapshot, RUN_ID)
    assert system.state == "active"


@pytest.mark.parametrize(
    "kind", ["traversal", "absolute", "symlink", "hardlink", "duplicate"]
)
def test_isolated_archive_refuses_unsafe_members(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(backup, "ROOT_UID", os.getuid())
    root = tmp_path.resolve()
    destination = root / "restore"
    destination.mkdir(mode=0o700)
    archive = root / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo(
            "../escape"
            if kind == "traversal"
            else "/escape"
            if kind == "absolute"
            else "entry"
        )
        if kind in {"symlink", "hardlink"}:
            member.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
            member.linkname = "/etc/passwd"
        else:
            member.size = 1
        tar.addfile(member, io.BytesIO(b"x"))
        if kind == "duplicate":
            tar.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(backup.BackupRefused, match="unsafe_archive_member"):
        backup.extract_archive(archive, destination)
    assert not (root / "escape").exists()


def test_private_file_symlink_and_group_readable_credentials_refused(fixture):
    host, _system, _snapshot = fixture
    token = host.path(backup.TOKENS) / "paper-host.token"
    token.chmod(0o644)
    with pytest.raises(backup.BackupRefused):
        backup.read_file(token, owner=os.getuid())
    token.unlink()
    token.symlink_to(host.path(backup.TOKENS) / "observer.token")
    with pytest.raises(OSError):
        backup.read_file(token, owner=os.getuid())


def test_retired_mask_required_before_stop(fixture):
    host, system, snapshot = fixture
    path = host.path("/etc/systemd/system/" + backup.RETIRED[0])
    path.unlink()
    write(path, "[Service]\n", 0o644)
    with pytest.raises(backup.BackupRefused, match="retired_unit_not_masked"):
        host.snapshot(snapshot, RUN_ID)
    assert not system.calls


@pytest.mark.parametrize(
    "failure",
    [None, "encryption", "archive_upload", "download_corruption", "receipt_upload"],
)
def test_offsite_pipeline_resumes_before_network_and_commits_only_verified_restore(
    fixture, monkeypatch, failure
):
    host, system, _snapshot = fixture
    write(host.path(backup.PASSPHRASE), "fixture-dedicated-backup-key-0123456789\n")
    for key, value in {
        "TYPE": "s3",
        "ENDPOINT": "https://storage.example.test",
        "REGION": "fixture",
        "ACCESS_KEY_ID": "fixture-access",
        "SECRET_ACCESS_KEY": "fixture-secret",
    }.items():
        monkeypatch.setenv("RCLONE_CONFIG_ANCHOR_" + key, value)
    monkeypatch.setenv("PAPER_BACKUP_BUCKET", "fixture-bucket")
    remote = {}
    commands = []

    def network(_env, _bucket, key="", *, bucket_lock=False):
        assert system.state == "active", "no network while host is quiesced"
        if bucket_lock:
            return (
                {},
                b"<ObjectLockConfiguration><ObjectLockEnabled>Enabled</ObjectLockEnabled><Rule><DefaultRetention><Mode>COMPLIANCE</Mode><Days>90</Days></DefaultRetention></Rule></ObjectLockConfiguration>",
            )
        assert "anchor:fixture-bucket/" + key in remote
        return {
            "x-amz-object-lock-mode": "COMPLIANCE",
            "x-amz-object-lock-retain-until-date": (
                datetime.now(UTC) + timedelta(days=90)
            ).isoformat(),
        }, b""

    def external(args, **kwargs):
        if args[0] in {"git", "systemctl"}:
            return system.command(args, **kwargs)
        assert system.state == "active", "encryption/transfers must follow recovery"
        commands.append(args)
        if args[0] == "gpg":
            if failure == "encryption":
                raise backup.BackupRefused("command_failed")
            source = Path(args[-1]).read_bytes()
            # Crypto is an explicit test double; production acceptance uses GPG.
            data = (
                b"fixture-ciphertext:" + source
                if "--symmetric" in args
                else source.removeprefix(b"fixture-ciphertext:")
            )
            write(Path(args[args.index("--output") + 1]), data)
        elif args[:2] == ["rclone", "copyto"]:
            source, target = args[2:4]
            if target.startswith("anchor:"):
                if failure == "archive_upload" and target.endswith(".gpg"):
                    raise backup.BackupRefused("command_failed")
                if failure == "receipt_upload" and target.endswith("RECEIPT.json"):
                    raise backup.BackupRefused("command_failed")
                assert "--immutable" in args and target not in remote
                assert target.endswith(("snapshot.tar.gz.gpg", "RECEIPT.json"))
                remote[target] = Path(source).read_bytes()
            else:
                data = remote[source]
                if failure == "download_corruption" and source.endswith(".gpg"):
                    data += b"corrupt"
                write(Path(target), data)
        else:
            raise AssertionError("unexpected command")
        return b""

    monkeypatch.setattr(backup, "s3_request", network)
    monkeypatch.setattr(backup, "command", external)
    if failure:
        with pytest.raises(backup.BackupRefused):
            backup.backup(host)
        assert not (host.work / "status.json").exists()
        assert not any(key.endswith("RECEIPT.json") for key in remote)
        assert (host.work / "last-failure.json").exists()
    else:
        result = backup.backup(host)
        assert result["restore_verified"] is True
        status = json.loads((host.work / "status.json").read_text())
        assert status["verification"]["verified"] is True
        assert status["verification"]["databases"] == 2
        assert set(status["source_commits"]) == {
            "carrier",
            "undertow",
            "observer",
            "backup",
        }
        assert len(remote) == 2
    assert system.state == "active" and host.read_intent()["phase"] == "complete"
    assert not list(host.work.glob("20*T*Z-*"))
    assert not any(
        "fixture-secret" in argument for args in commands for argument in args
    )


def test_signed_storage_probe_disables_redirects_and_environment_proxies(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def __init__(self):
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b""

    class Opener:
        def open(self, request, timeout):
            captured["request"] = request
            assert timeout == 30
            return Response()

    def opener(*handlers):
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setenv("HTTPS_PROXY", "https://attacker.example.test")
    monkeypatch.setattr(backup.urllib.request, "build_opener", opener)
    env = {
        "RCLONE_CONFIG_ANCHOR_" + key: value
        for key, value in {
            "ENDPOINT": "https://storage.example.test",
            "REGION": "fixture",
            "ACCESS_KEY_ID": "key",
            "SECRET_ACCESS_KEY": "secret",
        }.items()
    }
    backup.s3_request(env, "fixture-bucket", bucket_lock=True)
    assert (
        captured["request"].full_url
        == "https://fixture-bucket.storage.example.test/?object-lock="
    )
    assert captured["handlers"][0].proxies == {}
    with pytest.raises(backup.BackupRefused, match="storage_redirect_refused"):
        captured["handlers"][1].redirect_request(
            None, None, 302, "", {}, "https://attacker.example.test"
        )


def test_recovery_after_hard_interruption_removes_only_named_private_run(fixture):
    host, system, _snapshot = fixture
    run = host.work / RUN_ID
    run.mkdir(mode=0o700)
    write(run / "plaintext-incomplete", "private snapshot")
    unrelated = host.work / "operator-file"
    write(unrelated, "preserve")
    backup.durable_json(
        host.intent_path,
        {
            "schema": backup.SCHEMA,
            "run_id": RUN_ID,
            "previously_active": True,
            "phase": "quiescing",
            "guards": host.guards(),
        },
    )
    system.state = "inactive"
    host.recover()
    assert system.state == "active" and not run.exists()
    assert unrelated.read_text() == "preserve"
    assert host.read_intent()["phase"] == "complete"


def test_dangling_stop_symlink_refuses_capture_without_stopping_host(fixture):
    host, system, snapshot = fixture
    (host.path(backup.STATE) / "STOP").symlink_to("missing-stop-target")
    with pytest.raises(OSError):
        host.snapshot(snapshot, RUN_ID)
    assert not system.calls and system.state == "active"


def test_sqlite_snapshot_includes_committed_wal_without_archiving_sidecars(fixture):
    host, _system, snapshot = fixture
    path = host.path(backup.STATE) / "audit.sqlite3"
    writer = sqlite3.connect(path)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("INSERT INTO events VALUES (2, 'blocked')")
        writer.commit()
        for suffix in ("-wal", "-shm"):
            Path(str(path) + suffix).chmod(0o600)
        before = state_fingerprints(host)
        manifest = host.snapshot(snapshot, RUN_ID)
        assert state_fingerprints(host) == before
        assert manifest["databases"]["state/audit.sqlite3"]["rows"] == {"events": 2}
        assert not any(name.endswith(("-wal", "-shm")) for name in manifest["files"])
        assert backup.verify_snapshot(snapshot)["verified"] is True
        assert writer.execute("SELECT count(*) FROM events").fetchone() == (2,)
    finally:
        writer.close()


def state_fingerprints(host):
    def fingerprint(path):
        info = path.stat()
        return (
            backup.sha(path.read_bytes()),
            info.st_ino,
            info.st_uid,
            info.st_gid,
            info.st_mode,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    return {p.name: fingerprint(p) for p in host.path(backup.STATE).iterdir()}


@pytest.mark.parametrize("distinct_owner", [False, True])
def test_closed_wal_capture_never_opens_original_database_or_creates_sidecars(
    fixture, monkeypatch, distinct_owner
):
    host, _system, snapshot = fixture
    path = host.path(backup.STATE) / "alpaca-submissions.sqlite3"
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.close()
    if distinct_owner:
        if os.geteuid() != 0:
            pytest.skip("distinct account ownership requires native root qualification")
        host.owner = 65534
        for owned in (
            host.path(backup.STATE),
            *host.path(backup.STATE).iterdir(),
            host.path("/var/lib/liquilens-agent-attach"),
        ):
            os.chown(owned, host.owner, host.owner)
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()
    before = state_fingerprints(host)
    connect = backup.sqlite3.connect
    opened = []

    def isolated_only(database, *args, **kwargs):
        text = str(database)
        assert str(host.path(backup.STATE)) not in text
        assert str(snapshot) in text
        opened.append(text)
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(backup.sqlite3, "connect", isolated_only)
    host.snapshot(snapshot, RUN_ID)
    assert opened and state_fingerprints(host) == before
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()


def test_failed_readiness_preserves_quiescing_intent(fixture, monkeypatch):
    host, system, snapshot = fixture

    def startup_failed(_guards):
        system.state = "failed"
        raise backup.BackupRefused("host_resume_unverified")

    monkeypatch.setattr(host, "wait_ready", startup_failed)
    with pytest.raises(backup.BackupRefused, match="host_resume_unverified"):
        host.snapshot(snapshot, RUN_ID)
    assert host.read_intent()["phase"] == "quiescing"


def test_backup_unit_cannot_write_original_journals_or_sidecars():
    unit = Path(__file__).with_name("liquilens-paper-backup.service").read_text()
    writes = {
        path
        for line in unit.splitlines()
        if line.startswith("ReadWritePaths=")
        for path in line.split("=", 1)[1].split()
    }
    assert "ProtectSystem=strict" in unit.splitlines()
    assert writes == {
        backup.WORK,
        backup.STATE + "/operator.lock",
        backup.TOKENS + "/.renew.lock",
        str(Path(backup.REGISTRY).parent / ".service-identities.lock"),
    }


@pytest.mark.parametrize(
    "changed",
    [None, {"agent_id": "wrong-agent"}, {"execution_enabled": True}, {"mode": "live"}],
)
def test_readiness_uses_authenticated_loopback_capabilities(
    fixture, monkeypatch, changed
):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread

    host, system, _snapshot = fixture
    observed = []
    token = (host.path(backup.STATE) / "agent-read.token").read_text().strip()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append((self.path, self.headers.get("Authorization")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "schema": "liquilens.agent-host-capabilities.v1",
                        "agent_id": "fixture-agent",
                        "mode": "paper",
                        "execution_enabled": False,
                        "live_execution_supported": False,
                        **(changed or {}),
                    }
                ).encode()
            )

        def log_message(self, *_args):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.handle_request, daemon=True)
        thread.start()

        def unit_info(name):
            info = system.info(name)
            info["ExecStart"] += f" --port {server.server_port} "
            return info

        monkeypatch.setattr(backup, "unit_info", unit_info)
        monkeypatch.setenv("HTTP_PROXY", "http://unreachable.invalid:1")
        if changed:
            with pytest.raises(
                backup.BackupRefused, match="host_readiness_identity_mismatch"
            ):
                backup.HostBackup.wait_ready(host, host.guards())
        else:
            backup.HostBackup.wait_ready(host, host.guards())
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert observed == [("/v1/capabilities", "Bearer " + token)]


def test_readiness_rejects_transient_active_then_failed_unit(fixture, monkeypatch):
    host, system, _snapshot = fixture
    guards = host.guards()

    class UnreadyOpener:
        def open(self, *_args, **_kwargs):
            system.state = "failed"
            raise ConnectionRefusedError()

    monkeypatch.setattr(
        backup.urllib.request, "build_opener", lambda *_args: UnreadyOpener()
    )
    monkeypatch.setattr(backup.time, "sleep", lambda _seconds: None)
    with pytest.raises(backup.BackupRefused, match="host_resume_unverified"):
        backup.HostBackup.wait_ready(host, guards)


def test_atime_changes_do_not_masquerade_as_content_drift(fixture):
    host, _system, _snapshot = fixture
    path = host.path(backup.TOKENS) / "observer.token"
    before = path.stat()
    os.utime(path, ns=(1, before.st_mtime_ns))
    data, metadata = backup.read_file(path, owner=os.getuid())
    assert data and metadata["mtime_ns"] == before.st_mtime_ns


def test_cli_holds_operation_lock_across_the_entire_backup(
    fixture, monkeypatch, capsys
):
    host, _system, _snapshot = fixture
    monkeypatch.setattr(backup, "HostBackup", lambda: host)
    monkeypatch.setattr(backup.os, "geteuid", lambda: os.getuid())
    monkeypatch.setattr(backup.os, "umask", lambda _mode: 0o077)
    monkeypatch.setattr("sys.argv", ["backup_agent_host.py", "backup"])

    def inspect_lock(_host):
        with (
            (host.work / "backup.lock").open("rb") as stream,
            pytest.raises(BlockingIOError),
        ):
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return {"status": "verified"}

    monkeypatch.setattr(backup, "backup", inspect_lock)
    assert backup.main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "verified"


def test_cli_never_prints_secret_bearing_external_errors(fixture, monkeypatch, capsys):
    host, _system, _snapshot = fixture
    monkeypatch.setattr(backup, "HostBackup", lambda: host)
    monkeypatch.setattr(backup.os, "geteuid", lambda: os.getuid())
    monkeypatch.setattr(backup.os, "umask", lambda _mode: 0o077)
    monkeypatch.setattr("sys.argv", ["backup_agent_host.py", "backup"])

    def fail(_host):
        raise ValueError("provider-secret-value")

    monkeypatch.setattr(backup, "backup", fail)
    assert backup.main() == 1
    output = capsys.readouterr().out
    assert "provider-secret-value" not in output
    assert json.loads(output)["reason"] == "ValueError"


def test_capture_size_bound_resumes_host_before_exhausting_workspace(
    fixture, monkeypatch
):
    host, system, snapshot = fixture
    monkeypatch.setattr(backup, "MAX_CAPTURE_BYTES", 1024)
    with pytest.raises(backup.BackupRefused, match="snapshot_limits_exceeded"):
        host.snapshot(snapshot, RUN_ID)
    assert system.state == "active"
    assert sum(p.stat().st_size for p in snapshot.rglob("*") if p.is_file()) <= 1024


def test_workspace_reserve_covers_all_temporary_copies_before_capture(
    fixture, monkeypatch
):
    from types import SimpleNamespace

    host, system, _snapshot = fixture
    monkeypatch.setattr(
        backup.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=6 * backup.MAX_TOTAL),
    )
    with pytest.raises(backup.BackupRefused, match="backup_space_insufficient"):
        backup.backup(host)
    assert not system.calls and system.state == "active"
