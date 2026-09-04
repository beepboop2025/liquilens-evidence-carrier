from __future__ import annotations

import asyncio
import base64
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from trade_safety_gateway import x402_runtime
from trade_safety_gateway.x402_access import LIQUILENS_EXTENSION
from trade_safety_gateway.x402_runtime import (
    X402RuntimeBusyError,
    X402RuntimeConfigurationError,
    x402_runtime_from_env,
)

RESOURCE = "https://api.liquilens.in/v1/x402/check"
FACILITATOR = "https://facilitator.example.test/platform/v2/x402"
NETWORK = "eip155:84532"
ASSET = "0x" + "1" * 40
PAY_TO = "0x" + "2" * 40


def _environment(journal: Path) -> dict[str, str]:
    return {
        "TRADE_SAFETY_X402_RESOURCE_URL": RESOURCE,
        "TRADE_SAFETY_X402_FACILITATOR_URL": FACILITATOR,
        "TRADE_SAFETY_X402_NETWORK": NETWORK,
        "TRADE_SAFETY_X402_AMOUNT": "10000",
        "TRADE_SAFETY_X402_ASSET": ASSET,
        "TRADE_SAFETY_X402_ASSET_NAME": "USD Coin",
        "TRADE_SAFETY_X402_ASSET_VERSION": "2",
        "TRADE_SAFETY_X402_PAY_TO": PAY_TO,
        "TRADE_SAFETY_X402_QUOTE_BINDING_KEY_B64": base64.b64encode(
            b"separate-x402-quote-binding-secret"
        ).decode("ascii"),
        "TRADE_SAFETY_X402_JOURNAL_PATH": str(journal),
    }


def test_absent_x402_environment_is_disabled() -> None:
    assert x402_runtime_from_env({}) is None


@pytest.mark.parametrize(
    "environment",
    [
        {"TRADE_SAFETY_X402_PAY_TO": PAY_TO},
        {"TRADE_SAFETY_X402_UNKNOWN": "value"},
        {"TRADE_SAFETY_X402_RESOURCE_URL": ""},
    ],
)
def test_partial_unknown_or_empty_environment_fails_closed(
    environment: dict[str, str],
) -> None:
    with pytest.raises(X402RuntimeConfigurationError):
        x402_runtime_from_env(environment)


