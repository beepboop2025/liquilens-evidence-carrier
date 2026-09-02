"""Deterministic, order-bound trade-safety receipts.

The protocol composes already-produced evidence into a customer-owned policy
check.  It never turns evidence into a recommendation, executable quote, order,
or capital-allocation instruction.  Missing, stale, restricted, or ineligible
evidence fails closed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from math import isfinite
from typing import Any
from urllib.parse import urlparse

from .canonical import canonical_hash_bytes
from .evidence_carrier import EvidenceCarrierError

TRADE_SAFETY_REQUEST_SCHEMA = "liquilens.trade-safety-request.v1"
TRADE_SAFETY_REQUEST_SCHEMA_URL = (
    "https://liquilens.in/protocol/liquilens-trade-safety-request-v1.schema.json"
)
TRADE_SAFETY_POLICY_SCHEMA = "liquilens.trade-safety-policy.v1"
TRADE_SAFETY_POLICY_SCHEMA_URL = (
    "https://liquilens.in/protocol/liquilens-trade-safety-policy-v1.schema.json"
)
TRADE_SAFETY_RECEIPT_SCHEMA = "liquilens.trade-safety-receipt.v1"
TRADE_SAFETY_RECEIPT_SCHEMA_URL = (
    "https://liquilens.in/protocol/liquilens-trade-safety-receipt-v1.schema.json"
)
BROKER_PREVIEW_REFERENCE_SCHEMA = "liquilens.broker-preview-reference.v1"
BROKER_PREVIEW_REFERENCE_SCHEMA_URL = (
    "https://liquilens.in/protocol/liquilens-broker-preview-reference-v1.schema.json"
)
TRADE_SAFETY_CANONICALIZATION = "liquilens-hash-tree-v1"
TRADE_SAFETY_HMAC_DOMAIN = b"liquilens.trade-safety-receipt.v1\n"
TRADE_SAFETY_MAX_BYTES = 1_048_576
TRADE_SAFETY_PRODUCTS = ("seiche", "undertow", "liquilens")

_PRODUCT_SET = frozenset(TRADE_SAFETY_PRODUCTS)
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_RECEIPT_ID_RE = re.compile(r"^trade_safety_[0-9a-f]{24}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_ASSET_CLASSES = frozenset(
    {
        "crypto",
        "equity",
        "etf",
        "fixed_income",
        "fx",
        "commodity",
        "derivative",
        "other",
    }
)
_SIDES = frozenset({"buy", "sell"})
_ORDER_TYPES = frozenset({"market", "limit", "stop", "stop_limit", "other"})
_REGIMES = frozenset({"CALM", "EROSION", "STRAIN", "STRESS"})
_EVIDENCE_CLASSES = frozenset(
    {"observed", "derived", "structural", "research", "restricted", "unavailable"}
)
_RIGHTS_STATUSES = frozenset(
    {"licensed", "allowed", "metadata_only", "restricted", "unknown", "blocked"}
)
_UNSAFE_RIGHTS = frozenset({"restricted", "unknown", "blocked"})
_REAL_MONEY_RIGHTS = frozenset({"allowed", "licensed"})
_AUTHORITY_BOUNDARY = {
    "financial_authority": "operator_policy_check_only",
    "can_execute": False,
    "can_recommend": False,
    "can_allocate_capital": False,
    "is_credit_rating": False,
    "is_executable_quote": False,
}
_VERIFIED_TRADE_SAFETY_RECEIPT_SEAL = object()


class TradeSafetyError(EvidenceCarrierError):
    """Raised when a trade-safety artifact is malformed or unsafe."""


class TradeSafetyMode(StrEnum):
    """Where the policy result is intended to operate."""

    OBSERVE = "observe"
    PAPER = "paper"
    LIVE = "live"


class TradeSafetyOutcome(StrEnum):
    """A policy result, never an investment recommendation."""

    PASS = "pass"
    LIMIT = "limit"
    HOLD = "hold"
    UNAVAILABLE = "unavailable"


class TradeEvidenceState(StrEnum):
    """Whether one product's evidence can participate in this check."""

    ELIGIBLE = "eligible"
    CONTEXT_ONLY = "context_only"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


class BrokerPreviewState(StrEnum):
    """Whether an exact-order broker preview is attached to the receipt."""

    VERIFIED = "verified"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class VerifiedTradeSafetyReceipt:
    """Immutable result returned only by :func:`verify_trade_safety_receipt`."""

    receipt_json: str
    authenticated: bool
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal is not _VERIFIED_TRADE_SAFETY_RECEIPT_SEAL:
            raise TypeError(
                "VerifiedTradeSafetyReceipt values must come from "
                "verify_trade_safety_receipt"
            )

    @property
    def receipt(self) -> dict[str, Any]:
        value = json.loads(self.receipt_json)
        if not isinstance(value, dict):  # pragma: no cover - constructor invariant
            raise TypeError("verified receipt root changed shape")
        return value

    @property
    def outcome(self) -> TradeSafetyOutcome:
        return TradeSafetyOutcome(self.receipt["decision"]["outcome"])

    @property
    def policy_satisfied(self) -> bool:
        return self.outcome is TradeSafetyOutcome.PASS


def _json_copy(value: Any, field_name: str) -> Any:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise TradeSafetyError(f"{field_name} must contain finite JSON") from error
    if len(encoded) > TRADE_SAFETY_MAX_BYTES:
        raise TradeSafetyError(
            f"trade-safety artifact exceeds {TRADE_SAFETY_MAX_BYTES} encoded bytes"
        )
    return json.loads(encoded)


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TradeSafetyError(f"{field_name} must be an object")
    copied = _json_copy(value, field_name)
    if not isinstance(copied, dict):  # pragma: no cover - Mapping invariant
        raise TypeError("mapping normalized to a non-object")
    return copied


def _exact_keys(
    value: Mapping[str, Any],
    field_name: str,
    required: set[str],
) -> None:
    missing = required - set(value)
    extra = set(value) - required
    if missing:
        raise TradeSafetyError(
            f"{field_name} is missing fields: {', '.join(sorted(missing))}"
        )
    if extra:
        raise TradeSafetyError(
            f"{field_name} has unsupported fields: {', '.join(sorted(extra))}"
        )


