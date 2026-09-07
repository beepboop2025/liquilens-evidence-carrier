"""Explicit operator configuration; secrets are read only from paper env names."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from liquilens_evidence import (
    TradeSafetyExecutionBinding,
    trade_safety_policy_hash,
    validate_trade_safety_policy,
)
from trade_safety_gateway.policy_guard import PolicyAdmissionGuard

from .strategy import StrategyConfig


class ConfigurationError(ValueError):
    """A stable code without secret values."""


def strict_json(raw: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ConfigurationError("duplicate_json_key")
            result[key] = value
        return result

    def invalid_constant(_value: str) -> None:
        raise ConfigurationError("nonfinite_json_number")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)


def default_policy() -> dict[str, Any]:
    return {
        "schema": "liquilens.trade-safety-policy.v1",
        "policy_id": "copilot-paper-v1",
        "version": "1.0.0",
        "required_products": ["seiche", "undertow"],
        "max_evidence_age_seconds": {
            "seiche": 691200,
            "undertow": 300,
            "liquilens": 86400,
        },
        "hold_regimes": ["STRAIN", "STRESS"],
        "max_notional_usd": 1000.0,
        "max_exit_cost_bps": 25.0,
        "max_venue_spread_bps": 15.0,
        "missing_evidence": "fail_closed",
        "live_requires_executable_quote": True,
        "live_requires_broker_preview": True,
        "auto_resize": False,
        "extensions": {},
    }


SCOPED_PROFILE = "liquilens.paper-funding-exit.v1"


def scoped_policy() -> dict[str, Any]:
    """A distinct paper policy for current funding and weekly CP observations."""
    policy = default_policy()
    policy["policy_id"] = SCOPED_PROFILE
    policy["required_products"] = ["seiche", "undertow", "liquilens"]
    # The CP source has a weekly rollover leg. Preserve its observation date;
    # this is a new, explicit source/cadence contract, not the native bank route.
    policy["max_evidence_age_seconds"]["liquilens"] = 691200
    return policy


@dataclass(frozen=True, slots=True)
class CopilotConfig:
    account_id: str | None = None
    enabled: bool = False
    mode: str = "paper"
    evidence_profile: str = "native_gateway_v1"
    state_dir: str = "/var/lib/liquilens-trading-copilot"
    operator_id: str = "mrinal"
    tenant_id: str = "liquilens-operator"
    agent_id: str = "liquilens-paper-copilot"
    strategy_id: str = "btc-trend-volatility-v1"
    issuer_endpoint: str = "https://liquilens.in/operator/paper-copilot"
    max_daily_attempts: int = 2
    reserved_daily_exit_attempts: int = 1
    cycle_interval_seconds: int = 900
    liquilens_institution_slug: str | None = None
    liquilens_required: bool = False
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    policy: dict[str, Any] = field(default_factory=default_policy)

    def validate(self) -> None:
        if self.mode != "paper":
            raise ConfigurationError("only_paper_mode_supported")
        if self.evidence_profile not in {"native_gateway_v1", SCOPED_PROFILE}:
            raise ConfigurationError("unsupported_evidence_profile")
        if type(self.enabled) is not bool or type(self.liquilens_required) is not bool:
            raise ConfigurationError("boolean_setting_required")
        if not Path(self.state_dir).is_absolute():
            raise ConfigurationError("state_directory_must_be_absolute")
        for value in (
            self.operator_id,
            self.tenant_id,
            self.agent_id,
            self.strategy_id,
        ):
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise ConfigurationError("invalid_operator_identity")
        if self.account_id is not None and (
            not isinstance(self.account_id, str)
            or not self.account_id.strip()
            or len(self.account_id) > 128
        ):
            raise ConfigurationError("invalid_paper_account_id")
        if (
            type(self.max_daily_attempts) is not int
            or not 1 <= self.max_daily_attempts <= 10
        ):
            raise ConfigurationError("daily_attempt_limit_out_of_range")
        if (
            type(self.reserved_daily_exit_attempts) is not int
            or not 0 <= self.reserved_daily_exit_attempts <= self.max_daily_attempts
        ):
            raise ConfigurationError("reserved_exit_attempts_out_of_range")
        if (
            type(self.cycle_interval_seconds) is not int
            or not 60 <= self.cycle_interval_seconds <= 86400
        ):
            raise ConfigurationError("cycle_interval_out_of_range")
        policy = validate_trade_safety_policy(self.policy)
        if self.evidence_profile == SCOPED_PROFILE:
            if policy != scoped_policy():
                raise ConfigurationError(
                    "scoped_paper_policy_must_match_versioned_profile"
                )
            if self.liquilens_institution_slug is not None or self.liquilens_required:
                raise ConfigurationError(
                    "scoped_profile_uses_aggregate_corporate_context"
                )
        else:
            PolicyAdmissionGuard().admit(policy)
        if self.strategy.order_notional_usd > policy["max_notional_usd"]:
            raise ConfigurationError("candidate_exceeds_operator_notional_limit")
        if self.liquilens_required and (
            not self.liquilens_institution_slug
            or "liquilens" not in policy["required_products"]
        ):
            raise ConfigurationError("required_liquilens_context_not_in_policy")

    def binding(self) -> TradeSafetyExecutionBinding:
        self.validate()
        if not self.account_id:
            raise ConfigurationError("paper_account_id_missing")
        return TradeSafetyExecutionBinding(
            account_id=self.account_id,
            tenant_id=self.tenant_id,
            operator_id=self.operator_id,
            agent_id=self.agent_id,
            runtime="liquilens-trading-copilot/0.1.0",
            strategy_id=self.strategy_id,
            policy_id=self.policy["policy_id"],
            policy_version=self.policy["version"],
            policy_hash=trade_safety_policy_hash(self.policy),
            issuer_name=(
                "liquilens-operator-scoped-paper-copilot"
                if self.evidence_profile == SCOPED_PROFILE
                else "liquilens-operator-paper-copilot"
            ),
            issuer_version="0.1.0",
            issuer_endpoint=self.issuer_endpoint,
            hmac_key_id=(
                "operator-paper-funding-exit-v1"
                if self.evidence_profile == SCOPED_PROFILE
                else "operator-paper-v1"
            ),
        )


def load_config(path: Path) -> CopilotConfig:
    if path.is_symlink() or path.stat().st_size > 65536:
        raise ConfigurationError("configuration_file_invalid")
    raw = strict_json(path.read_bytes())
    if not isinstance(raw, dict):
        raise ConfigurationError("configuration_object_required")
    raw = dict(raw)
    if "strategy" in raw:
        if not isinstance(raw["strategy"], dict):
            raise ConfigurationError("strategy_object_required")
        raw["strategy"] = StrategyConfig(**raw["strategy"])
    try:
        config = CopilotConfig(**raw)
    except TypeError as exc:
        raise ConfigurationError("unknown_configuration_setting") from exc
    config.validate()
    return config


@dataclass(frozen=True, slots=True, repr=False)
class PaperCredentials:
    api_key: str
    secret_key: str
    hmac_key: bytes

    @classmethod
    def from_environment(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> PaperCredentials:
        values = os.environ if env is None else env
        key = values.get("ALPACA_PAPER_API_KEY", "")
        secret = values.get("ALPACA_PAPER_SECRET_KEY", "")
        hmac_value = values.get("COPILOT_PAPER_HMAC_KEY", "")
        if not key or not secret:
            raise ConfigurationError("paper_credentials_missing")
        if len(hmac_value.encode()) < 32:
            raise ConfigurationError("operator_hmac_key_missing_or_too_short")
        return cls(key, secret, hmac_value.encode())


def load_secret_file(path: Path) -> dict[str, str]:
    """Read literal KEY=value settings, never shell code or generic env files."""
    import stat

    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_size > 16384
        ):
            raise ConfigurationError("paper_secret_file_permissions_invalid")
        with os.fdopen(fd, "r", closefd=False) as handle:
            lines = handle.read(16385).splitlines()
    finally:
        os.close(fd)
    allowed = {
        "ALPACA_PAPER_API_KEY",
        "ALPACA_PAPER_SECRET_KEY",
        "COPILOT_PAPER_HMAC_KEY",
    }
    result = {}
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in allowed or key in result:
            raise ConfigurationError("paper_secret_file_setting_invalid")
        result[key] = value.strip()
    return result
