"""Fail-closed paper-order middleware for Trade Safety Receipts."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Generic, Protocol, TypeVar

from .trade_safety import (
    TradeSafetyError,
    TradeSafetyMode,
    TradeSafetyOutcome,
    trade_safety_request_hash,
    validate_trade_safety_request,
    verify_trade_safety_receipt,
)

_ORDER_AUTHORIZATION_SEAL = object()
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
SubmitResult = TypeVar("SubmitResult")


class TradeSafetyOrderGuardError(TradeSafetyError):
    """Base error for order-guard configuration and enforcement failures."""


class TradeSafetyOrderBlocked(TradeSafetyOrderGuardError):
    """A proposed order was stopped before the protected submit callable ran."""

    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        outcome: TradeSafetyOutcome | None = None,
        receipt_id: str | None = None,
    ) -> None:
        super().__init__(f"{reason_code}: {message}")
        self.reason_code = reason_code
        self.outcome = outcome
        self.receipt_id = receipt_id


def _required_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise TradeSafetyOrderGuardError(f"{field_name} must be a non-blank string")


@dataclass(frozen=True, slots=True)
class TradeSafetyExecutionBinding:
    """Operator-owned identity and policy pinned to one broker credential lane."""

    account_id: str
    tenant_id: str
    operator_id: str
    agent_id: str
    runtime: str
    strategy_id: str | None
    policy_id: str
    policy_version: str
    policy_hash: str
    issuer_name: str
    issuer_version: str
    issuer_endpoint: str
    hmac_key_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "account_id",
            "tenant_id",
            "operator_id",
            "agent_id",
            "runtime",
            "policy_id",
            "policy_version",
            "issuer_name",
            "issuer_version",
            "issuer_endpoint",
        ):
            _required_text(getattr(self, field_name), field_name)
        if self.strategy_id is not None:
            _required_text(self.strategy_id, "strategy_id")
        if self.hmac_key_id is not None:
            _required_text(self.hmac_key_id, "hmac_key_id")
        if _SHA256_RE.fullmatch(self.policy_hash) is None:
            raise TradeSafetyOrderGuardError(
                "policy_hash must be a lowercase SHA-256 digest"
            )


class ReceiptConsumer(Protocol):
    """Atomic paper-receipt claim store implemented inside the operator boundary."""

    def consume(
        self,
        *,
        receipt_id: str,
        request_hash: str,
        expires_at: str,
    ) -> bool:
        """Return true only for the first unexpired atomic claim."""


class AsyncReceiptConsumer(Protocol):
    """Native asynchronous paper-receipt claim store."""

    async def consume(
        self,
        *,
        receipt_id: str,
        request_hash: str,
        expires_at: str,
    ) -> bool:
        """Return true only for the first unexpired atomic claim."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _is_async_callable(value: Any) -> bool:
    if inspect.iscoroutinefunction(value):
        return True
    if not callable(value):
        return False
    return inspect.iscoroutinefunction(value.__call__)


def _clock_value(clock: Callable[[], datetime], field_name: str) -> datetime:
    try:
        value = clock()
    except Exception as error:
        raise TradeSafetyOrderGuardError(f"{field_name} is unavailable") from error
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise TradeSafetyOrderGuardError(
            f"{field_name} must return a timezone-aware datetime"
        )
    return value.astimezone(UTC)


