"""GET-only qualification uses synthetic HTTP responses, never a live account."""

import json
import os
import socket

import httpx
import pytest
from test_live_connector import Broker

from liquilens_trading_copilot import live_account, live_cli, live_connector
from liquilens_trading_copilot.live_account import (
    AlpacaAccountReadTransport,
    check_account,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("account check attempted a real network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)


def configured(tmp_path):
    directory = tmp_path.resolve()
    live_cli.initialize(directory)
    path = directory / "live-config.json"
    config = json.loads(path.read_text())
    # Intentionally no issuer, policy identity, receipt or verification key.
    config["binding"] = {"account_id": "synthetic-live-account"}
    config["limits"]["max_gross_exposure_usd"] = 10000
    path.write_text(json.dumps(config))
    (directory / "live-secrets.json").write_text(
        json.dumps(
            {
                "api_key": "test-key",
                "secret_key": "PRIVATE-SECRET",
            }
        )
    )
    return directory


def snapshot(directory):
    return {
        p.name: (
            p.lstat().st_mode,
            p.lstat().st_mtime_ns,
            os.readlink(p) if p.is_symlink() else p.read_bytes(),
        )
        for p in directory.iterdir()
    }


def install_broker(monkeypatch, handler):
    def factory(**kwargs):
        return AlpacaAccountReadTransport(
            **kwargs, transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(live_account, "AlpacaAccountReadTransport", factory)


@pytest.mark.parametrize("stop", [False, True, "dangling-symlink"])
def test_disabled_account_check_needs_no_issuer_and_never_initializes_state(
    tmp_path, monkeypatch, capsys, stop
):
    directory = configured(tmp_path)
    if stop == "dangling-symlink":
        (directory / "STOP").symlink_to(directory / "absent")
    elif stop:
        (directory / "STOP").touch()
    # Existing malformed journal and lock must not be opened or repaired either.
    (directory / "live-orders.sqlite3").write_bytes(b"PRIVATE corrupt old journal")
    (directory / "operator.lock").write_bytes(b"PRIVATE existing lock")
    before = snapshot(directory)
    broker = Broker()
    install_broker(monkeypatch, broker)

    def forbidden(*args, **kwargs):
        pytest.fail("account check reached execution or receipt machinery")

    monkeypatch.setattr(live_cli, "prepare_state", forbidden)
    monkeypatch.setattr(live_cli, "LiveAccountConnector", forbidden)
    monkeypatch.setattr(live_connector, "verify_trade_safety_receipt", forbidden)
    monkeypatch.setattr(live_connector.sqlite3, "connect", forbidden)
    original_open = os.open

    def read_only_open(path, flags, *args, **kwargs):
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", read_only_open)
    monkeypatch.setattr(
        "sys.argv", ["liquilens-live", "check-account", "--state-dir", str(directory)]
    )
    assert live_cli.main() == 0
    rendered = capsys.readouterr().out
    result = json.loads(rendered)
    assert result["account_qualified"] is True
    assert result["live_ready"] is False and result["order_submitted"] is False
    assert result["state_modified"] is False
    assert result["activation_configured"] is False
    assert result["stop_present"] == bool(stop)
    assert result["position_count"] == 1
    assert result["managed_live_endpoint"] is None
    assert result["broker_preview_adapter_available"] is False
    assert result["qualified_live_issuer_available"] is False
    assert all(result["checks"].values())
    assert result["reason_codes"] == []
    for private in (
        "PRIVATE",
        "test-key",
        "synthetic-live-account",
        "BTCUSD",
    ):
        assert private not in rendered
    assert broker.calls == [
        ("GET", "/v2/account"),
        ("GET", "/v2/orders"),
        ("GET", "/v2/positions"),
    ]
    assert broker.posts == 0 and snapshot(directory) == before


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"id": "PRIVATE-other-account"}, "live_account_not_eligible"),
        ({"currency": "EUR"}, "live_account_not_eligible"),
        ({"status": "ACCOUNT_UPDATED"}, "live_account_not_eligible"),
        ({"trading_blocked": True}, "live_account_not_eligible"),
        ({"account_blocked": 0}, "live_account_not_eligible"),
        ({"trade_suspended_by_user": None}, "live_account_not_eligible"),
        ({"cash": "-1"}, "account_loss_or_cash_limit"),
        ({"equity": "0"}, "account_loss_or_cash_limit"),
        ({"last_equity": "0"}, "account_loss_or_cash_limit"),
        ({"equity": "14900"}, "account_loss_or_cash_limit"),
        ({"cash": "NaN"}, "invalid_numeric_value"),
        ({"cash": True}, "invalid_numeric_value"),
    ],
)
def test_account_status_binding_and_numeric_failures_stop_after_account_get(
    tmp_path, monkeypatch, change, reason
):
    directory = configured(tmp_path)
    before = snapshot(directory)
    broker = Broker()
    broker.account.update(change)
    install_broker(monkeypatch, broker)
    report = check_account(directory)
    assert report["account_qualified"] is False
    assert report["reason_codes"] == [reason]
    assert broker.calls == [("GET", "/v2/account")]
    assert snapshot(directory) == before and "PRIVATE" not in json.dumps(report)


