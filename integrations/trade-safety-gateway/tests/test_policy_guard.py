from __future__ import annotations

import socket
from typing import Any

import pytest
from liquilens_evidence.trade_safety import (
    TRADE_SAFETY_POLICY_SCHEMA,
    trade_safety_policy_hash,
    validate_trade_safety_policy,
)

from trade_safety_gateway.policy_guard import (
    HARD_MAX_EVIDENCE_AGE_SECONDS,
    HARD_MAX_EXIT_COST_BPS,
    HARD_MAX_NOTIONAL_USD,
    HARD_MAX_VENUE_SPREAD_BPS,
    POLICY_MAX_NOTIONAL_ENV,
    POLICY_SHA256_ALLOWLIST_ENV,
    PolicyAdmissionConfig,
    PolicyAdmissionError,
    PolicyAdmissionGuard,
    PolicyAdmissionReason,
    PolicyGuardConfigurationError,
    PolicyGuardConfigurationReason,
)


def _boundary_policy() -> dict[str, Any]:
    return {
        "schema": TRADE_SAFETY_POLICY_SCHEMA,
        "policy_id": "sandbox-default",
        "version": "1.0.0",
        "required_products": ["seiche", "undertow"],
        "max_evidence_age_seconds": dict(HARD_MAX_EVIDENCE_AGE_SECONDS),
        "hold_regimes": ["STRESS"],
        "max_notional_usd": HARD_MAX_NOTIONAL_USD,
        "max_exit_cost_bps": HARD_MAX_EXIT_COST_BPS,
        "max_venue_spread_bps": HARD_MAX_VENUE_SPREAD_BPS,
        "missing_evidence": "fail_closed",
        "live_requires_executable_quote": True,
        "live_requires_broker_preview": True,
        "auto_resize": False,
        "extensions": {},
    }


def _assert_reason(
    guard: PolicyAdmissionGuard,
    policy: dict[str, Any],
    reason: PolicyAdmissionReason,
) -> None:
    with pytest.raises(PolicyAdmissionError) as caught:
        guard.admit(validate_trade_safety_policy(policy))
    assert caught.value.reason_code == reason.value
    assert str(caught.value) == reason.value


def test_reproduced_stress_billion_policy_rejected_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbid_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("policy admission attempted network I/O")

    monkeypatch.setattr(socket, "create_connection", forbid_network)
    guard = PolicyAdmissionGuard()
    permissive = _boundary_policy()
    permissive["hold_regimes"] = []
    permissive["max_notional_usd"] = 1_000_000_000.0

    _assert_reason(
        guard,
        permissive,
        PolicyAdmissionReason.HOLD_REGIMES_TOO_PERMISSIVE,
    )

    # Holding STRESS alone cannot disguise the independently permissive cap.
    permissive["hold_regimes"] = ["STRESS"]
    _assert_reason(
        guard,
        permissive,
        PolicyAdmissionReason.MAX_NOTIONAL_TOO_LOOSE,
    )


def test_exact_hard_boundaries_are_admitted_with_canonical_hash() -> None:
    normalized = validate_trade_safety_policy(_boundary_policy())

    admitted_hash = PolicyAdmissionGuard().admit(normalized)

    assert admitted_hash == trade_safety_policy_hash(normalized)
    assert len(admitted_hash) == 64


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        (
            "max_notional_usd",
            None,
            PolicyAdmissionReason.MAX_NOTIONAL_REQUIRED,
        ),
        (
            "max_notional_usd",
            HARD_MAX_NOTIONAL_USD + 1,
            PolicyAdmissionReason.MAX_NOTIONAL_TOO_LOOSE,
        ),
        (
            "max_exit_cost_bps",
            None,
            PolicyAdmissionReason.MAX_EXIT_COST_REQUIRED,
        ),
        (
            "max_exit_cost_bps",
            HARD_MAX_EXIT_COST_BPS + 1,
            PolicyAdmissionReason.MAX_EXIT_COST_TOO_LOOSE,
        ),
        (
            "max_venue_spread_bps",
            None,
            PolicyAdmissionReason.MAX_VENUE_SPREAD_REQUIRED,
        ),
        (
            "max_venue_spread_bps",
            HARD_MAX_VENUE_SPREAD_BPS + 1,
            PolicyAdmissionReason.MAX_VENUE_SPREAD_TOO_LOOSE,
        ),
    ],
)
def test_numeric_policy_limits_are_non_null_and_bounded(
    field: str,
    value: float | None,
    reason: PolicyAdmissionReason,
) -> None:
    policy = _boundary_policy()
    policy[field] = value

    _assert_reason(PolicyAdmissionGuard(), policy, reason)