def _string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TradeSafetyError(f"{field_name} must be a non-blank string")
    return value


def _nullable_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _string(value, field_name)


def _enum(value: Any, field_name: str, allowed: frozenset[str]) -> str:
    item = _string(value, field_name)
    if item not in allowed:
        raise TradeSafetyError(f"{field_name} has an unsupported value")
    return item


def _number(value: Any, field_name: str, *, nullable: bool = False) -> float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TradeSafetyError(f"{field_name} must be a finite positive number")
    converted = float(value)
    if not isfinite(converted) or converted <= 0:
        raise TradeSafetyError(f"{field_name} must be a finite positive number")
    return converted


def _nonnegative_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TradeSafetyError(f"{field_name} must be a finite non-negative number")
    converted = float(value)
    if not isfinite(converted) or converted < 0:
        raise TradeSafetyError(f"{field_name} must be a finite non-negative number")
    return converted


def _positive_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise TradeSafetyError(f"{field_name} must be a positive integer")
    return int(value)


def _utc(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise TradeSafetyError(f"{field_name} must be a UTC timestamp ending in Z")
    try:
        instant = datetime.fromisoformat(value)
    except ValueError as error:
        raise TradeSafetyError(f"{field_name} is not a valid timestamp") from error
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise TradeSafetyError(f"{field_name} must be timezone-aware")
    return instant.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _instant(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _https_url(value: Any, field_name: str) -> str:
    item = _string(value, field_name)
    parsed = urlparse(item)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username is not None:
        raise TradeSafetyError(f"{field_name} must be an HTTPS URL without userinfo")
    return item


def _sha256(value: Any, field_name: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise TradeSafetyError(f"{field_name} must be lowercase SHA-256")
    return value


def _string_list(
    value: Any,
    field_name: str,
    *,
    allow_empty: bool = False,
    allowed: frozenset[str] | None = None,
) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        suffix = "an array" if allow_empty else "a non-empty array"
        raise TradeSafetyError(f"{field_name} must be {suffix}")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise TradeSafetyError(f"{field_name} must contain non-blank strings")
    if len(value) != len(set(value)):
        raise TradeSafetyError(f"{field_name} must not contain duplicates")
    if allowed is not None and not set(value) <= allowed:
        raise TradeSafetyError(f"{field_name} contains an unsupported value")
    return tuple(value)


def _object_of_strings(value: Any, field_name: str) -> dict[str, str]:
    result = _mapping(value, field_name)
    for key, item in result.items():
        if (
            not isinstance(key, str)
            or not key.strip()
            or any(char.isspace() for char in key)
        ):
            raise TradeSafetyError(f"{field_name} keys must be non-blank tokens")
        _string(item, f"{field_name}.{key}")
    return result


def validate_trade_safety_request(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a finite, strict copy of an exact proposed-order request."""

    request = _mapping(value, "request")
    _exact_keys(
        request,
        "request",
        {
            "schema",
            "request_id",
            "created_at",
            "expires_at",
            "mode",
            "agent",
            "order",
            "policy_ref",
            "extensions",
        },
    )
    if request["schema"] != TRADE_SAFETY_REQUEST_SCHEMA:
        raise TradeSafetyError("request.schema is unsupported")
    request_id = _string(request["request_id"], "request.request_id")
    if _REQUEST_ID_RE.fullmatch(request_id) is None:
        raise TradeSafetyError("request.request_id has an invalid shape")
    created_at = _utc(request["created_at"], "request.created_at")
    expires_at = _utc(request["expires_at"], "request.expires_at")
    if expires_at <= created_at:
        raise TradeSafetyError("request.expires_at must follow request.created_at")
    _enum(
        request["mode"],
        "request.mode",
        frozenset(item.value for item in TradeSafetyMode),
    )

    agent = _mapping(request["agent"], "request.agent")
    _exact_keys(
        agent,
        "request.agent",
        {
            "agent_id",
            "operator_id",
            "tenant_id",
            "account_id",
            "runtime",
            "strategy_id",
            "authorization_scope",
        },
    )
    _string(agent["agent_id"], "request.agent.agent_id")
    _string(agent["operator_id"], "request.agent.operator_id")
    _string(agent["tenant_id"], "request.agent.tenant_id")
    _string(agent["account_id"], "request.agent.account_id")
    _string(agent["runtime"], "request.agent.runtime")
    _nullable_string(agent["strategy_id"], "request.agent.strategy_id")
    authorization_scope = _string_list(
        agent["authorization_scope"], "request.agent.authorization_scope"
    )
    if request["mode"] == TradeSafetyMode.PAPER.value and (
        "orders:paper" not in authorization_scope
    ):
        raise TradeSafetyError(
            "paper mode requires orders:paper in request.agent.authorization_scope"
        )
    if request["mode"] == TradeSafetyMode.LIVE.value and (
        "orders:live" not in authorization_scope
    ):
        raise TradeSafetyError(
            "live mode requires orders:live in request.agent.authorization_scope"
        )

    order = _mapping(request["order"], "request.order")
    _exact_keys(
        order,
        "request.order",
        {
            "instrument",
            "side",
            "order_type",
            "notional",
            "quantity",
            "limit_price",
            "stop_price",
            "venue",
            "time_in_force",
        },
    )
    instrument = _mapping(order["instrument"], "request.order.instrument")
    _exact_keys(
        instrument,
        "request.order.instrument",
        {"asset_class", "symbol", "identifiers"},
    )
    _enum(
        instrument["asset_class"],
        "request.order.instrument.asset_class",
        _ASSET_CLASSES,
    )
    _string(instrument["symbol"], "request.order.instrument.symbol")
    _object_of_strings(
        instrument["identifiers"], "request.order.instrument.identifiers"
    )
    _enum(order["side"], "request.order.side", _SIDES)
    order_type = _enum(order["order_type"], "request.order.order_type", _ORDER_TYPES)
    notional = _mapping(order["notional"], "request.order.notional")
    _exact_keys(notional, "request.order.notional", {"amount", "currency"})
    _number(notional["amount"], "request.order.notional.amount")
    currency = _string(notional["currency"], "request.order.notional.currency")
    if _CURRENCY_RE.fullmatch(currency) is None:
        raise TradeSafetyError(
            "request.order.notional.currency must be an uppercase three-letter code"
        )
    _number(order["quantity"], "request.order.quantity", nullable=True)
    limit_price = _number(
        order["limit_price"], "request.order.limit_price", nullable=True
    )
    stop_price = _number(order["stop_price"], "request.order.stop_price", nullable=True)
    if order_type in {"limit", "stop_limit"} and limit_price is None:
        raise TradeSafetyError(
            "request.order.limit_price is required for limit and stop_limit orders"
        )
    if order_type in {"stop", "stop_limit"} and stop_price is None:
        raise TradeSafetyError(
            "request.order.stop_price is required for stop and stop_limit orders"
        )
    if order_type not in {"limit", "stop_limit"} and limit_price is not None:
        raise TradeSafetyError(
            "request.order.limit_price is only valid for limit and stop_limit orders"
        )
    if order_type not in {"stop", "stop_limit"} and stop_price is not None:
        raise TradeSafetyError(
            "request.order.stop_price is only valid for stop and stop_limit orders"
        )
    _nullable_string(order["venue"], "request.order.venue")
    _string(order["time_in_force"], "request.order.time_in_force")

    policy_ref = _mapping(request["policy_ref"], "request.policy_ref")
    _exact_keys(policy_ref, "request.policy_ref", {"policy_id", "version"})
    _string(policy_ref["policy_id"], "request.policy_ref.policy_id")
    _string(policy_ref["version"], "request.policy_ref.version")
    extensions = _mapping(request["extensions"], "request.extensions")
    if extensions:
        raise TradeSafetyError(
            "request.extensions contains unsupported execution semantics"
        )
    return request


def validate_trade_safety_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a strict policy copy without granting financial authority."""

    policy = _mapping(value, "policy")
    _exact_keys(
        policy,
        "policy",
        {
            "schema",
            "policy_id",
            "version",
            "required_products",
            "max_evidence_age_seconds",
            "hold_regimes",
            "max_notional_usd",
            "max_exit_cost_bps",
            "max_venue_spread_bps",
            "missing_evidence",
            "live_requires_executable_quote",
            "live_requires_broker_preview",
            "auto_resize",
            "extensions",
        },
    )
    if policy["schema"] != TRADE_SAFETY_POLICY_SCHEMA:
        raise TradeSafetyError("policy.schema is unsupported")
    _string(policy["policy_id"], "policy.policy_id")
    _string(policy["version"], "policy.version")
    required_products = _string_list(
        policy["required_products"],
        "policy.required_products",
        allowed=_PRODUCT_SET,
    )
    if not {"seiche", "undertow"} <= set(required_products):
        raise TradeSafetyError(
            "policy.required_products must include both seiche and undertow"
        )
    ages = _mapping(
        policy["max_evidence_age_seconds"], "policy.max_evidence_age_seconds"
    )
    _exact_keys(
        ages,
        "policy.max_evidence_age_seconds",
        set(TRADE_SAFETY_PRODUCTS),
    )
    for product in TRADE_SAFETY_PRODUCTS:
        _positive_integer(ages[product], f"policy.max_evidence_age_seconds.{product}")
    _string_list(
        policy["hold_regimes"],
        "policy.hold_regimes",
        allow_empty=True,
        allowed=_REGIMES,
    )
    _number(policy["max_notional_usd"], "policy.max_notional_usd", nullable=True)
    _number(
        policy["max_exit_cost_bps"],
        "policy.max_exit_cost_bps",
        nullable=True,
    )
    _number(
        policy["max_venue_spread_bps"],
        "policy.max_venue_spread_bps",
        nullable=True,
    )
    if policy["missing_evidence"] != "fail_closed":
        raise TradeSafetyError("policy.missing_evidence must be fail_closed")
    if policy["live_requires_executable_quote"] is not True:
        raise TradeSafetyError(
            "policy.live_requires_executable_quote cannot be disabled"
        )
    if policy["live_requires_broker_preview"] is not True:
        raise TradeSafetyError("policy.live_requires_broker_preview cannot be disabled")
    if policy["auto_resize"] is not False:
        raise TradeSafetyError("policy.auto_resize cannot be enabled")
    extensions = _mapping(policy["extensions"], "policy.extensions")
    if extensions:
        raise TradeSafetyError(
            "policy.extensions contains unsupported operator constraints"
        )
    return policy


def _validate_fact_number(
    facts: Mapping[str, Any], key: str, field_name: str
) -> float | None:
    value = facts.get(key)
    if value is None:
        return None
    return _nonnegative_number(value, f"{field_name}.{key}")


def _validate_evidence_section(product: str, value: Any) -> dict[str, Any]:
    section = _mapping(value, f"evidence.{product}")
    _exact_keys(
        section,
        f"evidence.{product}",
        {
            "product",
            "request_hash",
            "state",
            "evidence_class",
            "source_url",
            "source_schema",
            "source_sha256",
            "as_of",
            "knowledge_time",
            "retrieved_at",
            "expires_at",
            "rights_status",
            "real_money_eligible",
            "executable_quote",
            "limitations",
            "facts",
        },
    )
    if section["product"] != product:
        raise TradeSafetyError(f"evidence.{product}.product does not match its key")
    _sha256(section["request_hash"], f"evidence.{product}.request_hash")
    state = TradeEvidenceState(
        _enum(
            section["state"],
            f"evidence.{product}.state",
            frozenset(item.value for item in TradeEvidenceState),
        )
    )
    _enum(
        section["evidence_class"],
        f"evidence.{product}.evidence_class",
        _EVIDENCE_CLASSES,
    )
    _https_url(section["source_url"], f"evidence.{product}.source_url")
    _nullable_string(section["source_schema"], f"evidence.{product}.source_schema")
    source_sha = _sha256(
        section["source_sha256"],
        f"evidence.{product}.source_sha256",
        nullable=True,
    )
    retrieved_at = _utc(section["retrieved_at"], f"evidence.{product}.retrieved_at")
    as_of = (
        None
        if section["as_of"] is None
        else _utc(section["as_of"], f"evidence.{product}.as_of")
    )
    knowledge_time = (
        None
        if section["knowledge_time"] is None
        else _utc(section["knowledge_time"], f"evidence.{product}.knowledge_time")
    )
    expires_at = (
        None
        if section["expires_at"] is None
        else _utc(section["expires_at"], f"evidence.{product}.expires_at")
    )
    if as_of is not None and knowledge_time is not None and as_of > knowledge_time:
        raise TradeSafetyError(
            f"evidence.{product} clocks must satisfy as_of <= knowledge_time"
        )
    if knowledge_time is not None and knowledge_time > retrieved_at:
        raise TradeSafetyError(
            f"evidence.{product} knowledge_time cannot follow retrieved_at"
        )
    if expires_at is not None and expires_at <= retrieved_at:
        raise TradeSafetyError(
            f"evidence.{product}.expires_at must follow retrieved_at"
        )
    rights_status = _enum(
        section["rights_status"],
        f"evidence.{product}.rights_status",
        _RIGHTS_STATUSES,
    )
    if not isinstance(section["real_money_eligible"], bool):
        raise TradeSafetyError(
            f"evidence.{product}.real_money_eligible must be boolean"
        )
    if not isinstance(section["executable_quote"], bool):
        raise TradeSafetyError(f"evidence.{product}.executable_quote must be boolean")
    _string_list(section["limitations"], f"evidence.{product}.limitations")
    facts = _mapping(section["facts"], f"evidence.{product}.facts")

    if state is TradeEvidenceState.NOT_APPLICABLE:
        if any(
            item is not None for item in (source_sha, as_of, knowledge_time, expires_at)
        ):
            raise TradeSafetyError(
                f"evidence.{product} not_applicable state cannot carry source data"
            )
        if facts:
            raise TradeSafetyError(
                f"evidence.{product} not_applicable state must have empty facts"
            )
    elif state is TradeEvidenceState.UNAVAILABLE:
        if section["real_money_eligible"] or section["executable_quote"]:
            raise TradeSafetyError(
                f"evidence.{product} unavailable state cannot be eligible or quoted"
            )
    else:
        if source_sha is None or as_of is None or knowledge_time is None:
            raise TradeSafetyError(
                f"evidence.{product} usable state requires source hash and clocks"
            )
        if expires_at is None:
            raise TradeSafetyError(
                f"evidence.{product} usable state requires expires_at"
            )
    if rights_status in _UNSAFE_RIGHTS and state is TradeEvidenceState.ELIGIBLE:
        raise TradeSafetyError(
            f"evidence.{product} unsafe rights cannot be marked eligible"
        )
    if section["executable_quote"] and product != "undertow":
        raise TradeSafetyError(
            f"evidence.{product} cannot claim to be an executable quote"
        )
    if section["executable_quote"] and not section["real_money_eligible"]:
        raise TradeSafetyError(
            "an executable quote must also be marked real_money_eligible"
        )
    if section["real_money_eligible"] and state is not TradeEvidenceState.ELIGIBLE:
        raise TradeSafetyError(
            f"evidence.{product} real-money evidence must have eligible state"
        )
    if section["real_money_eligible"] and rights_status not in _REAL_MONEY_RIGHTS:
        raise TradeSafetyError(
            f"evidence.{product} real-money evidence requires allowed or licensed rights"
        )
    usable = state in {
        TradeEvidenceState.ELIGIBLE,
        TradeEvidenceState.CONTEXT_ONLY,
    }
    if product == "seiche":
        if usable and "regime" not in facts:
            raise TradeSafetyError(
                "evidence.seiche usable state requires facts.regime"
            )
        if "regime" in facts:
            _enum(facts["regime"], "evidence.seiche.facts.regime", _REGIMES)
    if product == "undertow":
        size_keys = ("requested_size_usd", "published_rung_used_usd")
        cost_keys = ("worst_sell_cost_bps", "venue_spread_bps")
        if usable:
            missing = [key for key in (*size_keys, *cost_keys) if key not in facts]
            if missing:
                raise TradeSafetyError(
                    "evidence.undertow usable state requires normative facts: "
                    + ", ".join(missing)
                )
        for key in size_keys:
            value = facts.get(key)
            if value is not None:
                _number(value, f"evidence.undertow.facts.{key}")
        for key in cost_keys:
            _validate_fact_number(facts, key, "evidence.undertow.facts")
    return section


def validate_trade_safety_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the three independent product sections without flattening them."""

    evidence = _mapping(value, "evidence")
    _exact_keys(evidence, "evidence", set(TRADE_SAFETY_PRODUCTS))
    return {
        product: _validate_evidence_section(product, evidence[product])
        for product in TRADE_SAFETY_PRODUCTS
    }


def validate_broker_preview_reference(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a broker-owned preview reference without treating it as an order."""

    preview = _mapping(value, "broker_preview")
    _exact_keys(
        preview,
        "broker_preview",
        {
            "schema",
            "state",
            "provider",
            "account_id",
            "request_hash",
            "preview_id",
            "source_url",
            "source_sha256",
            "retrieved_at",
            "expires_at",
            "limitations",
            "facts",
        },
    )
    if preview["schema"] != BROKER_PREVIEW_REFERENCE_SCHEMA:
        raise TradeSafetyError("broker_preview.schema is unsupported")
    state = BrokerPreviewState(
        _enum(
            preview["state"],
            "broker_preview.state",
            frozenset(item.value for item in BrokerPreviewState),
        )
    )
    provider = _nullable_string(preview["provider"], "broker_preview.provider")
    _string(preview["account_id"], "broker_preview.account_id")
    _sha256(preview["request_hash"], "broker_preview.request_hash")
    preview_id = _nullable_string(preview["preview_id"], "broker_preview.preview_id")
    source_url = (
        None
        if preview["source_url"] is None
        else _https_url(preview["source_url"], "broker_preview.source_url")
    )
    source_sha = _sha256(
        preview["source_sha256"], "broker_preview.source_sha256", nullable=True
    )
    retrieved_at = _utc(preview["retrieved_at"], "broker_preview.retrieved_at")
    expires_at = (
        None
        if preview["expires_at"] is None
        else _utc(preview["expires_at"], "broker_preview.expires_at")
    )
    if expires_at is not None and expires_at <= retrieved_at:
        raise TradeSafetyError(
            "broker_preview.expires_at must follow broker_preview.retrieved_at"
        )
    _string_list(preview["limitations"], "broker_preview.limitations")
    facts = _mapping(preview["facts"], "broker_preview.facts")
    if state is BrokerPreviewState.VERIFIED:
        if any(
            item is None
            for item in (provider, preview_id, source_url, source_sha, expires_at)
        ):
            raise TradeSafetyError(
                "verified broker_preview requires provider, preview identity, "
                "source identity, and expiry"
            )
    elif state is BrokerPreviewState.NOT_APPLICABLE:
        if any(
            item is not None
            for item in (provider, preview_id, source_url, source_sha, expires_at)
        ):
            raise TradeSafetyError(
                "not_applicable broker_preview cannot carry broker source data"
            )
        if facts:
            raise TradeSafetyError(
                "not_applicable broker_preview must have empty facts"
            )
    return preview


def _hash_object(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_hash_bytes(value)).hexdigest()


def trade_safety_request_hash(value: Mapping[str, Any]) -> str:
    """Return the canonical hash adapters must bind into every evidence section."""

    return _hash_object(validate_trade_safety_request(value))


def _append_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _fact_number(section: Mapping[str, Any], key: str) -> float | None:
    value = section["facts"].get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if isfinite(converted) and converted >= 0 else None


def _evaluate_decision(
    *,
    request: Mapping[str, Any],
    evidence: Mapping[str, Mapping[str, Any]],
    broker_preview: Mapping[str, Any],
    policy: Mapping[str, Any],
    evaluated_at: datetime,
    authenticated: bool,
) -> dict[str, Any]:
    mode = TradeSafetyMode(request["mode"])
    required_products = tuple(policy["required_products"])
    unavailable: list[str] = []
    holds: list[str] = []
    limits: list[str] = []

    for product in required_products:
        section = evidence[product]
        state = TradeEvidenceState(section["state"])
        if state in {
            TradeEvidenceState.UNAVAILABLE,
            TradeEvidenceState.NOT_APPLICABLE,
        }:
            _append_reason(unavailable, f"{product}_evidence_{state.value}")
            continue
        if section["rights_status"] in _UNSAFE_RIGHTS:
            _append_reason(unavailable, f"{product}_rights_not_usable")
        knowledge_time = _utc(
            section["knowledge_time"], f"evidence.{product}.knowledge_time"
        )
        as_of = _utc(section["as_of"], f"evidence.{product}.as_of")
        retrieved_at = _utc(section["retrieved_at"], f"evidence.{product}.retrieved_at")
        expires_at = _utc(section["expires_at"], f"evidence.{product}.expires_at")
        if evaluated_at < knowledge_time:
            _append_reason(unavailable, f"{product}_evidence_not_yet_known")
        if evaluated_at < retrieved_at:
            _append_reason(unavailable, f"{product}_evidence_not_yet_retrieved")
        if evaluated_at >= expires_at:
            _append_reason(unavailable, f"{product}_evidence_expired")
        max_age = timedelta(seconds=policy["max_evidence_age_seconds"][product])
        if evaluated_at - as_of > max_age:
            _append_reason(unavailable, f"{product}_evidence_too_old")
        if mode is TradeSafetyMode.LIVE and not section["real_money_eligible"]:
            _append_reason(unavailable, f"{product}_not_real_money_eligible")

    if mode is TradeSafetyMode.LIVE:
        if not authenticated:
            _append_reason(unavailable, "live_receipt_authentication_missing")
        if (
            policy["live_requires_executable_quote"]
            and not evidence["undertow"]["executable_quote"]
        ):
            _append_reason(unavailable, "undertow_executable_quote_unavailable")
        preview_state = BrokerPreviewState(broker_preview["state"])
        if policy["live_requires_broker_preview"] and (
            preview_state is not BrokerPreviewState.VERIFIED
        ):
            _append_reason(unavailable, "broker_preview_unavailable")
        if preview_state is BrokerPreviewState.VERIFIED:
            preview_retrieved_at = _utc(
                broker_preview["retrieved_at"], "broker_preview.retrieved_at"
            )
            preview_expires_at = _utc(
                broker_preview["expires_at"], "broker_preview.expires_at"
            )
            if evaluated_at < preview_retrieved_at:
                _append_reason(unavailable, "broker_preview_not_yet_retrieved")
            if evaluated_at >= preview_expires_at:
                _append_reason(unavailable, "broker_preview_expired")

    regime = evidence["seiche"]["facts"].get("regime")
    if regime in policy["hold_regimes"]:
        _append_reason(holds, f"seiche_regime_{str(regime).lower()}_held_by_policy")

    order = request["order"]
    notional = float(order["notional"]["amount"])
    currency = order["notional"]["currency"]
    if currency != "USD" and any(
        policy[key] is not None
        for key in (
            "max_notional_usd",
            "max_exit_cost_bps",
            "max_venue_spread_bps",
        )
    ):
        _append_reason(unavailable, "usd_policy_requires_usd_order_notional")

    max_notional = policy["max_notional_usd"]
    if max_notional is not None and currency == "USD" and notional > max_notional:
        _append_reason(limits, "max_notional_usd_exceeded")

    undertow = evidence["undertow"]
    requested_size = _fact_number(undertow, "requested_size_usd")
    published_rung = _fact_number(undertow, "published_rung_used_usd")
    exit_cost = _fact_number(undertow, "worst_sell_cost_bps")
    venue_spread = _fact_number(undertow, "venue_spread_bps")
    if currency == "USD" and requested_size is not None:
        tolerance = max(0.01, notional * 1e-9)
        if abs(requested_size - notional) > tolerance:
            _append_reason(unavailable, "undertow_order_size_mismatch")
    if requested_size is not None and published_rung is not None:
        tolerance = max(0.01, requested_size * 1e-9)
        if abs(published_rung - requested_size) > tolerance:
            _append_reason(unavailable, "undertow_published_rung_mismatch")
    if policy["max_exit_cost_bps"] is not None:
        if exit_cost is None:
            _append_reason(unavailable, "undertow_exit_cost_missing")
        elif exit_cost > policy["max_exit_cost_bps"]:
            _append_reason(limits, "max_exit_cost_bps_exceeded")
    if policy["max_venue_spread_bps"] is not None:
        if venue_spread is None:
            _append_reason(unavailable, "undertow_venue_spread_missing")
        elif venue_spread > policy["max_venue_spread_bps"]:
            _append_reason(limits, "max_venue_spread_bps_exceeded")

    constraints = {
        "required_products": list(required_products),
        "max_notional_usd": policy["max_notional_usd"],
        "max_exit_cost_bps": policy["max_exit_cost_bps"],
        "max_venue_spread_bps": policy["max_venue_spread_bps"],
        "auto_resize": False,
    }
    enforced = mode is not TradeSafetyMode.OBSERVE
    if unavailable:
        outcome = TradeSafetyOutcome.UNAVAILABLE
        reasons = unavailable + holds + limits
        summary = (
            "Required evidence or authentication is unavailable; fail closed for "
            "this exact order."
        )
        resubmit_required = False
    elif holds:
        outcome = TradeSafetyOutcome.HOLD
        reasons = holds + limits
        summary = (
            "An operator-authored policy condition holds this exact order; this is "
            "not a trade recommendation."
        )
        resubmit_required = False
    elif limits:
        outcome = TradeSafetyOutcome.LIMIT
        reasons = limits
        summary = (
            "This exact order exceeds an operator-authored limit; no automatic "
            "resizing is permitted."
        )
        resubmit_required = True
    else:
        outcome = TradeSafetyOutcome.PASS
        reasons = ["operator_policy_satisfied"]
        summary = (
            "The operator-authored policy is satisfied for this exact order; this "
            "is not approval, advice, or an execution instruction."
        )
        resubmit_required = False
    return {
        "outcome": outcome.value,
        "enforced": enforced,
        "reason_codes": reasons,
        "constraints": constraints,
        "summary": summary,
        "resubmit_required": resubmit_required,
    }


def _receipt_expiry_ceiling(
    *,
    request: Mapping[str, Any],
    evidence: Mapping[str, Mapping[str, Any]],
    broker_preview: Mapping[str, Any],
    policy: Mapping[str, Any],
    issued_at: datetime,
    ttl_seconds: int,
    protect_dependencies: bool,
) -> datetime:
    """Return the latest safe expiry for the decision embedded in a receipt."""

    boundaries = [
        _utc(request["expires_at"], "request.expires_at"),
        issued_at + timedelta(seconds=ttl_seconds),
    ]
    if protect_dependencies:
        for product in policy["required_products"]:
            section = evidence[product]
            state = TradeEvidenceState(section["state"])
            if state not in {
                TradeEvidenceState.ELIGIBLE,
                TradeEvidenceState.CONTEXT_ONLY,
            }:
                continue
            boundaries.append(
                _utc(section["expires_at"], f"evidence.{product}.expires_at")
            )
            boundaries.append(
                _utc(section["as_of"], f"evidence.{product}.as_of")
                + timedelta(
                    seconds=policy["max_evidence_age_seconds"][product]
                )
            )
        if (
            request["mode"] == TradeSafetyMode.LIVE.value
            and broker_preview["state"] == BrokerPreviewState.VERIFIED.value
        ):
            boundaries.append(
                _utc(broker_preview["expires_at"], "broker_preview.expires_at")
            )
    return min(boundaries)


def _validate_issuer(value: Any) -> dict[str, Any]:
    issuer = _mapping(value, "issuer")
    _exact_keys(issuer, "issuer", {"name", "version", "endpoint"})
    _string(issuer["name"], "issuer.name")
    _string(issuer["version"], "issuer.version")
    _https_url(issuer["endpoint"], "issuer.endpoint")
    return issuer


def _receipt_payload(receipt: Mapping[str, Any]) -> dict[str, Any]:
    payload = {
        key: value
        for key, value in receipt.items()
        if key not in {"receipt_id", "record_hash"}
    }
    integrity = dict(payload["integrity"])
    integrity["signature"] = None
    payload["integrity"] = integrity
    return payload


def _receipt_digest(receipt: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_hash_bytes(_receipt_payload(receipt))).hexdigest()


def issue_trade_safety_receipt(
    *,
    request: Mapping[str, Any],
    evidence: Mapping[str, Any],
    policy: Mapping[str, Any],
    broker_preview: Mapping[str, Any],
    evaluated_at: datetime,
    issuer: Mapping[str, Any],
    ttl_seconds: int = 60,
    hmac_key: bytes | None = None,
    hmac_key_id: str | None = None,
) -> dict[str, Any]:
    """Evaluate policy and issue a short-lived receipt bound to one exact order.

    A hash-only receipt is suitable for observation and paper conformance.  Live
    mode cannot pass unless a non-empty HMAC key and key identifier authenticate
    the tenant-local receipt.  The function never contacts a market or broker.
    """

    normalized_request = validate_trade_safety_request(request)
    normalized_evidence = validate_trade_safety_evidence(evidence)
    normalized_policy = validate_trade_safety_policy(policy)
    normalized_broker_preview = validate_broker_preview_reference(broker_preview)
    normalized_issuer = _validate_issuer(issuer)
    instant = _instant(evaluated_at, "evaluated_at")
    ttl = _positive_integer(ttl_seconds, "ttl_seconds")
    if ttl > 3600:
        raise TradeSafetyError("ttl_seconds cannot exceed 3600")

    created_at = _utc(normalized_request["created_at"], "request.created_at")
    request_expires_at = _utc(normalized_request["expires_at"], "request.expires_at")
    if instant < created_at:
        raise TradeSafetyError("request is not yet valid at evaluated_at")
    if instant >= request_expires_at:
        raise TradeSafetyError("request is expired at evaluated_at")
    if normalized_request["policy_ref"] != {
        "policy_id": normalized_policy["policy_id"],
        "version": normalized_policy["version"],
    }:
        raise TradeSafetyError("request.policy_ref does not match policy identity")
    request_hash = _hash_object(normalized_request)
    for product in TRADE_SAFETY_PRODUCTS:
        if normalized_evidence[product]["request_hash"] != request_hash:
            raise TradeSafetyError(
                f"evidence.{product}.request_hash does not match request"
            )
    if normalized_broker_preview["request_hash"] != request_hash:
        raise TradeSafetyError("broker_preview.request_hash does not match request")
    if (
        normalized_broker_preview["account_id"]
        != normalized_request["agent"]["account_id"]
    ):
        raise TradeSafetyError("broker_preview.account_id does not match request")

    if (hmac_key is None) != (hmac_key_id is None):
        raise TradeSafetyError("hmac_key and hmac_key_id must be supplied together")
    if hmac_key is not None and (not isinstance(hmac_key, bytes) or not hmac_key):
        raise TradeSafetyError("hmac_key must be non-empty bytes")
    if hmac_key_id is not None:
        _string(hmac_key_id, "hmac_key_id")
    authenticated = hmac_key is not None
    integrity = {
        "profile": "hmac-sha256" if authenticated else "sha256",
        "key_id": hmac_key_id,
        "signature": None,
    }
    decision = _evaluate_decision(
        request=normalized_request,
        evidence=normalized_evidence,
        broker_preview=normalized_broker_preview,
        policy=normalized_policy,
        evaluated_at=instant,
        authenticated=authenticated,
    )
    expires_at = _receipt_expiry_ceiling(
        request=normalized_request,
        evidence=normalized_evidence,
        broker_preview=normalized_broker_preview,
        policy=normalized_policy,
        issued_at=instant,
        ttl_seconds=ttl,
        protect_dependencies=(
            decision["outcome"] != TradeSafetyOutcome.UNAVAILABLE.value
        ),
    )
    if expires_at <= instant:
        raise TradeSafetyError("receipt has no positive safe validity window")
    receipt: dict[str, Any] = {
        "schema": TRADE_SAFETY_RECEIPT_SCHEMA,
        "canonicalization": TRADE_SAFETY_CANONICALIZATION,
        "receipt_id": "trade_safety_" + "0" * 24,
        "record_hash": "0" * 64,
        "evaluated_at": _utc_text(instant),
        "expires_at": _utc_text(expires_at),
        "request": normalized_request,
        "request_hash": request_hash,
        "policy": normalized_policy,
        "policy_hash": _hash_object(normalized_policy),
        "evidence": normalized_evidence,
        "broker_preview": normalized_broker_preview,
        "decision": decision,
        "issuer": normalized_issuer,
        "integrity": integrity,
        "authority": dict(_AUTHORITY_BOUNDARY),
    }
    receipt = _json_copy(receipt, "receipt")
    digest = _receipt_digest(receipt)
    receipt["receipt_id"] = f"trade_safety_{digest[:24]}"
    receipt["record_hash"] = digest
    if hmac_key is not None:
        receipt["integrity"]["signature"] = hmac.new(
            hmac_key,
            TRADE_SAFETY_HMAC_DOMAIN + digest.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
    verify_trade_safety_receipt(
        receipt,
        evaluated_at=instant,
        hmac_key=hmac_key,
    )
    return receipt


def _validate_integrity(value: Any) -> dict[str, Any]:
    integrity = _mapping(value, "receipt.integrity")
    _exact_keys(integrity, "receipt.integrity", {"profile", "key_id", "signature"})
    profile = _enum(
        integrity["profile"],
        "receipt.integrity.profile",
        frozenset({"sha256", "hmac-sha256"}),
    )
    key_id = _nullable_string(integrity["key_id"], "receipt.integrity.key_id")
    signature = _sha256(
        integrity["signature"], "receipt.integrity.signature", nullable=True
    )
    if profile == "sha256" and (key_id is not None or signature is not None):
        raise TradeSafetyError("sha256 integrity cannot carry a key or signature")
    if profile == "hmac-sha256" and (key_id is None or signature is None):
        raise TradeSafetyError("hmac-sha256 integrity requires key_id and signature")
    return integrity


def _validate_decision(value: Any) -> dict[str, Any]:
    decision = _mapping(value, "receipt.decision")
    _exact_keys(
        decision,
        "receipt.decision",
        {
            "outcome",
            "enforced",
            "reason_codes",
            "constraints",
            "summary",
            "resubmit_required",
        },
    )
    _enum(
        decision["outcome"],
        "receipt.decision.outcome",
        frozenset(item.value for item in TradeSafetyOutcome),
    )
    if not isinstance(decision["enforced"], bool):
        raise TradeSafetyError("receipt.decision.enforced must be boolean")
    _string_list(decision["reason_codes"], "receipt.decision.reason_codes")
    _mapping(decision["constraints"], "receipt.decision.constraints")
    _string(decision["summary"], "receipt.decision.summary")
    if not isinstance(decision["resubmit_required"], bool):
        raise TradeSafetyError("receipt.decision.resubmit_required must be boolean")
    return decision


def verify_trade_safety_receipt(
    value: Mapping[str, Any],
    *,
    evaluated_at: datetime,
    hmac_key: bytes | None = None,
) -> VerifiedTradeSafetyReceipt:
    """Verify identity, policy result, authentication, clocks, and authority."""

    receipt = _mapping(value, "receipt")
    _exact_keys(
        receipt,
        "receipt",
        {
            "schema",
            "canonicalization",
            "receipt_id",
            "record_hash",
            "evaluated_at",
            "expires_at",
            "request",
            "request_hash",
            "policy",
            "policy_hash",
            "evidence",
            "broker_preview",
            "decision",
            "issuer",
            "integrity",
            "authority",
        },
    )
    if receipt["schema"] != TRADE_SAFETY_RECEIPT_SCHEMA:
        raise TradeSafetyError("receipt.schema is unsupported")
    if receipt["canonicalization"] != TRADE_SAFETY_CANONICALIZATION:
        raise TradeSafetyError("receipt.canonicalization is unsupported")
    if (
        not isinstance(receipt["receipt_id"], str)
        or _RECEIPT_ID_RE.fullmatch(receipt["receipt_id"]) is None
    ):
        raise TradeSafetyError("receipt.receipt_id has an invalid shape")
    _sha256(receipt["record_hash"], "receipt.record_hash")
    instant = _instant(evaluated_at, "evaluated_at")
    issued_at = _utc(receipt["evaluated_at"], "receipt.evaluated_at")
    expires_at = _utc(receipt["expires_at"], "receipt.expires_at")
    if expires_at <= issued_at:
        raise TradeSafetyError("receipt.expires_at must follow receipt.evaluated_at")
    if instant < issued_at:
        raise TradeSafetyError("receipt is not yet valid at evaluated_at")
    if instant >= expires_at:
        raise TradeSafetyError("receipt is expired at evaluated_at")

    request = validate_trade_safety_request(receipt["request"])
    policy = validate_trade_safety_policy(receipt["policy"])
    evidence = validate_trade_safety_evidence(receipt["evidence"])
    broker_preview = validate_broker_preview_reference(receipt["broker_preview"])
    _validate_issuer(receipt["issuer"])
    integrity = _validate_integrity(receipt["integrity"])
    decision = _validate_decision(receipt["decision"])
    request_created_at = _utc(request["created_at"], "request.created_at")
    request_expires_at = _utc(request["expires_at"], "request.expires_at")
    if issued_at < request_created_at:
        raise TradeSafetyError("receipt was evaluated before request.created_at")
    if issued_at >= request_expires_at:
        raise TradeSafetyError("receipt was evaluated after request expiry")
    if receipt["authority"] != _AUTHORITY_BOUNDARY:
        raise TradeSafetyError("receipt.authority widens the protocol boundary")
    if receipt["request_hash"] != _hash_object(request):
        raise TradeSafetyError("receipt.request_hash does not match request")
    for product in TRADE_SAFETY_PRODUCTS:
        if evidence[product]["request_hash"] != receipt["request_hash"]:
            raise TradeSafetyError(
                f"evidence.{product}.request_hash does not match request"
            )
    if broker_preview["request_hash"] != receipt["request_hash"]:
        raise TradeSafetyError("broker_preview.request_hash does not match request")
    if broker_preview["account_id"] != request["agent"]["account_id"]:
        raise TradeSafetyError("broker_preview.account_id does not match request")
    if receipt["policy_hash"] != _hash_object(policy):
        raise TradeSafetyError("receipt.policy_hash does not match policy")
    if request["policy_ref"] != {
        "policy_id": policy["policy_id"],
        "version": policy["version"],
    }:
        raise TradeSafetyError("request.policy_ref does not match embedded policy")

    expected_digest = _receipt_digest(receipt)
    if receipt["record_hash"] != expected_digest:
        raise TradeSafetyError("receipt.record_hash does not match receipt content")
    if receipt["receipt_id"] != f"trade_safety_{expected_digest[:24]}":
        raise TradeSafetyError("receipt.receipt_id does not match receipt content")

    authenticated = integrity["profile"] == "hmac-sha256"
    if authenticated:
        if hmac_key is None:
            raise TradeSafetyError("hmac_key is required to authenticate this receipt")
        if not isinstance(hmac_key, bytes) or not hmac_key:
            raise TradeSafetyError("hmac_key must be non-empty bytes")
        expected_signature = hmac.new(
            hmac_key,
            TRADE_SAFETY_HMAC_DOMAIN + expected_digest.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(integrity["signature"], expected_signature):
            raise TradeSafetyError("receipt HMAC signature is invalid")
    elif hmac_key is not None:
        raise TradeSafetyError("hmac_key was supplied for a hash-only receipt")

    expected_decision = _evaluate_decision(
        request=request,
        evidence=evidence,
        broker_preview=broker_preview,
        policy=policy,
        evaluated_at=issued_at,
        authenticated=authenticated,
    )
    if decision != expected_decision:
        raise TradeSafetyError("receipt.decision does not match deterministic policy")
    safe_expiry = _receipt_expiry_ceiling(
        request=request,
        evidence=evidence,
        broker_preview=broker_preview,
        policy=policy,
        issued_at=issued_at,
        ttl_seconds=3600,
        protect_dependencies=(
            decision["outcome"] != TradeSafetyOutcome.UNAVAILABLE.value
        ),
    )
    if expires_at > safe_expiry:
        raise TradeSafetyError(
            "receipt.expires_at exceeds its request, evidence, preview, or TTL boundary"
        )
    if (
        request["mode"] == TradeSafetyMode.LIVE.value
        and decision["outcome"] == TradeSafetyOutcome.PASS.value
        and not authenticated
    ):
        raise TradeSafetyError("a live pass must be authenticated")

    canonical = json.dumps(
        receipt,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return VerifiedTradeSafetyReceipt(
        receipt_json=canonical,
        authenticated=authenticated,
        _seal=_VERIFIED_TRADE_SAFETY_RECEIPT_SEAL,
    )


__all__ = [
    "BROKER_PREVIEW_REFERENCE_SCHEMA",
    "BROKER_PREVIEW_REFERENCE_SCHEMA_URL",
    "TRADE_SAFETY_CANONICALIZATION",
    "TRADE_SAFETY_HMAC_DOMAIN",
    "TRADE_SAFETY_MAX_BYTES",
    "TRADE_SAFETY_POLICY_SCHEMA",
    "TRADE_SAFETY_POLICY_SCHEMA_URL",
    "TRADE_SAFETY_PRODUCTS",
    "TRADE_SAFETY_RECEIPT_SCHEMA",
    "TRADE_SAFETY_RECEIPT_SCHEMA_URL",
    "TRADE_SAFETY_REQUEST_SCHEMA",
    "TRADE_SAFETY_REQUEST_SCHEMA_URL",
    "BrokerPreviewState",
    "TradeEvidenceState",
    "TradeSafetyError",
    "TradeSafetyMode",
    "TradeSafetyOutcome",
    "VerifiedTradeSafetyReceipt",
    "issue_trade_safety_receipt",
    "trade_safety_request_hash",
    "validate_broker_preview_reference",
    "validate_trade_safety_evidence",
    "validate_trade_safety_policy",
    "validate_trade_safety_request",
    "verify_trade_safety_receipt",
]
