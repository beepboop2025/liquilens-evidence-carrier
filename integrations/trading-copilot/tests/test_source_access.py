"""Credentials stay private, fixed-origin, and never degrade to anonymous access."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from liquilens_trading_copilot.observatory import collect_observatory
from liquilens_trading_copilot.scoped import (
    CORPORATE_URL,
    FUNDING_URL,
    UNDERTOW_URL,
    ScopedUpstreamTransport,
)
from liquilens_trading_copilot.source_access import read_source_token

TOKEN = "test_source_identity.only_test_material_123456789"
BODY = {"method": "tools/call", "params": {"name": "trade_safety_exit_context"}}


class SourceAccessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name).resolve()
        self.path = self.directory / "source.token"
        self.path.write_text(TOKEN + "\n")
        self.path.chmod(0o600)

    def tearDown(self):
        self.temp.cleanup()

    def test_accepts_private_plain_and_systemd_readonly_credentials(self):
        self.assertEqual(read_source_token(self.path), TOKEN)
        self.path.chmod(0o400)
        self.assertEqual(read_source_token(self.path), TOKEN)

    def test_rejects_unsafe_content_modes_links_and_owner_without_secret_errors(self):
        for raw in (
            b"short",
            b"x" * 8193,
            b"x" * 20 + b"\r\nInjected: x",
            b"\xff" * 20,
        ):
            self.path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, "^source_credential_unavailable$"):
                read_source_token(self.path)
        self.path.write_text(TOKEN)
        for mode in (0o644, 0o640, 0o660, 0o700):
            self.path.chmod(mode)
            with self.assertRaises(ValueError):
                read_source_token(self.path)
        self.path.chmod(0o600)
        with (
            patch("os.geteuid", return_value=os.geteuid() + 1),
            self.assertRaises(ValueError),
        ):
            read_source_token(self.path)
        link = self.directory / "linked"
        link.symlink_to(self.path)
        with self.assertRaises(ValueError):
            read_source_token(link)
        link.unlink()
        os.link(self.path, link)
        with self.assertRaises(ValueError):
            read_source_token(self.path)

    def test_fifo_and_relative_paths_fail_without_blocking(self):
        fifo = self.directory / "fifo"
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(ValueError):
            read_source_token(fifo)
        with self.assertRaises(ValueError):
            read_source_token(Path("source.token"))

    async def test_auth_only_on_fixed_undertow_route_and_no_redirect_following(self):
        seen = []

        def handle(request):
            seen.append(request)
            if request.url == UNDERTOW_URL:
                return httpx.Response(
                    302, headers={"Location": "https://attacker.invalid"}
                )
            return httpx.Response(200, json={})

        client = ScopedUpstreamTransport(
            transport=httpx.MockTransport(handle), undertow_token_file=self.path
        )
        try:
            await client.request("GET", FUNDING_URL)
            await client.request("GET", CORPORATE_URL)
            with self.assertRaises(ValueError):
                await client.request("POST", UNDERTOW_URL, json_body=BODY)
        finally:
            await client.aclose()
        self.assertEqual(len(seen), 3)
        self.assertTrue(all("authorization" not in r.headers for r in seen[:2]))
        self.assertEqual(seen[2].headers["authorization"], "Bearer " + TOKEN)

    async def test_missing_explicit_credential_never_falls_back_to_anonymous(self):
        seen = []
        self.path.unlink()
        client = ScopedUpstreamTransport(
            transport=httpx.MockTransport(
                lambda r: seen.append(r) or httpx.Response(200, json={})
            ),
            undertow_token_file=self.path,
        )
        try:
            with self.assertRaisesRegex(ValueError, "source_credential_unavailable"):
                await client.request("POST", UNDERTOW_URL, json_body=BODY)
            await client.request("GET", FUNDING_URL)
        finally:
            await client.aclose()
        self.assertEqual([str(r.url) for r in seen], [FUNDING_URL])

    async def test_revocation_401_is_not_retried_and_rotation_reloads_token(self):
        seen = []
        client = ScopedUpstreamTransport(
            transport=httpx.MockTransport(
                lambda r: seen.append(r) or httpx.Response(401)
            ),
            undertow_token_file=self.path,
        )
        try:
            for token in (TOKEN, TOKEN + "rotated"):
                self.path.write_text(token)
                with self.assertRaises(ValueError):
                    await client.request("POST", UNDERTOW_URL, json_body=BODY)
            self.assertEqual(
                [r.headers["authorization"] for r in seen],
                ["Bearer " + TOKEN, "Bearer " + TOKEN + "rotated"],
            )
        finally:
            await client.aclose()

    async def test_credential_failure_still_reports_independent_sources(self):
        self.path.unlink()
        seen = []
        transport = ScopedUpstreamTransport(
            transport=httpx.MockTransport(
                lambda r: seen.append(r) or httpx.Response(200, json={})
            ),
            undertow_token_file=self.path,
        )
        try:
            report = await asyncio.wait_for(collect_observatory(transport=transport), 3)
        finally:
            await transport.aclose()
        self.assertEqual(set(report["sources"]), {"seiche", "liquilens", "undertow"})
        self.assertFalse(report["sources"]["undertow"]["admitted"])
        self.assertFalse(report["source_checks_passed"])
        self.assertEqual(len(seen), 2)
        self.assertNotIn(TOKEN, json.dumps(report))
        self.assertNotIn(str(self.path), json.dumps(report))
