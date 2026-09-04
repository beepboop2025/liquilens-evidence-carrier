"""Dormant-by-default operator configuration for the x402 access route.

The payment rail is an access-control plane beside Trade Safety, never an input
to its evidence or policy decision.  A partial configuration fails at startup
so the gateway cannot advertise a paid resource that it cannot safely settle.
"""

from __future__ import annotations

import base64
import binascii
import errno
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised through the explicit guard
    fcntl = None  # type: ignore[assignment]

from liquilens_evidence.trade_safety import (
    TRADE_SAFETY_POLICY_SCHEMA,
    TRADE_SAFETY_REQUEST_SCHEMA,
)

from .x402_access import (
    HttpxFacilitator,
    SQLiteSettlementJournal,
    X402AccessGate,
    X402Config,
)

X402_ENV_PREFIX: Final = "TRADE_SAFETY_X402_"
X402_RESOURCE_URL_ENV: Final = f"{X402_ENV_PREFIX}RESOURCE_URL"
X402_FACILITATOR_URL_ENV: Final = f"{X402_ENV_PREFIX}FACILITATOR_URL"
X402_NETWORK_ENV: Final = f"{X402_ENV_PREFIX}NETWORK"
X402_AMOUNT_ENV: Final = f"{X402_ENV_PREFIX}AMOUNT"
X402_ASSET_ENV: Final = f"{X402_ENV_PREFIX}ASSET"
X402_ASSET_NAME_ENV: Final = f"{X402_ENV_PREFIX}ASSET_NAME"
X402_ASSET_VERSION_ENV: Final = f"{X402_ENV_PREFIX}ASSET_VERSION"
X402_ASSET_TRANSFER_METHOD_ENV: Final = f"{X402_ENV_PREFIX}ASSET_TRANSFER_METHOD"
X402_PAY_TO_ENV: Final = f"{X402_ENV_PREFIX}PAY_TO"
X402_QUOTE_BINDING_KEY_ENV: Final = f"{X402_ENV_PREFIX}QUOTE_BINDING_KEY_B64"
X402_JOURNAL_PATH_ENV: Final = f"{X402_ENV_PREFIX}JOURNAL_PATH"

_REQUIRED_ENVIRONMENT_NAMES: Final = frozenset(
    {
        X402_RESOURCE_URL_ENV,
        X402_FACILITATOR_URL_ENV,
        X402_NETWORK_ENV,
        X402_AMOUNT_ENV,
        X402_ASSET_ENV,
        X402_ASSET_NAME_ENV,
        X402_ASSET_VERSION_ENV,
        X402_PAY_TO_ENV,
        X402_QUOTE_BINDING_KEY_ENV,
        X402_JOURNAL_PATH_ENV,
    }
)
_OPTIONAL_ENVIRONMENT_NAMES: Final = frozenset({X402_ASSET_TRANSFER_METHOD_ENV})
_KNOWN_ENVIRONMENT_NAMES: Final = (
    _REQUIRED_ENVIRONMENT_NAMES | _OPTIONAL_ENVIRONMENT_NAMES
)


class X402RuntimeConfigurationError(RuntimeError):
    """The operator supplied a partial or unsafe x402 configuration."""


class X402RuntimeBusyError(X402RuntimeConfigurationError):
    """Another gateway or reconciliation process owns this journal."""


