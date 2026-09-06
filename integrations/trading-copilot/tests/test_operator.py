from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from liquilens_trading_copilot.cli import _initialize
from liquilens_trading_copilot.config import (
    ConfigurationError,
    CopilotConfig,
    PaperCredentials,
    load_config,
    load_secret_file,
    strict_json,
)
from liquilens_trading_copilot.market import (
    MARKET_BARS_URL,
    PAPER_ORIGIN,
    InputUnavailable,
    PaperAccountReader,
    fetch_bars,
)
from liquilens_trading_copilot.runner import run_configured_cycle
from liquilens_trading_copilot.state import CycleStore, StateError, operator_lock

NOW = datetime(2026, 9, 6, 10, 15, tzinfo=UTC)
KEYS = PaperCredentials("paper-test-key", "paper-test-secret", b"h" * 32)


def test_init_private_disabled_and_never_replaces_identity(tmp_path: Path) -> None:
    state = tmp_path / "operator"
    result = _initialize(state)
    assert result["order_submission_enabled"] is False
    config = load_config(state / "config.json")
    assert not config.enabled and config.account_id is None and config.mode == "paper"
    first = (state / "paper.env").read_bytes()
    assert (state / "paper.env").stat().st_mode & 0o777 == 0o600
    assert state.stat().st_mode & 0o777 == 0o700
    with pytest.raises(ConfigurationError):
        _initialize(state)
    assert (state / "paper.env").read_bytes() == first
    assert "COPILOT_PAPER_HMAC_KEY" in load_secret_file(state / "paper.env")


def test_no_live_mode_or_fallback_credentials() -> None:
    with pytest.raises(ConfigurationError, match="only_paper"):
        replace(CopilotConfig(), mode="live").validate()
    with pytest.raises(ConfigurationError, match="paper_credentials_missing"):
        PaperCredentials.from_environment(
            {"APCA_API_KEY_ID": "live", "APCA_API_SECRET_KEY": "live"}
        )
    assert "paper-test-secret" not in repr(KEYS)


@pytest.mark.parametrize(
    "value", [b'{"enabled": true, "enabled":false}', b'{"value":NaN}']
)
def test_invalid_json_fails(value: bytes) -> None:
    with pytest.raises(ConfigurationError):
        strict_json(value)


def test_secret_file_permissions_and_literal_parsing(tmp_path: Path) -> None:
    path = tmp_path / "paper.env"
    path.write_text(
        "ALPACA_PAPER_API_KEY=$(do-not-execute)\nALPACA_PAPER_SECRET_KEY=secret\nCOPILOT_PAPER_HMAC_KEY="
        + "a" * 32
        + "\n"
    )
    path.chmod(0o644)
    with pytest.raises(ConfigurationError):
        load_secret_file(path)
    path.chmod(0o600)
    assert load_secret_file(path)["ALPACA_PAPER_API_KEY"] == "$(do-not-execute)"
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(OSError):
        load_secret_file(link)


def test_single_operator_lock_and_symlink_refusal(tmp_path: Path) -> None:
    state = tmp_path / "state"
    with (
        operator_lock(state),
        pytest.raises(StateError, match="another_copilot"),
        operator_lock(state),
    ):
        pytest.fail("second owner acquired lock")
    link = tmp_path / "linked"
    link.symlink_to(state)
    with pytest.raises(StateError), operator_lock(link):
        pytest.fail("linked state accepted")


def test_intent_dedup_and_daily_budget_survive_restart(tmp_path: Path) -> None:
    store = CycleStore(tmp_path / "state")
    options = {"amount": 1000, "now": NOW, "max_daily_attempts": 2}
    assert store.reserve(intent_key="a", request_hash="1", **options)
    store.close()
    store = CycleStore(tmp_path / "state")
    assert not store.reserve(intent_key="a", request_hash="2", **options)
    assert store.reserve(intent_key="b", request_hash="3", **options)
    assert not store.reserve(intent_key="c", request_hash="4", **options)
    assert store.status()["intent_count"] == 2
    assert store.reserve(
        intent_key="c", request_hash="4", **{**options, "now": NOW + timedelta(days=1)}
    )
    store.close()


def test_unconfigured_cycle_never_initializes_broker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("broker constructed for missing configuration")

    monkeypatch.setattr(
        "liquilens_trading_copilot.runner.OperatorPaperTradeSafetyGateway", forbidden
    )
    store = CycleStore(tmp_path / "state")
    result = asyncio.run(
        run_configured_cycle(
            CopilotConfig(state_dir=str(tmp_path / "state")), store, None
        )
    )
    assert result["status"] == "blocked"
    assert "paper_credentials_or_operator_hmac_missing" in result["reasons"]
    store.close()


def account_payload() -> dict:
    return {
        "id": "paper-account",
        "status": "ACTIVE",
        "currency": "USD",
        "trading_blocked": False,
        "account_blocked": False,
        "trade_suspended_by_user": False,
        "equity": "100000",
        "last_equity": "101000",
        "cash": "95000",
    }


def test_account_reads_only_fixed_paper_origin() -> None:
    seen = []

    def serve(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert str(request.url).startswith(PAPER_ORIGIN + "/v2/")
        assert request.headers["APCA-API-KEY-ID"] == "paper-test-key"
        value = account_payload() if request.url.path == "/v2/account" else []
        return httpx.Response(200, json=value)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
            return await PaperAccountReader(client, KEYS, "paper-account").snapshot()

    result = asyncio.run(run())
    assert len(seen) == 3
    assert result.daily_pnl_usd == -1000 and result.open_orders == 0


@pytest.mark.parametrize(
    "patch",
    [
        {"id": "wrong"},
        {"trading_blocked": True},
        {"trade_suspended_by_user": None},
        {"equity": "NaN"},
        {"status": "CLOSED"},
    ],
)
def test_account_uncertainty_blocks(patch: dict) -> None:
    def serve(request: httpx.Request) -> httpx.Response:
        value = (
            {**account_payload(), **patch} if request.url.path == "/v2/account" else []
        )
        return httpx.Response(200, json=value)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
            return await PaperAccountReader(client, KEYS, "paper-account").snapshot()

    with pytest.raises(InputUnavailable):
        asyncio.run(run())


def test_market_drops_forming_bar_and_uses_close_clock() -> None:
    def serve(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(MARKET_BARS_URL)
        return httpx.Response(
            200,
            json={
                "bars": {
                    "BTC/USD": [
                        {"t": "2026-09-06T09:00:00Z", "c": 100},
                        {"t": "2026-09-06T10:00:00Z", "c": 110},
                    ]
                },
                "next_page_token": None,
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
            return await fetch_bars(client, now=NOW)

    bars = asyncio.run(run())
    assert len(bars) == 1 and bars[0].at.hour == 10 and bars[0].close == 100


@pytest.mark.parametrize(
    "status,payload",
    [
        (302, {}),
        (200, {"bars": {}, "next_page_token": "more"}),
        (200, {"bars": {"BTC/USD": [{"t": "2026-09-06T09:00:00", "c": 100}]}}),
    ],
)
def test_market_missing_or_redirected_data_fails(status: int, payload: dict) -> None:
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    status, json=payload, headers={"Location": "https://evil.example"}
                ),
            )
        ) as client:
            return await fetch_bars(client, now=NOW)

    with pytest.raises(InputUnavailable):
        asyncio.run(run())