def _expiry(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (AttributeError, ValueError) as error:
        raise TradeSafetyOrderGuardError("receipt expiry is invalid") from error
    if parsed.tzinfo is None:
        raise TradeSafetyOrderGuardError("receipt expiry must be timezone-aware")
    return parsed.astimezone(UTC)


class InMemoryReceiptConsumer:
    """Bounded thread-safe replay guard for local paper tests and demos."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = _utc_now,
        max_entries: int = 10_000,
    ) -> None:
        if isinstance(max_entries, bool) or not isinstance(max_entries, int):
            raise TradeSafetyOrderGuardError("max_entries must be a positive integer")
        if max_entries <= 0:
            raise TradeSafetyOrderGuardError("max_entries must be a positive integer")
        self._used: dict[str, datetime] = {}
        self._lock = Lock()
        self._clock = clock
        self._max_entries = max_entries
        self._last_now: datetime | None = None

    def consume(
        self,
        *,
        receipt_id: str,
        request_hash: str,
        expires_at: str,
    ) -> bool:
        del request_hash
        expiry = _expiry(expires_at)
        now = _clock_value(self._clock, "receipt-consumer clock")
        with self._lock:
            if self._last_now is not None and now < self._last_now:
                return False
            self._last_now = now
            expired = [key for key, deadline in self._used.items() if deadline <= now]
            for key in expired:
                del self._used[key]
            if expiry <= now or receipt_id in self._used:
                return False
            if len(self._used) >= self._max_entries:
                return False
            self._used[receipt_id] = expiry
            return True


class AsyncInMemoryReceiptConsumer:
    """Bounded async replay guard for local paper tests and demos."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = _utc_now,
        max_entries: int = 10_000,
    ) -> None:
        if isinstance(max_entries, bool) or not isinstance(max_entries, int):
            raise TradeSafetyOrderGuardError("max_entries must be a positive integer")
        if max_entries <= 0:
            raise TradeSafetyOrderGuardError("max_entries must be a positive integer")
        self._used: dict[str, datetime] = {}
        self._lock = asyncio.Lock()
        self._clock = clock
        self._max_entries = max_entries
        self._last_now: datetime | None = None

    async def consume(
        self,
        *,
        receipt_id: str,
        request_hash: str,
        expires_at: str,
    ) -> bool:
        del request_hash
        expiry = _expiry(expires_at)
        now = _clock_value(self._clock, "async receipt-consumer clock")
        async with self._lock:
            if self._last_now is not None and now < self._last_now:
                return False
            self._last_now = now
            expired = [key for key, deadline in self._used.items() if deadline <= now]
            for key in expired:
                del self._used[key]
            if expiry <= now or receipt_id in self._used:
                return False
            if len(self._used) >= self._max_entries:
                return False
            self._used[receipt_id] = expiry
            return True


@dataclass(frozen=True, slots=True)
class TradeSafetyOrderAuthorization:
    """Verified paper-order input passed to a protected broker/OMS adapter."""

    request_json: str
    receipt_json: str
    request_hash: str
    receipt_id: str
    authenticated: bool
    binding: TradeSafetyExecutionBinding
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal is not _ORDER_AUTHORIZATION_SEAL:
            raise TypeError(
                "TradeSafetyOrderAuthorization values must come from before_order"
            )

    @property
    def request(self) -> dict[str, Any]:
        value = json.loads(self.request_json)
        if not isinstance(value, dict):  # pragma: no cover - constructor invariant
            raise TypeError("authorized request root changed shape")
        return value

    @property
    def order(self) -> dict[str, Any]:
        order = self.request["order"]
        if not isinstance(order, dict):  # pragma: no cover - validator invariant
            raise TypeError("authorized order changed shape")
        return order

    @property
    def receipt(self) -> dict[str, Any]:
        value = json.loads(self.receipt_json)
        if not isinstance(value, dict):  # pragma: no cover - constructor invariant
            raise TypeError("authorized receipt root changed shape")
        return value


def _block(
    reason_code: str,
    message: str,
    *,
    outcome: TradeSafetyOutcome | None = None,
    receipt_id: str | None = None,
) -> TradeSafetyOrderBlocked:
    return TradeSafetyOrderBlocked(
        reason_code,
        message,
        outcome=outcome,
        receipt_id=receipt_id,
    )


def _assert_execution_binding(
    request: Mapping[str, Any],
    receipt: Mapping[str, Any],
    binding: TradeSafetyExecutionBinding,
) -> None:
    agent = request["agent"]
    expected_agent = {
        "account_id": binding.account_id,
        "tenant_id": binding.tenant_id,
        "operator_id": binding.operator_id,
        "agent_id": binding.agent_id,
        "runtime": binding.runtime,
        "strategy_id": binding.strategy_id,
    }
    if any(agent[field] != expected for field, expected in expected_agent.items()):
        raise _block(
            "execution_context_mismatch",
            "request identity does not match this broker credential lane",
            receipt_id=receipt["receipt_id"],
        )
    if request["policy_ref"] != {
        "policy_id": binding.policy_id,
        "version": binding.policy_version,
    }:
        raise _block(
            "policy_identity_mismatch",
            "request policy identity does not match the operator binding",
            receipt_id=receipt["receipt_id"],
        )
    if receipt["policy_hash"] != binding.policy_hash:
        raise _block(
            "policy_hash_mismatch",
            "receipt policy content does not match the operator binding",
            receipt_id=receipt["receipt_id"],
        )
    if receipt["issuer"] != {
        "name": binding.issuer_name,
        "version": binding.issuer_version,
        "endpoint": binding.issuer_endpoint,
    }:
        raise _block(
            "issuer_mismatch",
            "receipt issuer does not match the trusted gateway binding",
            receipt_id=receipt["receipt_id"],
        )
    if receipt["integrity"]["key_id"] != binding.hmac_key_id:
        raise _block(
            "integrity_key_mismatch",
            "receipt integrity key does not match the operator binding",
            receipt_id=receipt["receipt_id"],
        )


