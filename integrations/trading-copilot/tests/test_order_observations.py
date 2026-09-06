"""Synthetic read-only broker outcomes; no live account or fill is represented."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from liquilens_trading_copilot.config import PaperCredentials
from liquilens_trading_copilot.market import (
    PAPER_ORIGIN,
    InputUnavailable,
    PaperAccountReader,
    validate_order_observation,
)
from liquilens_trading_copilot.state import CycleStore, StateError

NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)
HASH = "a" * 64
ACCOUNT = "synthetic-paper-account"
CREDENTIALS = PaperCredentials("synthetic-key", "synthetic-secret", b"h" * 32)


def broker_order(**overrides: object) -> dict:
    return {
        "id": "synthetic-broker-order",
        "client_order_id": "llts-" + HASH,
        "status": "accepted",
        "symbol": "BTCUSD",
        "side": "buy",
        "filled_qty": "0",
        "filled_avg_price": None,
        "private_unrelated_provider_field": "must-not-be-retained",
        **overrides,
    }


def observation(**overrides: object) -> dict:
    return {
        "request_hash": HASH,
        "account_id": ACCOUNT,
        "broker_order_id": "synthetic-broker-order",
        "client_order_id": "llts-" + HASH,
        "status": "accepted",
        "terminal": False,
        "symbol": "BTC/USD",
        "side": "buy",
        "filled_qty": "0",
        "filled_avg_price": None,
        **overrides,
    }


def read_order(
    payload: object, *, account: str = ACCOUNT, status_code: int = 200
) -> tuple[dict, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def serve(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.url.scheme == "https"
        assert request.url.host == "paper-api.alpaca.markets"
        assert request.headers["APCA-API-KEY-ID"] == CREDENTIALS.api_key
        if request.url.path == "/v2/account":
            return httpx.Response(200, json={"id": account})
        assert request.url.path == "/v2/orders:by_client_order_id"
        assert dict(request.url.params) == {"client_order_id": "llts-" + HASH}
        return httpx.Response(
            status_code, json=payload, headers={"Location": "https://invalid.example"}
        )

    async def run() -> dict:
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
            reader = PaperAccountReader(client, CREDENTIALS, ACCOUNT)
            return await reader.observe_order(HASH)

    return asyncio.run(run()), seen


def reserve(store: CycleStore, request_hash: str = HASH, *, index: int = 0) -> None:
    assert store.reserve(
        intent_key=f"{ACCOUNT}|synthetic-strategy|{index}",
        request_hash=request_hash,
        amount=1000,
        now=NOW,
        max_daily_attempts=1000,
    )


def test_accepted_is_read_only_pending_with_no_fill_claim() -> None:
    result, seen = read_order(broker_order())
    assert len(seen) == 2
    assert result["status"] == "accepted"
    assert result["terminal"] is False
    assert result["filled_qty"] == "0"
    assert result["filled_avg_price"] is None
    assert result["account_id"] == ACCOUNT
    assert result["symbol"] == "BTC/USD"
    assert "must-not-be-retained" not in json.dumps(result)


def test_partial_fill_is_observed_without_claiming_completion() -> None:
    result, _ = read_order(
        broker_order(
            status="partially_filled",
            filled_qty="0.0001234500",
            filled_avg_price="60000.123456789",
        )
    )
    assert result["terminal"] is False
    assert result["filled_qty"] == "0.0001234500"
    assert result["filled_avg_price"] == "60000.123456789"


@pytest.mark.parametrize(
    "status", ["filled", "canceled", "expired", "rejected", "replaced"]
)
def test_only_explicit_terminal_statuses_end_pending_tracking(status: str) -> None:
    quantity = "0.02" if status == "filled" else "0"
    average = "50000" if status == "filled" else None
    result, _ = read_order(
        broker_order(status=status, filled_qty=quantity, filled_avg_price=average)
    )
    assert result["terminal"] is True


@pytest.mark.parametrize(
    "status", ["new", "pending_cancel", "pending_replace", "done_for_day", "suspended"]
)
def test_nonterminal_statuses_keep_tracking(status: str) -> None:
    result, _ = read_order(broker_order(status=status))
    assert result["terminal"] is False


def test_account_mismatch_stops_before_order_lookup() -> None:
    seen = []

    def serve(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert str(request.url) == PAPER_ORIGIN + "/v2/account"
        return httpx.Response(200, json={"id": "other-paper-account"})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
            with pytest.raises(InputUnavailable, match="account_binding_mismatch"):
                await PaperAccountReader(client, CREDENTIALS, ACCOUNT).observe_order(
                    HASH
                )

    asyncio.run(run())
    assert len(seen) == 1


@pytest.mark.parametrize("status", [302, 404, 429, 500])
def test_lookup_failure_is_unavailable_never_a_rejection_or_retry(status: int) -> None:
    with pytest.raises(InputUnavailable):
        read_order(broker_order(), status_code=status)


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": ""},
        {"client_order_id": "llts-" + "b" * 64},
        {"status": "unexpected-status"},
        {"status": []},
        {"symbol": "ETH/USD"},
        {"symbol": {}},
        {"side": "unknown"},
        {"filled_qty": "NaN"},
        {"filled_qty": "-1"},
        {"filled_qty": True},
        {"filled_qty": "1e99999"},
        {"filled_qty": "1", "filled_avg_price": None},
        {"status": "filled", "filled_qty": "0"},
        {"status": "partially_filled", "filled_qty": "0"},
        {"filled_avg_price": "Infinity"},
        {"filled_avg_price": "50000"},
    ],
)
def test_invalid_or_mismatched_broker_results_fail_closed(overrides: dict) -> None:
    with pytest.raises(InputUnavailable):
        read_order(broker_order(**overrides))


def test_invalid_request_hash_is_rejected_before_any_network_read() -> None:
    def forbidden(_request: httpx.Request) -> httpx.Response:
        pytest.fail("invalid identity reached network")

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(forbidden)
        ) as client:
            with pytest.raises(InputUnavailable, match="request_hash"):
                await PaperAccountReader(client, CREDENTIALS, ACCOUNT).observe_order(
                    "../order"
                )

    asyncio.run(run())


def test_normalizer_does_not_accept_forged_terminal_marker() -> None:
    with pytest.raises(InputUnavailable, match="terminal_status_mismatch"):
        validate_order_observation(HASH, observation(terminal=True))


def test_accepted_to_filled_persists_across_restart_without_releasing_intent(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    store = CycleStore(state)
    reserve(store)
    store.order_observation(request_hash=HASH, observation=observation(), now=NOW)
    assert store.status()["filled_order_count"] == 0
    assert len(store.pending_intents()) == 1
    store.close()
    store = CycleStore(state)
    assert len(store.pending_intents()) == 1
    filled = observation(
        status="filled", terminal=True, filled_qty="0.02", filled_avg_price="50000"
    )
    store.order_observation(
        request_hash=HASH, observation=filled, now=NOW + timedelta(seconds=1)
    )
    assert store.pending_intents() == ()
    assert store.status()["filled_order_count"] == 1
    assert store.status()["intent_count"] == 1
    assert not store.reserve(
        intent_key=f"{ACCOUNT}|synthetic-strategy|0",
        request_hash="b" * 64,
        amount=1000,
        now=NOW,
        max_daily_attempts=2,
    )
    store.close()
    store = CycleStore(state)
    assert store.pending_intents() == ()
    assert store.status()["filled_order_count"] == 1
    assert store.status()["order_observation_count"] == 1
    assert store.status()["event_count"] == 2
    store.close()


def test_cancelled_partial_fill_preserves_quantity_but_is_not_a_full_fill(
    tmp_path: Path,
) -> None:
    store = CycleStore(tmp_path / "state")
    reserve(store)
    partial = observation(
        status="partially_filled", filled_qty="0.01", filled_avg_price="50000"
    )
    store.order_observation(request_hash=HASH, observation=partial, now=NOW)
    assert len(store.pending_intents()) == 1
    cancelled = {**partial, "status": "canceled", "terminal": True}
    store.order_observation(
        request_hash=HASH, observation=cancelled, now=NOW + timedelta(seconds=1)
    )
    assert store.pending_intents() == ()
    status = store.status()
    assert status["filled_order_count"] == 0
    assert status["latest"]["record"]["filled_qty"] == "0.01"
    assert status["intent_count"] == 1
    store.close()


def test_unknown_intent_and_wrong_account_cannot_be_observed(tmp_path: Path) -> None:
    store = CycleStore(tmp_path / "state")
    with pytest.raises(StateError, match="intent_unknown"):
        store.order_observation(request_hash=HASH, observation=observation(), now=NOW)
    reserve(store)
    with pytest.raises(StateError, match="account_mismatch"):
        store.order_observation(
            request_hash=HASH, observation=observation(account_id="other"), now=NOW
        )
    assert store.status()["order_observation_count"] == 0
    assert store.status()["event_count"] == 0
    store.close()


@pytest.mark.parametrize(
    "changed,clock,error",
    [
        ({"filled_qty": "0.001"}, NOW, "fill_regressed"),
        ({"broker_order_id": "another-order"}, NOW, "broker_identity_changed"),
        ({"side": "sell"}, NOW, "order_semantics_changed"),
        ({}, NOW - timedelta(seconds=1), "clock_regressed"),
    ],
)
def test_observation_regressions_roll_back_atomically(
    tmp_path: Path, changed: dict, clock: datetime, error: str
) -> None:
    store = CycleStore(tmp_path / "state")
    reserve(store)
    initial = observation(
        status="partially_filled", filled_qty="0.01", filled_avg_price="50000"
    )
    store.order_observation(request_hash=HASH, observation=initial, now=NOW)
    with pytest.raises(StateError, match=error):
        store.order_observation(
            request_hash=HASH, observation={**initial, **changed}, now=clock
        )
    assert store.status()["event_count"] == 1
    assert store.status()["latest"]["record"]["filled_qty"] == "0.01"
    store.close()


def test_terminal_observation_cannot_reopen_an_order(tmp_path: Path) -> None:
    store = CycleStore(tmp_path / "state")
    reserve(store)
    store.order_observation(
        request_hash=HASH,
        observation=observation(status="canceled", terminal=True),
        now=NOW,
    )
    with pytest.raises(StateError, match="terminal_state_changed"):
        store.order_observation(request_hash=HASH, observation=observation(), now=NOW)
    assert store.pending_intents() == ()
    assert store.status()["event_count"] == 1
    store.close()


def test_missing_lookup_does_not_erase_pending_intent(tmp_path: Path) -> None:
    store = CycleStore(tmp_path / "state")
    reserve(store)
    with pytest.raises(InputUnavailable):
        read_order({}, status_code=404)
    assert len(store.pending_intents()) == 1
    assert store.status()["intent_count"] == 1
    assert store.status()["order_observation_count"] == 0
    store.close()


def test_pending_query_is_bounded_and_deterministically_oldest_first(
    tmp_path: Path,
) -> None:
    store = CycleStore(tmp_path / "state")
    for index in range(105):
        reserve(store, f"{index:064x}", index=index)
    pending = store.pending_intents()
    assert len(pending) == 100
    assert pending[0]["request_hash"] == "0" * 64
    assert pending[-1]["request_hash"] == f"{99:064x}"
    assert store.status()["pending_intent_count"] == 105
    for limit in (0, 101, True):
        with pytest.raises(StateError):
            store.pending_intents(limit=limit)
    store.close()