@dataclass(slots=True)
class _ExclusiveRuntimeLock:
    """Process-lifetime interlock shared by the gateway and operator CLI."""

    path: Path
    _descriptor: int | None

    @classmethod
    def acquire(cls, journal_path: Path) -> _ExclusiveRuntimeLock:
        if fcntl is None:
            raise X402RuntimeConfigurationError(
                "x402 runtime requires POSIX advisory file locks"
            )
        path = Path(f"{journal_path}.runtime.lock")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise X402RuntimeConfigurationError(
                "x402 runtime lock must be a private regular file"
            ) from exc
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or (hasattr(os, "geteuid") and metadata.st_uid != os.geteuid())
            ):
                raise X402RuntimeConfigurationError(
                    "x402 runtime lock must be a private service-owned regular file"
                )
            os.fchmod(descriptor, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    raise X402RuntimeBusyError(
                        "x402 journal already has an active runtime"
                    ) from exc
                raise X402RuntimeConfigurationError(
                    "x402 runtime lock could not be acquired"
                ) from exc
            return cls(path=path, _descriptor=descriptor)
        except Exception:
            os.close(descriptor)
            raise

    def close(self) -> None:
        descriptor = self._descriptor
        if descriptor is None:
            return
        self._descriptor = None
        try:
            assert fcntl is not None
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


@dataclass(slots=True)
class X402Runtime:
    """Owned facilitator and durable settlement journal for one paid route."""

    gate: X402AccessGate
    journal: SQLiteSettlementJournal
    _runtime_lock: _ExclusiveRuntimeLock | None = None

    async def aclose(self) -> None:
        try:
            await self.gate.aclose()
        finally:
            try:
                self.journal.close()
            finally:
                if self._runtime_lock is not None:
                    self._runtime_lock.close()


def _required_value(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name)
    if not isinstance(value, str) or not value.strip():
        raise X402RuntimeConfigurationError(
            "x402 configuration is incomplete or contains an empty value"
        )
    return value.strip()


def _quote_binding_key(value: str) -> bytes:
    try:
        encoded = value.encode("ascii", errors="strict")
    except UnicodeEncodeError as exc:
        raise X402RuntimeConfigurationError(
            "x402 quote-binding key must be canonical base64"
        ) from exc
    if len(encoded) % 4 != 0 or any(character.isspace() for character in value):
        raise X402RuntimeConfigurationError(
            "x402 quote-binding key must be canonical base64"
        )
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise X402RuntimeConfigurationError(
            "x402 quote-binding key must be canonical base64"
        ) from exc
    if base64.b64encode(decoded) != encoded or not 32 <= len(decoded) <= 128:
        raise X402RuntimeConfigurationError(
            "x402 quote-binding key must decode to 32 through 128 bytes"
        )
    return decoded


def _assert_private_regular_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or (hasattr(os, "geteuid") and metadata.st_uid != os.geteuid())
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise X402RuntimeConfigurationError(
            "x402 journal files must be private service-owned regular files"
        )


def _journal_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise X402RuntimeConfigurationError(
            "x402 journal path must be an absolute file path"
        )
    parent = path.parent
    try:
        metadata = parent.lstat()
    except FileNotFoundError as exc:
        raise X402RuntimeConfigurationError(
            "x402 journal parent directory must already exist"
        ) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or (hasattr(os, "geteuid") and metadata.st_uid != os.geteuid())
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise X402RuntimeConfigurationError(
            "x402 journal parent must be a private service-owned directory"
        )
    for candidate in (
        path,
        Path(f"{path}-wal"),
        Path(f"{path}-shm"),
        Path(f"{path}.runtime.lock"),
    ):
        _assert_private_regular_file(candidate)
    return path


def _bazaar_extension() -> dict[str, object]:
    """Return static, non-identifying x402 Bazaar v2 discovery metadata."""

    info = {
        "input": {
            "type": "http",
            "method": "POST",
            "bodyType": "json",
            "body": {
                "request": {
                    "schema": TRADE_SAFETY_REQUEST_SCHEMA,
                    "request_id": "replace-with-a-unique-request-id",
                    "created_at": "replace-with-current-RFC3339-time",
                    "expires_at": "replace-with-a-short-RFC3339-expiry",
                    "mode": "paper",
                    "agent": {
                        "agent_id": "your-agent",
                        "operator_id": "your-operator",
                        "tenant_id": "your-tenant",
                        "account_id": "your-paper-account",
                        "runtime": "your-runtime/version",
                        "strategy_id": "your-strategy",
                        "authorization_scope": [
                            "evidence:read",
                            "orders:paper",
                        ],
                    },
                    "order": {
                        "instrument": {
                            "asset_class": "crypto",
                            "symbol": "BTC/USD",
                            "identifiers": {},
                        },
                        "side": "sell",
                        "order_type": "market",
                        "notional": {"amount": 1000, "currency": "USD"},
                        "quantity": None,
                        "limit_price": None,
                        "stop_price": None,
                        "venue": None,
                        "time_in_force": "IOC",
                    },
                    "policy_ref": {
                        "policy_id": "sandbox-default",
                        "version": "1.0.0",
                    },
                    "extensions": {},
                },
                "policy": {
                    "schema": TRADE_SAFETY_POLICY_SCHEMA,
                    "policy_id": "sandbox-default",
                    "version": "1.0.0",
                    "required_products": ["seiche", "undertow"],
                    "max_evidence_age_seconds": {
                        "seiche": 691200,
                        "undertow": 86400,
                        "liquilens": 86400,
                    },
                    "hold_regimes": ["STRESS"],
                    "max_notional_usd": 100000,
                    "max_exit_cost_bps": 50,
                    "max_venue_spread_bps": 20,
                    "missing_evidence": "fail_closed",
                    "live_requires_executable_quote": True,
                    "live_requires_broker_preview": True,
                    "auto_resize": False,
                    "extensions": {},
                },
            },
        },
        "output": {
            "type": "json",
            "example": {
                "decision": {"outcome": "unavailable"},
                "authority": {
                    "can_execute": False,
                    "can_recommend": False,
                },
            },
        },
    }
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "input": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "const": "http"},
                    "method": {"type": "string", "enum": ["POST"]},
                    "bodyType": {"type": "string", "enum": ["json"]},
                    "body": {"type": "object"},
                },
                "required": ["type", "method", "bodyType", "body"],
                "additionalProperties": False,
            },
            "output": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "const": "json"},
                    "example": {"type": "object"},
                },
                "required": ["type"],
                "additionalProperties": False,
            },
        },
        "required": ["input"],
        "additionalProperties": False,
    }
    return {"info": info, "schema": schema}