def _verify_order(
    proposed_request: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    evaluated_at: datetime,
    binding: TradeSafetyExecutionBinding,
    hmac_key: bytes | None,
) -> TradeSafetyOrderAuthorization:
    try:
        normalized_request = validate_trade_safety_request(proposed_request)
    except TradeSafetyError as error:
        raise _block("request_invalid", str(error)) from error
    if normalized_request["mode"] != TradeSafetyMode.PAPER.value:
        raise _block(
            "mode_not_supported",
            "the reference order guard is paper-only; live routing is held",
        )

    try:
        verified = verify_trade_safety_receipt(
            receipt,
            evaluated_at=evaluated_at,
            hmac_key=hmac_key,
        )
    except TradeSafetyError as error:
        raise _block("receipt_invalid", str(error)) from error

    verified_receipt = verified.receipt
    receipt_id = verified_receipt["receipt_id"]
    request_hash = trade_safety_request_hash(normalized_request)
    if (
        request_hash != verified_receipt["request_hash"]
        or normalized_request != verified_receipt["request"]
    ):
        raise _block(
            "request_mismatch",
            "receipt does not bind the exact proposed request",
            receipt_id=receipt_id,
        )
    _assert_execution_binding(normalized_request, verified_receipt, binding)

    decision = verified_receipt["decision"]
    outcome = verified.outcome
    if decision["enforced"] is not True:
        raise _block(
            "decision_not_enforced",
            "receipt was produced for an observation-only workflow",
            outcome=outcome,
            receipt_id=receipt_id,
        )
    if outcome is not TradeSafetyOutcome.PASS:
        raise _block(
            f"decision_{outcome.value}",
            "operator policy did not authorize this exact paper order",
            outcome=outcome,
            receipt_id=receipt_id,
        )

    request_json = json.dumps(
        normalized_request,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return TradeSafetyOrderAuthorization(
        request_json=request_json,
        receipt_json=verified.receipt_json,
        request_hash=request_hash,
        receipt_id=receipt_id,
        authenticated=verified.authenticated,
        binding=binding,
        _seal=_ORDER_AUTHORIZATION_SEAL,
    )


def _consume_error(
    authorization: TradeSafetyOrderAuthorization,
    error: Exception | None = None,
) -> TradeSafetyOrderBlocked:
    if error is not None:
        return _block(
            "receipt_consumer_unavailable",
            "receipt replay protection could not be reached",
            outcome=TradeSafetyOutcome.PASS,
            receipt_id=authorization.receipt_id,
        )
    return _block(
        "receipt_replay",
        "receipt was expired, already claimed, or could not be atomically claimed",
        outcome=TradeSafetyOutcome.PASS,
        receipt_id=authorization.receipt_id,
    )


def before_order(
    proposed_request: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    evaluated_at: datetime,
    binding: TradeSafetyExecutionBinding,
    receipt_consumer: ReceiptConsumer,
    hmac_key: bytes | None = None,
) -> TradeSafetyOrderAuthorization:
    """Low-level deterministic paper-order hook for trusted adapter code.

    Agent-facing tools should expose :class:`PaperTradeSafetyOrderGateway`, whose
    clock is owned by the operator boundary. This explicit clock exists only for
    deterministic replay and conformance tests.
    """

    authorization = _verify_order(
        proposed_request,
        receipt,
        evaluated_at=evaluated_at,
        binding=binding,
        hmac_key=hmac_key,
    )
    try:
        consumed = receipt_consumer.consume(
            receipt_id=authorization.receipt_id,
            request_hash=authorization.request_hash,
            expires_at=authorization.receipt["expires_at"],
        )
    except Exception as error:
        raise _consume_error(authorization, error) from error
    if consumed is not True:
        raise _consume_error(authorization)
    return authorization


async def before_order_async(
    proposed_request: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    evaluated_at: datetime,
    binding: TradeSafetyExecutionBinding,
    receipt_consumer: AsyncReceiptConsumer,
    hmac_key: bytes | None = None,
) -> TradeSafetyOrderAuthorization:
    """Native-async equivalent of :func:`before_order` for trusted adapters."""

    authorization = _verify_order(
        proposed_request,
        receipt,
        evaluated_at=evaluated_at,
        binding=binding,
        hmac_key=hmac_key,
    )
    try:
        consumed = await receipt_consumer.consume(
            receipt_id=authorization.receipt_id,
            request_hash=authorization.request_hash,
            expires_at=authorization.receipt["expires_at"],
        )
    except Exception as error:
        raise _consume_error(authorization, error) from error
    if consumed is not True:
        raise _consume_error(authorization)
    return authorization


def _validate_integrity_configuration(
    binding: TradeSafetyExecutionBinding, hmac_key: bytes | None
) -> None:
    if binding.hmac_key_id is None or hmac_key is None:
        raise TradeSafetyOrderGuardError(
            "the agent-facing paper gateway requires an authenticated HMAC receipt"
        )
    if not isinstance(hmac_key, bytes) or not hmac_key:
        raise TradeSafetyOrderGuardError("hmac_key must be non-empty bytes")


class PaperTradeSafetyOrderGateway(Generic[SubmitResult]):
    """Paper broker/OMS wrapper whose only submit path requires a fresh receipt."""

    def __init__(
        self,
        submit_order: Callable[[TradeSafetyOrderAuthorization], SubmitResult],
        *,
        binding: TradeSafetyExecutionBinding,
        receipt_consumer: ReceiptConsumer,
        hmac_key: bytes | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not callable(submit_order):
            raise TradeSafetyOrderGuardError("submit_order must be callable")
        if _is_async_callable(submit_order):
            raise TradeSafetyOrderGuardError(
                "the synchronous gateway requires a synchronous submit_order"
            )
        if not callable(clock):
            raise TradeSafetyOrderGuardError("clock must be callable")
        _validate_integrity_configuration(binding, hmac_key)
        self._binding = binding
        self._clock = clock
        self._hmac_key = hmac_key
        self._receipt_consumer = receipt_consumer
        self._submit_order = submit_order

    def submit(
        self,
        proposed_request: Mapping[str, Any],
        receipt: Mapping[str, Any],
    ) -> SubmitResult:
        authorization = before_order(
            proposed_request,
            receipt,
            evaluated_at=_clock_value(self._clock, "order-gateway clock"),
            binding=self._binding,
            hmac_key=self._hmac_key,
            receipt_consumer=self._receipt_consumer,
        )
        return self._submit_order(authorization)


class AsyncPaperTradeSafetyOrderGateway(Generic[SubmitResult]):
    """Native-async paper gateway with a native-async receipt consumer."""

    def __init__(
        self,
        submit_order: Callable[
            [TradeSafetyOrderAuthorization], Awaitable[SubmitResult]
        ],
        *,
        binding: TradeSafetyExecutionBinding,
        receipt_consumer: AsyncReceiptConsumer,
        hmac_key: bytes | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not callable(submit_order):
            raise TradeSafetyOrderGuardError("submit_order must be callable")
        if not _is_async_callable(submit_order):
            raise TradeSafetyOrderGuardError(
                "the asynchronous gateway requires an async submit_order"
            )
        if not callable(clock):
            raise TradeSafetyOrderGuardError("clock must be callable")
        _validate_integrity_configuration(binding, hmac_key)
        self._binding = binding
        self._clock = clock
        self._hmac_key = hmac_key
        self._receipt_consumer = receipt_consumer
        self._submit_order = submit_order

    async def submit(
        self,
        proposed_request: Mapping[str, Any],
        receipt: Mapping[str, Any],
    ) -> SubmitResult:
        authorization = await before_order_async(
            proposed_request,
            receipt,
            evaluated_at=_clock_value(self._clock, "async order-gateway clock"),
            binding=self._binding,
            hmac_key=self._hmac_key,
            receipt_consumer=self._receipt_consumer,
        )
        return await self._submit_order(authorization)


__all__ = [
    "AsyncInMemoryReceiptConsumer",
    "AsyncPaperTradeSafetyOrderGateway",
    "AsyncReceiptConsumer",
    "InMemoryReceiptConsumer",
    "PaperTradeSafetyOrderGateway",
    "ReceiptConsumer",
    "TradeSafetyExecutionBinding",
    "TradeSafetyOrderAuthorization",
    "TradeSafetyOrderBlocked",
    "TradeSafetyOrderGuardError",
    "before_order",
    "before_order_async",
]