def test_complete_environment_builds_bound_bazaar_v2_challenge(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    journal_path = tmp_path / "x402.sqlite3"
    runtime = x402_runtime_from_env(_environment(journal_path))
    assert runtime is not None
    challenge = runtime.gate.challenge(
        b'{"policy":{},"request":{}}',
        resource=RESOURCE,
    ).payment_required

    assert challenge["x402Version"] == 2
    assert challenge["resource"] == {
        "url": RESOURCE,
        "description": (
            "Order-bound Seiche, Undertow, and conditional LiquiLens "
            "trade-safety assessment for AI agents. Research control only; "
            "no recommendation, order routing, custody, or execution."
        ),
        "mimeType": "application/json",
        "serviceName": "LiquiLens",
        "tags": [
            "trade-safety",
            "ai-agents",
            "risk-controls",
            "liquidity",
            "financial-data",
        ],
    }
    assert challenge["accepts"] == [
        {
            "scheme": "exact",
            "network": NETWORK,
            "amount": "10000",
            "asset": ASSET,
            "payTo": PAY_TO,
            "maxTimeoutSeconds": 60,
            "extra": {
                "name": "USD Coin",
                "paymentFlow": "authorization",
                "version": "2",
            },
        }
    ]
    assert set(challenge["extensions"]) == {"bazaar", LIQUILENS_EXTENSION}
    bazaar = challenge["extensions"]["bazaar"]
    assert bazaar["info"]["input"]["method"] == "POST"
    assert (
        bazaar["info"]["input"]["body"]["policy"]["missing_evidence"] == "fail_closed"
    )
    assert bazaar["info"]["output"]["example"]["authority"]["can_execute"] is False
    assert journal_path.exists()
    assert stat.S_IMODE(journal_path.stat().st_mode) & 0o077 == 0
    lock_path = Path(f"{journal_path}.runtime.lock")
    assert lock_path.is_file()
    assert not lock_path.is_symlink()
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
    asyncio.run(runtime.aclose())


def test_only_implemented_eip3009_transfer_method_is_advertised(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    environment = _environment(tmp_path / "x402.sqlite3")
    environment["TRADE_SAFETY_X402_ASSET_TRANSFER_METHOD"] = "eip3009"
    runtime = x402_runtime_from_env(environment)
    assert runtime is not None
    assert runtime.gate.config.payment_requirements()["extra"][
        "assetTransferMethod"
    ] == "eip3009"
    asyncio.run(runtime.aclose())


def test_runtime_exclusively_owns_journal_until_fully_closed(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    environment = _environment(tmp_path / "x402.sqlite3")
    first = x402_runtime_from_env(environment)
    assert first is not None

    with pytest.raises(X402RuntimeBusyError):
        x402_runtime_from_env(environment)

    asyncio.run(first.aclose())
    replacement = x402_runtime_from_env(environment)
    assert replacement is not None
    asyncio.run(replacement.aclose())


def test_x402_activation_requires_posix_advisory_locks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path.chmod(0o700)
    journal = tmp_path / "x402.sqlite3"
    monkeypatch.setattr(x402_runtime, "fcntl", None)

    with pytest.raises(
        X402RuntimeConfigurationError,
        match="requires POSIX advisory file locks",
    ):
        x402_runtime_from_env(_environment(journal))

    assert not journal.exists()
    assert not Path(f"{journal}.runtime.lock").exists()


def test_importing_factory_does_not_construct_configured_runtime(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    journal = tmp_path / "x402.sqlite3"
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("TRADE_SAFETY_X402_")
    }
    environment.update(_environment(journal))
    program = f"""
import asyncio
from pathlib import Path
from trade_safety_gateway import create_app

journal = Path({str(journal)!r})
assert not journal.exists()
assert not Path(f"{{journal}}.runtime.lock").exists()
application = create_app()
assert journal.exists()
assert Path(f"{{journal}}.runtime.lock").exists()
asyncio.run(application.state.x402_runtime.aclose())
asyncio.run(application.state.upstream.aclose())
"""

    completed = subprocess.run(
        [sys.executable, "-c", program],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""


@pytest.mark.parametrize("key", ["not-base64", "YQ=="])
def test_invalid_quote_binding_key_fails_without_opening_journal(
    tmp_path: Path,
    key: str,
) -> None:
    tmp_path.chmod(0o700)
    journal_path = tmp_path / "x402.sqlite3"
    environment = _environment(journal_path)
    environment["TRADE_SAFETY_X402_QUOTE_BINDING_KEY_B64"] = key
    with pytest.raises(X402RuntimeConfigurationError):
        x402_runtime_from_env(environment)
    assert not journal_path.exists()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TRADE_SAFETY_X402_RESOURCE_URL", "https://api.liquilens.in/v1/check"),
        ("TRADE_SAFETY_X402_AMOUNT", "0"),
        ("TRADE_SAFETY_X402_NETWORK", "base-mainnet"),
        ("TRADE_SAFETY_X402_ASSET_TRANSFER_METHOD", "permit2"),
    ],
)
def test_invalid_offer_or_wrong_route_fails_before_opening_journal(
    tmp_path: Path,
    name: str,
    value: str,
) -> None:
    tmp_path.chmod(0o700)
    journal_path = tmp_path / "x402.sqlite3"
    environment = _environment(journal_path)
    environment[name] = value

    with pytest.raises(X402RuntimeConfigurationError):
        x402_runtime_from_env(environment)

    assert not journal_path.exists()


def test_relative_or_public_journal_location_is_rejected(tmp_path: Path) -> None:
    relative = _environment(Path("x402.sqlite3"))
    with pytest.raises(X402RuntimeConfigurationError):
        x402_runtime_from_env(relative)

    tmp_path.chmod(0o755)
    public = _environment(tmp_path / "x402.sqlite3")
    with pytest.raises(X402RuntimeConfigurationError):
        x402_runtime_from_env(public)


def test_symlink_journal_is_rejected(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    tmp_path.chmod(0o700)
    actual = tmp_path / "actual.sqlite3"
    actual.write_bytes(b"")
    actual.chmod(0o600)
    link = tmp_path / "link.sqlite3"
    link.symlink_to(actual)
    with pytest.raises(X402RuntimeConfigurationError):
        x402_runtime_from_env(_environment(link))


def test_symlink_runtime_lock_is_rejected(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    tmp_path.chmod(0o700)
    journal = tmp_path / "x402.sqlite3"
    actual = tmp_path / "actual.lock"
    actual.write_bytes(b"")
    actual.chmod(0o600)
    Path(f"{journal}.runtime.lock").symlink_to(actual)

    with pytest.raises(X402RuntimeConfigurationError):
        x402_runtime_from_env(_environment(journal))

    assert not journal.exists()


def test_hardlinked_runtime_lock_is_rejected(tmp_path: Path) -> None:
    if not hasattr(os, "link"):
        pytest.skip("hard links unavailable")
    tmp_path.chmod(0o700)
    journal = tmp_path / "x402.sqlite3"
    actual = tmp_path / "actual.lock"
    actual.write_bytes(b"")
    actual.chmod(0o600)
    os.link(actual, Path(f"{journal}.runtime.lock"))

    with pytest.raises(X402RuntimeConfigurationError):
        x402_runtime_from_env(_environment(journal))

    assert not journal.exists()


def test_discovery_example_contains_no_real_identity_or_payment_secret(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    runtime = x402_runtime_from_env(_environment(tmp_path / "x402.sqlite3"))
    assert runtime is not None
    challenge = runtime.gate.challenge(
        b'{"policy":{},"request":{}}', resource=RESOURCE
    ).payment_required
    serialized = json.dumps(challenge["extensions"]["bazaar"], sort_keys=True)
    assert "mrinal" not in serialized.lower()
    assert PAY_TO not in serialized
    assert "quote-binding-secret" not in serialized
    asyncio.run(runtime.aclose())
