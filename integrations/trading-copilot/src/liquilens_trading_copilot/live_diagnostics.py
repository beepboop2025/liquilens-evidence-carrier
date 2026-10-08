"""Offline configuration checks; never broker qualification or activation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from liquilens_evidence import TradeSafetyExecutionBinding

from .agent_host import private_json
from .live_connector import LiveExecutionBlocked, LiveLimits
from .live_journal import local_orders, private_directory

# Legacy aggregate retained for output compatibility; not all gaps are external.
EXTERNAL_REQUIREMENTS = (
    "qualified_live_issuer_unverified",
    "executable_quote_entitlement_unverified",
    "alpaca_limit_order_broker_preview_unavailable",
    "account_qualification_unverified",
    "account_activation_unverified",
)

ENGINEERING_REQUIREMENTS = (
    "qualified_live_issuer_unverified",
    "alpaca_limit_order_broker_preview_unavailable",
)
QUALIFICATION_REQUIREMENTS = tuple(
    reason for reason in EXTERNAL_REQUIREMENTS if reason not in ENGINEERING_REQUIREMENTS
)


def live_readiness(state_dir: Path) -> dict[str, Any]:
    """Inspect owner-controlled local files only, without making changes."""
    reasons: list[str] = []
    checks: dict[str, bool | None] = {
        "private_state": False,
        "configuration": False,
        "binding": False,
        "limits": False,
        "private_credentials_present": False,
        "receipt_key_present": False,
        "journal_valid": None,
    }
    activated = False
    stopped = None
    journal_present = None
    unresolved = None
    config = {}
    try:
        private_directory(state_dir)
        checks["private_state"] = True
    except LiveExecutionBlocked as error:
        reasons.append(str(error))
    if checks["private_state"]:
        stopped = (state_dir / "STOP").exists() or (state_dir / "STOP").is_symlink()
        if stopped:
            reasons.append("stop_present")
        try:
            config = private_json(state_dir / "live-config.json")
            if (
                config.get("schema") != "liquilens.customer-live-connector.v1"
                or type(config.get("live_enabled")) is not bool
                or not isinstance(config.get("activation_acknowledgment"), str)
            ):
                raise ValueError
            checks["configuration"] = True
        except Exception:
            reasons.append("live_configuration_missing_or_invalid")
        if checks["configuration"]:
            try:
                binding = TradeSafetyExecutionBinding(**config["binding"])
                if not binding.hmac_key_id:
                    raise ValueError
                checks["binding"] = True
                activated = (
                    config["live_enabled"] is True
                    and config["activation_acknowledgment"]
                    == "LIVE-ACCOUNT:" + binding.account_id
                )
            except Exception:
                reasons.append("live_binding_missing_or_invalid")
            try:
                raw_limits = config["limits"]
                if not isinstance(raw_limits["symbols"], list):
                    raise ValueError
                LiveLimits(
                    **{**raw_limits, "symbols": tuple(raw_limits["symbols"])}
                ).validate()
                checks["limits"] = True
            except Exception:
                reasons.append("live_limits_missing_or_invalid")
        try:
            secrets = private_json(state_dir / "live-secrets.json")
            checks["private_credentials_present"] = all(
                isinstance(secrets.get(key), str) and bool(secrets[key].strip())
                for key in ("api_key", "secret_key")
            )
            encoded_key = secrets.get("receipt_hmac_key_hex")
            checks["receipt_key_present"] = (
                isinstance(encoded_key, str) and len(bytes.fromhex(encoded_key)) >= 32
            )
        except Exception:
            reasons.append("live_secrets_missing_or_invalid")
        if not checks["private_credentials_present"]:
            reasons.append("broker_credentials_not_provisioned")
        if not checks["receipt_key_present"]:
            reasons.append("receipt_verification_key_not_provisioned")
        path = state_dir / "live-orders.sqlite3"
        journal_present = path.exists() or path.is_symlink()
        if journal_present:
            try:
                journal = local_orders(state_dir, limit=1, unresolved_only=True)
                checks["journal_valid"] = True
                unresolved = journal["unresolved_count"]
                if unresolved:
                    reasons.append("unresolved_order_blocks_submission")
            except LiveExecutionBlocked as error:
                checks["journal_valid"] = False
                reasons.append(str(error))
        else:
            reasons.append("live_journal_not_initialized")
    if not activated:
        reasons.append("live_execution_disabled_in_configuration")
    return {
        "schema": "liquilens.customer-live-readiness.v1",
        "mode": "live",
        "scope": "offline_configuration_only",
        "network_accessed": False,
        "broker_connected": False,
        "order_submitted": False,
        "live_ready": False,
        "checks": checks,
        "activation_configured": activated,
        "stop_present": stopped,
        "journal_present": journal_present,
        "unresolved_count": unresolved,
        "local_reason_codes": reasons,
        "external_requirements": list(EXTERNAL_REQUIREMENTS),
        "engineering_requirements": list(ENGINEERING_REQUIREMENTS),
        "qualification_requirements": list(QUALIFICATION_REQUIREMENTS),
        "limitations": [
            "Credential presence does not verify an account, entitlement or mandate.",
            "Alpaca Broker API estimation is indicative and excludes limit orders "
            "and crypto; it cannot qualify this Trading API connector's preview.",
            "No qualified live issuer, executable quote or broker preview is "
            "provided by this configuration check.",
        ],
    }