@pytest.mark.parametrize(
    ("product", "reason"),
    [
        ("seiche", PolicyAdmissionReason.SEICHE_EVIDENCE_AGE_TOO_LOOSE),
        ("undertow", PolicyAdmissionReason.UNDERTOW_EVIDENCE_AGE_TOO_LOOSE),
        ("liquilens", PolicyAdmissionReason.LIQUILENS_EVIDENCE_AGE_TOO_LOOSE),
    ],
)
def test_each_evidence_age_is_bounded(
    product: str, reason: PolicyAdmissionReason
) -> None:
    policy = _boundary_policy()
    policy["max_evidence_age_seconds"][product] += 1

    _assert_reason(PolicyAdmissionGuard(), policy, reason)


def test_exact_canonical_policy_hash_allowlist() -> None:
    policy = _boundary_policy()
    approved_hash = trade_safety_policy_hash(policy)
    guard = PolicyAdmissionGuard(
        PolicyAdmissionConfig(allowed_policy_sha256=[approved_hash])
    )

    assert guard.admit(policy) == approved_hash

    policy["version"] = "1.0.1"
    with pytest.raises(PolicyAdmissionError) as caught:
        guard.admit(policy)
    assert caught.value.reason_code == PolicyAdmissionReason.POLICY_HASH_NOT_ALLOWED
    assert approved_hash not in str(caught.value)


def test_hash_allowlist_loads_from_environment_exactly() -> None:
    policy = _boundary_policy()
    approved_hash = trade_safety_policy_hash(policy)
    guard = PolicyAdmissionGuard.from_env({POLICY_SHA256_ALLOWLIST_ENV: approved_hash})

    assert guard.admit(policy) == approved_hash

    with pytest.raises(PolicyGuardConfigurationError) as caught:
        PolicyAdmissionGuard.from_env(
            {POLICY_SHA256_ALLOWLIST_ENV: approved_hash.upper()}
        )
    assert caught.value.reason_code == (
        PolicyGuardConfigurationReason.INVALID_POLICY_SHA256_ALLOWLIST
    )


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        (
            {"required_products": ["seiche"]},
            PolicyGuardConfigurationReason.REQUIRED_PRODUCTS_TOO_LOOSE,
        ),
        (
            {"hold_regimes": ["CALM"]},
            PolicyGuardConfigurationReason.HOLD_REGIMES_TOO_LOOSE,
        ),
        (
            {
                "max_evidence_age_seconds": {
                    **HARD_MAX_EVIDENCE_AGE_SECONDS,
                    "seiche": HARD_MAX_EVIDENCE_AGE_SECONDS["seiche"] + 1,
                }
            },
            PolicyGuardConfigurationReason.EVIDENCE_AGES_TOO_LOOSE,
        ),
        (
            {"max_notional_usd": HARD_MAX_NOTIONAL_USD + 1},
            PolicyGuardConfigurationReason.MAX_NOTIONAL_TOO_LOOSE,
        ),
        (
            {"max_exit_cost_bps": HARD_MAX_EXIT_COST_BPS + 1},
            PolicyGuardConfigurationReason.MAX_EXIT_COST_TOO_LOOSE,
        ),
        (
            {"max_venue_spread_bps": HARD_MAX_VENUE_SPREAD_BPS + 1},
            PolicyGuardConfigurationReason.MAX_VENUE_SPREAD_TOO_LOOSE,
        ),
        (
            {"max_notional_usd": float("nan")},
            PolicyGuardConfigurationReason.INVALID_MAX_NOTIONAL,
        ),
    ],
)
def test_invalid_or_looser_direct_configuration_fails_startup(
    kwargs: dict[str, Any], reason: PolicyGuardConfigurationReason
) -> None:
    with pytest.raises(PolicyGuardConfigurationError) as caught:
        PolicyAdmissionConfig(**kwargs)
    assert caught.value.reason_code == reason.value
    assert str(caught.value) == reason.value