@pytest.mark.parametrize(
    "positions,reason",
    [
        ({}, "invalid_account_positions"),
        ([None], "invalid_account_positions"),
        (
            [{"symbol": "", "qty": "1", "market_value": "1"}],
            "invalid_account_positions",
        ),
        (
            [{"symbol": " ", "qty": "1", "market_value": "1"}],
            "invalid_account_positions",
        ),
        (
            [
                {"symbol": "BTC/USD", "qty": "1", "market_value": "1"},
                {"symbol": "BTCUSD", "qty": "1", "market_value": "1"},
            ],
            "duplicate_account_position",
        ),
        (
            [{"symbol": "BTCUSD", "qty": "-1", "market_value": "1"}],
            "short_or_margin_position_unsupported",
        ),
        (
            [{"symbol": "BTCUSD", "qty": "1", "market_value": "-1"}],
            "short_or_margin_position_unsupported",
        ),
        (
            [{"symbol": "BTCUSD", "qty": "NaN", "market_value": "1"}],
            "invalid_numeric_value",
        ),
        (
            [{"symbol": "BTCUSD", "qty": "1", "market_value": "Infinity"}],
            "invalid_numeric_value",
        ),
        (
            [{"symbol": "BTCUSD", "qty": "1", "market_value": "10001"}],
            "account_exposure_or_cash_limit",
        ),
    ],
)
def test_positions_fail_closed_and_preserve_state(
    tmp_path, monkeypatch, positions, reason
):
    directory = configured(tmp_path)
    before = snapshot(directory)
    broker = Broker()
    broker.positions = positions
    install_broker(monkeypatch, broker)
    report = check_account(directory)
    assert report["reason_codes"] == [reason]
    assert report["account_qualified"] is False
    assert all(method == "GET" for method, _ in broker.calls)
    assert snapshot(directory) == before


@pytest.mark.parametrize("orders", [[{"id": "PRIVATE"}], {}, None])
def test_nonempty_or_malformed_orders_refuse_without_listing_positions(
    tmp_path, monkeypatch, orders
):
    directory = configured(tmp_path)
    broker = Broker()
    broker.orders = orders
    install_broker(monkeypatch, broker)
    report = check_account(directory)
    expected = (
        "broker_response_unavailable" if orders is None else "open_orders_pending"
    )
    assert report["reason_codes"] == [expected]
    assert broker.calls == [("GET", "/v2/account"), ("GET", "/v2/orders")]
    assert "PRIVATE" not in json.dumps(report)


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "permissive",
        "symlink",
        "binding",
        "credentials",
        "limits",
        "malformed",
    ],
)
def test_invalid_local_setup_never_contacts_broker_or_creates_files(
    tmp_path, monkeypatch, failure
):
    directory = configured(tmp_path)
    if failure == "missing":
        directory = directory / "absent"
    elif failure == "permissive":
        (directory / "live-secrets.json").chmod(0o644)
    elif failure == "symlink":
        (directory / "live-secrets.json").unlink()
        (directory / "live-secrets.json").symlink_to(directory / "live-config.json")
    elif failure == "credentials":
        (directory / "live-secrets.json").write_text(
            '{"api_key":false,"secret_key":"PRIVATE"}'
        )
    elif failure == "malformed":
        (directory / "live-config.json").write_text('{"PRIVATE":')
    else:
        p = directory / "live-config.json"
        config = json.loads(p.read_text())
        if failure == "binding":
            config["binding"]["account_id"] = ""
        else:
            config["limits"]["max_daily_loss_usd"] = 0
        p.write_text(json.dumps(config))
    before = snapshot(tmp_path)

    def forbidden(**kwargs):
        pytest.fail("invalid setup reached broker construction")

    monkeypatch.setattr(live_account, "AlpacaAccountReadTransport", forbidden)
    report = check_account(directory)
    assert report["account_qualified"] is False
    assert report["network_accessed"] is False
    assert snapshot(tmp_path) == before
    assert "PRIVATE" not in json.dumps(report)


