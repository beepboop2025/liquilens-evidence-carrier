"""Privacy-minimized, opt-in adoption telemetry for the gateway.

The emitter accepts only finite event, transport, property, and property-value
vocabularies.  It cannot serialize request data or arbitrary diagnostic text.
No sink is configured by default, so constructing an emitter never enables
collection by itself.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol

TELEMETRY_SCHEMA: Final = "liquilens.trade-safety-traction.v1"
MAX_TELEMETRY_LINE_BYTES: Final = 2048
TELEMETRY_PATH_ENV: Final = "TRADE_SAFETY_TELEMETRY_PATH"

ASSESSMENT_ACCEPTED: Final = "assessment_accepted"
ASSESSMENT_REJECTED: Final = "assessment_rejected"
ASSESSMENT_OUTCOME: Final = "assessment_outcome"
MCP_ACTIVATION: Final = "mcp_activation"
X402_OFFERED: Final = "x402_offered"
X402_VERIFY_FAILED: Final = "x402_verify_failed"
X402_SETTLE_FAILED: Final = "x402_settle_failed"
X402_SETTLED: Final = "x402_settled"
X402_RELEASE_FAILED: Final = "x402_release_failed"
X402_RELEASED: Final = "x402_released"

EVENT_NAMES: Final = frozenset(
    {
        ASSESSMENT_ACCEPTED,
        ASSESSMENT_REJECTED,
        ASSESSMENT_OUTCOME,
        MCP_ACTIVATION,
        X402_OFFERED,
        X402_VERIFY_FAILED,
        X402_SETTLE_FAILED,
        X402_SETTLED,
        X402_RELEASE_FAILED,
        X402_RELEASED,
    }
)
ALLOWED_TRANSPORTS: Final = frozenset({"rest", "mcp", "x402"})
ASSESSMENT_OUTCOMES: Final = frozenset({"pass", "limit", "hold", "unavailable"})
MCP_OPERATIONS: Final = frozenset(
    {
        "transport",
        "initialize",
        "tools_list",
        "assess_trade_safety",
        "trade_safety_capabilities",
    }
)
MCP_OUTCOMES: Final = frozenset({"success", "error"})

# Reasons are deliberately coarser than wire errors.  Callers must translate
# internal errors into one of these values before invoking the emitter.
ASSESSMENT_REJECTION_REASONS: Final = frozenset(
    {"policy_not_admitted", "invalid_request", "internal_error"}
)
X402_VERIFY_FAILURE_REASONS: Final = frozenset(
    {
        "payment_missing",
        "payment_malformed",
        "offer_mismatch",
        "facilitator_unavailable",
        "payment_rejected",
        "replay_in_progress",
        "authorization_retired",
        "capacity_exhausted",
        "internal_error",
    }
)
X402_SETTLE_FAILURE_REASONS: Final = frozenset(
    {
        "facilitator_unavailable",
        "payment_rejected",
        "settlement_failed",
        "settlement_uncertain",
        "replay_in_progress",
        "internal_error",
    }
)
X402_RELEASE_FAILURE_REASONS: Final = frozenset(
    {
        "response_expired",
        "response_invalid",
        "response_retired",
        "response_too_large",
    }
)
X402_DELIVERIES: Final = frozenset({"initial", "replay"})

_SERVICE_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[a-z0-9.+-]{1,32})?$")
_SOURCE_REVISION_RE = re.compile(r"^(?:source-checkout|[0-9a-f]{40,64})$")


class TelemetrySchemaError(ValueError):
    """An event did not match the closed telemetry schema."""


class TelemetrySink(Protocol):
    """A destination for one encoded JSON record without a trailing newline."""

    def write(self, line: bytes, /) -> None: ...


@dataclass(frozen=True, slots=True)
class _EventRule:
    properties: Mapping[str, frozenset[str]]
    transports: frozenset[str]


_ASSESSMENT_TRANSPORTS = frozenset({"rest", "mcp", "x402"})
_MCP_TRANSPORTS = frozenset({"mcp"})
_X402_TRANSPORTS = frozenset({"x402"})

_EVENT_RULES: Final = {
    ASSESSMENT_ACCEPTED: _EventRule({}, _ASSESSMENT_TRANSPORTS),
    ASSESSMENT_REJECTED: _EventRule(
        {"reason": ASSESSMENT_REJECTION_REASONS}, _ASSESSMENT_TRANSPORTS
    ),
    ASSESSMENT_OUTCOME: _EventRule(
        {"outcome": ASSESSMENT_OUTCOMES}, _ASSESSMENT_TRANSPORTS
    ),
    MCP_ACTIVATION: _EventRule(
        {"operation": MCP_OPERATIONS, "outcome": MCP_OUTCOMES}, _MCP_TRANSPORTS
    ),
    X402_OFFERED: _EventRule({}, _X402_TRANSPORTS),
    X402_VERIFY_FAILED: _EventRule(
        {"reason": X402_VERIFY_FAILURE_REASONS}, _X402_TRANSPORTS
    ),
    X402_SETTLE_FAILED: _EventRule(
        {"reason": X402_SETTLE_FAILURE_REASONS}, _X402_TRANSPORTS
    ),
    X402_SETTLED: _EventRule({}, _X402_TRANSPORTS),
    X402_RELEASE_FAILED: _EventRule(
        {
            "delivery": X402_DELIVERIES,
            "reason": X402_RELEASE_FAILURE_REASONS,
        },
        _X402_TRANSPORTS,
    ),
    X402_RELEASED: _EventRule(
        {"delivery": X402_DELIVERIES, "outcome": ASSESSMENT_OUTCOMES},
        _X402_TRANSPORTS,
    ),
}


@dataclass(slots=True)
class InMemoryTelemetrySink:
    """Deterministic injected sink for tests; never selected automatically."""

    lines: list[bytes] = field(default_factory=list)

    def write(self, line: bytes, /) -> None:
        self.lines.append(bytes(line))


class AppendOnlyJsonlSink:
    """Append each bounded event with one ``O_APPEND`` write.

    The parent directory must already exist and the path must be absolute.  A
    new file is created with mode ``0600``.  Existing non-regular, non-owned, or
    group/world-accessible files are rejected.  Rotation and deletion remain an
    operator responsibility; see ``docs/TRADE-SAFETY-TRACTION.md``.
    """

    __slots__ = ("_max_line_bytes", "_path")

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        max_line_bytes: int = MAX_TELEMETRY_LINE_BYTES,
    ) -> None:
        candidate = Path(path)
        if not candidate.is_absolute():
            raise ValueError("telemetry path must be absolute")
        if isinstance(max_line_bytes, bool) or not isinstance(max_line_bytes, int):
            raise TypeError("max_line_bytes must be an integer")
        if max_line_bytes < 1 or max_line_bytes > MAX_TELEMETRY_LINE_BYTES:
            raise ValueError("max_line_bytes is outside the supported bound")
        parent = candidate.parent
        if not parent.exists():
            raise FileNotFoundError("telemetry parent directory does not exist")
        if not parent.is_dir():
            raise NotADirectoryError("telemetry parent must be a directory")
        try:
            existing = os.lstat(candidate)
        except FileNotFoundError:
            pass
        else:
            self._validate_metadata(existing)
        self._path = candidate
        self._max_line_bytes = max_line_bytes
        descriptor = self._open_descriptor()
        os.close(descriptor)

    @property
    def path(self) -> Path:
        return self._path

    @staticmethod
    def _validate_metadata(metadata: os.stat_result) -> None:
        if not stat.S_ISREG(metadata.st_mode):
            raise PermissionError("telemetry destination must be a regular file")
        if hasattr(os, "geteuid") and metadata.st_uid != os.geteuid():
            raise PermissionError("telemetry destination must be owned by the service")
        if metadata.st_nlink != 1:
            raise PermissionError("telemetry destination must not be hard linked")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise PermissionError("telemetry destination permissions exceed 0600")
        if not metadata.st_mode & stat.S_IWUSR:
            raise PermissionError("telemetry destination must be owner-writable")

    def _open_descriptor(self) -> int:
        # O_NONBLOCK prevents a hostile pre-existing FIFO from hanging before
        # fstat can reject the non-regular destination.
        flags = os.O_APPEND | os.O_CREAT | os.O_NONBLOCK | os.O_WRONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._path, flags, 0o600)
        except OSError as exc:
            raise PermissionError(
                "telemetry destination cannot be opened privately for append"
            ) from exc
        try:
            self._validate_metadata(os.fstat(descriptor))
        except Exception:
            os.close(descriptor)
            raise
        return descriptor

    def write(self, line: bytes, /) -> None:
        if not isinstance(line, bytes):
            raise TypeError("telemetry line must be bytes")
        if b"\n" in line or b"\r" in line:
            raise ValueError("telemetry line must contain one record")
        payload = line + b"\n"
        if len(payload) > self._max_line_bytes:
            raise ValueError("telemetry line exceeds the configured byte bound")

        descriptor = self._open_descriptor()
        try:
            written = os.write(descriptor, payload)
            if written != len(payload):
                # Do not retry a partial append: another process could append
                # between writes and turn two fragments into misleading JSONL.
                raise OSError("telemetry append was incomplete")
        finally:
            os.close(descriptor)


def _duration_bucket(duration_ms: int | float | None) -> str:
    if duration_ms is None:
        return "unknown"
    if (
        isinstance(duration_ms, bool)
        or not isinstance(duration_ms, (int, float))
        or not math.isfinite(duration_ms)
        or duration_ms < 0
    ):
        raise TelemetrySchemaError("duration must be a finite non-negative number")
    if duration_ms < 10:
        return "lt_10_ms"
    if duration_ms < 50:
        return "10_to_49_ms"
    if duration_ms < 250:
        return "50_to_249_ms"
    if duration_ms < 1_000:
        return "250_to_999_ms"
    if duration_ms < 5_000:
        return "1_to_4_s"
    return "gte_5_s"


def _observed_at(clock: Callable[[], datetime]) -> str:
    observed = clock()
    if not isinstance(observed, datetime):
        raise TelemetrySchemaError("telemetry clock must return a datetime")
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise TelemetrySchemaError("telemetry clock must return an aware datetime")
    # Whole seconds are enough for aggregate funnels and reduce fingerprinting.
    return (
        observed.astimezone(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


class TelemetryEmitter:
    """Emit closed-schema adoption events to an explicitly injected sink.

    ``emit`` returns ``True`` only after a sink accepts the record.  Destination
    failures are deliberately reduced to ``False`` so telemetry cannot alter an
    assessment or payment result.  Schema errors remain exceptions because they
    indicate a programming error and should fail tests before deployment.
    """

    __slots__ = (
        "_clock",
        "_delivery_failures",
        "_delivery_lock",
        "_service_version",
        "_sink",
        "_source_revision",
    )

    def __init__(
        self,
        *,
        service_version: str,
        source_revision: str,
        sink: TelemetrySink | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            not isinstance(service_version, str)
            or _SERVICE_VERSION_RE.fullmatch(service_version) is None
        ):
            raise TelemetrySchemaError("service version is invalid")
        if (
            not isinstance(source_revision, str)
            or _SOURCE_REVISION_RE.fullmatch(source_revision) is None
        ):
            raise TelemetrySchemaError("source revision is invalid")
        self._service_version = service_version
        self._source_revision = source_revision
        self._sink = sink
        self._clock = clock or (lambda: datetime.now(UTC))
        self._delivery_failures = 0
        self._delivery_lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._sink is not None

    @property
    def status(self) -> dict[str, int | str]:
        with self._delivery_lock:
            failures = self._delivery_failures
        if self._sink is None:
            state = "disabled"
        else:
            state = "degraded" if failures else "ready"
        return {"state": state, "delivery_failures": failures}

    def emit(
        self,
        event: str,
        *,
        transport: str,
        duration_ms: int | float | None = None,
        **properties: str,
    ) -> bool:
        """Validate and emit one event; arbitrary keys or values are rejected."""

        if not isinstance(event, str) or event not in EVENT_NAMES:
            raise TelemetrySchemaError("event name is not allowed")
        rule = _EVENT_RULES[event]
        if not isinstance(transport, str) or transport not in rule.transports:
            raise TelemetrySchemaError("transport is not allowed for this event")
        if set(properties) != set(rule.properties):
            # Do not reflect an unknown key: a caller may have supplied a secret
            # as the key as well as the value.
            raise TelemetrySchemaError("properties do not match the event schema")
        for key, allowed_values in rule.properties.items():
            value = properties[key]
            if not isinstance(value, str) or value not in allowed_values:
                # Never echo arbitrary exception, wallet, or request text.
                raise TelemetrySchemaError("property value is not allowed")
        duration_bucket = _duration_bucket(duration_ms)
        if self._sink is None:
            return False

        record = {
            "duration_bucket": duration_bucket,
            "event": event,
            "observed_at": _observed_at(self._clock),
            "properties": properties,
            "schema": TELEMETRY_SCHEMA,
            "service_version": self._service_version,
            "source_revision": self._source_revision,
            "transport": transport,
        }
        encoded = json.dumps(
            record,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        if len(encoded) + 1 > MAX_TELEMETRY_LINE_BYTES:
            raise TelemetrySchemaError("encoded telemetry event exceeds its byte bound")
        try:
            self._sink.write(encoded)
        except Exception:
            # The caller gets a health signal without propagating path names,
            # provider errors, or other sink diagnostics into the request path.
            with self._delivery_lock:
                self._delivery_failures += 1
            return False
        return True


def telemetry_from_env(
    service_version: str,
    source_revision: str,
) -> TelemetryEmitter:
    """Build disabled telemetry or an explicitly configured local JSONL sink.

    Absence of ``TRADE_SAFETY_TELEMETRY_PATH`` is the only disabled state.  A
    present value, including an empty or relative path, is validated eagerly so
    a broken opt-in cannot be mistaken for working collection.
    """

    configured_path = os.environ.get(TELEMETRY_PATH_ENV)
    sink = None if configured_path is None else AppendOnlyJsonlSink(configured_path)
    return TelemetryEmitter(
        service_version=service_version,
        source_revision=source_revision,
        sink=sink,
    )


__all__ = [
    "ALLOWED_TRANSPORTS",
    "ASSESSMENT_ACCEPTED",
    "ASSESSMENT_OUTCOME",
    "ASSESSMENT_OUTCOMES",
    "ASSESSMENT_REJECTED",
    "ASSESSMENT_REJECTION_REASONS",
    "EVENT_NAMES",
    "MAX_TELEMETRY_LINE_BYTES",
    "MCP_ACTIVATION",
    "MCP_OPERATIONS",
    "MCP_OUTCOMES",
    "TELEMETRY_PATH_ENV",
    "TELEMETRY_SCHEMA",
    "X402_DELIVERIES",
    "X402_OFFERED",
    "X402_RELEASED",
    "X402_RELEASE_FAILED",
    "X402_RELEASE_FAILURE_REASONS",
    "X402_SETTLED",
    "X402_SETTLE_FAILED",
    "X402_SETTLE_FAILURE_REASONS",
    "X402_VERIFY_FAILED",
    "X402_VERIFY_FAILURE_REASONS",
    "AppendOnlyJsonlSink",
    "InMemoryTelemetrySink",
    "TelemetryEmitter",
    "TelemetrySchemaError",
    "TelemetrySink",
    "telemetry_from_env",
]
