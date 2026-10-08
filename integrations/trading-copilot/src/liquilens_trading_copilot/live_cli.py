"""Explicit commands for a customer-owned live connector; disabled on init."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from liquilens_evidence import TradeSafetyExecutionBinding

from .agent_host import private_json
from .config import strict_json
from .live_account import check_account
from .live_connector import (
    AlpacaLiveTransport,
    LiveAccountConnector,
    LiveExecutionBlocked,
    LiveLimits,
)
from .live_diagnostics import live_readiness
from .live_journal import local_orders, local_status
from .state import prepare_state


def initialize(directory: Path) -> dict:
    prepare_state(directory)
    binding = {
        key: ""
        for key in (
            "account_id",
            "tenant_id",
            "operator_id",
            "agent_id",
            "runtime",
            "strategy_id",
            "policy_id",
            "policy_version",
            "policy_hash",
            "issuer_name",
            "issuer_version",
            "issuer_endpoint",
            "hmac_key_id",
        )
    }
    config = {
        "schema": "liquilens.customer-live-connector.v1",
        "live_enabled": False,
        "activation_acknowledgment": "",
        "binding": binding,
        "limits": {
            "symbols": ["BTC/USD"],
            "max_order_usd": 1000,
            "max_gross_exposure_usd": 1000,
            "max_daily_loss_usd": 100,
            "max_daily_attempts": 2,
        },
    }
    for name, value in (
        ("live-config.json", config),
        (
            "live-secrets.json",
            {"api_key": "", "secret_key": "", "receipt_hmac_key_hex": ""},
        ),
    ):
        fd = os.open(
            directory / name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(fd, "w") as stream:
            stream.write(json.dumps(value, indent=2) + "\n")
    return {
        "mode": "live",
        "initialized": True,
        "live_enabled": False,
        "credentials_provisioned": False,
        "order_submitted": False,
    }


def payload(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(524289)
    if len(raw) > 524288:
        raise ValueError("input_too_large")
    value = strict_json(raw)
    if not isinstance(value, dict):
        raise ValueError("object_required")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=(
            "init",
            "capabilities",
            "doctor",
            "check-account",
            "orders",
            "export",
            "preview",
            "submit",
            "status",
            "reconcile",
            "cancel",
        ),
    )
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--request-hash")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--unresolved-only", action="store_true")
    parser.add_argument("--after-hash")
    args = parser.parse_args()
    if args.operation in {"preview", "submit"}:
        if not args.request or not args.receipt or args.request_hash:
            parser.error("preview/submit require request and receipt files only")
    elif (
        args.request
        or args.receipt
        or bool(args.request_hash)
        != (args.operation in {"status", "reconcile", "cancel"})
    ):
        parser.error("status/reconcile/cancel require only request-hash")
    if args.operation not in {"orders", "export"} and (
        args.limit != 50 or args.unresolved_only or args.after_hash
    ):
        parser.error("journal filters are supported by orders/export only")
    broker = None
    try:
        # The trusted operator may select an SSD alias or a relative CLI path.
        # Use its physical target consistently, as the paper host CLI does.
        # Lower-level state operations still reject symlinks within that lane.
        args.state_dir = args.state_dir.expanduser().resolve()
        if args.operation == "init":
            result = initialize(args.state_dir)
        elif args.operation == "capabilities":
            result = {
                "mode": "live",
                "connector": "customer_owned_alpaca",
                "order_types": ["limit"],
                "shorting": False,
                "requires": [
                    "own_broker_account",
                    "authenticated_live_receipt",
                    "execution_eligible_sources",
                    "verified_broker_preview",
                    "explicit_account_activation",
                    "dedicated_durable_account_lane",
                ],
                "broker_connected": False,
                "order_submitted": False,
                "managed_live_service": False,
                "broker_preview_adapter_available": False,
                "qualified_live_issuer_available": False,
                "managed_live_endpoint": None,
                "read_only_account_check_available": True,
                "live_ready": False,
            }
        elif args.operation == "doctor":
            result = live_readiness(args.state_dir)
        elif args.operation == "check-account":
            result = check_account(args.state_dir)
        elif args.operation == "status":
            result = local_status(args.state_dir, args.request_hash)
        elif args.operation in {"orders", "export"}:
            result = local_orders(
                args.state_dir,
                limit=args.limit,
                unresolved_only=args.unresolved_only,
                after=args.after_hash,
            )
            if args.operation == "export":
                result["export_scope"] = "bounded_sanitized_metadata_page"
        else:
            prepare_state(args.state_dir)
            config = private_json(args.state_dir / "live-config.json")
            secrets = private_json(args.state_dir / "live-secrets.json")
            if config.get("schema") != "liquilens.customer-live-connector.v1":
                raise ValueError("unsupported_configuration")
            binding = TradeSafetyExecutionBinding(**config["binding"])
            limits = {**config["limits"], "symbols": tuple(config["limits"]["symbols"])}
            broker = AlpacaLiveTransport(
                api_key=secrets["api_key"], secret_key=secrets["secret_key"]
            )

            # Re-read activation immediately before submission, including after I/O.
            def activated():
                current = private_json(args.state_dir / "live-config.json")
                return (
                    current.get("binding") == config["binding"]
                    and current.get("limits") == config["limits"]
                    and current.get("live_enabled") is True
                    and current.get("activation_acknowledgment")
                    == "LIVE-ACCOUNT:" + binding.account_id
                )

            client = LiveAccountConnector(
                state_dir=args.state_dir,
                binding=binding,
                hmac_key=bytes.fromhex(secrets["receipt_hmac_key_hex"]),
                limits=LiveLimits(**limits),
                broker=broker,
                activated=activated,
            )
            if args.operation in {"preview", "submit"}:
                result = getattr(client, args.operation)(
                    payload(args.request), payload(args.receipt)
                )
            else:
                result = client.inspect(
                    args.request_hash,
                    reconcile=args.operation == "reconcile",
                    cancel=args.operation == "cancel",
                )
    except Exception as error:
        # Provider errors, paths, receipts and credentials never enter stdout.
        result = {
            "mode": "live",
            "error": "live_operation_not_completed",
            "resubmit_allowed": False,
            "next_action": "retain_request_hash_and_review_configuration_or_reconcile",
        }
        if isinstance(error, LiveExecutionBlocked):
            result["reason_code"] = str(error)
    finally:
        if broker:
            broker.close()
    print(json.dumps(result, allow_nan=False))
    return (
        2
        if result.get("error")
        or result.get("state") == "uncertain"
        or (args.operation == "doctor" and not result["live_ready"])
        or (args.operation == "check-account" and not result["account_qualified"])
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
