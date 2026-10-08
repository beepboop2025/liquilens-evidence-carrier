"""Offline same-state attach, drift, ownership and crash-recovery qualification."""

import fcntl
import importlib.util
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from liquilens_trading_copilot.agent_client import read_agent_token
from liquilens_trading_copilot.agent_diagnostics import diagnose_agent_host
from liquilens_trading_copilot.config import (
    SCOPED_PROFILE,
    CopilotConfig,
    scoped_policy,
)
from liquilens_trading_copilot.state import CycleStore

spec = importlib.util.spec_from_file_location(
    "attach_agent_host", Path(__file__).with_name("attach_agent_host.py")
)
attach = importlib.util.module_from_spec(spec)
spec.loader.exec_module(attach)


class AttachTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.base.chmod(0o700)
        self.state = self.base / "state"
        self.state.mkdir(mode=0o700)
        self.plan = self.base / "attach-plan"
        self.config = replace(
            CopilotConfig(),
            state_dir=str(self.state),
            account_id="fixture-account",
            evidence_profile=SCOPED_PROFILE,
            policy=scoped_policy(),
        )
        self.write_config()
        attach.write_private(
            self.state / "paper.env",
            (
                "ALPACA_PAPER_API_KEY=fixture-key\nALPACA_PAPER_SECRET_KEY=fixture-secret\n"
                "COPILOT_PAPER_HMAC_KEY=" + "h" * 32 + "\n"
            ).encode(),
        )
        attach.write_private(self.state / "operator.lock", b"")
        store = CycleStore(self.state)
        store.event("configuration_blocked", {"status": "blocked"}, datetime.now(UTC))
        store.close()
        self.inactive = patch.object(attach, "units_inactive")
        self.inactive.start()
        self.addCleanup(self.inactive.stop)

    def write_config(self):
        path = self.state / "config.json"
        path.write_text(json.dumps(asdict(self.config)))
        path.chmod(0o600)

    def prepare(self):
        result = attach.prepare(self.state, self.plan)
        return result["manifest_sha256"]

    def test_check_and_attach_preserve_originals_and_authenticate(self):
        before = attach.snapshot(self.state)
        self.assertEqual(attach.check(self.state)["status"], "eligible_disabled")
        digest = self.prepare()
        self.assertEqual(attach.snapshot(self.state), before)
        result = attach.apply(self.state, self.plan, digest)
        self.assertEqual(result["status"], "attached_disabled")
        self.assertEqual(
            attach.snapshot(self.state, allowed_additions=attach.ADDITIONS), before
        )
        self.assertEqual(before["audit_counts"]["events"], 1)
        for name in attach.ADDITIONS[:2]:
            self.assertGreaterEqual(len(read_agent_token(self.state / name)), 32)
        doctor = diagnose_agent_host(self.state)
        self.assertTrue(doctor["local_configuration_ready"])
        self.assertFalse(doctor["ready_for_order"])
        self.assertEqual(attach.apply(self.state, self.plan, digest), result)

    def test_crash_after_each_link_resumes_without_rotating_tokens(self):
        real_link = os.link
        digest = self.prepare()
        for crash_at in range(1, 4):
            with self.subTest(crash_at=crash_at):
                for name in attach.ADDITIONS:
                    if (self.state / name).exists():
                        (self.state / name).unlink()
                calls = 0

                def link_and_crash(*args, fail_at=crash_at, **kwargs):
                    nonlocal calls
                    real_link(*args, **kwargs)
                    calls += 1
                    if calls == fail_at:
                        raise OSError("simulated_crash")

                with (
                    patch.object(attach.os, "link", side_effect=link_and_crash),
                    self.assertRaises(OSError),
                ):
                    attach.apply(self.state, self.plan, digest)
                self.assertEqual(
                    (self.state / "agent-auth.json").exists(), crash_at == 3
                )
                linked = {
                    name: attach.file_record(self.state / name)
                    for name in attach.ADDITIONS
                    if (self.state / name).exists()
                }
                attach.apply(self.state, self.plan, digest)
                self.assertTrue(
                    all(
                        attach.file_record(self.state / name) == value
                        for name, value in linked.items()
                    )
                )

    def test_prepare_crash_leaves_state_unchanged_and_cannot_apply(self):
        before = attach.snapshot(self.state)
        original = attach.write_private

        def crash(path, content):
            if path.name == "manifest.json":
                raise OSError("simulated_prepare_crash")
            original(path, content)

        with (
            patch.object(attach, "write_private", side_effect=crash),
            self.assertRaises(OSError),
        ):
            self.prepare()
        self.assertEqual(attach.snapshot(self.state), before)
        with self.assertRaises(attach.AttachBlocked):
            attach.apply(self.state, self.plan, "0" * 64)
        with self.assertRaises(FileExistsError):
            self.prepare()

    def test_unknown_matching_auth_bytes_cannot_be_adopted_or_overwritten(self):
        digest = self.prepare()
        name = attach.ADDITIONS[0]
        attach.write_private(self.state / name, (self.plan / name).read_bytes())
        before = (self.state / name).read_bytes()
        with self.assertRaisesRegex(attach.AttachBlocked, "unowned_existing_auth_file"):
            attach.apply(self.state, self.plan, digest)
        self.assertEqual((self.state / name).read_bytes(), before)
        self.assertFalse((self.state / "agent-auth.json").exists())

    def test_prepare_never_overwrites_existing_directory(self):
        digest = self.prepare()
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(
            attach.file_record(self.plan / "manifest.json")["sha256"], digest
        )

    def test_apply_rejects_original_content_or_metadata_drift(self):
        for change in ("content", "mtime", "replacement"):
            with self.subTest(change=change):
                plan = self.base / change
                result = attach.prepare(self.state, plan)
                path = self.state / "operator.lock"
                if change == "content":
                    path.write_bytes(b"changed")
                elif change == "mtime":
                    os.utime(
                        path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1)
                    )
                else:
                    path.unlink()
                    attach.write_private(path, b"changed")
                with self.assertRaisesRegex(
                    attach.AttachBlocked, "state_changed_since_prepare"
                ):
                    attach.apply(self.state, plan, result["manifest_sha256"])
                self.assertFalse((self.state / "agent-auth.json").exists())

    def test_apply_checks_drift_before_each_publication(self):
        digest = self.prepare()
        original = os.link

        def mutate(*args, **kwargs):
            original(*args, **kwargs)
            self.config = replace(self.config, enabled=True)
            self.write_config()

        with (
            patch.object(attach.os, "link", side_effect=mutate),
            self.assertRaisesRegex(attach.AttachBlocked, "disabled_paper_required"),
        ):
            attach.apply(self.state, self.plan, digest)
        self.assertFalse((self.state / "agent-auth.json").exists())

    def test_plan_digest_or_token_changes_refused(self):
        digest = self.prepare()
        with self.assertRaisesRegex(
            attach.AttachBlocked, "plan_manifest_digest_mismatch"
        ):
            attach.apply(self.state, self.plan, "0" * 64)
        (self.plan / attach.ADDITIONS[0]).write_text("changed")
        with self.assertRaisesRegex(attach.AttachBlocked, "plan_file_changed"):
            attach.apply(self.state, self.plan, digest)

    def test_missing_lock_is_never_created(self):
        (self.state / "operator.lock").unlink()
        with self.assertRaises(FileNotFoundError):
            attach.check(self.state)
        self.assertFalse((self.state / "operator.lock").exists())

    def test_held_lock_refuses_check_and_prepare(self):
        with (self.state / "operator.lock").open("rb") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(attach.AttachBlocked, "operator_lock_busy"):
                attach.check(self.state)
            with self.assertRaisesRegex(attach.AttachBlocked, "operator_lock_busy"):
                self.prepare()
        self.assertFalse(self.plan.exists())

    def test_enabled_unbound_or_different_profile_refused(self):
        for changes in (
            {"enabled": True},
            {"account_id": None},
            {"evidence_profile": "native_gateway_v1"},
        ):
            with self.subTest(changes=changes):
                original = self.config
                self.config = replace(original, **changes)
                self.write_config()
                with self.assertRaises(ValueError):
                    attach.check(self.state)
                self.config = original

    def test_unexpected_state_file_and_sqlite_sidecars_refused(self):
        for name in (
            "STOP",
            "audit.sqlite3-wal",
            "alpaca-submissions.sqlite3",
            "agent-auth.json",
        ):
            with self.subTest(name=name):
                path = self.state / name
                attach.write_private(path, b"")
                with self.assertRaisesRegex(
                    attach.AttachBlocked, "unexpected_state_files"
                ):
                    attach.check(self.state)
                path.unlink()

    def test_symlink_hardlink_or_public_file_refused(self):
        path = self.state / "paper.env"
        path.chmod(0o644)
        with self.assertRaisesRegex(attach.AttachBlocked, "private_file_owner_mode"):
            attach.check(self.state)
        path.chmod(0o600)
        linked = self.base / "linked-secret"
        os.link(path, linked)
        with self.assertRaisesRegex(attach.AttachBlocked, "original_hardlink_refused"):
            attach.check(self.state)
        linked.unlink()
        path.rename(linked)
        path.symlink_to(linked)
        with self.assertRaises(OSError):
            attach.check(self.state)

    def test_state_or_plan_ancestor_symlink_refused(self):
        alias = self.base / "alias"
        alias.symlink_to(self.state)
        with self.assertRaisesRegex(attach.AttachBlocked, "symlink_path_refused"):
            attach.check(alias)
        alias.unlink()
        alias.symlink_to(self.base)
        with self.assertRaisesRegex(attach.AttachBlocked, "symlink_path_refused"):
            attach.prepare(self.state, alias / "new-plan")

    def test_wrong_owner_refused(self):
        with (
            patch.object(attach.os, "getuid", return_value=os.getuid() + 1),
            self.assertRaisesRegex(attach.AttachBlocked, "owner_or_mode"),
        ):
            attach.check(self.state)

    def test_execution_history_or_unreviewed_event_refused(self):
        for statement in (
            "INSERT INTO intents VALUES ('intent','2026-10-08','hash',1000)",
            "INSERT INTO intent_directions VALUES ('intent','buy')",
            "INSERT INTO order_observations VALUES ('hash','time','filled',1,'{}')",
            "INSERT INTO events VALUES (2,'time','submitted','{}')",
        ):
            with self.subTest(statement=statement):
                connection = sqlite3.connect(self.state / "audit.sqlite3")
                connection.execute(statement)
                connection.commit()
                with self.assertRaises(attach.AttachBlocked):
                    attach.check(self.state)
                for name in ("intents", "intent_directions", "order_observations"):
                    connection.execute(f"DELETE FROM {name}")
                connection.execute("DELETE FROM events WHERE id=2")
                connection.commit()
                connection.close()

    def test_existing_host_journal_tables_refused(self):
        connection = sqlite3.connect(self.state / "audit.sqlite3")
        connection.execute("CREATE TABLE agent_host_identity (id TEXT)")
        connection.close()
        with self.assertRaisesRegex(attach.AttachBlocked, "audit_schema_not_eligible"):
            attach.check(self.state)

    def test_apply_prechecks_all_existing_destinations(self):
        digest = self.prepare()
        attach.write_private(self.state / "agent-auth.json", b"unknown")
        with self.assertRaisesRegex(attach.AttachBlocked, "unowned_existing_auth_file"):
            attach.apply(self.state, self.plan, digest)
        self.assertFalse((self.state / "agent-read.token").exists())

    def test_unit_guard_accepts_masked_and_missing_optional_host(self):
        self.inactive.stop()
        for load, state in (("loaded", "disabled"), ("masked", "masked")):
            response = subprocess.CompletedProcess(
                [],
                0,
                stdout=(
                    f"LoadState={load}\nActiveState=inactive\nUnitFileState={state}\nMainPID=0\n"
                ),
            )
            with patch.object(attach.subprocess, "run", return_value=response):
                attach.units_inactive()

    def test_unit_guard_refuses_enabled_running_or_missing_paper_unit(self):
        self.inactive.stop()
        for output in (
            "LoadState=loaded\nActiveState=active\nUnitFileState=disabled\nMainPID=42\n",
            "LoadState=loaded\nActiveState=inactive\nUnitFileState=enabled\nMainPID=0\n",
            "LoadState=not-found\nActiveState=inactive\nMainPID=0\n",
        ):
            with (
                patch.object(
                    attach.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, stdout=output),
                ),
                self.assertRaises(attach.AttachBlocked),
            ):
                attach.units_inactive()


if __name__ == "__main__":
    unittest.main()
