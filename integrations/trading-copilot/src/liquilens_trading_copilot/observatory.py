"""Read-only three-product diagnostics, without a request, receipt or state store.

The scenario hash belongs to this diagnostic schema. It is deliberately not a
canonical Trade Safety request hash and cannot enter any execution pipeline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from trade_safety_gateway.app import (
    UNDERTOW_URL,
    RawUpstreamResponse,
    _mcp_call,
    _mcp_structured,
    _strict_json_object,
    _undertow_section,
)
from trade_safety_gateway.http_safety import cookie_free_jar
from trade_safety_gateway.upstream_contracts import (
    _UNDERTOW_ROOT_KEYS,
    UNDERTOW_SCHEMA,
    UNDERTOW_SCHEMA_URL,
    _canonical_sha,
    _validate_undertow_authority,
)

from .config import (
    SCOPED_PROFILE,
    PaperCredentials,
    load_secret_file,
    scoped_policy,
    strict_json,
)
from .funding import FundingScopeError, parse_corporate_research, parse_funding_scope
from .market import PAPER_ORIGIN, InputUnavailable, PaperAccountReader
from .scoped import CORPORATE_URL, FUNDING_URL, ScopedUpstreamTransport

SCHEMA = "liquilens.execution-observatory.v1"
_PRODUCTS = ("seiche", "liquilens", "undertow")
_URLS = (FUNDING_URL, CORPORATE_URL, UNDERTOW_URL)
_MAX_AGE = {"seiche": 691200, "liquilens": 691200, "undertow": 300}
_CONTRIBUTIONS = {
    "seiche": (
        "Measures USD funding pressure from aligned SOFR, EFFR and IORB observations."
    ),
    "liquilens": (
        "Shows commercial-paper spread and rollover pressure "
        "with dated corporate coverage gaps."
    ),
    "undertow": (
        "Checks size-specific cross-venue exit costs, depth, "
        "peg conversion and data rights."
    ),
}
_ACTIONS = {
    "current": (
        "Retain this dated diagnostic; evaluate all order-specific controls separately."
    ),
    "stale": (
        "Await or recheck a newer eligible source observation; "
        "retrieval cannot refresh its age."
    ),
    "restricted": (
        "Obtain and record the provider rights approval before admitting this source."
    ),
    "unavailable": (
        "Restore the published source or required coverage, then collect again."
    ),
    "invalid": (
        "Investigate the source contract or binding failure before admitting its facts."
    ),
}
_NOT_CHECKED = [
    "strategy_and_completed_market_bars",
    "specific_order_and_position_ownership",
    "portfolio_and_daily_loss_limits",
    "operator_enablement_and_stop_state",
    "authenticated_exact_order_receipt",
    "durable_replay_and_submission_journals",
    "broker_order_acceptance_and_reconciliation",
    "live_executable_quote_and_broker_preview",
]


def _text(at: datetime) -> str:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("aware_evaluation_clock_required")
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _clock_value(value: Any, now: datetime) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if len(value) == 10:
            at = at.replace(tzinfo=UTC)
        return _text(at) if at <= now else None
    except (TypeError, ValueError):
        return None


def _reported_clocks(product: str, raw: bytes, now: datetime) -> dict[str, str]:
    """Dated producer claims, never a substitute for admission by the parser."""
    result: dict[str, str] = {}
    try:
        payload = (
            _mcp_structured(raw, "trade-safety-undertow-v1")
            if product == "undertow"
            else strict_json(raw)
        )
        if product == "seiche":
            values = {
                row["id"]: row.get("asof")
                for section in payload["sections"]
                if section.get("id") == "policy_corridor"
                for row in section["metrics"]
                if row.get("id") in {"policy.sofr", "policy.effr", "policy.iorb"}
            }
        elif product == "liquilens":
            legs = payload["channels"]["cp_market"]["legs"]
            values = {
                f"cp_{key}": legs[key].get("as_of") for key in ("spread", "rollover")
            }
        else:
            values = {"oldest_observation": payload["clocks"]["oldest_observation_at"]}
        for key, value in values.items():
            normalized = _clock_value(value, now)
            if normalized is not None:
                result[key] = normalized
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
        pass
    return result


def _undertow_tool_error(raw: bytes) -> str | None:
    """Recognize a bounded MCP error response without admitting source evidence.

    Hosted quota errors contain no structuredContent or request digest. Their
    exact JSON-RPC identity and error shape can explain unavailability, but can
    never establish rights, source facts or a passing scenario binding.
    """
    envelope = _strict_json_object(raw, "Undertow diagnostic response")
    if (
        set(envelope) != {"jsonrpc", "id", "result"}
        or envelope["jsonrpc"] != "2.0"
        or envelope["id"] != "trade-safety-undertow-v1"
    ):
        return None
    result = envelope["result"]
    if (
        not isinstance(result, dict)
        or set(result) != {"content", "isError"}
        or result["isError"] is not True
    ):
        return None
    content = result["content"]
    if not isinstance(content, list) or not 1 <= len(content) <= 4:
        return None
    for item in content:
        if (
            not isinstance(item, dict)
            or set(item) != {"type", "text"}
            or item["type"] != "text"
            or not isinstance(item["text"], str)
            or not 0 < len(item["text"]) <= 2048
        ):
            return None
    if len(content) == 1:
        quota = re.match(
            r"^ERROR: daily MCP quota reached \(([0-9]{1,6})/([0-9]{1,6}) "
            r"tool calls today, resets at UTC midnight\)\.(?:\s|$)",
            content[0]["text"],
        )
        if quota is not None and int(quota[1]) >= int(quota[2]) > 0:
            return "source_quota_exhausted"
    # Raw upstream prose and any links or claimed rights stay out of the report.
    return "source_unavailable"


def _unavailable_undertow_reason(raw: bytes, expected: dict[str, Any]) -> str | None:
    """Validate only the unavailable envelope; admit none of its market facts.

    This digest is an integrity check, not authentication or a rights grant.
    A rights denial requires the full native envelope; tool errors explain only
    source unavailability and cannot establish any rights state.
    """
    tool_error = _undertow_tool_error(raw)
    if tool_error is not None:
        return tool_error
    payload = _mcp_structured(raw, "trade-safety-undertow-v1")
    if (
        set(payload) != _UNDERTOW_ROOT_KEYS
        or payload["schema"] != UNDERTOW_SCHEMA
        or payload["schema_url"] != UNDERTOW_SCHEMA_URL
        or payload["context_sha256"] != _canonical_sha(payload, "context_sha256")
        or payload["status"] != "unavailable"
        or payload["evidence_class"] != "unavailable"
        or payload["measurement"] is not None
        or not isinstance(payload["source"], dict)
        or payload["source"].get("url") != UNDERTOW_URL
        or payload["request_hash"] != expected["request_hash"]
        or payload["request"] != expected
    ):
        return None
    _validate_undertow_authority(payload["authority"], "paper")
    if payload["reason"] == "rights_manifest_not_approved":
        return "rights_manifest_not_approved"
    return "source_reported_unavailable"


def _failure(
    error: Exception, product: str, raw: Any, now: datetime
) -> tuple[str, str]:
    # Never copy arbitrary exception text or upstream response prose into reports.
    code = str(error)
    if isinstance(error, FundingScopeError):
        if code.endswith(": future observation"):
            return "invalid", "source_clock_in_future"
        if code.endswith(": stale observation"):
            return "stale", "source_observation_stale"
        # Some pure-parser errors group unavailable/stale/withheld causes.
        # Inspect only the precise native flags to explain the denial; this
        # diagnostic classification never admits facts from the failed parser.
        try:
            payload = strict_json(raw.body)
            if product == "liquilens":
                cp = payload["channels"]["cp_market"]
                candidates = [cp, *cp["legs"].values()]
                if any(row.get("withheld") is True for row in candidates):
                    return "restricted", "source_observation_withheld"
                if any(row.get("stale") is True for row in candidates):
                    return "stale", "source_observation_stale"
                if payload.get("available") is False or cp.get("available") is False:
                    return "unavailable", "source_reported_unavailable"
            elif product == "seiche":
                cards = [
                    card
                    for section in payload["sections"]
                    if section.get("id") == "policy_corridor"
                    for card in section["metrics"]
                    if card.get("id")
                    in {
                        "policy.sofr",
                        "policy.effr",
                        "policy.iorb",
                        "policy.sofr_minus_iorb",
                        "policy.effr_minus_iorb",
                    }
                ]
                if any(card.get("freshness") == "stale" for card in cards):
                    return "stale", "source_observation_stale"
                if any(card.get("status") == "unavailable" for card in cards):
                    return "unavailable", "source_reported_unavailable"
        except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
            pass
    if code == "native_evidence_stale":
        return "stale", "source_observation_stale"
    if code == "undertow_clock_mismatch_or_stale":
        clocks = _reported_clocks(product, raw.body, now)
        oldest = clocks.get("oldest_observation")
        if (
            oldest
            and (
                now - datetime.fromisoformat(oldest.replace("Z", "+00:00"))
            ).total_seconds()
            > _MAX_AGE[product]
        ):
            return "stale", "source_observation_stale"
    if isinstance(error, (httpx.HTTPError, TimeoutError)) or code in {
        "source_unreachable",
        "scoped_source_http_invalid",
    }:
        return "unavailable", "source_unreachable_or_http_invalid"
    return "invalid", "source_contract_invalid"


def _facts(product: str, facts: dict[str, Any]) -> dict[str, Any]:
    keys = {
        "seiche": (
            "regime",
            "pressure_bp",
            "sofr_minus_iorb_bp",
            "effr_minus_iorb_bp",
            "regime_basis",
        ),
        "liquilens": (
            "cp_market_state",
            "cp_spread_bp",
            "cp_rollover_chg_8w_pct",
            "cp_spread_as_of",
            "cp_rollover_as_of",
            "coverage",
            "available_channels",
            "total_channels",
        ),
        "undertow": (
            "requested_size_usd",
            "published_rung_used_usd",
            "worst_sell_cost_bps",
            "venue_spread_bps",
        ),
    }
    selected = {key: facts[key] for key in keys[product]}
    if product == "undertow":
        selected["priced_venue_count"] = len(facts["coverage"]["priced_venues"])
        selected["peg_state"] = facts["peg"]["state"]
        selected["rights_status"] = facts["rights"]["status"]
    return selected


def _row(
    product: str, raw: Any, now: datetime, expected: dict[str, Any]
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "product": product,
        "state": "unavailable",
        "admitted": False,
        "reason_codes": [],
        "policy_state": "not_checked",
        "policy_reason_codes": [],
        "source_url": dict(zip(_PRODUCTS, _URLS, strict=True))[product],
        "source_sha256": None,
        "as_of": None,
        "retrieved_at": _text(now),
        "expires_at": None,
        "native_expires_at": None,
        "max_age_seconds": _MAX_AGE[product],
        "observation_age_seconds": None,
        "reported_observation_clocks": {},
        "reported_clocks_admitted": False,
        "facts": {},
        "contribution": _CONTRIBUTIONS[product],
    }
    try:
        if isinstance(raw, Exception):
            raise raw
        if (
            not isinstance(raw, RawUpstreamResponse)
            or not isinstance(raw.body, bytes)
            or not 0 < len(raw.body) <= 1048576
        ):
            raise ValueError("source_unreachable")
        row["source_sha256"] = hashlib.sha256(raw.body).hexdigest()
        row["reported_observation_clocks"] = _reported_clocks(product, raw.body, now)
        kwargs = {
            "request_hash": expected["request_hash"],
            "retrieved_at": now,
            "request_expires_at": now + timedelta(seconds=60),
            "max_age_seconds": _MAX_AGE[product],
        }
        if product == "undertow":
            reason = _unavailable_undertow_reason(raw.body, expected)
            if reason is not None:
                row["state"] = (
                    "restricted"
                    if reason == "rights_manifest_not_approved"
                    else "unavailable"
                )
                row["reason_codes"] = [reason]
                row["next_action"] = (
                    "Wait for the UTC quota reset or an approved higher allowance; "
                    "schedule checks within the source allowance."
                    if reason == "source_quota_exhausted"
                    else _ACTIONS[row["state"]]
                )
                return row
            section = _undertow_section(raw=raw, expected_request=expected, **kwargs)
            row["native_expires_at"] = section["facts"]["clocks"]["expires_at"]
        else:
            parser = (
                parse_funding_scope if product == "seiche" else parse_corporate_research
            )
            section = parser(raw=raw.body, **kwargs)
        row.update(
            {
                "state": "current",
                "admitted": True,
                "as_of": section["as_of"],
                "expires_at": _text(
                    min(
                        datetime.fromisoformat(
                            section["expires_at"].replace("Z", "+00:00")
                        ),
                        datetime.fromisoformat(section["as_of"].replace("Z", "+00:00"))
                        + timedelta(seconds=_MAX_AGE[product]),
                    )
                ),
                "facts": _facts(product, section["facts"]),
                "reported_clocks_admitted": True,
                "policy_state": "pass",
                "observation_age_seconds": (
                    now
                    - datetime.fromisoformat(section["as_of"].replace("Z", "+00:00"))
                ).total_seconds(),
            }
        )
        policy = scoped_policy()
        if product == "seiche" and row["facts"]["regime"] in policy["hold_regimes"]:
            row["policy_reason_codes"].append("funding_regime_held_by_policy")
        if product == "undertow":
            for fact, limit in (
                ("worst_sell_cost_bps", "max_exit_cost_bps"),
                ("venue_spread_bps", "max_venue_spread_bps"),
            ):
                if row["facts"][fact] > policy[limit]:
                    row["policy_reason_codes"].append(limit + "_exceeded")
        if row["policy_reason_codes"]:
            row["policy_state"] = "hold"
    except Exception as error:
        row["state"], reason = _failure(error, product, raw, now)
        row["reason_codes"] = [reason]
    row["next_action"] = (
        "Reconcile producer and observer UTC clocks; keep the source unadmitted "
        "until the original clock ordering is valid."
        if "source_clock_in_future" in row["reason_codes"]
        else (
            "Keep the source-policy hold; "
            "a valid observation does not clear the risk limit."
        )
        if row["policy_state"] == "hold"
        else _ACTIONS[row["state"]]
    )
    return row


def _complete_row(
    row: dict[str, Any], *, completed: datetime, started: datetime
) -> None:
    """Recheck capture-time admission at the one final report clock.

    Network completion is not report completion. Preserve capture provenance,
    but remove current admission if another source or account read consumed the
    remaining source-age, native-expiry or local diagnostic lifetime.
    """
    row["checked_at"] = _text(completed)
    if not row["admitted"]:
        return
    as_of = datetime.fromisoformat(row["as_of"].replace("Z", "+00:00"))
    retrieved = datetime.fromisoformat(row["retrieved_at"].replace("Z", "+00:00"))
    expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
    age = (completed - as_of).total_seconds()
    row["observation_age_seconds"] = age
    state, reason = None, None
    if completed < max(started, retrieved):
        state, reason = "invalid", "diagnostic_clock_regressed"
    elif age >= row["max_age_seconds"]:
        state, reason = "stale", "source_observation_stale_at_completion"
    elif completed >= expires:
        state, reason = "stale", "diagnostic_context_expired_at_completion"
    if state is not None:
        row.update(
            {
                "state": state,
                "admitted": False,
                "reason_codes": [reason],
                "policy_state": "not_checked",
                "policy_reason_codes": [],
                "reported_clocks_admitted": False,
                "facts": {},
                "next_action": _ACTIONS[state],
            }
        )


class PaperReadTransport(httpx.AsyncBaseTransport):
    """Only the three GETs required by PaperAccountReader.snapshot are possible."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.inner = (
            transport
            if transport is not None
            else httpx.AsyncHTTPTransport(trust_env=False)
        )
        self.position_count: int | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        allowed = {
            PAPER_ORIGIN + "/v2/account",
            PAPER_ORIGIN + "/v2/positions",
            PAPER_ORIGIN + "/v2/orders?status=open&limit=500",
        }
        if (
            request.method != "GET"
            or str(request.url) not in allowed
            or request.content
        ):
            raise InputUnavailable("observer_paper_route_not_allowed")
        async with asyncio.timeout(15):
            response = await self.inner.handle_async_request(request)
            try:
                if (
                    response.status_code != 200
                    or response.headers.get("content-encoding", "identity")
                    != "identity"
                ):
                    raise InputUnavailable("observer_paper_http_invalid")
                body = bytearray()
                if response.is_stream_consumed:
                    body.extend(response.content)
                else:
                    async for chunk in response.aiter_raw():
                        body.extend(chunk)
                        if len(body) > 1048576:
                            raise InputUnavailable("observer_paper_response_too_large")
                if len(body) > 1048576:
                    raise InputUnavailable("observer_paper_response_too_large")
                if request.url.path in {"/v2/orders", "/v2/positions"}:
                    rows = strict_json(bytes(body))
                    if not isinstance(rows, list) or len(rows) >= 500:
                        raise InputUnavailable("observer_paper_list_incomplete")
                    if request.url.path == "/v2/positions":
                        self.position_count = len(rows)
                return httpx.Response(
                    200,
                    content=bytes(body),
                    headers={"Content-Type": "application/json"},
                )
            finally:
                await response.aclose()

    async def aclose(self) -> None:
        await self.inner.aclose()


