"""Exercise private capture, stale-output replacement and authority boundaries."""

import fcntl
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "capture", Path(__file__).with_name("capture_observatory.py")
)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


def report():
    return {
        "schema": capture.REPORT_SCHEMA,
        **dict.fromkeys(capture.AUTHORITY, False),
        "source_checks_passed": False,
        "sources": {
            name: {"admitted": False} for name in ("seiche", "liquilens", "undertow")
        },
    }


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        self.state.chmod(0o700)

    def tearDown(self):
        self.temp.cleanup()

    def run_capture(self, result=None, keep=288):
        if result is None:
            result = (report(), None)
        with patch.object(capture, "observe", return_value=result):
            return capture.capture(self.state, "a" * 40, keep)

    def test_failure_replaces_previous_report_without_claiming_current_success(self):
        self.run_capture()
        failed = self.run_capture((None, "observer_timed_out"))
        self.assertEqual(json.loads((self.state / "latest.json").read_bytes()), failed)
        self.assertEqual(failed["capture_status"], "failed")
        self.assertIsNone(failed["report"])
        self.assertFalse(failed["execution_authorized"])
        self.assertEqual(len(list((self.state / "history").iterdir())), 2)

    def test_private_modes_and_bounded_retention_leave_unknown_files(self):
        self.run_capture(keep=2)
        unknown = self.state / "history" / "operator-note.txt"
        unknown.write_text("preserve")
        for _ in range(3):
            self.run_capture(keep=2)
        retained = list((self.state / "history").glob("run-*.json"))
        self.assertEqual(len(retained), 2)
        self.assertEqual(unknown.read_text(), "preserve")
        for path in [
            *retained,
            self.state / "latest.json",
            self.state / ".observer.lock",
        ]:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_existing_lock_prevents_concurrent_capture(self):
        self.run_capture()
        fd = os.open(self.state / ".observer.lock", os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.run_capture()
        finally:
            os.close(fd)

    def test_symlink_latest_cannot_overwrite_another_file(self):
        target = self.state / "operator-note"
        target.write_text("preserve")
        (self.state / "latest.json").symlink_to(target)
        with self.assertRaises(capture.CaptureError):
            self.run_capture()
        self.assertEqual(target.read_text(), "preserve")

    def test_report_cannot_authorize_or_omit_a_product(self):
        for key in capture.AUTHORITY:
            value = report()
            value[key] = True
            with self.subTest(key=key), self.assertRaises(capture.CaptureError):
                capture.validate_report(json.dumps(value).encode())
        value = report()
        del value["sources"]["undertow"]
        with self.assertRaises(capture.CaptureError):
            capture.validate_report(json.dumps(value).encode())

    def test_report_rejects_duplicate_nonfinite_and_false_passing_summary(self):
        for raw in (b'{"schema":1,"schema":2}', b'{"value":NaN}'):
            with self.assertRaises(capture.CaptureError):
                capture.validate_report(raw)
        value = report()
        value["source_checks_passed"] = True
        with self.assertRaises(capture.CaptureError):
            capture.validate_report(json.dumps(value).encode())

    def test_child_is_source_only_and_does_not_inherit_credentials(self):
        completed = (0, json.dumps(report()).encode())
        with (
            patch.dict(os.environ, {"ALPACA_PAPER_SECRET_KEY": "do-not-copy"}),
            patch.object(capture, "run_bounded", return_value=completed) as run,
        ):
            value, error = capture.observe()
        self.assertEqual(value, report())
        self.assertIsNone(error)
        args, kwargs = run.call_args
        self.assertEqual(args[0][-3:], ["observe", "--format", "json"])
        self.assertNotIn("--check-account", args[0])
        self.assertNotIn("--env-file", args[0])
        self.assertNotIn("ALPACA_PAPER_SECRET_KEY", args[1])
        self.assertNotIn("shell", kwargs)

    def test_child_failure_and_invalid_output_do_not_retain_stderr(self):
        for code, stdout in (
            (1, b""),
            (0, b"<html>secret</html>"),
            (0, b"x" * (capture.MAX_OUTPUT + 1)),
        ):
            completed = (code, stdout)
            with patch.object(capture, "run_bounded", return_value=completed):
                value, error = capture.observe()
            self.assertIsNone(value)
            self.assertNotIn("secret", error)

    def test_only_bound_systemd_directory_path_reaches_source_child(self):
        path = Path("/run/credentials/observer.service/undertow.token")
        completed = (0, json.dumps(report()).encode())
        for directory in (
            str(path.parent),
            "/tmp/credentials",
            "/run/credentials/other.service",
        ):
            with (
                patch.dict(
                    os.environ,
                    {
                        "CREDENTIALS_DIRECTORY": directory,
                        "ALPACA_PAPER_SECRET_KEY": "never-forward",
                    },
                ),
                patch.object(capture, "run_bounded", return_value=completed) as run,
            ):
                capture.observe(path)
            env = run.call_args.args[1]
            self.assertEqual(
                "CREDENTIALS_DIRECTORY" in env, directory == str(path.parent)
            )
            self.assertNotIn("ALPACA_PAPER_SECRET_KEY", env)

    def test_source_credential_path_passed_without_inherited_broker_secrets(self):
        path = Path("/run/credentials/observer/undertow.token")
        with (
            patch.dict(os.environ, {"ALPACA_PAPER_SECRET_KEY": "do-not-copy"}),
            patch.object(
                capture, "run_bounded", return_value=(0, json.dumps(report()).encode())
            ) as run,
        ):
            capture.observe(path)
        command, env = run.call_args.args
        self.assertEqual(command[-2:], ["--undertow-token-file", str(path)])
        self.assertNotIn("ALPACA_PAPER_SECRET_KEY", env)
        self.assertNotIn("--env-file", command)

    def test_child_timeout_is_an_explicit_failure(self):
        with patch.object(
            capture,
            "run_bounded",
            side_effect=subprocess.TimeoutExpired("observe", 45),
        ):
            self.assertEqual(capture.observe(), (None, "observer_timed_out"))

    def test_interruption_and_history_failure_leave_nonpassing_latest(self):
        for failure in (KeyboardInterrupt(), OSError("disk failure")):
            self.run_capture()
            if isinstance(failure, KeyboardInterrupt):
                target = "observe"
            else:
                target = "write_new"
            with (
                patch.object(capture, "observe", return_value=(report(), None)),
                patch.object(capture, target, side_effect=failure),
                self.assertRaises(type(failure)),
            ):
                capture.capture(self.state, "a" * 40)
            latest = json.loads((self.state / "latest.json").read_bytes())
            self.assertEqual(latest["capture_status"], "in_progress")
            self.assertIsNone(latest["report"])

    def test_owned_temporary_files_recover_and_history_is_atomic(self):
        self.run_capture()
        orphan = self.state / "history" / ".observation-abcdefgh"
        orphan.write_bytes(b'{"incomplete"')
        orphan.chmod(0o600)
        self.run_capture()
        self.assertFalse(orphan.exists())
        for path in (self.state / "history").glob("run-*.json"):
            self.assertEqual(json.loads(path.read_bytes())["schema"], capture.SCHEMA)
            self.assertEqual(path.stat().st_nlink, 1)

    def test_corrupted_old_history_stops_without_unbounded_growth(self):
        self.run_capture(keep=1)
        old = next((self.state / "history").glob("run-*.json"))
        old.write_bytes(b'{"incomplete"')
        for _ in range(2):
            with self.assertRaises(ValueError):
                self.run_capture(keep=1)
            self.assertEqual(len(list((self.state / "history").glob("run-*.json"))), 1)
            self.assertEqual(
                json.loads((self.state / "latest.json").read_bytes())["capture_status"],
                "in_progress",
            )

    def test_streaming_limits_stdout_stderr_and_runtime(self):
        cases = [
            ("import sys; sys.stdout.write('x' * 1100000)", 5),
            ("import sys; sys.stderr.write('x' * 10000)", 5),
            ("import time; time.sleep(5)", 0.05),
        ]
        for program, timeout in cases:
            with self.subTest(program=program), self.assertRaises(capture.CaptureError):
                capture.run_bounded(
                    [sys.executable, "-c", program], {"PATH": "/usr/bin:/bin"}, timeout
                )


if __name__ == "__main__":
    unittest.main()
