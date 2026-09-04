"""Local, redacted operator CLI for x402 settlement reconciliation.

This module deliberately exposes no HTTP route and never calls the facilitator.
It opens only the runtime's configured private journal, then delegates terminal
state changes to the journal's compare-and-swap reconciliation API.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .x402_access import X402AccessError
from .x402_runtime import (
    X402Runtime,
    X402RuntimeBusyError,
    X402RuntimeConfigurationError,
    x402_runtime_from_env,
)

MAX_RECONCILIATION_RESPONSE_BYTES = 64 * 1024


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _private_response_file(value: str) -> bytes:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("reconciliation response path must be absolute")
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or (hasattr(os, "geteuid") and metadata.st_uid != os.geteuid())
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or metadata.st_size > MAX_RECONCILIATION_RESPONSE_BYTES
    ):
        raise ValueError("reconciliation response file is not private and bounded")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != metadata.st_dev
            or opened.st_ino != metadata.st_ino
            or (hasattr(os, "geteuid") and opened.st_uid != os.geteuid())
            or stat.S_IMODE(opened.st_mode) & 0o077
            or opened.st_size > MAX_RECONCILIATION_RESPONSE_BYTES
        ):
            raise ValueError("reconciliation response file changed or is not private")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 8192)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RECONCILIATION_RESPONSE_BYTES:
                raise ValueError("reconciliation response exceeds its byte budget")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _settlement_response(path: str) -> dict[str, Any]:
    raw = _private_response_file(path)
    try:
        parsed = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
    ) as exc:
        raise ValueError("settlement response must be one strict JSON object") from exc
    if not isinstance(parsed, dict):
        raise ValueError("settlement response must be one strict JSON object")
    return parsed


def _limit(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer") from exc
    if not 1 <= parsed <= 100:
        raise argparse.ArgumentTypeError("limit must be between 1 and 100")
    return parsed


def _retention_days(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("retention days must be an integer") from exc
    if not 1 <= parsed <= 36_500:
        raise argparse.ArgumentTypeError("retention days must be between 1 and 36500")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="liquilens-trade-safety-x402-reconcile",
        description=(
            "Inspect or reconcile the configured private x402 journal without "
            "calling a facilitator"
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="show redacted capacity and attempts")
    status.add_argument("--limit", type=_limit, default=100)

    settled = commands.add_parser(
        "reconcile-settled",
        help="finalize an independently confirmed successful settlement",
    )
    settled.add_argument("payment_id")
    settled.add_argument(
        "--response",
        metavar="PRIVATE_JSON_FILE",
        help=(
            "private absolute path to a confirmed settlement response; omit "
            "only when the journal already captured a final response"
        ),
    )

    retired = commands.add_parser(
        "retire-unsettled",
        help="permanently retire an independently confirmed unpaid authorization",
    )
    retired.add_argument("payment_id")
    retired.add_argument(
        "--confirm-not-settled",
        action="store_true",
        help=(
            "required acknowledgement that independent reconciliation found no payment"
        ),
    )

    retained = commands.add_parser(
        "retire-terminal-responses",
        help="retire aged settled and failed-settlement response material",
    )
    retained.add_argument(
        "--older-than-days",
        required=True,
        type=_retention_days,
        metavar="DAYS",
    )
    retained.add_argument("--limit", type=_limit, default=100)
    retained.add_argument(
        "--confirm-replay-loss",
        action="store_true",
        help="required acknowledgement that exact paid replay will be removed",
    )
    return parser


def _write(payload: dict[str, Any]) -> None:
    print(
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


async def _execute(runtime: X402Runtime, arguments: argparse.Namespace) -> None:
    gate = runtime.gate
    if arguments.command == "status":
        _write(
            {
                "counts": asdict(gate.journal_counts()),
                "records": [
                    asdict(record)
                    for record in gate.reconciliation_records(limit=arguments.limit)
                ],
                "schema": "liquilens.trade-safety-x402-reconciliation.v1",
            }
        )
        return
    if arguments.command == "reconcile-settled":
        response = (
            None
            if arguments.response is None
            else _settlement_response(arguments.response)
        )
        gate.reconcile_settled(arguments.payment_id, response)
        _write(
            {
                "ok": True,
                "payment_id": arguments.payment_id,
                "state": "settled",
            }
        )
        return
    if arguments.command == "retire-unsettled":
        if arguments.confirm_not_settled is not True:
            raise X402AccessError(
                "reconciliation_confirmation_required",
                "retirement requires explicit independent confirmation",
                http_status=400,
            )
        gate.retire_unsettled(arguments.payment_id)
        _write(
            {
                "ok": True,
                "payment_id": arguments.payment_id,
                "state": "payment_authorization_retired",
            }
        )
        return
    if arguments.command == "retire-terminal-responses":
        if arguments.confirm_replay_loss is not True:
            raise X402AccessError(
                "retention_confirmation_required",
                "retention requires explicit replay-loss confirmation",
                http_status=400,
            )
        retired_count = gate.retire_terminal_responses(
            older_than_seconds=arguments.older_than_days * 86_400,
            limit=arguments.limit,
        )
        _write(
            {
                "ok": True,
                "older_than_days": arguments.older_than_days,
                "retired_count": retired_count,
                "state": "terminal_response_material_retired",
            }
        )
        return
    raise RuntimeError("unsupported reconciliation command")


async def _run(arguments: argparse.Namespace) -> None:
    runtime = x402_runtime_from_env(maintenance=True)
    if runtime is None:
        raise X402RuntimeConfigurationError("x402 runtime is disabled")
    try:
        await _execute(runtime, arguments)
    finally:
        await runtime.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        asyncio.run(_run(arguments))
    except X402AccessError as exc:
        _write({"code": exc.code, "ok": False})
        return 1
    except X402RuntimeBusyError:
        _write({"code": "reconciliation_runtime_active", "ok": False})
        return 1
    except X402RuntimeConfigurationError:
        _write({"code": "x402_configuration_invalid", "ok": False})
        return 1
    except (OSError, ValueError):
        _write({"code": "reconciliation_input_invalid", "ok": False})
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - console entry point
    raise SystemExit(main())


__all__ = ["MAX_RECONCILIATION_RESPONSE_BYTES", "main"]