def _account_id(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            raise ValueError("observer_configuration_invalid")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            config = strict_json(handle.read(65537))
    finally:
        os.close(fd)
    if not isinstance(config, dict) or config.get("mode") != "paper":
        raise ValueError("observer_configuration_invalid")
    account_id = config.get("account_id")
    if (
        not isinstance(account_id, str)
        or not 0 < len(account_id) <= 128
        or account_id != account_id.strip()
    ):
        raise ValueError("observer_account_binding_missing")
    return account_id


async def _check_account(
    config_path: Path | None,
    env_path: Path | None,
    transport: httpx.AsyncBaseTransport | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "state": "unavailable",
        "binding_verified": False,
        "broker_calls_performed": False,
        "reason_codes": [],
        "status": None,
        "currency": None,
        "trading_blocked": None,
        "account_blocked": None,
        "trade_suspended_by_user": None,
        "open_order_count": None,
        "position_count": None,
    }
    try:
        if config_path is None or env_path is None:
            raise ValueError("observer_account_files_required")
        account_id = _account_id(config_path)
        # The observer does not use or require receipt-signing authority.
        env = load_secret_file(env_path)
        if not env.get("ALPACA_PAPER_API_KEY") or not env.get(
            "ALPACA_PAPER_SECRET_KEY"
        ):
            raise ValueError("observer_paper_credentials_missing")
        credentials = PaperCredentials(
            env["ALPACA_PAPER_API_KEY"], env["ALPACA_PAPER_SECRET_KEY"], b""
        )
        read_transport = PaperReadTransport(transport)
        async with httpx.AsyncClient(
            transport=read_transport,
            trust_env=False,
            follow_redirects=False,
            cookies=cookie_free_jar(),
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        ) as client:
            result["broker_calls_performed"] = True
            snapshot = await PaperAccountReader(
                client, credentials, account_id
            ).snapshot()
        result.update(
            {
                "state": "verified",
                "binding_verified": True,
                "status": "ACTIVE",
                "currency": "USD",
                "trading_blocked": False,
                "account_blocked": False,
                "trade_suspended_by_user": False,
                "open_order_count": snapshot.open_orders,
                "position_count": read_transport.position_count,
            }
        )
    except Exception:
        result["reason_codes"] = ["paper_account_check_unavailable_or_invalid"]
    return result


async def collect_observatory(
    *,
    transport: ScopedUpstreamTransport | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    check_account: bool = False,
    config_path: Path | None = None,
    env_path: Path | None = None,
    account_transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Collect independent diagnostics; never create a receipt or touch state."""
    if not check_account and any(
        value is not None for value in (config_path, env_path, account_transport)
    ):
        raise ValueError("observer_account_files_require_explicit_check")
    evaluated = clock()
    scenario = {
        "hypothetical": True,
        "mode": "paper",
        "instrument": "BTC/USD",
        "side": "sell",
        "venue": None,
        "requested_size_usd": 1000.0,
    }
    scenario_hash = _hash(
        {"schema": SCHEMA, "scenario": scenario, "evaluated_at": _text(evaluated)}
    )
    expected = {key: value for key, value in scenario.items() if key != "hypothetical"}
    expected["request_hash"] = scenario_hash
    upstream = transport if transport is not None else ScopedUpstreamTransport()

    async def capture(method: str, url: str, **kwargs: Any) -> tuple[Any, datetime]:
        try:
            async with asyncio.timeout(10):
                response: Any = await upstream.request(method, url, **kwargs)
        except Exception as error:
            response = error
        return response, clock()

    try:
        captures = await asyncio.gather(
            capture("GET", FUNDING_URL),
            capture("GET", CORPORATE_URL),
            capture(
                "POST",
                UNDERTOW_URL,
                json_body=_mcp_call(
                    "trade_safety_exit_context", expected, "trade-safety-undertow-v1"
                ),
            ),
        )
    finally:
        if transport is None:
            await upstream.aclose()
    sources = {
        product: _row(product, raw, now, expected)
        for product, (raw, now) in zip(_PRODUCTS, captures, strict=True)
    }
    account = (
        await _check_account(config_path, env_path, account_transport)
        if check_account
        else {
            "state": "not_checked",
            "binding_verified": False,
            "broker_calls_performed": False,
            "reason_codes": ["account_check_not_requested"],
        }
    )
    completed = clock()
    for row in sources.values():
        _complete_row(row, completed=completed, started=evaluated)
    admitted = all(row["admitted"] for row in sources.values())
    policy_passed = admitted and all(
        row["policy_state"] == "pass" for row in sources.values()
    )
    return {
        "schema": SCHEMA,
        "evaluated_at": _text(evaluated),
        "completed_at": _text(completed),
        "profile": SCOPED_PROFILE,
        "scenario": {**scenario, "scenario_hash": scenario_hash},
        "status": "source_checks_passed"
        if policy_passed
        else "source_policy_hold"
        if admitted
        else "source_checks_blocked",
        "source_checks_passed": admitted,
        "source_policy_checks_passed": policy_passed,
        "sources": sources,
        "paper_account": account,
        "ready_for_order": False,
        "receipt_issued": False,
        "order_authorized": False,
        "order_submitted": False,
        "state_modified": False,
        "execution_requirements_not_checked": _NOT_CHECKED.copy(),
        "limitations": [
            "diagnostic_scenario_is_not_a_canonical_trade_request",
            "source_admission_is_not_execution_readiness",
            "source_hashes_are_integrity_metadata_not_authentication",
            "account_check_does_not_check_order_or_portfolio_limits",
            "hypothetical_exit_is_not_a_recommendation_or_an_executable_quote",
        ],
    }


def format_observatory_markdown(report: dict[str, Any]) -> str:
    """Human-readable summary plus the complete identical JSON contract."""
    lines = [
        "# Execution observatory",
        "",
        f"Evaluated: {report['evaluated_at']}",
        "",
        "Hypothetical paper BTC/USD sale: $1,000. No order or receipt is issued.",
        "",
        "| Product | Source | Policy | Contribution |",
        "| --- | --- | --- | --- |",
    ]
    for product in _PRODUCTS:
        row = report["sources"][product]
        lines.append(
            f"| {product} | {row['state']} | {row['policy_state']} | "
            f"{row['contribution']} |"
        )
    lines.extend(
        [
            "",
            f"Paper account: {report['paper_account']['state']}. "
            "Ready for order: false.",
            "",
            "Source admission alone never clears the remaining execution controls.",
            "",
            "```json",
            json.dumps(report, sort_keys=True, indent=2, allow_nan=False),
            "```",
            "",
        ]
    )
    return "\n".join(lines)
