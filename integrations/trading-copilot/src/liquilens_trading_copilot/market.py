"""Fixed-origin, bounded reads of Alpaca paper account and hourly crypto bars."""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from alpaca.trading.enums import OrderStatus
from liquilens_alpaca_paper import client_order_id_for_request_hash

from .config import PaperCredentials, strict_json
from .strategy import MarketBar, PortfolioSnapshot

PAPER_ORIGIN = "https://paper-api.alpaca.markets"
MARKET_BARS_URL = "https://data.alpaca.markets/v1beta3/crypto/us/bars"
TERMINAL_ORDER_STATUSES = frozenset(
    {"filled", "canceled", "expired", "rejected", "replaced"}
)
_ORDER_STATUSES = frozenset(status.value for status in OrderStatus)


class InputUnavailable(RuntimeError):
    pass


async def bounded_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> Any:
    async def read() -> Any:
        async with client.stream(
            "GET",
            url,
            headers=headers,
            params=params,
            follow_redirects=False,
            timeout=httpx.Timeout(10.0, connect=3.0),
        ) as response:
            if response.status_code != 200:
                raise InputUnavailable("upstream_http_unavailable")
            parts = bytearray()
            async for chunk in response.aiter_bytes():
                parts.extend(chunk)
                if len(parts) > 1048576:
                    raise InputUnavailable("upstream_response_too_large")
            return strict_json(bytes(parts))

    try:
        return await asyncio.wait_for(read(), timeout=15)
    except (httpx.HTTPError, TimeoutError, ValueError) as exc:
        raise InputUnavailable("upstream_invalid_or_unavailable") from exc


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise InputUnavailable(f"invalid_account_{field}")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise InputUnavailable(f"invalid_account_{field}") from exc
    if not math.isfinite(result):
        raise InputUnavailable(f"invalid_account_{field}")
    return result


def _order_text(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 256
    ):
        raise InputUnavailable(f"invalid_order_{name}")
    return value


def _order_decimal(value: Any, name: str) -> tuple[str, Decimal]:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise InputUnavailable(f"invalid_order_{name}")
    text = str(value)
    if not text or text != text.strip() or len(text) > 128:
        raise InputUnavailable(f"invalid_order_{name}")
    try:
        number = Decimal(text)
    except InvalidOperation as error:
        raise InputUnavailable(f"invalid_order_{name}") from error
    if not number.is_finite() or not 0 <= number <= Decimal("1e30"):
        raise InputUnavailable(f"invalid_order_{name}")
    return text, number


def validate_order_observation(request_hash: str, observation: Any) -> dict[str, Any]:
    """Return only validated outcome fields for one known order identity.

    This normalizer supplies no execution authority and does not interpret an
    accepted order as filled. Monetary strings preserve broker decimal values.
    """
    try:
        expected_id = client_order_id_for_request_hash(request_hash)
    except Exception as error:
        raise InputUnavailable("invalid_order_request_hash") from error
    if not isinstance(observation, dict):
        raise InputUnavailable("invalid_order_observation")
    if (
        observation.get("request_hash") != request_hash
        or observation.get("client_order_id") != expected_id
    ):
        raise InputUnavailable("paper_order_binding_mismatch")
    status = observation.get("status")
    if not isinstance(status, str) or status not in _ORDER_STATUSES:
        raise InputUnavailable("paper_order_status_unknown")
    terminal = status in TERMINAL_ORDER_STATUSES
    if (
        type(observation.get("terminal")) is not bool
        or observation["terminal"] != terminal
    ):
        raise InputUnavailable("paper_order_terminal_status_mismatch")
    if not isinstance(observation.get("symbol"), str) or observation["symbol"] not in {
        "BTCUSD",
        "BTC/USD",
    }:
        raise InputUnavailable("paper_order_symbol_mismatch")
    if not isinstance(observation.get("side"), str) or observation["side"] not in {
        "buy",
        "sell",
    }:
        raise InputUnavailable("paper_order_side_invalid")
    quantity_text, quantity = _order_decimal(
        observation.get("filled_qty"), "filled_qty"
    )
    average_value = observation.get("filled_avg_price")
    average_text = None
    average = Decimal(0)
    if average_value is not None:
        average_text, average = _order_decimal(average_value, "filled_avg_price")
    if quantity > 0 and average <= 0:
        raise InputUnavailable("paper_order_fill_price_missing")
    if quantity == 0 and average > 0:
        raise InputUnavailable("paper_order_fill_quantity_missing")
    if status in {"filled", "partially_filled"} and quantity <= 0:
        raise InputUnavailable("paper_order_fill_quantity_missing")
    return {
        "request_hash": request_hash,
        "account_id": _order_text(observation.get("account_id"), "account_id"),
        "broker_order_id": _order_text(observation.get("broker_order_id"), "id"),
        "client_order_id": expected_id,
        "status": status,
        "terminal": terminal,
        "symbol": "BTC/USD",
        "side": observation["side"],
        "filled_qty": quantity_text,
        "filled_avg_price": average_text,
    }