@pytest.mark.parametrize(
    ("environ", "reason"),
    [
        (
            {POLICY_MAX_NOTIONAL_ENV: str(HARD_MAX_NOTIONAL_USD + 1)},
            PolicyGuardConfigurationReason.MAX_NOTIONAL_TOO_LOOSE,
        ),
        (
            {POLICY_MAX_NOTIONAL_ENV: "not-a-number"},
            PolicyGuardConfigurationReason.INVALID_MAX_NOTIONAL,
        ),
        (
            {POLICY_SHA256_ALLOWLIST_ENV: ""},
            PolicyGuardConfigurationReason.INVALID_POLICY_SHA256_ALLOWLIST,
        ),
        (
            {"TRADE_SAFETY_POLICY_MAX_NOTIONL_USD": "10"},
            PolicyGuardConfigurationReason.UNKNOWN_ENVIRONMENT_SETTING,
        ),
    ],
)
def test_invalid_or_looser_environment_fails_startup(
    environ: dict[str, str], reason: PolicyGuardConfigurationReason
) -> None:
    with pytest.raises(PolicyGuardConfigurationError) as caught:
        PolicyAdmissionGuard.from_env(environ)
    assert caught.value.reason_code == reason.value


def test_server_configuration_can_only_tighten_admission() -> None:
    config = PolicyAdmissionConfig(
        required_products=["seiche", "undertow", "liquilens"],
        hold_regimes=["STRAIN", "STRESS"],
        max_evidence_age_seconds={
            "seiche": 900,
            "undertow": 60,
            "liquilens": 3_600,
        },
        max_notional_usd=10_000,
        max_exit_cost_bps=35,
        max_venue_spread_bps=10,
    )
    guard = PolicyAdmissionGuard(config)
    policy = _boundary_policy()
    policy["required_products"].append("liquilens")
    policy["hold_regimes"].append("STRAIN")
    policy["max_evidence_age_seconds"] = dict(config.max_evidence_age_seconds)
    policy["max_notional_usd"] = config.max_notional_usd
    policy["max_exit_cost_bps"] = config.max_exit_cost_bps
    policy["max_venue_spread_bps"] = config.max_venue_spread_bps

    assert guard.admit(policy) == trade_safety_policy_hash(policy)

    policy["max_notional_usd"] = config.max_notional_usd + 1
    _assert_reason(guard, policy, PolicyAdmissionReason.MAX_NOTIONAL_TOO_LOOSE)


def test_guard_rejects_wrong_configuration_type_at_startup() -> None:
    with pytest.raises(PolicyGuardConfigurationError) as caught:
        PolicyAdmissionGuard(object())  # type: ignore[arg-type]
    assert caught.value.reason_code == PolicyGuardConfigurationReason.INVALID_CONFIG


def test_policy_errors_do_not_reflect_caller_values() -> None:
    caller_value = "do-not-reflect-this-policy-id"
    policy = _boundary_policy()
    policy["policy_id"] = caller_value
    policy["hold_regimes"] = []

    with pytest.raises(PolicyAdmissionError) as caught:
        PolicyAdmissionGuard().admit(policy)
    assert caller_value not in str(caught.value)
