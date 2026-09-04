"""Server-owned admission limits for caller-supplied sandbox policies.

The protocol policy schema intentionally permits an operator to choose limits.  A
public gateway cannot treat every schema-valid caller choice as its own policy,
however.  This module applies an immutable server ceiling before any evidence I/O.
Configuration may make that ceiling stricter, but can never relax it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Self

from liquilens_evidence.trade_safety import (
    TradeSafetyError,
    trade_safety_policy_hash,
    validate_trade_safety_policy,
)

POLICY_REQUIRED_PRODUCTS_ENV = "TRADE_SAFETY_POLICY_REQUIRED_PRODUCTS"
POLICY_HOLD_REGIMES_ENV = "TRADE_SAFETY_POLICY_HOLD_REGIMES"
POLICY_MAX_SEICHE_AGE_ENV = "TRADE_SAFETY_POLICY_MAX_SEICHE_AGE_SECONDS"
POLICY_MAX_UNDERTOW_AGE_ENV = "TRADE_SAFETY_POLICY_MAX_UNDERTOW_AGE_SECONDS"
POLICY_MAX_LIQUILENS_AGE_ENV = "TRADE_SAFETY_POLICY_MAX_LIQUILENS_AGE_SECONDS"
POLICY_MAX_NOTIONAL_ENV = "TRADE_SAFETY_POLICY_MAX_NOTIONAL_USD"
POLICY_MAX_EXIT_COST_ENV = "TRADE_SAFETY_POLICY_MAX_EXIT_COST_BPS"
POLICY_MAX_VENUE_SPREAD_ENV = "TRADE_SAFETY_POLICY_MAX_VENUE_SPREAD_BPS"
POLICY_SHA256_ALLOWLIST_ENV = "TRADE_SAFETY_POLICY_SHA256_ALLOWLIST"

_POLICY_ENVIRONMENT_NAMES = frozenset(
    {
        POLICY_REQUIRED_PRODUCTS_ENV,
        POLICY_HOLD_REGIMES_ENV,
        POLICY_MAX_SEICHE_AGE_ENV,
        POLICY_MAX_UNDERTOW_AGE_ENV,
        POLICY_MAX_LIQUILENS_AGE_ENV,
        POLICY_MAX_NOTIONAL_ENV,
        POLICY_MAX_EXIT_COST_ENV,
        POLICY_MAX_VENUE_SPREAD_ENV,
        POLICY_SHA256_ALLOWLIST_ENV,
    }
)

POLICY_PRODUCTS = frozenset({"seiche", "undertow", "liquilens"})
POLICY_REGIMES = frozenset({"CALM", "EROSION", "STRAIN", "STRESS"})
HARD_REQUIRED_PRODUCTS = frozenset({"seiche", "undertow"})
HARD_HOLD_REGIMES = frozenset({"STRESS"})
HARD_MAX_EVIDENCE_AGE_SECONDS = MappingProxyType(
    {
        "seiche": 8 * 86_400,
        "undertow": 86_400,
        "liquilens": 86_400,
    }
)
HARD_MAX_NOTIONAL_USD = 100_000.0
HARD_MAX_EXIT_COST_BPS = 50.0
HARD_MAX_VENUE_SPREAD_BPS = 20.0

_PRODUCT_ORDER = ("seiche", "undertow", "liquilens")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PolicyAdmissionReason(StrEnum):
    """Stable, non-reflective reason codes safe to expose to callers."""

    POLICY_NOT_NORMALIZED = "policy_not_normalized"
    REQUIRED_PRODUCTS_TOO_PERMISSIVE = "policy_required_products_too_permissive"
    HOLD_REGIMES_TOO_PERMISSIVE = "policy_hold_regimes_too_permissive"
    SEICHE_EVIDENCE_AGE_TOO_LOOSE = "policy_seiche_evidence_age_too_loose"
    UNDERTOW_EVIDENCE_AGE_TOO_LOOSE = "policy_undertow_evidence_age_too_loose"
    LIQUILENS_EVIDENCE_AGE_TOO_LOOSE = "policy_liquilens_evidence_age_too_loose"
    MAX_NOTIONAL_REQUIRED = "policy_max_notional_required"
    MAX_NOTIONAL_TOO_LOOSE = "policy_max_notional_too_loose"
    MAX_EXIT_COST_REQUIRED = "policy_max_exit_cost_required"
    MAX_EXIT_COST_TOO_LOOSE = "policy_max_exit_cost_too_loose"
    MAX_VENUE_SPREAD_REQUIRED = "policy_max_venue_spread_required"
    MAX_VENUE_SPREAD_TOO_LOOSE = "policy_max_venue_spread_too_loose"
    MISSING_EVIDENCE_MUST_FAIL_CLOSED = "policy_missing_evidence_must_fail_closed"
    LIVE_EXECUTABLE_QUOTE_REQUIRED = "policy_live_executable_quote_required"
    LIVE_BROKER_PREVIEW_REQUIRED = "policy_live_broker_preview_required"
    AUTO_RESIZE_FORBIDDEN = "policy_auto_resize_forbidden"
    POLICY_HASH_NOT_ALLOWED = "policy_hash_not_allowed"


class PolicyGuardConfigurationReason(StrEnum):
    """Stable startup-failure codes for invalid server configuration."""

    INVALID_CONFIG = "policy_guard_config_invalid"
    UNKNOWN_ENVIRONMENT_SETTING = "policy_guard_config_unknown_environment_setting"
    INVALID_REQUIRED_PRODUCTS = "policy_guard_config_invalid_required_products"
    REQUIRED_PRODUCTS_TOO_LOOSE = "policy_guard_config_required_products_too_loose"
    INVALID_HOLD_REGIMES = "policy_guard_config_invalid_hold_regimes"
    HOLD_REGIMES_TOO_LOOSE = "policy_guard_config_hold_regimes_too_loose"
    INVALID_EVIDENCE_AGES = "policy_guard_config_invalid_evidence_ages"
    EVIDENCE_AGES_TOO_LOOSE = "policy_guard_config_evidence_ages_too_loose"
    INVALID_MAX_NOTIONAL = "policy_guard_config_invalid_max_notional"
    MAX_NOTIONAL_TOO_LOOSE = "policy_guard_config_max_notional_too_loose"
    INVALID_MAX_EXIT_COST = "policy_guard_config_invalid_max_exit_cost"
    MAX_EXIT_COST_TOO_LOOSE = "policy_guard_config_max_exit_cost_too_loose"
    INVALID_MAX_VENUE_SPREAD = "policy_guard_config_invalid_max_venue_spread"
    MAX_VENUE_SPREAD_TOO_LOOSE = "policy_guard_config_max_venue_spread_too_loose"
    INVALID_POLICY_SHA256_ALLOWLIST = (
        "policy_guard_config_invalid_policy_sha256_allowlist"
    )


class PolicyAdmissionError(TradeSafetyError):
    """A schema-valid caller policy exceeds the server-owned safety envelope."""

    def __init__(self, reason: PolicyAdmissionReason) -> None:
        self.reason_code = reason.value
        super().__init__(reason.value)


class PolicyGuardConfigurationError(RuntimeError):
    """Server policy configuration is invalid or attempts to loosen a hard cap."""

    def __init__(self, reason: PolicyGuardConfigurationReason) -> None:
        self.reason_code = reason.value
        super().__init__(reason.value)


def _config_error(reason: PolicyGuardConfigurationReason) -> None:
    raise PolicyGuardConfigurationError(reason)


def _tokens(
    value: Collection[str],
    *,
    allowed: frozenset[str],
    invalid_reason: PolicyGuardConfigurationReason,
) -> frozenset[str]:
    if isinstance(value, str):
        _config_error(invalid_reason)
    try:
        items = tuple(value)
    except TypeError:
        _config_error(invalid_reason)
    if (
        not items
        or not all(isinstance(item, str) and item in allowed for item in items)
        or len(items) != len(set(items))
    ):
        _config_error(invalid_reason)
    return frozenset(items)


def _positive_number(
    value: object, invalid_reason: PolicyGuardConfigurationReason
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _config_error(invalid_reason)
    converted = float(value)
    if not isfinite(converted) or converted <= 0:
        _config_error(invalid_reason)
    return converted


def _positive_integer(
    value: object, invalid_reason: PolicyGuardConfigurationReason
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _config_error(invalid_reason)
    return value


def _parse_env_tokens(
    raw: object,
    *,
    allowed: frozenset[str],
    invalid_reason: PolicyGuardConfigurationReason,
) -> frozenset[str]:
    if not isinstance(raw, str) or not raw.strip():
        _config_error(invalid_reason)
    parts = tuple(part.strip() for part in raw.split(","))
    if any(not part for part in parts):
        _config_error(invalid_reason)
    return _tokens(parts, allowed=allowed, invalid_reason=invalid_reason)


def _parse_env_integer(
    raw: object, invalid_reason: PolicyGuardConfigurationReason
) -> int:
    if not isinstance(raw, str) or re.fullmatch(r"[1-9][0-9]*", raw.strip()) is None:
        _config_error(invalid_reason)
    return int(raw.strip())


def _parse_env_number(
    raw: object, invalid_reason: PolicyGuardConfigurationReason
) -> float:
    if not isinstance(raw, str) or not raw.strip():
        _config_error(invalid_reason)
    try:
        parsed = float(raw.strip())
    except ValueError:
        _config_error(invalid_reason)
    return _positive_number(parsed, invalid_reason)


def _parse_env_hashes(
    raw: object, invalid_reason: PolicyGuardConfigurationReason
) -> tuple[str, ...]:
    if not isinstance(raw, str) or not raw.strip():
        _config_error(invalid_reason)
    hashes = tuple(item.strip() for item in raw.split(","))
    if any(_SHA256_RE.fullmatch(item) is None for item in hashes) or len(hashes) != len(
        set(hashes)
    ):
        _config_error(invalid_reason)
    return hashes


def _env_or_default(
    environ: Mapping[str, str],
    name: str,
    default: object,
) -> object:
    return environ.get(name, default)


@dataclass(frozen=True, slots=True)
class PolicyAdmissionConfig:
    """Immutable server policy; every override must be at least as restrictive."""

    required_products: Collection[str] = HARD_REQUIRED_PRODUCTS
    hold_regimes: Collection[str] = HARD_HOLD_REGIMES
    max_evidence_age_seconds: Mapping[str, int] = field(
        default_factory=lambda: dict(HARD_MAX_EVIDENCE_AGE_SECONDS)
    )
    max_notional_usd: float = HARD_MAX_NOTIONAL_USD
    max_exit_cost_bps: float = HARD_MAX_EXIT_COST_BPS
    max_venue_spread_bps: float = HARD_MAX_VENUE_SPREAD_BPS
    allowed_policy_sha256: Collection[str] | None = None

    def __post_init__(self) -> None:
        required_products = _tokens(
            self.required_products,
            allowed=POLICY_PRODUCTS,
            invalid_reason=PolicyGuardConfigurationReason.INVALID_REQUIRED_PRODUCTS,
        )
        if not required_products >= HARD_REQUIRED_PRODUCTS:
            _config_error(PolicyGuardConfigurationReason.REQUIRED_PRODUCTS_TOO_LOOSE)

        hold_regimes = _tokens(
            self.hold_regimes,
            allowed=POLICY_REGIMES,
            invalid_reason=PolicyGuardConfigurationReason.INVALID_HOLD_REGIMES,
        )
        if not hold_regimes >= HARD_HOLD_REGIMES:
            _config_error(PolicyGuardConfigurationReason.HOLD_REGIMES_TOO_LOOSE)

        if not isinstance(self.max_evidence_age_seconds, Mapping):
            _config_error(PolicyGuardConfigurationReason.INVALID_EVIDENCE_AGES)
        ages = dict(self.max_evidence_age_seconds)
        if set(ages) != set(_PRODUCT_ORDER):
            _config_error(PolicyGuardConfigurationReason.INVALID_EVIDENCE_AGES)
        normalized_ages = {
            product: _positive_integer(
                ages[product],
                PolicyGuardConfigurationReason.INVALID_EVIDENCE_AGES,
            )
            for product in _PRODUCT_ORDER
        }
        if any(
            normalized_ages[product] > HARD_MAX_EVIDENCE_AGE_SECONDS[product]
            for product in _PRODUCT_ORDER
        ):
            _config_error(PolicyGuardConfigurationReason.EVIDENCE_AGES_TOO_LOOSE)

        max_notional = _positive_number(
            self.max_notional_usd,
            PolicyGuardConfigurationReason.INVALID_MAX_NOTIONAL,
        )
        if max_notional > HARD_MAX_NOTIONAL_USD:
            _config_error(PolicyGuardConfigurationReason.MAX_NOTIONAL_TOO_LOOSE)

        max_exit_cost = _positive_number(
            self.max_exit_cost_bps,
            PolicyGuardConfigurationReason.INVALID_MAX_EXIT_COST,
        )
        if max_exit_cost > HARD_MAX_EXIT_COST_BPS:
            _config_error(PolicyGuardConfigurationReason.MAX_EXIT_COST_TOO_LOOSE)

        max_venue_spread = _positive_number(
            self.max_venue_spread_bps,
            PolicyGuardConfigurationReason.INVALID_MAX_VENUE_SPREAD,
        )
        if max_venue_spread > HARD_MAX_VENUE_SPREAD_BPS:
            _config_error(PolicyGuardConfigurationReason.MAX_VENUE_SPREAD_TOO_LOOSE)

        hashes: frozenset[str] | None = None
        if self.allowed_policy_sha256 is not None:
            if isinstance(self.allowed_policy_sha256, str):
                _config_error(
                    PolicyGuardConfigurationReason.INVALID_POLICY_SHA256_ALLOWLIST
                )
            try:
                hash_items = tuple(self.allowed_policy_sha256)
            except TypeError:
                _config_error(
                    PolicyGuardConfigurationReason.INVALID_POLICY_SHA256_ALLOWLIST
                )
            if (
                not hash_items
                or not all(
                    isinstance(item, str) and _SHA256_RE.fullmatch(item) is not None
                    for item in hash_items
                )
                or len(hash_items) != len(set(hash_items))
            ):
                _config_error(
                    PolicyGuardConfigurationReason.INVALID_POLICY_SHA256_ALLOWLIST
                )
            hashes = frozenset(hash_items)

        object.__setattr__(self, "required_products", required_products)
        object.__setattr__(self, "hold_regimes", hold_regimes)
        object.__setattr__(
            self,
            "max_evidence_age_seconds",
            MappingProxyType(normalized_ages),
        )
        object.__setattr__(self, "max_notional_usd", max_notional)
        object.__setattr__(self, "max_exit_cost_bps", max_exit_cost)
        object.__setattr__(self, "max_venue_spread_bps", max_venue_spread)
        object.__setattr__(self, "allowed_policy_sha256", hashes)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Self:
        """Load recognized environment values, failing closed on every typo."""

        source = os.environ if environ is None else environ
        if any(
            name.startswith("TRADE_SAFETY_POLICY_")
            and name not in _POLICY_ENVIRONMENT_NAMES
            for name in source
        ):
            _config_error(PolicyGuardConfigurationReason.UNKNOWN_ENVIRONMENT_SETTING)
        required_products = HARD_REQUIRED_PRODUCTS
        if POLICY_REQUIRED_PRODUCTS_ENV in source:
            required_products = _parse_env_tokens(
                source[POLICY_REQUIRED_PRODUCTS_ENV],
                allowed=POLICY_PRODUCTS,
                invalid_reason=(
                    PolicyGuardConfigurationReason.INVALID_REQUIRED_PRODUCTS
                ),
            )
        hold_regimes = HARD_HOLD_REGIMES
        if POLICY_HOLD_REGIMES_ENV in source:
            hold_regimes = _parse_env_tokens(
                source[POLICY_HOLD_REGIMES_ENV],
                allowed=POLICY_REGIMES,
                invalid_reason=PolicyGuardConfigurationReason.INVALID_HOLD_REGIMES,
            )

        age_env = {
            "seiche": POLICY_MAX_SEICHE_AGE_ENV,
            "undertow": POLICY_MAX_UNDERTOW_AGE_ENV,
            "liquilens": POLICY_MAX_LIQUILENS_AGE_ENV,
        }
        ages = {
            product: _parse_env_integer(
                _env_or_default(
                    source,
                    age_env[product],
                    str(HARD_MAX_EVIDENCE_AGE_SECONDS[product]),
                ),
                PolicyGuardConfigurationReason.INVALID_EVIDENCE_AGES,
            )
            for product in _PRODUCT_ORDER
        }

        max_notional = _parse_env_number(
            _env_or_default(
                source, POLICY_MAX_NOTIONAL_ENV, str(HARD_MAX_NOTIONAL_USD)
            ),
            PolicyGuardConfigurationReason.INVALID_MAX_NOTIONAL,
        )
        max_exit_cost = _parse_env_number(
            _env_or_default(
                source, POLICY_MAX_EXIT_COST_ENV, str(HARD_MAX_EXIT_COST_BPS)
            ),
            PolicyGuardConfigurationReason.INVALID_MAX_EXIT_COST,
        )
        max_venue_spread = _parse_env_number(
            _env_or_default(
                source,
                POLICY_MAX_VENUE_SPREAD_ENV,
                str(HARD_MAX_VENUE_SPREAD_BPS),
            ),
            PolicyGuardConfigurationReason.INVALID_MAX_VENUE_SPREAD,
        )

        allowed_hashes: Collection[str] | None = None
        if POLICY_SHA256_ALLOWLIST_ENV in source:
            allowed_hashes = _parse_env_hashes(
                source[POLICY_SHA256_ALLOWLIST_ENV],
                (PolicyGuardConfigurationReason.INVALID_POLICY_SHA256_ALLOWLIST),
            )

        return cls(
            required_products=required_products,
            hold_regimes=hold_regimes,
            max_evidence_age_seconds=ages,
            max_notional_usd=max_notional,
            max_exit_cost_bps=max_exit_cost,
            max_venue_spread_bps=max_venue_spread,
            allowed_policy_sha256=allowed_hashes,
        )


_AGE_REASON = {
    "seiche": PolicyAdmissionReason.SEICHE_EVIDENCE_AGE_TOO_LOOSE,
    "undertow": PolicyAdmissionReason.UNDERTOW_EVIDENCE_AGE_TOO_LOOSE,
    "liquilens": PolicyAdmissionReason.LIQUILENS_EVIDENCE_AGE_TOO_LOOSE,
}


class PolicyAdmissionGuard:
    """Admit only policies inside one immutable server-owned safety envelope."""

    def __init__(self, config: PolicyAdmissionConfig | None = None) -> None:
        if config is not None and not isinstance(config, PolicyAdmissionConfig):
            _config_error(PolicyGuardConfigurationReason.INVALID_CONFIG)
        self.config = PolicyAdmissionConfig() if config is None else config

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Self:
        return cls(PolicyAdmissionConfig.from_env(environ))

    def admit(self, normalized_policy: Mapping[str, object]) -> str:
        """Return its canonical hash, or reject before the caller can trigger I/O."""

        try:
            policy = validate_trade_safety_policy(normalized_policy)
        except (TradeSafetyError, TypeError, ValueError, RecursionError):
            raise PolicyAdmissionError(
                PolicyAdmissionReason.POLICY_NOT_NORMALIZED
            ) from None

        if not self.config.required_products <= set(policy["required_products"]):
            raise PolicyAdmissionError(
                PolicyAdmissionReason.REQUIRED_PRODUCTS_TOO_PERMISSIVE
            )
        if not self.config.hold_regimes <= set(policy["hold_regimes"]):
            raise PolicyAdmissionError(
                PolicyAdmissionReason.HOLD_REGIMES_TOO_PERMISSIVE
            )

        ages = policy["max_evidence_age_seconds"]
        for product in _PRODUCT_ORDER:
            if ages[product] > self.config.max_evidence_age_seconds[product]:
                raise PolicyAdmissionError(_AGE_REASON[product])

        self._bounded_policy_number(
            policy,
            field_name="max_notional_usd",
            limit=self.config.max_notional_usd,
            required_reason=PolicyAdmissionReason.MAX_NOTIONAL_REQUIRED,
            loose_reason=PolicyAdmissionReason.MAX_NOTIONAL_TOO_LOOSE,
        )
        self._bounded_policy_number(
            policy,
            field_name="max_exit_cost_bps",
            limit=self.config.max_exit_cost_bps,
            required_reason=PolicyAdmissionReason.MAX_EXIT_COST_REQUIRED,
            loose_reason=PolicyAdmissionReason.MAX_EXIT_COST_TOO_LOOSE,
        )
        self._bounded_policy_number(
            policy,
            field_name="max_venue_spread_bps",
            limit=self.config.max_venue_spread_bps,
            required_reason=PolicyAdmissionReason.MAX_VENUE_SPREAD_REQUIRED,
            loose_reason=PolicyAdmissionReason.MAX_VENUE_SPREAD_TOO_LOOSE,
        )

        # These are schema invariants today.  Retain explicit server checks so a
        # future protocol relaxation cannot silently relax this public gateway.
        if policy["missing_evidence"] != "fail_closed":
            raise PolicyAdmissionError(
                PolicyAdmissionReason.MISSING_EVIDENCE_MUST_FAIL_CLOSED
            )
        if policy["live_requires_executable_quote"] is not True:
            raise PolicyAdmissionError(
                PolicyAdmissionReason.LIVE_EXECUTABLE_QUOTE_REQUIRED
            )
        if policy["live_requires_broker_preview"] is not True:
            raise PolicyAdmissionError(
                PolicyAdmissionReason.LIVE_BROKER_PREVIEW_REQUIRED
            )
        if policy["auto_resize"] is not False:
            raise PolicyAdmissionError(PolicyAdmissionReason.AUTO_RESIZE_FORBIDDEN)

        policy_hash = trade_safety_policy_hash(policy)
        allowlist = self.config.allowed_policy_sha256
        if allowlist is not None and policy_hash not in allowlist:
            raise PolicyAdmissionError(PolicyAdmissionReason.POLICY_HASH_NOT_ALLOWED)
        return policy_hash

    @staticmethod
    def _bounded_policy_number(
        policy: Mapping[str, object],
        *,
        field_name: str,
        limit: float,
        required_reason: PolicyAdmissionReason,
        loose_reason: PolicyAdmissionReason,
    ) -> None:
        value = policy[field_name]
        if value is None:
            raise PolicyAdmissionError(required_reason)
        if float(value) > limit:
            raise PolicyAdmissionError(loose_reason)


__all__ = [
    "HARD_HOLD_REGIMES",
    "HARD_MAX_EVIDENCE_AGE_SECONDS",
    "HARD_MAX_EXIT_COST_BPS",
    "HARD_MAX_NOTIONAL_USD",
    "HARD_MAX_VENUE_SPREAD_BPS",
    "HARD_REQUIRED_PRODUCTS",
    "POLICY_HOLD_REGIMES_ENV",
    "POLICY_MAX_EXIT_COST_ENV",
    "POLICY_MAX_LIQUILENS_AGE_ENV",
    "POLICY_MAX_NOTIONAL_ENV",
    "POLICY_MAX_SEICHE_AGE_ENV",
    "POLICY_MAX_UNDERTOW_AGE_ENV",
    "POLICY_MAX_VENUE_SPREAD_ENV",
    "POLICY_REQUIRED_PRODUCTS_ENV",
    "POLICY_SHA256_ALLOWLIST_ENV",
    "PolicyAdmissionConfig",
    "PolicyAdmissionError",
    "PolicyAdmissionGuard",
    "PolicyAdmissionReason",
    "PolicyGuardConfigurationError",
    "PolicyGuardConfigurationReason",
]
