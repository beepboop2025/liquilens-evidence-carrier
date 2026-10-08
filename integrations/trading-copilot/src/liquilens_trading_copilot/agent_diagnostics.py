"""Offline private-host setup checks; no broker, source fetch or state mutation."""

from __future__ import annotations

import os
import stat
from contextlib import suppress
from pathlib import Path
from typing import Any

from .agent_client import read_agent_token
from .agent_host import BearerAuthority, private_json
from .agent_service import agent_binding
from .config import SCOPED_PROFILE, load_config, load_secret_file
from .strategy import _valid_config


def diagnose_agent_host(state_dir: Path) -> dict[str, Any]:
    """Report local setup separately from evidence and account qualification.

    This command does not chmod paths, initialize files, open a journal, fetch
    sources or construct a broker client. It never authorizes an order.
    """
    checks: list[dict[str, str]] = []

    def record(name: str, valid: bool, code: str, action: str) -> None:
        checks.append(
            {
                "check": name,
                "status": "pass" if valid else "blocked",
                "code": "ok" if valid else code,
                "action": "none" if valid else action,
            }
        )

    directory_ok = False
    try:
        info = state_dir.stat()
        directory_ok = (
            state_dir.is_absolute()
            and not any(p.is_symlink() for p in (state_dir, *state_dir.parents))
            and stat.S_ISDIR(info.st_mode)
            and info.st_uid == os.getuid()
            and not info.st_mode & 0o077
        )
    except OSError:
        pass
    record(
        "private_state_directory",
        directory_ok,
        "private_state_directory_unavailable",
        "Initialize a new private host directory or restore its owner-only access.",
    )
    config = None
    authority = None
    enabled = None
    stopped = None
    if directory_ok:
        try:
            private_json(state_dir / "config.json")
            config = load_config(state_dir / "config.json")
        except Exception:
            pass
        record(
            "configuration",
            config is not None,
            "private_configuration_invalid",
            "Restore a valid owner-only config.json using the host guide.",
        )
        if config is not None:
            enabled = config.enabled
            record(
                "configured_state_directory",
                Path(config.state_dir) == state_dir,
                "configured_state_directory_mismatch",
                "Set config.state_dir to this directory's absolute physical path.",
            )
            record(
                "paper_account_identity",
                bool(config.account_id),
                "paper_account_id_missing",
                "Provision the customer's own paper account ID in config.json.",
            )
            if config.account_id:
                binding = None
                with suppress(Exception):
                    binding = agent_binding(config)
                record(
                    "execution_binding",
                    binding is not None,
                    "invalid_execution_binding",
                    "Correct the configured identity and issuer binding.",
                )
            record(
                "supported_evidence_profile",
                config.evidence_profile in {"native_gateway_v1", SCOPED_PROFILE},
                "private_host_profile_mismatch",
                "Use the documented private host profile and its exact policy.",
            )
            record(
                "account_limits",
                _valid_config(config.strategy),
                "invalid_account_limits",
                "Correct the account limits before starting the host.",
            )
            with suppress(Exception):
                authority = BearerAuthority(
                    private_json(state_dir / "agent-auth.json"),
                    agent_id=config.agent_id,
                )
        record(
            "agent_authority",
            authority is not None,
            "agent_authority_unavailable",
            "Restore agent-auth.json with the configured agent and token scopes.",
        )
        secrets = None
        with suppress(Exception):
            secrets = load_secret_file(state_dir / "paper.env")
        record(
            "private_secret_file",
            secrets is not None,
            "private_paper_secret_file_invalid",
            "Restore a valid owner-only paper.env; never paste credentials in chat.",
        )
        if secrets is not None:
            record(
                "paper_credentials",
                bool(secrets.get("ALPACA_PAPER_API_KEY"))
                and bool(secrets.get("ALPACA_PAPER_SECRET_KEY")),
                "paper_credentials_missing",
                "Provision customer paper API credentials in paper.env locally.",
            )
            record(
                "receipt_key",
                len(secrets.get("COPILOT_PAPER_HMAC_KEY", "").encode()) >= 32,
                "paper_receipt_key_missing_or_short",
                "Provision a private receipt key of at least 32 bytes.",
            )
        for name, scopes in (
            ("agent-read.token", {"read", "assess"}),
            ("agent-execution.token", {"read", "assess", "submit", "reconcile"}),
        ):
            valid = False
            try:
                token = read_agent_token(state_dir / name)
                valid = (
                    authority is not None
                    and authority.authenticate(["Bearer " + token]) == scopes
                )
            except Exception:
                pass
            record(
                name,
                valid,
                "agent_token_missing_invalid_or_wrong_scope",
                "Restore the private token and matching digest with its exact scope.",
            )
        stop = state_dir / "STOP"
        stopped = stop.exists() or stop.is_symlink()
    return {
        "schema": "liquilens.paper-host-readiness.v1",
        "mode": "paper",
        "checks": checks,
        "local_configuration_ready": all(c["status"] == "pass" for c in checks),
        "execution_enabled": enabled,
        "stop_active": stopped,
        "external_requirements": [
            {
                "check": "current_eligible_source_evidence",
                "status": "not_checked",
                "action": "Assess current sources through the disabled private host.",
            },
            {
                "check": "broker_account_identity_and_controls",
                "status": "not_checked",
                "action": "Qualify the customer's paper account before activation.",
            },
            {
                "check": "deployment_and_recovery",
                "status": "not_checked",
                "action": "Verify private networking, durable state and recovery.",
            },
        ],
        "ready_for_order": False,
        "network_accessed": False,
        "broker_connected": False,
        "order_submitted": False,
        "state_modified": False,
    }