class PaperAccountReader:
    def __init__(
        self,
        client: httpx.AsyncClient,
        credentials: PaperCredentials,
        account_id: str,
    ) -> None:
        self.client = client
        self.account_id = account_id
        self.headers = {
            "APCA-API-KEY-ID": credentials.api_key,
            "APCA-API-SECRET-KEY": credentials.secret_key,
        }

    async def snapshot(self) -> PortfolioSnapshot:
        account, positions, orders = await asyncio.gather(
            bounded_json(
                self.client, PAPER_ORIGIN + "/v2/account", headers=self.headers
            ),
            bounded_json(
                self.client, PAPER_ORIGIN + "/v2/positions", headers=self.headers
            ),
            bounded_json(
                self.client,
                PAPER_ORIGIN + "/v2/orders",
                headers=self.headers,
                params={"status": "open", "limit": "500"},
            ),
        )
        if not isinstance(account, dict) or account.get("id") != self.account_id:
            raise InputUnavailable("paper_account_binding_mismatch")
        if account.get("status") != "ACTIVE" or account.get("currency") != "USD":
            raise InputUnavailable("paper_account_not_active_usd")
        for key in ("trading_blocked", "account_blocked", "trade_suspended_by_user"):
            if account.get(key) is not False:
                raise InputUnavailable("paper_account_trading_status_not_clear")
        if not isinstance(positions, list) or not isinstance(orders, list):
            raise InputUnavailable("paper_positions_or_orders_invalid")
        btc = 0.0
        for position in positions:
            if not isinstance(position, dict):
                raise InputUnavailable("paper_position_invalid")
            if position.get("symbol") in {"BTCUSD", "BTC/USD"}:
                btc += _number(position.get("market_value"), "btc_value")
        equity = _number(account.get("equity"), "equity")
        previous = _number(account.get("last_equity"), "last_equity")
        return PortfolioSnapshot(
            cash_usd=_number(account.get("cash"), "cash"),
            equity_usd=equity,
            btc_notional_usd=btc,
            daily_pnl_usd=equity - previous,
            open_orders=len(orders),
        )

    async def observe_order(self, request_hash: str) -> dict[str, Any]:
        """Read one broker outcome, preserving the account and client identity.

        A missing order remains unavailable; it is not a rejected order or
        permission to repeat submission. Only official paper GETs are made.
        """
        try:
            expected_id = client_order_id_for_request_hash(request_hash)
        except Exception as error:
            raise InputUnavailable("invalid_order_request_hash") from error
        account = await bounded_json(
            self.client, PAPER_ORIGIN + "/v2/account", headers=self.headers
        )
        if not isinstance(account, dict) or account.get("id") != self.account_id:
            raise InputUnavailable("paper_account_binding_mismatch")
        raw = await bounded_json(
            self.client,
            PAPER_ORIGIN + "/v2/orders:by_client_order_id",
            headers=self.headers,
            params={"client_order_id": expected_id},
        )
        if not isinstance(raw, dict):
            raise InputUnavailable("invalid_order_observation")
        return validate_order_observation(
            request_hash,
            {
                "request_hash": request_hash,
                "account_id": self.account_id,
                "broker_order_id": raw.get("id"),
                "client_order_id": raw.get("client_order_id"),
                "status": raw.get("status"),
                "terminal": isinstance(raw.get("status"), str)
                and raw["status"] in TERMINAL_ORDER_STATUSES,
                "symbol": raw.get("symbol"),
                "side": raw.get("side"),
                "filled_qty": raw.get("filled_qty"),
                "filled_avg_price": raw.get("filled_avg_price"),
            },
        )


async def fetch_bars(
    client: httpx.AsyncClient,
    *,
    now: datetime,
    credentials: PaperCredentials | None = None,
) -> list[MarketBar]:
    end = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=96)
    headers = (
        None
        if credentials is None
        else {
            "APCA-API-KEY-ID": credentials.api_key,
            "APCA-API-SECRET-KEY": credentials.secret_key,
        }
    )
    payload = await bounded_json(
        client,
        MARKET_BARS_URL,
        headers=headers,
        params={
            "symbols": "BTC/USD",
            "timeframe": "1Hour",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": "1000",
            "sort": "asc",
        },
    )
    if not isinstance(payload, dict) or payload.get("next_page_token"):
        raise InputUnavailable("market_bars_incomplete")
    grouped_bars = payload.get("bars")
    if not isinstance(grouped_bars, dict):
        raise InputUnavailable("market_bars_invalid")
    rows = grouped_bars.get("BTC/USD")
    if not isinstance(rows, list):
        raise InputUnavailable("market_bars_missing")
    bars: list[MarketBar] = []
    try:
        for row in rows:
            at = datetime.fromisoformat(row["t"].replace("Z", "+00:00"))
            if at.utcoffset() is None or at > end:
                raise InputUnavailable("market_bar_clock_invalid")
            # The provider dates bars at their start; expose their completed
            # interval end. Never use the currently forming hour in a signal.
            closed_at = at + timedelta(hours=1)
            if closed_at <= end:
                bars.append(MarketBar(at=closed_at, close=_number(row["c"], "close")))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise InputUnavailable("market_bars_invalid") from exc
    return bars
