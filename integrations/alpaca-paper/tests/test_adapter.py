from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alpaca.common.enums import BaseURL
from liquilens_evidence import (
    InMemoryReceiptConsumer,
    TradeSafetyExecutionBinding,
    TradeSafetyOrderBlocked,
    issue_trade_safety_receipt,
    trade_safety_request_hash,
)

from liquilens_alpaca_paper import (
    AlpacaPaperAccountMismatch,
    AlpacaPaperAccountUnavailable,
    AlpacaPaperAdapterError,
    AlpacaPaperAdapterOrderUnsupported,
    AlpacaPaperConfigurationError,
    AlpacaPaperReconciliation,
    AlpacaPaperReconciliationUnavailable,
    AlpacaPaperSubmissionJournalError,
    AlpacaPaperSubmissionState,
    AlpacaPaperSubmissionUncertain,
    AlpacaPaperTradeSafetyGateway,
    SQLiteAlpacaPaperSubmissionJournal,
    client_order_id_for_request_hash,
)

ROOT = Path(__file__).resolve().parents[3]
EVALUATED_AT = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
HMAC_KEY = b"operator-owned-alpaca-paper-test-key"


def _json(name: str) -> dict[str, Any]:
    value = json.loads(
        (ROOT / "examples" / "trade-safety" / name).read_text(encoding="utf-8")
    )
    assert isinstance(value, dict)
    return value


