"""No broker connections: real receipts/journal and an HTTP transport double."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from liquilens_evidence import (
    TradeSafetyExecutionBinding,
    issue_trade_safety_receipt,
    trade_safety_request_hash,
)

from liquilens_trading_copilot.live_connector import (
    AlpacaLiveTransport,
    LiveAccountConnector,
    LiveExecutionBlocked,
    LiveLimits,
)

NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)
KEY = b"synthetic-live-test-key-never-a-customer-key"
ROOT = Path(__file__).resolve().parents[3] / "examples/trade-safety"


def fixture(name):
    return json.loads((ROOT / name).read_text())


def bundle(*, change=None, eligible=True, mode="live"):
    request = fixture("request.paper.json")
    request["mode"] = mode
    request["agent"]["account_id"] = "synthetic-live-account"
    request["agent"]["authorization_scope"] = ["evidence:read", "orders:" + mode]
    request["order"].update(order_type="limit", limit_price=40000.0)
    if change:
        change(request)
    digest = trade_safety_request_hash(request)
    evidence = fixture("evidence.paper.json")
    for product, value in evidence.items():
        value["request_hash"] = digest
        if product != "liquilens":
            value.update(
                state="eligible",
                rights_status="licensed",
                real_money_eligible=eligible,
                executable_quote=eligible and product == "undertow",
            )
    preview = fixture("broker-preview.paper.json")
    preview.update(
        state="verified",
        provider="synthetic-broker",
        account_id=request["agent"]["account_id"],
        request_hash=digest,
        preview_id="synthetic-preview",
        source_url="https://example.org/preview",
        source_sha256="d" * 64,
        expires_at="2026-09-02T12:02:00Z",
    )
    receipt = issue_trade_safety_receipt(
        request=request,
        evidence=evidence,
        policy=fixture("policy.paper.json"),
        broker_preview=preview,
        issuer=fixture("issuer.paper.json"),
        evaluated_at=NOW,
        ttl_seconds=60,
        hmac_key=KEY,
        hmac_key_id="test-live-key",
    )
    binding = TradeSafetyExecutionBinding(
        **{
            key: request["agent"][key]
            for key in (
                "account_id",
                "tenant_id",
                "operator_id",
                "agent_id",
                "runtime",
                "strategy_id",
            )
        },
        policy_id=receipt["policy"]["policy_id"],
        policy_version=receipt["policy"]["version"],
        policy_hash=receipt["policy_hash"],
        issuer_name=receipt["issuer"]["name"],
        issuer_version=receipt["issuer"]["version"],
        issuer_endpoint=receipt["issuer"]["endpoint"],
        hmac_key_id="test-live-key",
    )
    return request, receipt, binding


class Broker:
    def __init__(self):
        self.calls = []
        self.posts = 0
        self.timeout = False
        self.not_found = False
        self.account = dict(
            id="synthetic-live-account",
            status="ACTIVE",
            currency="USD",
            cash="10000",
            equity="15000",
            last_equity="15000",
            trading_blocked=False,
            account_blocked=False,
            trade_suspended_by_user=False,
        )
        self.positions = [{"symbol": "BTCUSD", "qty": "0.2", "market_value": "8000"}]
        self.orders = []
        self.order = None
        self.account_hook = lambda: None

    def __call__(self, request):
        assert request.url.host == "api.alpaca.markets"
        assert request.headers["APCA-API-KEY-ID"] == "test-key"
        self.calls.append((request.method, request.url.path))
        path = request.url.path
        if path == "/v2/account":
            self.account_hook()
            return httpx.Response(200, json=self.account)
        if path == "/v2/positions":
            return httpx.Response(200, json=self.positions)
        if path == "/v2/orders" and request.method == "GET":
            return httpx.Response(200, json=self.orders)
        if path == "/v2/orders" and request.method == "POST":
            self.posts += 1
            self.order = {
                **json.loads(request.content),
                "id": "00000000-0000-4000-8000-000000000001",
                "status": "new",
                "filled_qty": "0",
                "filled_avg_price": None,
            }
            if self.timeout:
                raise httpx.ReadTimeout("SECRET exception", request=request)
            return httpx.Response(200, json=self.order)
        if path == "/v2/orders:by_client_order_id":
            return httpx.Response(404 if self.not_found else 200, json=self.order or {})
        if request.method == "DELETE":
            return httpx.Response(204)
        raise AssertionError(path)


def lane(tmp_path, binding, broker, *, enabled=True, now=None, limits=None):
    return LiveAccountConnector(
        state_dir=tmp_path.resolve(),
        binding=binding,
        hmac_key=KEY,
        limits=limits or LiveLimits(("BTC/USD",), 1000, 10000, 1000),
        broker=AlpacaLiveTransport(
            api_key="test-key",
            secret_key="test-secret",
            transport=httpx.MockTransport(broker),
        ),
        activated=lambda: enabled,
        clock=now or (lambda: NOW),
    )


def test_live_lifecycle_restart_and_duplicate_request_are_single_submission(tmp_path):
    request, receipt, binding = bundle()
    broker = Broker()
    client = lane(tmp_path, binding, broker)
    assert client.preview(request, receipt)["order_submitted"] is False
    result = client.submit(request, receipt)
    assert result["state"] == "pending" and result["observation"]["filled_qty"] == "0"
    restarted = lane(
        tmp_path, binding, broker, enabled=False, now=lambda: NOW + timedelta(days=1)
    )
    assert restarted.submit(request, receipt) == result
    assert broker.posts == 1
    broker.order.update(status="filled", filled_qty="0.025", filled_avg_price="40001")
    reconciled = restarted.inspect(result["request_hash"], reconcile=True)
    assert reconciled["state"] == "terminal"
    assert reconciled["observation"]["filled_qty"] == "0.025"
    assert broker.posts == 1


def test_unknown_timeout_blocks_new_intents_and_404_does_not_clear_it(tmp_path):
    request, receipt, binding = bundle()
    broker = Broker()
    broker.timeout = True
    client = lane(tmp_path, binding, broker)
    result = client.submit(request, receipt)
    assert result["state"] == "uncertain" and result["resubmit_allowed"] is False
    request2, receipt2, _ = bundle(change=lambda r: r.update(request_id="new-id"))
    with pytest.raises(LiveExecutionBlocked, match="unresolved_order"):
        client.submit(request2, receipt2)
    broker.not_found = True
    with pytest.raises(LiveExecutionBlocked, match="broker_response"):
        client.inspect(result["request_hash"], reconcile=True)
    assert client.inspect(result["request_hash"])["state"] == "uncertain"
    broker.not_found = False
    assert client.inspect(result["request_hash"], reconcile=True)["state"] == "pending"
    assert broker.posts == 1 and "SECRET" not in str(result)


@pytest.mark.parametrize("enabled", [False, "true", 1, None])
def test_activation_requires_boolean_true(tmp_path, enabled):
    request, receipt, binding = bundle()
    broker = Broker()
    with pytest.raises(LiveExecutionBlocked, match="disabled"):
        lane(tmp_path, binding, broker, enabled=enabled).submit(request, receipt)
    assert broker.calls == []


@pytest.mark.parametrize(
    "failure",
    ["paper", "ineligible", "expired", "tampered", "binding", "sizing", "market"],
)
def test_invalid_or_ineligible_order_never_calls_broker(tmp_path, failure):
    def change(request):
        if failure == "sizing":
            request["order"].update(quantity=0.03)
        if failure == "market":
            request["order"].update(order_type="market", limit_price=None)

    request, receipt, binding = bundle(
        mode="paper" if failure == "paper" else "live",
        eligible=failure != "ineligible",
        change=change,
    )
    if failure == "tampered":
        receipt["integrity"]["signature"] = "0" * 64
    if failure == "binding":
        binding = replace(binding, account_id="other-account")
    broker = Broker()
    client = lane(
        tmp_path,
        binding,
        broker,
        now=lambda: NOW + timedelta(seconds=60 if failure == "expired" else 0),
    )
    with pytest.raises(LiveExecutionBlocked):
        client.submit(request, receipt)
    assert broker.calls == []


@pytest.mark.parametrize(
    "failure", ["account", "cash", "loss", "orders", "short", "stop", "expiry"]
)
def test_account_limits_and_late_stop_or_expiry_prevent_submission(tmp_path, failure):
    request, receipt, binding = bundle()
    broker = Broker()
    clock = [NOW]
    if failure == "account":
        broker.account["id"] = "another-account"
    if failure == "cash":
        broker.account["cash"] = "-1"
    if failure == "loss":
        broker.account["equity"] = "13999"
    if failure == "orders":
        broker.orders = [{"id": "pending"}]
    if failure == "short":
        broker.positions[0]["qty"] = "0.001"
    if failure == "stop":
        broker.account_hook = lambda: (tmp_path / "STOP").touch()
    if failure == "expiry":
        broker.account_hook = lambda: clock.__setitem__(0, NOW + timedelta(minutes=1))
    with pytest.raises(LiveExecutionBlocked):
        lane(tmp_path, binding, broker, now=lambda: clock[0]).submit(request, receipt)
    assert broker.posts == 0


def test_cancel_under_stop_is_not_reported_as_canceled(tmp_path):
    request, receipt, binding = bundle()
    broker = Broker()
    client = lane(tmp_path, binding, broker)
    result = client.submit(request, receipt)
    (tmp_path / "STOP").touch()
    result = client.inspect(result["request_hash"], cancel=True)
    assert result["cancel_requested"] is True
    assert result["state"] == "pending" and result["observation"]["status"] == "new"
    assert broker.calls[-1][0] == "DELETE" and broker.posts == 1


@pytest.mark.parametrize("mutation", ["side", "id", "regression", "terminal"])
def test_contradictory_broker_outcomes_preserve_previous_state(tmp_path, mutation):
    request, receipt, binding = bundle()
    broker = Broker()
    client = lane(tmp_path, binding, broker)
    result = client.submit(request, receipt)
    broker.order.update(status="filled", filled_qty="0.025", filled_avg_price="40001")
    result = client.inspect(result["request_hash"], reconcile=True)
    if mutation == "side":
        broker.order["side"] = "buy"
    if mutation == "id":
        broker.order["id"] = "00000000-0000-4000-8000-000000000002"
    if mutation == "regression":
        broker.order.update(status="partially_filled", filled_qty="0.01")
    if mutation == "terminal":
        broker.order["status"] = "canceled"
    with pytest.raises(LiveExecutionBlocked, match="identity_or_state"):
        client.inspect(result["request_hash"], reconcile=True)
    assert client.inspect(result["request_hash"]) == result


def test_reused_business_intent_and_lane_changes_are_rejected(tmp_path):
    request, receipt, binding = bundle()
    broker = Broker()
    client = lane(tmp_path, binding, broker)
    result = client.submit(request, receipt)
    broker.order.update(status="canceled")
    client.inspect(result["request_hash"], reconcile=True)
    request2, receipt2, _ = bundle(
        change=lambda r: r.update(created_at="2026-09-02T11:59:01Z")
    )
    with pytest.raises(LiveExecutionBlocked, match="already_used"):
        client.submit(request2, receipt2)
    with pytest.raises(LiveExecutionBlocked, match="identity_changed"):
        lane(tmp_path, replace(binding, tenant_id="other"), broker).inspect(
            result["request_hash"]
        )
    assert broker.posts == 1


def test_pinned_transport_rejects_redirect_and_secret_error_body():
    calls = []

    def redirect(request):
        calls.append(request)
        return httpx.Response(
            307, headers={"Location": "https://bad.example"}, text="SECRET"
        )

    broker = AlpacaLiveTransport(
        api_key="test", secret_key="secret", transport=httpx.MockTransport(redirect)
    )
    with pytest.raises(LiveExecutionBlocked, match="broker_response") as error:
        broker.call("GET", "/v2/account")
    assert "SECRET" not in str(error.value) and len(calls) == 1
    with pytest.raises(LiveExecutionBlocked, match="unsupported"):
        broker.call("GET", "https://bad.example")


@pytest.mark.parametrize("cash,gross_limit", [("500", 10000), ("10000", 8500)])
def test_buy_cannot_exceed_cash_or_total_exposure(tmp_path, cash, gross_limit):
    request, receipt, binding = bundle(change=lambda r: r["order"].update(side="buy"))
    broker = Broker()
    broker.account["cash"] = cash
    client = lane(
        tmp_path,
        binding,
        broker,
        limits=LiveLimits(("BTC/USD",), 1000, gross_limit, 1000),
    )
    with pytest.raises(LiveExecutionBlocked, match="exposure_or_cash"):
        client.submit(request, receipt)
    assert broker.posts == 0


def test_daily_budget_survives_restart_and_account_lock_blocks_overlap(tmp_path):
    from liquilens_trading_copilot.state import StateError, operator_lock

    request, receipt, binding = bundle()
    broker = Broker()
    limits = LiveLimits(("BTC/USD",), 1000, 10000, 1000, 1)
    client = lane(tmp_path, binding, broker, limits=limits)
    with operator_lock(tmp_path.resolve()), pytest.raises(StateError):
        client.submit(request, receipt)
    assert broker.posts == 0
    result = client.submit(request, receipt)
    broker.order.update(status="canceled")
    client.inspect(result["request_hash"], reconcile=True)
    request2, receipt2, _ = bundle(change=lambda r: r.update(request_id="new-decision"))
    with pytest.raises(LiveExecutionBlocked, match="daily_attempt_limit"):
        lane(tmp_path, binding, broker, limits=limits).submit(request2, receipt2)
    assert broker.posts == 1


def test_live_cli_initializes_private_disabled_state_and_refuses_overwrite(tmp_path):
    from liquilens_trading_copilot.live_cli import initialize

    directory = tmp_path.resolve() / "live"
    result = initialize(directory)
    assert (
        result["live_enabled"] is False and result["credentials_provisioned"] is False
    )
    config = json.loads((directory / "live-config.json").read_text())
    assert config["live_enabled"] is False and config["activation_acknowledgment"] == ""
    for filename in ("live-config.json", "live-secrets.json"):
        assert (directory / filename).stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        initialize(directory)


def test_live_cli_resolves_operator_storage_alias_without_broker_access(
    tmp_path, monkeypatch, capsys
):
    from liquilens_trading_copilot import live_cli
    from liquilens_trading_copilot.state import StateError

    physical = tmp_path.resolve() / "physical"
    physical.mkdir()
    alias = tmp_path / "ssd-alias"
    alias.symlink_to(physical, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv", ["liquilens-live", "init", "--state-dir", "ssd-alias/live"]
    )

    def no_broker(**_kwargs):
        pytest.fail("initialization must not create a broker transport")

    monkeypatch.setattr(live_cli, "AlpacaLiveTransport", no_broker)
    assert live_cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["initialized"] is True and result["live_enabled"] is False
    directory = physical / "live"
    assert directory.stat().st_mode & 0o777 == 0o700
    for filename in ("live-config.json", "live-secrets.json"):
        assert (directory / filename).stat().st_mode & 0o777 == 0o600
    original = (directory / "live-config.json").read_bytes()
    assert live_cli.main() == 2
    assert (directory / "live-config.json").read_bytes() == original
    with pytest.raises(StateError, match="without_symlinks"):
        live_cli.initialize(alias / "direct-api")