@pytest.mark.parametrize(
    "method,path,params,payload",
    [
        ("POST", "/v2/orders", None, {}),
        ("DELETE", "/v2/positions", None, None),
        ("DELETE", "/v2/orders/00000000-0000-4000-8000-000000000001", None, None),
        ("GET", "/v2/orders:by_client_order_id", None, None),
        ("GET", "https://bad.example/v2/account", None, None),
        ("GET", "/v2/account", {"redirect": "PRIVATE"}, None),
        ("GET", "/v2/account", None, {}),
        ("GET", "/v2/orders", {"status": "all", "limit": 1}, None),
        ("GET", "/v2/orders", {"status": "open", "limit": 500}, None),
        ("GET", "/v2/orders", {"status": "open", "limit": True}, None),
    ],
)
def test_read_transport_refuses_all_other_routes_before_network(
    method, path, params, payload
):
    calls = []
    transport = AlpacaAccountReadTransport(
        api_key="test-key",
        secret_key="PRIVATE",
        transport=httpx.MockTransport(lambda request: calls.append(request)),
    )
    with pytest.raises(
        live_connector.LiveExecutionBlocked, match="unsupported_account_check_route"
    ):
        transport.call(method, path, params=params, payload=payload)
    assert calls == []
    transport.close()


@pytest.mark.parametrize(
    "failure",
    [
        "redirect",
        "timeout",
        "unauthorized",
        "duplicate-json",
        "oversized",
        "unknown-reason",
    ],
)
def test_provider_failures_are_redacted_without_retry(
    tmp_path, monkeypatch, capsys, failure
):
    directory = configured(tmp_path)
    before = snapshot(directory)
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET" and not request.content
        assert request.url == "https://api.alpaca.markets/v2/account"
        if failure == "timeout":
            raise httpx.ReadTimeout("PRIVATE credential", request=request)
        if failure == "unknown-reason":
            raise live_connector.LiveExecutionBlocked("PRIVATE provider reason")
        if failure == "redirect":
            return httpx.Response(307, headers={"Location": "https://bad.example"})
        if failure == "duplicate-json":
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                content=b'{"id":"PRIVATE","id":"other"}',
            )
        if failure == "oversized":
            return httpx.Response(
                200, headers={"Content-Type": "application/json"}, content=b"x" * 524289
            )
        return httpx.Response(401, json={"message": "PRIVATE credential"})

    install_broker(monkeypatch, handler)
    monkeypatch.setattr(
        "sys.argv", ["liquilens-live", "check-account", "--state-dir", str(directory)]
    )
    assert live_cli.main() == 2
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["reason_codes"] == ["broker_response_unavailable"]
    assert report["account_qualified"] is False and "PRIVATE" not in output
    assert len(calls) == 1 and snapshot(directory) == before


def test_transport_cleanup_error_cannot_escape_redaction(tmp_path, monkeypatch):
    directory = configured(tmp_path)
    before = snapshot(directory)
    broker = Broker()

    class BadClose(AlpacaAccountReadTransport):
        def close(self):
            super().close()
            raise ValueError("PRIVATE close error")

    monkeypatch.setattr(
        live_account,
        "AlpacaAccountReadTransport",
        lambda **kwargs: BadClose(**kwargs, transport=httpx.MockTransport(broker)),
    )
    result = check_account(directory)
    assert result["account_qualified"] is False
    assert result["reason_codes"] == ["live_account_check_unavailable"]
    assert "PRIVATE" not in json.dumps(result)
    assert snapshot(directory) == before


def test_exact_get_requests_have_no_body_and_fixed_open_order_query(
    tmp_path, monkeypatch
):
    directory = configured(tmp_path)
    broker = Broker()

    def handler(request):
        assert request.method == "GET" and not request.content
        assert (
            request.url.scheme == "https" and request.url.host == "api.alpaca.markets"
        )
        expected = (
            {"status": "open", "limit": "1"} if request.url.path == "/v2/orders" else {}
        )
        assert dict(request.url.params) == expected
        return broker(request)

    install_broker(monkeypatch, handler)
    assert check_account(directory)["account_qualified"] is True
    assert broker.posts == 0
