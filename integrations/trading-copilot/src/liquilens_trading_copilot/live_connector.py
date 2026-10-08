"""Customer-owned live Alpaca connector, separate from every paper route.

Limit orders only, long-only, one durable account lane. The caller must provide
an independently qualified live receipt issuer and explicit activation. Public
research observations and paper receipts cannot satisfy that contract.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx
from liquilens_evidence import (
    TradeSafetyExecutionBinding,
    trade_safety_request_hash,
    validate_trade_safety_request,
    verify_trade_safety_receipt,
)
from liquilens_evidence.order_guard import _assert_execution_binding

from .config import strict_json
from .state import operator_lock

LIVE_URL = "https://api.alpaca.markets"
TERMINAL = frozenset({"filled", "canceled", "expired", "rejected"})
ORDER_STATES = TERMINAL | {
    "new",
    "accepted",
    "pending_new",
    "partially_filled",
    "pending_cancel",
    "pending_replace",
    "done_for_day",
    "stopped",
    "suspended",
    "calculated",
}


class LiveExecutionBlocked(ValueError):
    """Stable reason code; no credentials or upstream bodies."""


def number(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise LiveExecutionBlocked("invalid_numeric_value")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise LiveExecutionBlocked("invalid_numeric_value") from exc
    if not result.is_finite():
        raise LiveExecutionBlocked("invalid_numeric_value")
    return result


def encoded(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class LiveLimits:
    """Operator policy for a dedicated, non-shared USD account lane."""

    symbols: tuple[str, ...]
    max_order_usd: float
    max_gross_exposure_usd: float
    max_daily_loss_usd: float
    max_daily_attempts: int = 2

    def validate(self) -> None:
        if (
            not isinstance(self.symbols, tuple)
            or not self.symbols
            or len(set(self.symbols)) != len(self.symbols)
            or any(not isinstance(s, str) or not s or len(s) > 24 for s in self.symbols)
            or type(self.max_daily_attempts) is not int
            or not 1 <= self.max_daily_attempts <= 100
        ):
            raise LiveExecutionBlocked("invalid_live_limits")
        if any(
            number(v) <= 0
            for v in (
                self.max_order_usd,
                self.max_gross_exposure_usd,
                self.max_daily_loss_usd,
            )
        ):
            raise LiveExecutionBlocked("invalid_live_limits")


class AlpacaLiveTransport:
    """Pinned live origin; no redirect, proxy, SDK retry or broker URL input."""

    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key or not secret_key:
            raise LiveExecutionBlocked("live_credentials_required")
        self.client = httpx.Client(
            base_url=LIVE_URL,
            timeout=10,
            follow_redirects=False,
            trust_env=False,
            headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key},
            transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def call(
        self, method: str, path: str, *, payload: Any = None, params: Any = None
    ) -> Any:
        # Only fixed routes selected by this connector reach the credential lane.
        if path not in {
            "/v2/account",
            "/v2/positions",
            "/v2/orders",
            "/v2/orders:by_client_order_id",
        } and not (
            method == "DELETE"
            and path.startswith("/v2/orders/")
            and len(path) == len("/v2/orders/") + 36
            and all(c in "0123456789abcdef-" for c in path[len("/v2/orders/") :])
        ):
            raise LiveExecutionBlocked("unsupported_broker_route")
        try:
            with self.client.stream(
                method, path, json=payload, params=params
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise LiveExecutionBlocked("broker_response_unavailable")
                if method == "DELETE" and response.status_code == 204:
                    return {"cancel_requested": True}
                if (
                    response.headers.get("content-type", "").split(";", 1)[0]
                    != "application/json"
                ):
                    raise LiveExecutionBlocked("broker_response_unavailable")
                raw = bytearray()
                for chunk in response.iter_bytes(16384):
                    raw.extend(chunk)
                    if len(raw) > 524288:
                        raise LiveExecutionBlocked("broker_response_unavailable")
                result = strict_json(bytes(raw))
                encoded(result)
                return result
        except Exception:
            raise LiveExecutionBlocked("broker_response_unavailable") from None


class LiveAccountConnector:
    """Embed behind authenticated customer tools, never on a public research MCP.

    All operations acquire the same account lock. A persisted attempt, including
    an unknown outcome, is never replayed. A 404 cannot clear uncertainty.
    """

    def __init__(
        self,
        *,
        state_dir: Path,
        binding: TradeSafetyExecutionBinding,
        hmac_key: bytes,
        limits: LiveLimits,
        broker: AlpacaLiveTransport,
        activated: Callable[[], bool],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        limits.validate()
        if (
            not isinstance(hmac_key, bytes)
            or len(hmac_key) < 32
            or not binding.hmac_key_id
        ):
            raise LiveExecutionBlocked("authenticated_live_issuer_required")
        self.state_dir, self.binding, self.hmac_key = state_dir, binding, hmac_key
        self.limits, self.broker, self.activated, self.clock = (
            limits,
            broker,
            activated,
            clock,
        )

    def _now(self) -> datetime:
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise LiveExecutionBlocked("invalid_operator_clock")
        return now.astimezone(UTC)

    def _db(self) -> sqlite3.Connection:
        path = self.state_dir / "live-orders.sqlite3"
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        db = sqlite3.connect(path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        db.executescript("""
          CREATE TABLE IF NOT EXISTS identity (
            id INTEGER PRIMARY KEY, binding TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS orders (
            request_hash TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
            receipt_id TEXT NOT NULL UNIQUE, day TEXT NOT NULL,
            attempted_at TEXT NOT NULL,
            state TEXT NOT NULL, request TEXT NOT NULL, observation TEXT,
            cancel_requested INTEGER NOT NULL DEFAULT 0);
        """)
        identity = encoded(
            {"binding": asdict(self.binding), "limits": asdict(self.limits)}
        )
        previous = db.execute("SELECT binding FROM identity WHERE id=1").fetchone()
        if previous and previous[0] != identity:
            db.close()
            raise LiveExecutionBlocked("live_lane_identity_changed")
        db.execute("INSERT OR IGNORE INTO identity VALUES (1,?)", (identity,))
        db.commit()
        return db

    def _enabled(self) -> None:
        stop = self.state_dir / "STOP"
        try:
            allowed = self.activated() is True
        except Exception:
            allowed = False
        if not allowed or stop.exists() or stop.is_symlink():
            raise LiveExecutionBlocked("live_execution_disabled")

    def _validated(self, request: dict, receipt: dict) -> tuple[dict, str]:
        try:
            request = validate_trade_safety_request(request)
            if request["mode"] != "live" or request["extensions"]:
                raise LiveExecutionBlocked("live_request_required")
            verified = verify_trade_safety_receipt(
                receipt, evaluated_at=self._now(), hmac_key=self.hmac_key
            )
            checked = verified.receipt
            if (
                not verified.authenticated
                or checked["request"] != request
                or checked["decision"]["outcome"] != "pass"
                or checked["decision"]["enforced"] is not True
                or checked["policy"]["live_requires_executable_quote"] is not True
                or checked["policy"]["live_requires_broker_preview"] is not True
                or checked["broker_preview"]["state"] != "verified"
            ):
                raise LiveExecutionBlocked("live_receipt_not_eligible")
            _assert_execution_binding(request, checked, self.binding)
            order = request["order"]
            instrument = order["instrument"]
            if (
                instrument["symbol"] not in self.limits.symbols
                or instrument["asset_class"] not in {"equity", "etf", "crypto"}
                or instrument["identifiers"]
                or order["venue"] is not None
                or order["notional"]["currency"] != "USD"
                or order["order_type"] != "limit"
                or order["stop_price"] is not None
                or order["quantity"] is None
                or order["time_in_force"]
                not in ({"IOC"} if instrument["asset_class"] == "crypto" else {"DAY"})
            ):
                raise LiveExecutionBlocked("unsupported_live_order")
            amount = number(order["quantity"]) * number(order["limit_price"])
            if amount != number(order["notional"]["amount"]) or amount > number(
                self.limits.max_order_usd
            ):
                raise LiveExecutionBlocked("live_order_limit_exceeded")
            return request, trade_safety_request_hash(request)
        except LiveExecutionBlocked:
            raise
        except Exception:
            raise LiveExecutionBlocked("live_receipt_rejected") from None

    def _account(self, request: dict | None = None) -> None:
        account = self.broker.call("GET", "/v2/account")
        if (
            not isinstance(account, dict)
            or account.get("id") != self.binding.account_id
            or account.get("currency") != "USD"
        ):
            raise LiveExecutionBlocked("live_account_not_eligible")
        if request is None:
            return
        if account.get("status") != "ACTIVE" or any(
            account.get(key) is not False
            for key in (
                "trading_blocked",
                "account_blocked",
                "trade_suspended_by_user",
            )
        ):
            raise LiveExecutionBlocked("live_account_not_eligible")
        cash, equity, prior = (
            number(account.get(key)) for key in ("cash", "equity", "last_equity")
        )
        if (
            cash < 0
            or equity <= 0
            or prior <= 0
            or prior - equity >= number(self.limits.max_daily_loss_usd)
        ):
            raise LiveExecutionBlocked("account_loss_or_cash_limit")
        orders = self.broker.call(
            "GET", "/v2/orders", params={"status": "open", "limit": 1}
        )
        if orders != []:
            raise LiveExecutionBlocked("open_orders_pending")
        positions = self.broker.call("GET", "/v2/positions")
        if not isinstance(positions, list):
            raise LiveExecutionBlocked("invalid_account_positions")
        gross, held = Decimal(0), Decimal(0)
        symbol = request["order"]["instrument"]["symbol"].replace("/", "")
        seen = set()
        for position in positions:
            if not isinstance(position, dict) or not isinstance(
                position.get("symbol"), str
            ):
                raise LiveExecutionBlocked("invalid_account_positions")
            position_symbol = position["symbol"].replace("/", "")
            if position_symbol in seen:
                raise LiveExecutionBlocked("duplicate_account_position")
            seen.add(position_symbol)
            quantity, value = (
                number(position.get("qty")),
                number(position.get("market_value")),
            )
            if quantity < 0 or value < 0:
                raise LiveExecutionBlocked("short_or_margin_position_unsupported")
            gross += value
            if position_symbol == symbol:
                held = quantity
        order = request["order"]
        amount = number(order["notional"]["amount"])
        if order["side"] == "buy" and (
            amount > cash or gross + amount > number(self.limits.max_gross_exposure_usd)
        ):
            raise LiveExecutionBlocked("account_exposure_or_cash_limit")
        if order["side"] == "sell" and number(order["quantity"]) > held:
            raise LiveExecutionBlocked("short_sale_not_permitted")

    @staticmethod
    def _client_id(digest: str) -> str:
        return "ll-live-" + digest[:40]

    def _observation(
        self, raw: Any, request: dict, digest: str, previous: dict | None
    ) -> dict:
        import uuid

        order = request["order"]
        try:
            broker_id = str(uuid.UUID(raw["id"]))
            filled = number(raw["filled_qty"])
            if (
                raw["client_order_id"] != self._client_id(digest)
                or raw["symbol"].replace("/", "")
                != order["instrument"]["symbol"].replace("/", "")
                or raw["side"] != order["side"]
                or raw["type"] != "limit"
                or raw["time_in_force"] != order["time_in_force"].lower()
                or number(raw["qty"]) != number(order["quantity"])
                or number(raw["limit_price"]) != number(order["limit_price"])
                or raw["status"] not in ORDER_STATES
                or not 0 <= filled <= number(order["quantity"])
            ):
                raise ValueError
            price = (
                number(raw["filled_avg_price"])
                if raw.get("filled_avg_price") is not None
                else None
            )
            if filled > 0 and (price is None or price <= 0):
                raise ValueError
            if raw["status"] == "filled" and filled != number(order["quantity"]):
                raise ValueError
            if previous and (
                broker_id != previous["broker_order_id"]
                or filled < number(previous["filled_qty"])
                or (
                    previous["terminal"]
                    and (
                        raw["status"] != previous["status"]
                        or filled != number(previous["filled_qty"])
                    )
                )
            ):
                raise ValueError
        except Exception:
            raise LiveExecutionBlocked(
                "broker_order_identity_or_state_invalid"
            ) from None
        return {
            "broker_order_id": broker_id,
            "client_order_id": self._client_id(digest),
            "status": raw["status"],
            "terminal": raw["status"] in TERMINAL,
            "filled_qty": str(filled),
            "filled_avg_price": str(price) if price is not None else None,
            "broker_observed_at": self._now().isoformat(),
            "observation_scope": "last_observed",
        }

    def _record(self, row: sqlite3.Row) -> dict:
        return {
            "mode": "live",
            "request_hash": row["request_hash"],
            "state": row["state"],
            "client_order_id": self._client_id(row["request_hash"]),
            "observation": strict_json(row["observation"].encode())
            if row["observation"]
            else None,
            "cancel_requested": bool(row["cancel_requested"]),
            "resubmit_allowed": False,
        }

    def preview(self, request: dict, receipt: dict) -> dict:
        """Local enforcement preflight, NOT a broker-issued preview or order."""
        with operator_lock(self.state_dir):
            request, digest = self._validated(request, receipt)
            self._account(request)
            return {
                "mode": "live",
                "request_hash": digest,
                "preflight": "passed",
                "order_submitted": False,
                "activation_required": True,
                "broker_preview_issued": False,
            }

    def submit(self, request: dict, receipt: dict) -> dict:
        # De-duplicate by exact request BEFORE considering a fresh/expired receipt.
        request = validate_trade_safety_request(request)
        digest = trade_safety_request_hash(request)
        with operator_lock(self.state_dir):
            db = self._db()
            try:
                existing = db.execute(
                    "SELECT * FROM orders WHERE request_hash=?", (digest,)
                ).fetchone()
                if existing:
                    return self._record(existing)
                self._enabled()
                request, digest = self._validated(request, receipt)
                if db.execute(
                    "SELECT 1 FROM orders WHERE state != 'terminal' LIMIT 1"
                ).fetchone():
                    raise LiveExecutionBlocked("unresolved_order_blocks_submission")
                now = self._now()
                last = db.execute("SELECT MAX(attempted_at) FROM orders").fetchone()[0]
                if last and now.isoformat() < last:
                    raise LiveExecutionBlocked("operator_clock_regressed")
                day = now.date().isoformat()
                if (
                    db.execute(
                        "SELECT COUNT(*) FROM orders WHERE day=?", (day,)
                    ).fetchone()[0]
                    >= self.limits.max_daily_attempts
                ):
                    raise LiveExecutionBlocked("daily_attempt_limit")
                if db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] >= 10000:
                    raise LiveExecutionBlocked("live_journal_capacity")
                self._account(request)
                self._validated(
                    request, receipt
                )  # Account I/O cannot extend receipt validity.
                self._enabled()
                try:
                    with db:
                        db.execute(
                            "INSERT INTO orders VALUES "
                            "(?,?,?,?,?,'uncertain',?,NULL,0)",
                            (
                                digest,
                                request["request_id"],
                                receipt["receipt_id"],
                                day,
                                now.isoformat(),
                                encoded(request),
                            ),
                        )
                except sqlite3.IntegrityError:
                    raise LiveExecutionBlocked(
                        "intent_or_receipt_already_used"
                    ) from None
                order = request["order"]
                payload = {
                    "symbol": order["instrument"]["symbol"],
                    "side": order["side"],
                    "type": "limit",
                    "qty": str(number(order["quantity"])),
                    "limit_price": str(number(order["limit_price"])),
                    "time_in_force": order["time_in_force"].lower(),
                    "client_order_id": self._client_id(digest),
                    "extended_hours": False,
                }
                try:
                    # A late STOP leaves a conservative reserved intent.
                    self._enabled()
                    self._validated(request, receipt)
                    raw = self.broker.call("POST", "/v2/orders", payload=payload)
                    observation = self._observation(raw, request, digest, None)
                    with db:
                        db.execute(
                            "UPDATE orders SET state=?, observation=? "
                            "WHERE request_hash=?",
                            (
                                "terminal" if observation["terminal"] else "pending",
                                encoded(observation),
                                digest,
                            ),
                        )
                except Exception:
                    # A failed POST or journal write is never interpreted as absence.
                    pass
                return self._record(
                    db.execute(
                        "SELECT * FROM orders WHERE request_hash=?", (digest,)
                    ).fetchone()
                )
            finally:
                db.close()

    def inspect(
        self, digest: str, *, reconcile: bool = False, cancel: bool = False
    ) -> dict:
        """Status is local; reconciliation and cancellation never create an order."""
        import re

        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise LiveExecutionBlocked("invalid_request_hash")
        with operator_lock(self.state_dir):
            db = self._db()
            try:
                row = db.execute(
                    "SELECT * FROM orders WHERE request_hash=?", (digest,)
                ).fetchone()
                if row is None:
                    raise LiveExecutionBlocked("unknown_order")
                if reconcile or cancel:
                    self._account()
                    request = strict_json(row["request"].encode())
                    previous = (
                        strict_json(row["observation"].encode())
                        if row["observation"]
                        else None
                    )
                    raw = self.broker.call(
                        "GET",
                        "/v2/orders:by_client_order_id",
                        params={"client_order_id": self._client_id(digest)},
                    )
                    observed = self._observation(raw, request, digest, previous)
                    with db:
                        db.execute(
                            "UPDATE orders SET state=?, observation=? "
                            "WHERE request_hash=?",
                            (
                                "terminal" if observed["terminal"] else "pending",
                                encoded(observed),
                                digest,
                            ),
                        )
                    if cancel and not observed["terminal"]:
                        # Cancel remains available under STOP.
                        # An acknowledgment does not establish cancellation.
                        with db:
                            db.execute(
                                "UPDATE orders SET cancel_requested=1 "
                                "WHERE request_hash=?",
                                (digest,),
                            )
                        self.broker.call(
                            "DELETE", "/v2/orders/" + observed["broker_order_id"]
                        )
                    row = db.execute(
                        "SELECT * FROM orders WHERE request_hash=?", (digest,)
                    ).fetchone()
                return self._record(row)
            finally:
                db.close()
