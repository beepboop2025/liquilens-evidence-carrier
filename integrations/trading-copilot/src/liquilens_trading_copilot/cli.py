"""Operator CLI: diagnose, initialize private state, run paper cycles, reconcile."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import (
    SCOPED_PROFILE,
    ConfigurationError,
    CopilotConfig,
    PaperCredentials,
    load_config,
    load_secret_file,
    scoped_policy,
)
from .diagnostics import collect_readiness
from .runner import run_configured_cycle, utc_now
from .state import CycleStore, operator_lock, prepare_state


def _emit(value: Any) -> None:
    print(json.dumps(value, sort_keys=True, allow_nan=False))


def _exclusive_file(path: Path, body: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())


def _initialize(path: Path, profile: str = SCOPED_PROFILE) -> dict[str, Any]:
    prepare_state(path)
    config = CopilotConfig(state_dir=str(path))
    if profile == SCOPED_PROFILE:
        from dataclasses import replace

        config = replace(config, evidence_profile=profile, policy=scoped_policy())
    # Refuse overwrite of either identity/secret file, including dangling links.
    if any(os.path.lexists(path / name) for name in ("config.json", "paper.env")):
        raise ConfigurationError("operator_configuration_already_exists")
    _exclusive_file(path / "config.json", json.dumps(asdict(config), indent=2) + "\n")
    _exclusive_file(
        path / "paper.env",
        (
            "# Literal values only. No shell expansion. Never paste these into chat.\n"
            "ALPACA_PAPER_API_KEY=\nALPACA_PAPER_SECRET_KEY=\n"
            f"COPILOT_PAPER_HMAC_KEY={secrets.token_hex(32)}\n"
        ),
    )
    return {
        "status": "initialized_disabled",
        "mode": "paper",
        "config": str(path / "config.json"),
        "secret_file": str(path / "paper.env"),
        "order_submission_enabled": False,
        "evidence_profile": profile,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["init", "diagnose", "research", "once", "reconcile", "status", "stop"],
    )
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument(
        "--profile",
        choices=["native_gateway_v1", SCOPED_PROFILE],
        default=SCOPED_PROFILE,
    )
    args = parser.parse_args()
    try:
        if args.command == "init":
            if args.state_dir is None:
                parser.error("init requires --state-dir")
            _emit(_initialize(args.state_dir, args.profile))
            return 0
        config = load_config(args.config) if args.config else CopilotConfig()
        if args.command not in {"diagnose", "research"} and args.config is None:
            parser.error("this command requires --config")
        if args.command == "research":
            from .riptide import collect_riptide_context

            _emit(asyncio.run(collect_riptide_context()))
            return 0
        credentials = None
        if args.command in {"diagnose", "once", "reconcile"}:
            try:
                env = load_secret_file(args.env_file) if args.env_file else None
                credentials = PaperCredentials.from_environment(env)
            except ConfigurationError as error:
                if str(error) not in {
                    "paper_credentials_missing",
                    "operator_hmac_key_missing_or_too_short",
                }:
                    raise
        if args.command == "diagnose":
            if config.evidence_profile == SCOPED_PROFILE:
                from .riptide import collect_riptide_context
                from .scoped import scoped_readiness

                async def inspect_scoped() -> dict[str, Any]:
                    readiness, research = await asyncio.gather(
                        scoped_readiness(
                            paper_credentials_present=credentials is not None,
                            account_id_configured=bool(config.account_id),
                        ),
                        collect_riptide_context(),
                    )
                    return {**readiness, "riptide_research": research}

                report = asyncio.run(inspect_scoped())
            else:
                report = asyncio.run(
                    collect_readiness(
                        paper_credentials_present=credentials is not None,
                        account_id_configured=bool(config.account_id),
                    )
                )
            _emit(report)
            return 0
        path = Path(config.state_dir)
        if args.command == "stop":
            prepare_state(path)
            if not os.path.lexists(path / "STOP"):
                _exclusive_file(path / "STOP", "Operator stopped paper submission.\n")
            _emit(
                {
                    "status": "stopped",
                    "mode": "paper",
                    "existing_orders_cancelled": False,
                }
            )
            return 0
        with operator_lock(path):
            store = CycleStore(path)
            try:
                if args.command == "status":
                    _emit(
                        {"mode": "paper", "enabled": config.enabled, **store.status()}
                    )
                    return 0
                try:
                    result = asyncio.run(
                        run_configured_cycle(
                            config,
                            store,
                            credentials,
                            reconcile_only=args.command == "reconcile",
                        )
                    )
                except Exception as error:
                    # Keep network errors/SDK response bodies and secret values
                    # out of the console. Recovery state remains in the journal.
                    result = {
                        "mode": "paper",
                        "status": "error",
                        "error_type": type(error).__name__,
                    }
                    store.event("cycle_error", result, utc_now())
                    _emit(result)
                    return 1
                # Full evidence is available only in the private audit database.
                _emit(
                    {
                        key: value
                        for key, value in result.items()
                        if key not in {"receipt", "request", "portfolio"}
                    }
                )
                return 0
            finally:
                store.close()
    except (ConfigurationError, OSError, ValueError) as error:
        _emit({"status": "configuration_error", "error_type": type(error).__name__})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
