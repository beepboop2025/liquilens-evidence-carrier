"""Exact-order Alpaca paper adapter behind the LiquiLens order guard."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from alpaca.common.enums import BaseURL
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    OrderRequest,
    StopLimitOrderRequest,
    StopOrderRequest,
)
from liquilens_evidence import (
    PaperTradeSafetyOrderGateway,
    ReceiptConsumer,
    TradeSafetyExecutionBinding,
    TradeSafetyOrderAuthorization,
    trade_safety_request_hash,
    validate_trade_safety_request,
)

from .journal import (
    AlpacaPaperSubmissionJournal,
    AlpacaPaperSubmissionJournalError,
    AlpacaPaperSubmissionRecord,
    AlpacaPaperSubmissionState,
)
from .journal import (
    client_order_id_for_request_hash as _journal_client_order_id,
)

_SUPPORTED_ASSET_CLASSES = frozenset({"crypto", "equity", "etf"})
_SUPPORTED_TIME_IN_FORCE = frozenset({"DAY", "GTC", "OPG", "CLS", "IOC", "FOK"})


class AlpacaPaperAdapterError(Exception):
    """Base error for adapter configuration and pre-submit enforcement."""


class AlpacaPaperConfigurationError(AlpacaPaperAdapterError):
    """The broker client or credential lane is not provably paper-only."""


class AlpacaPaperAdapterOrderUnsupported(AlpacaPaperAdapterError):
    """The exact request contains semantics this adapter cannot preserve."""


class AlpacaPaperBrokerResponseInvalid(AlpacaPaperAdapterError):
    """A broker response did not preserve the request-bound order identity."""


class AlpacaPaperAccountUnavailable(AlpacaPaperAdapterError):
    """The paper account could not be checked before claiming the receipt."""


class AlpacaPaperAccountMismatch(AlpacaPaperAdapterError):
    """The credential-bound Alpaca account differs from the receipt binding."""


class AlpacaPaperSubmissionUncertain(AlpacaPaperAdapterError):
    """Submission raised after receipt claim, so broker acceptance is unknown."""

    def __init__(
        self,
        *,
        client_order_id: str,
        request_hash: str,
        receipt_id: str,
    ) -> None:
        super().__init__(
            "Alpaca paper submission outcome is uncertain; do not resubmit. "
            f"Reconcile client_order_id={client_order_id!r}."
        )
        self.client_order_id = client_order_id
        self.request_hash = request_hash
        self.receipt_id = receipt_id


class AlpacaPaperReconciliationUnavailable(AlpacaPaperAdapterError):
    """A request-hash lookup could not establish the paper-order state."""

    def __init__(self, *, client_order_id: str) -> None:
        super().__init__(
            "Alpaca paper reconciliation is unavailable for "
            f"client_order_id={client_order_id!r}."
        )
        self.client_order_id = client_order_id


@dataclass(frozen=True, slots=True)
class AlpacaPaperSubmission:
    """A broker response with the receipt and exact-order identities retained."""

    broker_order: Any
    client_order_id: str
    request_hash: str
    receipt_id: str


@dataclass(frozen=True, slots=True)
class AlpacaPaperReconciliation:
    """A durable reconciliation result and optional fresh broker response."""

    submission: AlpacaPaperSubmissionRecord
    broker_order: Any | None


class _AlpacaClient(Protocol):
    _base_url: BaseURL | str
    _sandbox: bool

    def get_account(self) -> Any: ...

    def submit_order(self, order_data: OrderRequest) -> Any: ...

    def get_order_by_client_id(self, client_id: str) -> Any: ...


_ClientFactory = Callable[..., _AlpacaClient]


def client_order_id_for_request_hash(request_hash: str) -> str:
    """Return the stable Alpaca idempotency/reconciliation key for one request."""

    try:
        return _journal_client_order_id(request_hash)
    except AlpacaPaperSubmissionJournalError as error:
        raise AlpacaPaperAdapterError(
            "request_hash must be a lowercase SHA-256 digest"
        ) from error


def _base_url_text(value: BaseURL | str | Any) -> str:
    if isinstance(value, BaseURL):
        return value.value
    return str(value)


def _assert_paper_client(client: _AlpacaClient) -> None:
    expected = BaseURL.TRADING_PAPER.value
    if (
        getattr(client, "_sandbox", None) is not True
        or _base_url_text(getattr(client, "_base_url", "")) != expected
    ):
        raise AlpacaPaperConfigurationError(
            "the Alpaca client must be pinned to the official paper endpoint"
        )


def _account_id(value: Any) -> str:
    raw = value.get("id") if isinstance(value, Mapping) else getattr(value, "id", None)
    if raw is None or not str(raw).strip():
        raise AlpacaPaperAccountUnavailable(
            "Alpaca paper account response did not contain an account id"
        )
    return str(raw)


def _broker_order_identity(value: Any, *, client_order_id: str) -> str:
    raw_id = (
        value.get("id") if isinstance(value, Mapping) else getattr(value, "id", None)
    )
    raw_client_id = (
        value.get("client_order_id")
        if isinstance(value, Mapping)
        else getattr(value, "client_order_id", None)
    )
    broker_order_id = "" if raw_id is None else str(raw_id)
    returned_client_id = "" if raw_client_id is None else str(raw_client_id)
    if (
        not broker_order_id
        or broker_order_id != broker_order_id.strip()
        or len(broker_order_id.encode("utf-8")) > 256
    ):
        raise AlpacaPaperBrokerResponseInvalid(
            "Alpaca paper response did not contain a bounded broker order id"
        )
    if returned_client_id != client_order_id:
        raise AlpacaPaperBrokerResponseInvalid(
            "Alpaca paper response client_order_id differs from the request binding"
        )
    return broker_order_id


def _order_request(
    order: Mapping[str, Any],
    *,
    client_order_id: str,
) -> OrderRequest:
    instrument = order["instrument"]
    asset_class = instrument["asset_class"]
    if asset_class not in _SUPPORTED_ASSET_CLASSES:
        raise AlpacaPaperAdapterOrderUnsupported(
            f"asset_class={asset_class!r} is not supported by this adapter"
        )
    if instrument["identifiers"]:
        raise AlpacaPaperAdapterOrderUnsupported(
            "instrument identifiers cannot yet be preserved in an Alpaca order"
        )
    symbol = instrument["symbol"]
    if symbol != symbol.strip():
        raise AlpacaPaperAdapterOrderUnsupported(
            "instrument symbol cannot contain surrounding whitespace"
        )
    if order["venue"] is not None:
        raise AlpacaPaperAdapterOrderUnsupported(
            "venue-specific routing is not supported by the Alpaca paper adapter"
        )
    if order["notional"]["currency"] != "USD":
        raise AlpacaPaperAdapterOrderUnsupported(
            "Alpaca paper order notional must be denominated in USD"
        )

    time_in_force_text = order["time_in_force"].upper()
    if time_in_force_text not in _SUPPORTED_TIME_IN_FORCE:
        raise AlpacaPaperAdapterOrderUnsupported(
            f"time_in_force={order['time_in_force']!r} is not supported by Alpaca"
        )
    time_in_force = TimeInForce(time_in_force_text.lower())
    side = OrderSide(order["side"])
    quantity = order["quantity"]
    sizing: dict[str, float] = (
        {"qty": float(quantity)}
        if quantity is not None
        else {"notional": float(order["notional"]["amount"])}
    )
    common: dict[str, Any] = {
        "symbol": symbol,
        "side": side,
        "time_in_force": time_in_force,
        "client_order_id": client_order_id,
        **sizing,
    }

    order_type = order["order_type"]
    if order_type == "market":
        return MarketOrderRequest(**common)
    if order_type == "limit":
        return LimitOrderRequest(limit_price=order["limit_price"], **common)
    if order_type == "stop":
        return StopOrderRequest(stop_price=order["stop_price"], **common)
    if order_type == "stop_limit":
        return StopLimitOrderRequest(
            limit_price=order["limit_price"],
            stop_price=order["stop_price"],
            **common,
        )
    raise AlpacaPaperAdapterOrderUnsupported(
        f"order_type={order_type!r} is not supported by this adapter"
    )


class AlpacaPaperTradeSafetyGateway:
    """The only paper-order capability exposed to a trading agent.

    Credentials and the raw ``TradingClient`` remain private. The constructor
    creates an official Alpaca client with ``paper=True`` and refuses a client
    whose resolved base URL or sandbox marker differs from the paper endpoint.
    """

    def __init__(
        self,
        *,
        binding: TradeSafetyExecutionBinding,
        receipt_consumer: ReceiptConsumer | None = None,
        submission_journal: AlpacaPaperSubmissionJournal | None = None,
        hmac_key: bytes,
        api_key: str | None = None,
        secret_key: str | None = None,
        oauth_token: str | None = None,
        clock: Callable[[], datetime],
        _client_factory: _ClientFactory = TradingClient,
    ) -> None:
        try:
            client = _client_factory(
                api_key=api_key,
                secret_key=secret_key,
                oauth_token=oauth_token,
                paper=True,
                raw_data=False,
            )
        except Exception as error:
            raise AlpacaPaperConfigurationError(
                "could not initialize the Alpaca paper credential lane"
            ) from error
        _assert_paper_client(client)
        if (receipt_consumer is None) == (submission_journal is None):
            raise AlpacaPaperConfigurationError(
                "provide exactly one receipt_consumer or submission_journal"
            )
        self._binding = binding
        self._client = client
        self._submission_journal = submission_journal
        consumer = (
            submission_journal if submission_journal is not None else receipt_consumer
        )
        assert consumer is not None
        self._guard: PaperTradeSafetyOrderGateway[AlpacaPaperSubmission] = (
            PaperTradeSafetyOrderGateway(
                self._submit_authorized,
                binding=binding,
                receipt_consumer=consumer,
                hmac_key=hmac_key,
                clock=clock,
            )
        )

    def _verify_account_binding(self) -> None:
        try:
            account = self._client.get_account()
            actual_account_id = _account_id(account)
        except AlpacaPaperAccountUnavailable:
            raise
        except Exception as error:
            raise AlpacaPaperAccountUnavailable(
                "could not read the Alpaca paper account before submission"
            ) from error
        if actual_account_id != self._binding.account_id:
            raise AlpacaPaperAccountMismatch(
                "Alpaca paper credentials resolve to account "
                f"{actual_account_id!r}, not bound account {self._binding.account_id!r}"
            )

    def _submit_authorized(
        self, authorization: TradeSafetyOrderAuthorization
    ) -> AlpacaPaperSubmission:
        client_order_id = client_order_id_for_request_hash(authorization.request_hash)
        order_data = _order_request(
            authorization.order,
            client_order_id=client_order_id,
        )
        if self._submission_journal is not None:
            self._submission_journal.begin_submission(
                receipt_id=authorization.receipt_id,
                request_hash=authorization.request_hash,
                client_order_id=client_order_id,
            )
        try:
            broker_order = self._client.submit_order(order_data=order_data)
            broker_order_id = _broker_order_identity(
                broker_order, client_order_id=client_order_id
            )
            if self._submission_journal is not None:
                self._submission_journal.mark_submitted(
                    receipt_id=authorization.receipt_id,
                    request_hash=authorization.request_hash,
                    client_order_id=client_order_id,
                    broker_order_id=broker_order_id,
                )
        except Exception as error:
            if self._submission_journal is not None:
                # ``submitting`` is itself a sticky uncertain state.  Never
                # replace the original post-attempt error or resubmit.
                with suppress(Exception):
                    self._submission_journal.mark_uncertain(
                        receipt_id=authorization.receipt_id,
                        request_hash=authorization.request_hash,
                        client_order_id=client_order_id,
                        error_type=type(error).__name__,
                    )
            raise AlpacaPaperSubmissionUncertain(
                client_order_id=client_order_id,
                request_hash=authorization.request_hash,
                receipt_id=authorization.receipt_id,
            ) from error
        return AlpacaPaperSubmission(
            broker_order=broker_order,
            client_order_id=client_order_id,
            request_hash=authorization.request_hash,
            receipt_id=authorization.receipt_id,
        )

    def submit(
        self,
        proposed_request: Mapping[str, Any],
        receipt: Mapping[str, Any],
    ) -> AlpacaPaperSubmission:
        """Check adapter semantics and account before consuming and submitting."""

        normalized = validate_trade_safety_request(proposed_request)
        if normalized["mode"] != "paper":
            return self._guard.submit(proposed_request, receipt)
        request_hash = trade_safety_request_hash(normalized)
        _order_request(
            normalized["order"],
            client_order_id=client_order_id_for_request_hash(request_hash),
        )
        self._verify_account_binding()
        return self._guard.submit(proposed_request, receipt)

    def reconcile(self, request_hash: str) -> Any | AlpacaPaperReconciliation:
        """Read and durably reconcile one request identity without resubmitting."""

        client_order_id = client_order_id_for_request_hash(request_hash)
        if self._submission_journal is not None:
            record = self._submission_journal.get(request_hash)
            if record is None:
                raise AlpacaPaperReconciliationUnavailable(
                    client_order_id=client_order_id
                )
            if record.state is AlpacaPaperSubmissionState.RECONCILED:
                return AlpacaPaperReconciliation(
                    submission=record,
                    broker_order=None,
                )
            if record.state is AlpacaPaperSubmissionState.CLAIMED:
                reconciled = self._submission_journal.mark_reconciled(
                    request_hash=request_hash,
                    resolution="not_submitted",
                )
                return AlpacaPaperReconciliation(
                    submission=reconciled,
                    broker_order=None,
                )
        self._verify_account_binding()
        try:
            broker_order = self._client.get_order_by_client_id(client_order_id)
            broker_order_id = _broker_order_identity(
                broker_order, client_order_id=client_order_id
            )
            if self._submission_journal is None:
                return broker_order
            reconciled = self._submission_journal.mark_reconciled(
                request_hash=request_hash,
                resolution="broker_order_found",
                broker_order_id=broker_order_id,
            )
            return AlpacaPaperReconciliation(
                submission=reconciled,
                broker_order=broker_order,
            )
        except Exception as error:
            raise AlpacaPaperReconciliationUnavailable(
                client_order_id=client_order_id
            ) from error

    def recovery_candidates(
        self, *, limit: int = 100
    ) -> tuple[AlpacaPaperSubmissionRecord, ...]:
        """List durable entries needing operator reconciliation."""

        if self._submission_journal is None:
            raise AlpacaPaperConfigurationError(
                "recovery candidates require a durable submission journal"
            )
        return self._submission_journal.recovery_candidates(limit=limit)


__all__ = [
    "AlpacaPaperAccountMismatch",
    "AlpacaPaperAccountUnavailable",
    "AlpacaPaperAdapterError",
    "AlpacaPaperAdapterOrderUnsupported",
    "AlpacaPaperBrokerResponseInvalid",
    "AlpacaPaperConfigurationError",
    "AlpacaPaperReconciliation",
    "AlpacaPaperReconciliationUnavailable",
    "AlpacaPaperSubmission",
    "AlpacaPaperSubmissionUncertain",
    "AlpacaPaperTradeSafetyGateway",
    "client_order_id_for_request_hash",
]
