"""Private, runtime-neutral paper execution tools for an authenticated host.

This module is a tool dispatcher, not a network server or authentication layer.
The host owns the principal-to-binding mapping, receipt issuer, credentials,
clock and enable/STOP callback. Never instantiate it inside an untrusted agent.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from threading import Lock
from typing import Any

from alpaca.trading.client import TradingClient
from liquilens_evidence import (
    TradeSafetyError,
    TradeSafetyExecutionBinding,
    TradeSafetyOrderAuthorization,
    TradeSafetyOrderBlocked,
    trade_safety_request_hash,
    validate_trade_safety_request,
)

from .adapter import (
    AlpacaPaperAdapterError,
    AlpacaPaperSubmission,
    AlpacaPaperSubmissionUncertain,
    AlpacaPaperTradeSafetyGateway,
    _ClientFactory,
)
from .journal import (
    AlpacaPaperSubmissionJournalError,
    AlpacaPaperSubmissionRecord,
    SQLiteAlpacaPaperSubmissionJournal,
)

_MAX_ARGUMENT_BYTES = 2_097_152
_REQUEST_DESCRIPTION = (
    "The complete, unchanged liquilens.trade-safety-request.v1 object used by "
    "the operator's receipt issuer. Its account, agent, runtime, strategy and "
    "policy must match this authenticated lane. Only paper mode is supported."
)
_OPERATIONS = {
    "paper_execution_capabilities": (
        "Describe the private paper lane; readiness never authorizes an order.",
        (),
        True,
    ),
    "submit_paper_order": (
        "Submit an exact paper order with an operator-authenticated pass receipt. "
        "An uncertain response requires reconciliation, never a replacement order.",
        ("request", "receipt"),
        False,
    ),
    "paper_order_status": (
        "Read the durable submission record for this exact request. "
        "Submission state does not establish a fill or current broker status.",
        ("request",),
        True,
    ),
    "reconcile_paper_order": (
        "Resolve a recorded attempt by broker lookup and update the local journal. "
        "Never submit, cancel, replace or authorize an order.",
        ("request",),
        False,
    ),
}


class PaperAgentToolProtocolError(ValueError):
    """The authenticated MCP host should map this to JSON-RPC -32602."""


def _disabled() -> bool:
    return False


def _enabled(check: Callable[[], bool]) -> bool:
    try:
        return check() is True
    except Exception:
        return False


class _EnabledPaperGateway(AlpacaPaperTradeSafetyGateway):
    def __init__(
        self, *, execution_enabled: Callable[[], bool], **options: Any
    ) -> None:
        self._execution_enabled = execution_enabled
        super().__init__(**options)

    def _submit_authorized(
        self, authorization: TradeSafetyOrderAuthorization
    ) -> AlpacaPaperSubmission:
        # Recheck after blocking account I/O and receipt verification/claim.
        # A stop here retains the claim and cannot be bypassed by retrying.
        if not _enabled(self._execution_enabled):
            raise TradeSafetyOrderBlocked(
                "operator_execution_disabled", "operator paper execution is disabled"
            )
        return super()._submit_authorized(authorization)


def _result(status: str, *, error: bool = False, **fields: Any) -> dict[str, Any]:
    value = {
        "schema": "liquilens.paper-agent-tool-result.v1",
        "mode": "paper",
        "status": status,
        "resubmit_allowed": False,
        "fill_status": "not_observed",
        **fields,
    }
    return {
        "resultType": "complete",
        "content": [{"type": "text", "text": json.dumps(value, allow_nan=False)}],
        "structuredContent": value,
        "isError": error,
    }


def _record_fields(record: AlpacaPaperSubmissionRecord) -> dict[str, Any]:
    # Never forward raw broker responses, exception text, credentials or evidence.
    return {
        "request_hash": record.request_hash,
        "receipt_id": record.receipt_id,
        "client_order_id": record.client_order_id,
        "submission_state": str(record.state),
        "submit_attempts": record.submit_attempts,
        "broker_order_id": record.broker_order_id,
        "reconciliation_resolution": record.reconciliation_resolution,
    }


class AlpacaPaperAgentTools:
    """One operator-bound lane exposed as MCP-shaped tools or Python dispatch.

    Register ``list_tools()`` and ``call_tool(name, arguments)`` in an existing
    authenticated host. Construct this object from operator configuration, never
    tool arguments. The SQLite journal is required and remains caller-owned.
    Use one serialized service owner per account; this is not portfolio sizing,
    an account-wide distributed lock, a strategy, or a live-money adapter.
    """

    def __init__(
        self,
        *,
        binding: TradeSafetyExecutionBinding,
        submission_journal: SQLiteAlpacaPaperSubmissionJournal,
        hmac_key: bytes,
        clock: Callable[[], datetime],
        execution_enabled: Callable[[], bool] = _disabled,
        api_key: str | None = None,
        secret_key: str | None = None,
        oauth_token: str | None = None,
        _client_factory: _ClientFactory = TradingClient,
    ) -> None:
        if not isinstance(submission_journal, SQLiteAlpacaPaperSubmissionJournal):
            raise TypeError("agent tools require a durable SQLite submission journal")
        self._binding = binding
        self._journal = submission_journal
        self._execution_enabled = execution_enabled
        self._lock = Lock()
        self._gateway = _EnabledPaperGateway(
            binding=binding,
            submission_journal=submission_journal,
            hmac_key=hmac_key,
            clock=clock,
            execution_enabled=execution_enabled,
            api_key=api_key,
            secret_key=secret_key,
            oauth_token=oauth_token,
            _client_factory=_client_factory,
        )

    def list_tools(self) -> dict[str, Any]:
        """Return detached tools/list data; annotations are not access control."""
        tools = []
        for name, (description, required, read_only) in _OPERATIONS.items():
            properties = {
                field: {
                    "type": "object",
                    "description": (
                        _REQUEST_DESCRIPTION
                        if field == "request"
                        else "Unchanged operator HMAC-authenticated safety receipt."
                    ),
                }
                for field in required
            }
            tools.append(
                {
                    "name": name,
                    "description": description,
                    "inputSchema": {
                        "type": "object",
                        "properties": properties,
                        "required": list(required),
                        "additionalProperties": False,
                    },
                    "annotations": {
                        "readOnlyHint": read_only,
                        "destructiveHint": name == "submit_paper_order",
                        "idempotentHint": True,
                        "openWorldHint": name
                        in {"submit_paper_order", "reconcile_paper_order"},
                    },
                }
            )
        return {
            "resultType": "complete",
            "cacheScope": "private",
            "ttlMs": 0,
            "tools": tools,
        }

    def _bound_request(self, value: Mapping[str, Any]) -> dict[str, Any]:
        request = validate_trade_safety_request(value)
        if request["mode"] != "paper":
            raise TradeSafetyOrderBlocked("paper_only", "paper mode is required")
        for key in (
            "account_id",
            "tenant_id",
            "operator_id",
            "agent_id",
            "runtime",
            "strategy_id",
        ):
            if request["agent"][key] != getattr(self._binding, key):
                raise TradeSafetyOrderBlocked(
                    "execution_binding_mismatch",
                    "request differs from operator binding",
                )
        if request["policy_ref"] != {
            "policy_id": self._binding.policy_id,
            "version": self._binding.policy_version,
        }:
            raise TradeSafetyOrderBlocked(
                "execution_binding_mismatch", "request differs from operator policy"
            )
        return request

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Dispatch bounded JSON data; return sanitized MCP CallToolResult data.

        The host must reject duplicate JSON keys before decoding the transport,
        authenticate/rate-limit the caller and select their bound instance. No
        tool argument can supply a clock, key, endpoint, policy or enable flag.
        """
        if not isinstance(name, str) or name not in _OPERATIONS:
            raise PaperAgentToolProtocolError("unknown paper execution tool")
        if not isinstance(arguments, dict) or set(arguments) != set(
            _OPERATIONS[name][1]
        ):
            raise PaperAgentToolProtocolError("invalid paper execution tool arguments")
        try:
            encoded = json.dumps(arguments, allow_nan=False).encode("utf-8")
            if len(encoded) > _MAX_ARGUMENT_BYTES:
                raise ValueError("argument limit")
            detached = json.loads(encoded)
            if any(not isinstance(value, dict) for value in detached.values()):
                raise ValueError("object arguments required")
        except (TypeError, ValueError, RecursionError) as error:
            raise PaperAgentToolProtocolError(
                "invalid bounded JSON arguments"
            ) from error
        if not self._lock.acquire(blocking=False):
            return _result("unavailable", error=True, reason_code="operator_lane_busy")
        try:
            return self._dispatch(name, detached)
        except AlpacaPaperSubmissionUncertain as error:
            return _result(
                "uncertain",
                error=True,
                reason_code="broker_lookup_required_no_resubmit",
                request_hash=error.request_hash,
                client_order_id=error.client_order_id,
                next_tool="reconcile_paper_order",
            )
        except TradeSafetyOrderBlocked as error:
            return _result("blocked", error=True, reason_code=error.reason_code)
        except TradeSafetyError:
            return _result(
                "blocked", error=True, reason_code="invalid_request_or_receipt"
            )
        except (AlpacaPaperAdapterError, AlpacaPaperSubmissionJournalError):
            return _result(
                "unavailable",
                error=True,
                reason_code="operator_or_broker_unavailable",
                next_tool="paper_order_status",
            )
        except Exception:
            # An error does not establish whether an attempt reached the broker.
            return _result(
                "unavailable",
                error=True,
                reason_code="operator_lane_unavailable",
                next_tool="paper_order_status",
            )
        finally:
            self._lock.release()

    def _dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "paper_execution_capabilities":
            return _result(
                "capabilities",
                submission_enabled=_enabled(self._execution_enabled),
                receipt_required=True,
                live_execution_supported=False,
                transport_authentication="required_from_host",
                evidence_issuer="operator_owned",
            )
        request = self._bound_request(arguments["request"])
        request_hash = trade_safety_request_hash(request)
        record = self._journal.get(request_hash)
        if name == "paper_order_status":
            return _result(
                "not_recorded" if record is None else "recorded",
                **(
                    {"request_hash": request_hash}
                    if record is None
                    else _record_fields(record)
                ),
            )
        if name == "reconcile_paper_order":
            if record is None:
                return _result("not_recorded", request_hash=request_hash)
            self._gateway.reconcile(request_hash)
        else:
            if record is not None:
                return _result("already_recorded", **_record_fields(record))
            if not _enabled(self._execution_enabled):
                return _result(
                    "blocked", error=True, reason_code="operator_execution_disabled"
                )
            if self._journal.recovery_candidates(limit=1):
                return _result(
                    "blocked", error=True, reason_code="unresolved_paper_submission"
                )
            self._gateway.submit(request, arguments["receipt"])
        record = self._journal.get(request_hash)
        if record is None:
            raise RuntimeError("durable submission record unavailable")
        return _result(str(record.state), **_record_fields(record))