def _receipt(
    *, order_change: dict[str, Any] | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    request = _json("request.paper.json")
    if order_change is not None:
        request["order"].update(order_change)
    request_hash = trade_safety_request_hash(request)
    evidence = _json("evidence.paper.json")
    for product in ("seiche", "undertow", "liquilens"):
        evidence[product]["request_hash"] = request_hash
    broker_preview = _json("broker-preview.paper.json")
    broker_preview["request_hash"] = request_hash
    receipt = issue_trade_safety_receipt(
        request=request,
        evidence=evidence,
        policy=_json("policy.paper.json"),
        broker_preview=broker_preview,
        evaluated_at=EVALUATED_AT,
        issuer=_json("issuer.paper.json"),
        ttl_seconds=60,
        hmac_key=HMAC_KEY,
        hmac_key_id="operator-paper-key-v1",
    )
    assert receipt["decision"]["outcome"] == "pass"
    return request, receipt


def _binding(receipt: dict[str, Any]) -> TradeSafetyExecutionBinding:
    request = receipt["request"]
    return TradeSafetyExecutionBinding(
        account_id=request["agent"]["account_id"],
        tenant_id=request["agent"]["tenant_id"],
        operator_id=request["agent"]["operator_id"],
        agent_id=request["agent"]["agent_id"],
        runtime=request["agent"]["runtime"],
        strategy_id=request["agent"]["strategy_id"],
        policy_id=receipt["policy"]["policy_id"],
        policy_version=receipt["policy"]["version"],
        policy_hash=receipt["policy_hash"],
        issuer_name=receipt["issuer"]["name"],
        issuer_version=receipt["issuer"]["version"],
        issuer_endpoint=receipt["issuer"]["endpoint"],
        hmac_key_id=receipt["integrity"]["key_id"],
    )


class FakeAlpacaClient:
    def __init__(self, *, account_id: str = "example-paper-account") -> None:
        self._base_url = BaseURL.TRADING_PAPER
        self._sandbox = True
        self.account_id = account_id
        self.account_reads = 0
        self.submitted: list[Any] = []
        self.lookups: list[str] = []
        self.account_error: Exception | None = None
        self.submit_error: Exception | None = None
        self.submit_response: dict[str, str] | None = None
        self.lookup_error: Exception | None = None

    def get_account(self) -> dict[str, str]:
        self.account_reads += 1
        if self.account_error is not None:
            raise self.account_error
        return {"id": self.account_id}

    def submit_order(self, order_data: Any) -> dict[str, str]:
        self.submitted.append(order_data)
        if self.submit_error is not None:
            raise self.submit_error
        if self.submit_response is not None:
            return self.submit_response
        return {"id": "paper-order-001", "client_order_id": order_data.client_order_id}

    def get_order_by_client_id(self, client_id: str) -> dict[str, str]:
        self.lookups.append(client_id)
        if self.lookup_error is not None:
            raise self.lookup_error
        return {"id": "paper-order-001", "client_order_id": client_id}


def _gateway(
    receipt: dict[str, Any],
    client: FakeAlpacaClient,
    *,
    factory_calls: list[dict[str, Any]] | None = None,
) -> AlpacaPaperTradeSafetyGateway:
    calls = factory_calls if factory_calls is not None else []

    def factory(**kwargs: Any) -> FakeAlpacaClient:
        calls.append(kwargs)
        return client

    def clock() -> datetime:
        return EVALUATED_AT + timedelta(seconds=30)

    return AlpacaPaperTradeSafetyGateway(
        binding=_binding(receipt),
        receipt_consumer=InMemoryReceiptConsumer(clock=clock),
        hmac_key=HMAC_KEY,
        api_key="paper-key",
        secret_key="paper-secret",
        clock=clock,
        _client_factory=factory,
    )


def _durable_gateway(
    receipt: dict[str, Any],
    client: FakeAlpacaClient,
    path: Path,
) -> tuple[AlpacaPaperTradeSafetyGateway, SQLiteAlpacaPaperSubmissionJournal]:
    def factory(**kwargs: Any) -> FakeAlpacaClient:
        del kwargs
        return client

    def clock() -> datetime:
        return EVALUATED_AT + timedelta(seconds=30)

    journal = SQLiteAlpacaPaperSubmissionJournal(path, clock=clock)
    gateway = AlpacaPaperTradeSafetyGateway(
        binding=_binding(receipt),
        submission_journal=journal,
        hmac_key=HMAC_KEY,
        api_key="paper-key",
        secret_key="paper-secret",
        clock=clock,
        _client_factory=factory,
    )
    return gateway, journal


def test_success_uses_sealed_order_and_request_hash_client_id() -> None:
    request, receipt = _receipt()
    client = FakeAlpacaClient()
    factory_calls: list[dict[str, Any]] = []
    gateway = _gateway(receipt, client, factory_calls=factory_calls)

    result = gateway.submit(request, receipt)

    request_hash = trade_safety_request_hash(request)
    expected_id = client_order_id_for_request_hash(request_hash)
    assert result.client_order_id == expected_id
    assert result.request_hash == request_hash
    assert result.receipt_id == receipt["receipt_id"]
    assert result.broker_order["id"] == "paper-order-001"
    assert client.account_reads == 1
    assert len(client.submitted) == 1
    assert client.submitted[0].to_request_fields() == {
        "symbol": "BTC/USD",
        "qty": 0.025,
        "side": "sell",
        "type": "market",
        "time_in_force": "ioc",
        "client_order_id": expected_id,
    }
    assert factory_calls == [
        {
            "api_key": "paper-key",
            "secret_key": "paper-secret",
            "oauth_token": None,
            "paper": True,
            "raw_data": False,
        }
    ]


@pytest.mark.parametrize(
    ("order_change", "expected"),
    (
        (
            {"order_type": "limit", "limit_price": 39_000.0},
            {"type": "limit", "limit_price": 39_000.0},
        ),
        (
            {"order_type": "stop", "stop_price": 37_500.0},
            {"type": "stop", "stop_price": 37_500.0},
        ),
        (
            {
                "order_type": "stop_limit",
                "limit_price": 37_000.0,
                "stop_price": 37_500.0,
            },
            {
                "type": "stop_limit",
                "limit_price": 37_000.0,
                "stop_price": 37_500.0,
            },
        ),
    ),
)
def test_supported_order_types_preserve_prices(
    order_change: dict[str, Any], expected: dict[str, Any]
) -> None:
    request, receipt = _receipt(order_change=order_change)
    client = FakeAlpacaClient()

    _gateway(receipt, client).submit(request, receipt)

    fields = client.submitted[0].to_request_fields()
    for key, value in expected.items():
        assert fields[key] == value


def test_account_mismatch_does_not_claim_receipt() -> None:
    request, receipt = _receipt()
    client = FakeAlpacaClient(account_id="wrong-paper-account")
    gateway = _gateway(receipt, client)

    with pytest.raises(AlpacaPaperAccountMismatch):
        gateway.submit(request, receipt)
    assert client.submitted == []

    client.account_id = "example-paper-account"
    assert gateway.submit(request, receipt).broker_order["id"] == "paper-order-001"


def test_account_outage_does_not_claim_receipt() -> None:
    request, receipt = _receipt()
    client = FakeAlpacaClient()
    client.account_error = TimeoutError("account endpoint timed out")
    gateway = _gateway(receipt, client)

    with pytest.raises(AlpacaPaperAccountUnavailable):
        gateway.submit(request, receipt)

    client.account_error = None
    assert gateway.submit(request, receipt).broker_order["id"] == "paper-order-001"


@pytest.mark.parametrize(
    "order_change",
    (
        {"venue": "NYSE"},
        {"time_in_force": "UNKNOWN"},
        {"order_type": "other"},
        {"notional": {"amount": 1000.0, "currency": "EUR"}},
        {
            "instrument": {
                "asset_class": "fixed_income",
                "symbol": "US912810TM09",
                "identifiers": {},
            }
        },
    ),
)
def test_unmapped_semantics_fail_before_account_or_broker(
    order_change: dict[str, Any],
) -> None:
    request, receipt = _receipt()
    changed = copy.deepcopy(request)
    changed["order"].update(order_change)
    client = FakeAlpacaClient()

    with pytest.raises(AlpacaPaperAdapterOrderUnsupported):
        _gateway(receipt, client).submit(changed, receipt)
    assert client.account_reads == 0
    assert client.submitted == []


def test_live_request_never_reaches_alpaca() -> None:
    request, receipt = _receipt()
    request["mode"] = "live"
    request["agent"]["authorization_scope"] = ["evidence:read", "orders:live"]
    client = FakeAlpacaClient()

    with pytest.raises(TradeSafetyOrderBlocked) as caught:
        _gateway(receipt, client).submit(request, receipt)
    assert caught.value.reason_code == "mode_not_supported"
    assert client.account_reads == 0
    assert client.submitted == []


def test_mutation_and_replay_never_submit_twice() -> None:
    request, receipt = _receipt()
    client = FakeAlpacaClient()
    gateway = _gateway(receipt, client)
    changed = copy.deepcopy(request)
    changed["order"]["quantity"] = 1.0

    with pytest.raises(TradeSafetyOrderBlocked) as caught:
        gateway.submit(changed, receipt)
    assert caught.value.reason_code == "request_mismatch"
    assert client.submitted == []

    gateway.submit(request, receipt)
    with pytest.raises(TradeSafetyOrderBlocked) as caught:
        gateway.submit(request, receipt)
    assert caught.value.reason_code == "receipt_replay"
    assert len(client.submitted) == 1


def test_timeout_is_uncertain_and_reconciles_without_resubmit() -> None:
    request, receipt = _receipt()
    client = FakeAlpacaClient()
    client.submit_error = TimeoutError("response lost after POST")
    gateway = _gateway(receipt, client)
    request_hash = trade_safety_request_hash(request)

    with pytest.raises(AlpacaPaperSubmissionUncertain) as caught:
        gateway.submit(request, receipt)
    assert caught.value.request_hash == request_hash
    assert caught.value.receipt_id == receipt["receipt_id"]

    client.submit_error = None
    reconciled = gateway.reconcile(request_hash)
    assert reconciled["client_order_id"] == caught.value.client_order_id
    assert client.lookups == [caught.value.client_order_id]
    with pytest.raises(TradeSafetyOrderBlocked) as replay:
        gateway.submit(request, receipt)
    assert replay.value.reason_code == "receipt_replay"
    assert len(client.submitted) == 1


def test_durable_submission_survives_restart_and_never_resubmits(
    tmp_path: Path,
) -> None:
    request, receipt = _receipt()
    request_hash = trade_safety_request_hash(request)
    path = tmp_path / "submission.sqlite3"
    client = FakeAlpacaClient()
    gateway, journal = _durable_gateway(receipt, client, path)

    submitted = gateway.submit(request, receipt)
    record = journal.get(request_hash)
    assert record is not None
    assert record.state is AlpacaPaperSubmissionState.SUBMITTED
    assert record.submit_attempts == 1
    assert record.broker_order_id == submitted.broker_order["id"]
    journal.close()

    restarted, reopened = _durable_gateway(receipt, client, path)
    try:
        with pytest.raises(TradeSafetyOrderBlocked) as replay:
            restarted.submit(request, receipt)
        assert replay.value.reason_code == "receipt_replay"
        assert len(client.submitted) == 1

        reconciled = restarted.reconcile(request_hash)
        assert isinstance(reconciled, AlpacaPaperReconciliation)
        assert reconciled.submission.state is AlpacaPaperSubmissionState.RECONCILED
        assert reconciled.submission.broker_order_id == "paper-order-001"
        assert reconciled.broker_order == {
            "id": "paper-order-001",
            "client_order_id": client_order_id_for_request_hash(request_hash),
        }
        assert restarted.recovery_candidates() == ()
        assert len(client.submitted) == 1
    finally:
        reopened.close()


def test_uncertain_submission_is_recovered_by_lookup_not_resubmit(
    tmp_path: Path,
) -> None:
    request, receipt = _receipt()
    request_hash = trade_safety_request_hash(request)
    path = tmp_path / "submission.sqlite3"
    client = FakeAlpacaClient()
    client.submit_error = TimeoutError("response lost after POST")
    gateway, journal = _durable_gateway(receipt, client, path)

    with pytest.raises(AlpacaPaperSubmissionUncertain):
        gateway.submit(request, receipt)
    uncertain = journal.get(request_hash)
    assert uncertain is not None
    assert uncertain.state is AlpacaPaperSubmissionState.UNCERTAIN
    assert uncertain.last_error_type == "TimeoutError"
    assert uncertain.submit_attempts == 1
    journal.close()

    client.submit_error = None
    restarted, reopened = _durable_gateway(receipt, client, path)
    try:
        with pytest.raises(TradeSafetyOrderBlocked):
            restarted.submit(request, receipt)
        reconciled = restarted.reconcile(request_hash)
        assert isinstance(reconciled, AlpacaPaperReconciliation)
        assert reconciled.submission.state is AlpacaPaperSubmissionState.RECONCILED
        assert client.lookups == [client_order_id_for_request_hash(request_hash)]
        assert len(client.submitted) == 1
    finally:
        reopened.close()


def test_mismatched_broker_identity_is_uncertain_and_never_resubmitted(
    tmp_path: Path,
) -> None:
    request, receipt = _receipt()
    request_hash = trade_safety_request_hash(request)
    client = FakeAlpacaClient()
    client.submit_response = {
        "id": "paper-order-001",
        "client_order_id": "different-order-identity",
    }
    gateway, journal = _durable_gateway(
        receipt, client, tmp_path / "submission.sqlite3"
    )
    try:
        with pytest.raises(AlpacaPaperSubmissionUncertain):
            gateway.submit(request, receipt)
        record = journal.get(request_hash)
        assert record is not None
        assert record.state is AlpacaPaperSubmissionState.UNCERTAIN
        assert record.last_error_type == "AlpacaPaperBrokerResponseInvalid"
        assert len(client.submitted) == 1

        with pytest.raises(TradeSafetyOrderBlocked):
            gateway.submit(request, receipt)
        client.submit_response = None
        result = gateway.reconcile(request_hash)
        assert isinstance(result, AlpacaPaperReconciliation)
        assert result.submission.state is AlpacaPaperSubmissionState.RECONCILED
        assert len(client.submitted) == 1
    finally:
        journal.close()


def test_reconciliation_outage_preserves_uncertain_state_for_later_lookup(
    tmp_path: Path,
) -> None:
    request, receipt = _receipt()
    request_hash = trade_safety_request_hash(request)
    client = FakeAlpacaClient()
    client.submit_error = TimeoutError("response lost after POST")
    gateway, journal = _durable_gateway(
        receipt, client, tmp_path / "submission.sqlite3"
    )
    try:
        with pytest.raises(AlpacaPaperSubmissionUncertain):
            gateway.submit(request, receipt)
        client.submit_error = None
        client.lookup_error = TimeoutError("lookup temporarily unavailable")
        with pytest.raises(AlpacaPaperReconciliationUnavailable):
            gateway.reconcile(request_hash)
        still_uncertain = journal.get(request_hash)
        assert still_uncertain is not None
        assert still_uncertain.state is AlpacaPaperSubmissionState.UNCERTAIN
        assert len(client.submitted) == 1

        client.lookup_error = None
        reconciled = gateway.reconcile(request_hash)
        assert isinstance(reconciled, AlpacaPaperReconciliation)
        assert reconciled.submission.state is AlpacaPaperSubmissionState.RECONCILED
        assert len(client.submitted) == 1
    finally:
        journal.close()


def test_journal_failure_after_broker_call_stays_sticky_submitting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request, receipt = _receipt()
    request_hash = trade_safety_request_hash(request)
    path = tmp_path / "submission.sqlite3"
    client = FakeAlpacaClient()
    gateway, journal = _durable_gateway(receipt, client, path)

    def unavailable(**kwargs: Any) -> None:
        del kwargs
        raise AlpacaPaperSubmissionJournalError(
            "journal_unavailable", "simulated durable write loss"
        )

    monkeypatch.setattr(journal, "mark_submitted", unavailable)
    monkeypatch.setattr(journal, "mark_uncertain", unavailable)
    with pytest.raises(AlpacaPaperSubmissionUncertain):
        gateway.submit(request, receipt)
    record = journal.get(request_hash)
    assert record is not None
    assert record.state is AlpacaPaperSubmissionState.SUBMITTING
    assert record.submit_attempts == 1
    assert len(client.submitted) == 1
    journal.close()

    restarted, reopened = _durable_gateway(receipt, client, path)
    try:
        with pytest.raises(TradeSafetyOrderBlocked):
            restarted.submit(request, receipt)
        reconciled = restarted.reconcile(request_hash)
        assert isinstance(reconciled, AlpacaPaperReconciliation)
        assert reconciled.submission.state is AlpacaPaperSubmissionState.RECONCILED
        assert len(client.submitted) == 1
    finally:
        reopened.close()


def test_live_request_never_creates_durable_claim(tmp_path: Path) -> None:
    request, receipt = _receipt()
    paper_hash = trade_safety_request_hash(request)
    request["mode"] = "live"
    request["agent"]["authorization_scope"] = ["evidence:read", "orders:live"]
    client = FakeAlpacaClient()
    gateway, journal = _durable_gateway(
        receipt, client, tmp_path / "submission.sqlite3"
    )
    try:
        with pytest.raises(TradeSafetyOrderBlocked) as caught:
            gateway.submit(request, receipt)
        assert caught.value.reason_code == "mode_not_supported"
        assert journal.get(paper_hash) is None
        assert sum(journal.counts().values()) == 0
        assert client.account_reads == 0
        assert client.submitted == []
    finally:
        journal.close()


def test_constructor_rejects_client_not_pinned_to_paper() -> None:
    _, receipt = _receipt()
    client = FakeAlpacaClient()
    client._sandbox = False
    client._base_url = BaseURL.TRADING_LIVE

    with pytest.raises(AlpacaPaperConfigurationError):
        _gateway(receipt, client)


def test_request_hash_client_id_rejects_non_digest() -> None:
    with pytest.raises(AlpacaPaperAdapterError):
        client_order_id_for_request_hash("not-a-digest")
