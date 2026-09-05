"""Bound anonymous work without storing client addresses or trusting headers."""

from __future__ import annotations

import math
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AdmissionLimits:
    max_in_flight: int = 16
    requests_per_second: int = 20
    burst: int = 40
    request_timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        for name, maximum in (
            ("max_in_flight", 64),
            ("requests_per_second", 100),
            ("burst", 200),
        ):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be an integer from 1 to {maximum}")
        timeout = self.request_timeout_seconds
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 < timeout <= 30
        ):
            raise ValueError("request_timeout_seconds must be positive and at most 30")

    @classmethod
    def from_env(cls) -> AdmissionLimits:
        values: dict[str, int | float] = {}
        for name, key in (
            ("max_in_flight", "TRADE_SAFETY_MAX_IN_FLIGHT"),
            ("requests_per_second", "TRADE_SAFETY_REQUESTS_PER_SECOND"),
            ("burst", "TRADE_SAFETY_REQUEST_BURST"),
        ):
            if key in os.environ:
                raw = os.environ[key]
                if not raw.isascii() or not raw.isdecimal():
                    raise ValueError(f"{key} must contain an unsigned integer")
                values[name] = int(raw)
        return cls(**values)

    @property
    def public(self) -> dict[str, object]:
        return {
            "scope": "per_worker_shared_anonymous_budget",
            "methods": ["POST"],
            "max_in_flight": self.max_in_flight,
            "requests_per_second": self.requests_per_second,
            "burst": self.burst,
            "request_timeout_seconds": self.request_timeout_seconds,
            "client_identity_required": False,
        }


class RequestAdmission:
    """A non-queuing token bucket and in-flight bound for one ASGI worker."""

    def __init__(
        self,
        limits: AdmissionLimits | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits if limits is not None else AdmissionLimits.from_env()
        self._clock = clock
        self._updated = clock()
        self._tokens = float(self.limits.burst)
        self._in_flight = 0
        self._lock = threading.Lock()

    def acquire(self) -> str | None:
        # There is no await or request data in this short critical section.
        with self._lock:
            now = max(self._updated, self._clock())
            self._tokens = min(
                float(self.limits.burst),
                self._tokens + (now - self._updated) * self.limits.requests_per_second,
            )
            self._updated = now
            if self._in_flight >= self.limits.max_in_flight:
                return "capacity_exhausted"
            if self._tokens < 1:
                return "rate_limited"
            self._tokens -= 1
            self._in_flight += 1
            return None

    def release(self) -> None:
        with self._lock:
            if self._in_flight <= 0:
                raise RuntimeError("request admission released without acquisition")
            self._in_flight -= 1
