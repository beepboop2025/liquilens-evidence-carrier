"""Owner-only provisioning of a single recipient's paper outcome grant."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

from .agent_host import BearerAuthority, private_json
from .agent_service import agent_binding
from .cli import _exclusive_file
from .config import load_config
from .live_journal import private_directory
from .outcomes import GRANT_SCHEMA, PURPOSE, authorize, instant, utc


def _replace(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        _exclusive_file(temporary, json.dumps(value, indent=2) + "\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def grant_outcomes(
    state_dir: Path,
    *,
    consent_id: str,
    recipient_id: str,
    expires_at: str,
    traffic_class: str,
    now: datetime | None = None,
) -> dict:
    """Explicit grant from now until expiry, never retroactive consent.

    Only an owner filesystem operation provisions the token. The running host
    loads token scopes at startup, and rereads the grant on each export request.
    """
    private_directory(state_dir)
    for name in ("outcome-consent.json", "agent-outcomes.token"):
        if os.path.lexists(state_dir / name):
            raise ValueError("outcome_consent_already_provisioned")
    private_json(state_dir / "config.json")
    config = load_config(state_dir / "config.json")
    if Path(config.state_dir) != state_dir:
        raise ValueError("configured_state_directory_mismatch")
    auth_path = state_dir / "agent-auth.json"
    auth = private_json(auth_path)
    BearerAuthority(auth, agent_id=config.agent_id)
    if len(auth["tokens"]) >= 16:
        raise ValueError("agent_token_capacity_reached")
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    binding = agent_binding(config)
    now = now or datetime.now(UTC)
    grant = {
        "schema": GRANT_SCHEMA,
        "consent_id": consent_id,
        "enabled": True,
        "binding": {
            name: getattr(binding, name)
            for name in (
                "tenant_id",
                "operator_id",
                "account_id",
                "agent_id",
            )
        },
        "recipient_id": recipient_id,
        "token_sha256": token_hash,
        "purpose": PURPOSE,
        "not_before": utc(now),
        "expires_at": utc(instant(expires_at)),
        "records_from": utc(now),
        "records_until": utc(instant(expires_at)),
        "traffic_class": traffic_class,
        "allow_training": False,
        "allow_cross_customer_aggregation": False,
    }
    authorize(grant, binding, token_hash, now)
    auth["tokens"].append({"sha256": token_hash, "scopes": ["outcomes:read"]})
    BearerAuthority(auth, agent_id=config.agent_id)
    _exclusive_file(state_dir / "agent-outcomes.token", token + "\n")
    _exclusive_file(
        state_dir / "outcome-consent.json", json.dumps(grant, indent=2) + "\n"
    )
    _replace(auth_path, auth)
    return {
        "mode": "paper",
        "status": "outcome_consent_provisioned",
        "consent_id": consent_id,
        "recipient_id": recipient_id,
        "expires_at": grant["expires_at"],
        "restart_host_required": True,
        "execution_authorized": False,
    }


def revoke_outcomes(state_dir: Path) -> dict:
    private_directory(state_dir)
    path = state_dir / "outcome-consent.json"
    grant = private_json(path)
    if grant.get("schema") != GRANT_SCHEMA:
        raise ValueError("outcome_consent_schema_invalid")
    grant["enabled"] = False
    _replace(path, grant)
    return {
        "mode": "paper",
        "status": "outcome_consent_revoked",
        "restart_host_required": False,
        "previously_exported_copies_recalled": False,
    }
