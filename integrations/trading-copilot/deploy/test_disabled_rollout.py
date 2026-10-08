"""Offline filesystem and systemd-double checks; never contacts a broker."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "disabled_rollout", Path(__file__).with_name("disabled_rollout.py")
)
rollout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rollout)
real_unit_state = rollout.unit_state


class DisabledRolloutTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        self.root = base / "install"
        self.old = self.root / "releases" / ("a" * 40)
        self.new = self.root / "releases" / ("b" * 40)
        self.old.mkdir(parents=True)
        self.new.mkdir()
        (self.old / "source.py").write_text("old source\n")
        (self.new / "source.py").write_text("new source\n")
        (self.root / "current").symlink_to(self.old)
        self.state = base / "state"
        self.state.mkdir(mode=0o700)
        self.config = self.state / "config.json"
        self.config.write_text(
            json.dumps({"mode": "paper", "enabled": False, "account_id": None})
        )
        (self.state / "paper.env").write_text(
            "ALPACA_PAPER_API_KEY=\nALPACA_PAPER_SECRET_KEY=\n"
            "COPILOT_PAPER_HMAC_KEY=private-test-only-key\n"
        )
        for file in self.state.iterdir():
            file.chmod(0o600)
        for change in (
            patch.object(rollout, "ROOT", self.root),
            patch.object(rollout, "STATE", self.state),
            patch.object(rollout, "unit_state", return_value={"inactive": True}),
        ):
            change.start()
            self.addCleanup(change.stop)

    def test_switch_and_rollback_preserve_private_state(self):
        before = rollout.snapshot()
        result = rollout.switch(before, self.new)
        self.assertEqual((self.root / "current").resolve(), self.new)
        self.assertEqual(
            result["after"]["private_state_sha256"], before["private_state_sha256"]
        )
        rolled_back = rollout.switch(result["after"], self.old)
        self.assertEqual(rolled_back["after"], before)

    def test_source_state_or_unit_changes_block_switch(self):
        for change in ("source", "state", "unit"):
            with self.subTest(change=change):
                before = rollout.snapshot()
                if change == "source":
                    (self.old / "source.py").write_text("changed source\n")
                elif change == "state":
                    (self.state / "journal.sqlite3").write_bytes(b"new journal")
                else:
                    before["units"] = {}
                with self.assertRaisesRegex(rollout.RolloutBlocked, "changed_since"):
                    rollout.switch(before, self.new)
                self.assertEqual((self.root / "current").resolve(), self.old)

    def test_enabled_or_account_configured_blocks_snapshot(self):
        for changes in ({"enabled": True}, {"account_id": "account"}):
            with self.subTest(changes=changes):
                config = {"mode": "paper", "enabled": False, "account_id": None}
                config.update(changes)
                self.config.write_text(json.dumps(config))
                with self.assertRaises(rollout.RolloutBlocked):
                    rollout.snapshot()

    def test_private_state_symlinks_block_snapshot(self):
        (self.state / "extra").symlink_to(self.old / "source.py")
        with self.assertRaisesRegex(rollout.RolloutBlocked, "symlink"):
            rollout.snapshot()

    def test_changed_during_hashing_blocks_before_switch(self):
        before = rollout.snapshot()
        original = rollout.digest_tree

        def race(path, **kwargs):
            result = original(path, **kwargs)
            if path == self.new:
                (self.state / "STOP").write_text("")
            return result

        with (
            patch.object(rollout, "digest_tree", side_effect=race),
            self.assertRaisesRegex(rollout.RolloutBlocked, "changed_before"),
        ):
            rollout.switch(before, self.new)
        self.assertEqual((self.root / "current").resolve(), self.old)
        self.assertFalse(os.path.lexists(self.root / (".current-" + str(os.getpid()))))

    def test_configured_credentials_block_snapshot(self):
        env = self.state / "paper.env"
        env.write_text(
            env.read_text().replace(
                "ALPACA_PAPER_API_KEY=", "ALPACA_PAPER_API_KEY=test"
            )
        )
        with self.assertRaisesRegex(rollout.RolloutBlocked, "key_already_configured"):
            rollout.snapshot()

    def test_systemd_active_or_enabled_units_block(self):
        for active, enabled in [("active", "disabled"), ("inactive", "enabled")]:
            with self.subTest(active=active, enabled=enabled):
                response = subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=(
                        f"LoadState=loaded\nActiveState={active}\n"
                        f"UnitFileState={enabled}\nMainPID=0\n"
                    ),
                )
                with (
                    patch.object(rollout.subprocess, "run", return_value=response),
                    self.assertRaises(rollout.RolloutBlocked),
                ):
                    real_unit_state(rollout.UNITS[1])

    def test_only_optional_missing_unit_is_accepted(self):
        response = subprocess.CompletedProcess(
            [], 1, stdout=("LoadState=not-found\nActiveState=inactive\nMainPID=0\n")
        )
        with patch.object(rollout.subprocess, "run", return_value=response):
            self.assertEqual(
                real_unit_state(rollout.UNITS[2])["LoadState"], "not-found"
            )
            with self.assertRaisesRegex(rollout.RolloutBlocked, "paper_unit_missing"):
                real_unit_state(rollout.UNITS[0])

    def test_receipts_are_exclusive_and_private(self):
        path = self.root / "receipt.json"
        rollout.save(path, {"status": "test"})
        self.assertEqual(rollout.read_receipt(path), {"status": "test"})
        with self.assertRaises(FileExistsError):
            rollout.save(path, {})
        path.chmod(0o644)
        with self.assertRaisesRegex(rollout.RolloutBlocked, "permissions_invalid"):
            rollout.read_receipt(path)


if __name__ == "__main__":
    unittest.main()