def x402_runtime_from_env(
    environ: Mapping[str, str] | None = None,
    *,
    maintenance: bool = False,
) -> X402Runtime | None:
    """Build a complete x402 runtime, or return ``None`` when fully absent."""

    if type(maintenance) is not bool:
        raise TypeError("maintenance must be a boolean")

    source = os.environ if environ is None else environ
    supplied = {name for name in source if name.startswith(X402_ENV_PREFIX)}
    unknown = supplied - _KNOWN_ENVIRONMENT_NAMES
    if unknown:
        raise X402RuntimeConfigurationError(
            "x402 configuration contains an unknown environment setting"
        )
    if not supplied:
        return None
    if not supplied >= _REQUIRED_ENVIRONMENT_NAMES:
        raise X402RuntimeConfigurationError(
            "x402 configuration is incomplete or contains an empty value"
        )

    offer_extra = {
        "name": _required_value(source, X402_ASSET_NAME_ENV),
        "version": _required_value(source, X402_ASSET_VERSION_ENV),
    }
    if X402_ASSET_TRANSFER_METHOD_ENV in supplied:
        offer_extra["assetTransferMethod"] = _required_value(
            source, X402_ASSET_TRANSFER_METHOD_ENV
        )
    try:
        config = X402Config(
            resource_url=_required_value(source, X402_RESOURCE_URL_ENV),
            facilitator_url=_required_value(source, X402_FACILITATOR_URL_ENV),
            network=_required_value(source, X402_NETWORK_ENV),
            amount=_required_value(source, X402_AMOUNT_ENV),
            asset=_required_value(source, X402_ASSET_ENV),
            pay_to=_required_value(source, X402_PAY_TO_ENV),
            quote_binding_key=_quote_binding_key(
                _required_value(source, X402_QUOTE_BINDING_KEY_ENV)
            ),
            offer_extra=offer_extra,
            resource_info_extra={
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
            },
            required_extensions={"bazaar": _bazaar_extension()},
        )
    except (TypeError, ValueError) as exc:
        raise X402RuntimeConfigurationError(
            "x402 configuration failed strict validation"
        ) from exc
    if urlsplit(config.resource_url).path != "/v1/x402/check":
        raise X402RuntimeConfigurationError(
            "x402 resource URL must identify the gateway /v1/x402/check route"
        )
    journal_path = _journal_path(_required_value(source, X402_JOURNAL_PATH_ENV))
    runtime_lock = _ExclusiveRuntimeLock.acquire(journal_path)
    try:
        journal = SQLiteSettlementJournal(
            journal_path,
            max_cached_response_bytes=config.max_cached_response_bytes,
        )
        # sqlite follows the process umask for a newly created database. Tighten
        # it explicitly before the runtime can accept any payment identity.
        os.chmod(journal_path, 0o600, follow_symlinks=False)
        _assert_private_regular_file(journal_path)
        facilitator = HttpxFacilitator(config)
        gate = X402AccessGate(
            config,
            facilitator=facilitator,
            journal=journal,
            maintenance=maintenance,
        )
    except Exception:
        if "journal" in locals():
            journal.close()
        runtime_lock.close()
        raise
    return X402Runtime(
        gate=gate,
        journal=journal,
        _runtime_lock=runtime_lock,
    )


__all__ = [
    "X402Runtime",
    "X402RuntimeBusyError",
    "X402RuntimeConfigurationError",
    "x402_runtime_from_env",
]
