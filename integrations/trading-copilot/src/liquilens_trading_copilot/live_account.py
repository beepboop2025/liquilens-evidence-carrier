"""Explicit GET-only account checks, independent of execution and receipts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .agent_host import private_json
from .live_connector import (
    AlpacaLiveTransport,
    LiveExecutionBlocked,
    LiveLimits,
    number,
    validate_account_controls,
    validate_account_positions,
)
from .live_journal import private_directory

# Only known local reason codes may cross the redacted reporting boundary.
_REASON_CODES = frozenset(
    {
        "local_state_path_invalid",
        "local_state_unavailable",
        "local_state_not_private",
        "live_configuration_missing_or_invalid",
        "live_account_binding_required",
        "live_limits_missing_or_invalid",
        "broker_credentials_not_provisioned",
        "live_secrets_missing_or_invalid",
        "live_account_not_eligible",
        "invalid_numeric_value",
        "account_loss_or_cash_limit",
        "open_orders_pending",
        "invalid_account_positions",
        "duplicate_account_position",
        "short_or_margin_position_unsupported",
        "account_exposure_or_cash_limit",
        "broker_response_unavailable",
    }
)


class AlpacaAccountReadTransport(AlpacaLiveTransport):
    """Narrow the pinned live transport to three fixed GET request shapes."""

    def call(
        self, method: str, path: str, *, payload: Any = None, params: Any = None
    ) -> Any:
        if (
            method != "GET"
            or payload is not None
            or not (
                (path in {"/v2/account", "/v2/positions"} and params is None)
                or (
                    path == "/v2/orders"
                    and isinstance(params, dict)
                    and params == {"status": "open", "limit": 1}
                    and type(params["limit"]) is int
                )
            )
        ):
            raise LiveExecutionBlocked("unsupported_account_check_route")
        return super().call(method, path, params=params)


def check_account(state_dir: Path) -> dict[str, Any]:
    """Read one account snapshot; never create state or an execution connector."""
    result: dict[str, Any] = {
        "schema": "liquilens.customer-live-account-check.v1",
        "mode": "live",
        "scope": "read_only_account_check",
        "checked_at": None,
        "network_accessed": False,
        "broker_connected": False,
        "account_qualified": False,
        "order_submitted": False,
        "state_modified": False,
        "live_ready": False,
        "managed_live_endpoint": None,
        "broker_preview_adapter_available": False,
        "qualified_live_issuer_available": False,
        "activation_configured": None,
        "stop_present": None,
        "position_count": None,
        "checks": {
            "private_configuration": False,
            "private_credentials": False,
            "account_controls": False,
            "no_open_orders": False,
            "positions": False,
            "gross_exposure_limit": False,
        },
        "reason_codes": [],
        "limitations": [
            "Account qualification is a momentary read, not live execution readiness.",
            "No receipt, issuer, source entitlement, quote or broker preview "
            "is verified.",
            "No journal is read or changed; other account users can change "
            "this snapshot.",
        ],
    }
    broker = None
    try:
        private_directory(state_dir)
        result["stop_present"] = (state_dir / "STOP").exists() or (
            state_dir / "STOP"
        ).is_symlink()
        try:
            config = private_json(state_dir / "live-config.json")
            if (
                config.get("schema") != "liquilens.customer-live-connector.v1"
                or type(config.get("live_enabled")) is not bool
                or not isinstance(config.get("activation_acknowledgment"), str)
            ):
                raise ValueError
        except Exception:
            raise LiveExecutionBlocked(
                "live_configuration_missing_or_invalid"
            ) from None
        binding = config.get("binding")
        account_id = binding.get("account_id") if isinstance(binding, dict) else None
        if (
            not isinstance(account_id, str)
            or not 1 <= len(account_id) <= 128
            or any(c.isspace() or ord(c) < 32 for c in account_id)
        ):
            raise LiveExecutionBlocked("live_account_binding_required")
        result["activation_configured"] = (
            config["live_enabled"] is True
            and config["activation_acknowledgment"] == "LIVE-ACCOUNT:" + account_id
        )
        try:
            raw_limits = config["limits"]
            if not isinstance(raw_limits["symbols"], list):
                raise ValueError
            limits = LiveLimits(
                **{**raw_limits, "symbols": tuple(raw_limits["symbols"])}
            )
            limits.validate()
        except Exception:
            raise LiveExecutionBlocked("live_limits_missing_or_invalid") from None
        result["checks"]["private_configuration"] = True
        try:
            secrets = private_json(state_dir / "live-secrets.json")
        except Exception:
            raise LiveExecutionBlocked("live_secrets_missing_or_invalid") from None
        if not all(
            isinstance(secrets.get(key), str) and bool(secrets[key].strip())
            for key in ("api_key", "secret_key")
        ):
            raise LiveExecutionBlocked("broker_credentials_not_provisioned")
        result["checks"]["private_credentials"] = True
        broker = AlpacaAccountReadTransport(
            api_key=secrets["api_key"], secret_key=secrets["secret_key"]
        )
        result["network_accessed"] = True
        account = broker.call("GET", "/v2/account")
        validate_account_controls(account, account_id, limits)
        result["broker_connected"] = True
        result["checks"]["account_controls"] = True
        if (
            broker.call("GET", "/v2/orders", params={"status": "open", "limit": 1})
            != []
        ):
            raise LiveExecutionBlocked("open_orders_pending")
        result["checks"]["no_open_orders"] = True
        gross, quantities = validate_account_positions(
            broker.call("GET", "/v2/positions")
        )
        result["checks"]["positions"] = True
        result["position_count"] = len(quantities)
        if gross > number(limits.max_gross_exposure_usd):
            raise LiveExecutionBlocked("account_exposure_or_cash_limit")
        result["checks"]["gross_exposure_limit"] = True
        result["account_qualified"] = True
    except Exception as error:
        reason = (
            str(error)
            if isinstance(error, LiveExecutionBlocked) and str(error) in _REASON_CODES
            else "live_account_check_unavailable"
        )
        result["reason_codes"] = [reason]
    finally:
        if broker is not None:
            try:
                broker.close()
            except Exception:
                result["account_qualified"] = False
                result["reason_codes"] = ["live_account_check_unavailable"]
        result["checked_at"] = datetime.now(UTC).isoformat()
    return result
