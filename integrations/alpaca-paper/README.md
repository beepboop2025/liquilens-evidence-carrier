# LiquiLens Alpaca paper adapter

This package is the first concrete broker reference path for LiquiLens Trade
Safety Receipts. It exposes one capability—submit an exact **paper** order after
the existing order guard verifies an authenticated, unexpired, one-time,
operator-bound `pass` receipt. It cannot route live orders.

The adapter deliberately keeps the raw Alpaca `TradingClient` and credentials
outside the agent's tool inventory. Before claiming a receipt it:

1. validates the canonical request and rejects semantics it cannot preserve;
2. verifies that the SDK client resolved to Alpaca's official paper endpoint;
3. reads the credential-bound paper account and matches it to the receipt's
   `account_id`; and
4. delegates exact-request, HMAC, policy, expiry, outcome, and replay checks to
   `PaperTradeSafetyOrderGateway`.

Only then does it translate the guard's sealed order snapshot to an Alpaca
request model and call `submit_order`.

## Supported surface

- Asset classes: `equity`, `etf`, and `crypto`
- Sides: `buy` and `sell`
- Orders: `market`, `limit`, `stop`, and `stop_limit`
- Time in force: `DAY`, `GTC`, `OPG`, `CLS`, `IOC`, and `FOK`
- Currency: USD
- Sizing: exact `quantity` when present; otherwise the request's USD notional

Venue-specific routing and non-empty alternate identifiers fail closed because
the Alpaca request would otherwise discard execution-material semantics.
Alpaca still applies its own account, asset, buying-power, session, and order
validation.

## Operator-owned setup

```python
import os
from datetime import UTC, datetime

from liquilens_alpaca_paper import AlpacaPaperTradeSafetyGateway
from liquilens_evidence import TradeSafetyExecutionBinding

# Implement the small ReceiptConsumer protocol with a durable atomic store.
my_durable_receipt_consumer = ...

gateway = AlpacaPaperTradeSafetyGateway(
    binding=TradeSafetyExecutionBinding(
        # Must be the id returned by Alpaca GET /v2/account.
        account_id=os.environ["ALPACA_PAPER_ACCOUNT_ID"],
        tenant_id="tenant-123",
        operator_id="operator-123",
        agent_id="research-agent-7",
        runtime="operator-runtime/1.0",
        strategy_id="strategy-42",
        policy_id="desk-paper-policy",
        policy_version="1.0.0",
        policy_hash=os.environ["TRADE_SAFETY_POLICY_SHA256"],
        issuer_name="operator-gateway",
        issuer_version="1.0.0",
        issuer_endpoint="https://operator.example/trade-safety",
        hmac_key_id="operator-paper-key-v1",
    ),
    receipt_consumer=my_durable_receipt_consumer,
    hmac_key=os.environ["TRADE_SAFETY_HMAC_KEY"].encode(),
    api_key=os.environ["ALPACA_PAPER_API_KEY"],
    secret_key=os.environ["ALPACA_PAPER_SECRET_KEY"],
    clock=lambda: datetime.now(UTC),
)

# This is the only order action the agent receives.
submission = gateway.submit(proposed_request, trade_safety_receipt)
```

The durable consumer above is an operator implementation of the package's
`ReceiptConsumer` protocol. The bundled in-memory consumer is for one-process
tests and demonstrations only.

## Ambiguous submissions

If the broker call raises after the receipt has been claimed, the adapter raises
`AlpacaPaperSubmissionUncertain`. Never submit again. Its `client_order_id` is
deterministically derived from the request hash (`llts-<sha256>`), so the
operator can call `gateway.reconcile(request_hash)` without creating a second
order. A production deployment must persist the claim and submission state and
continue reconciliation across process restarts.

## Boundary

This is a paper-integration reference, not investment advice, an execution
recommendation, a promise of fill, or evidence of production broker adoption.
It does not weaken Alpaca's controls, grant an agent custody, or make current
LiquiLens evidence eligible for live capital.

## Development

```bash
uv sync --project integrations/alpaca-paper --locked --extra test
uv run --project integrations/alpaca-paper --locked --extra test pytest
uv run --project integrations/alpaca-paper --locked --extra test ruff check \
  integrations/alpaca-paper/src integrations/alpaca-paper/tests
uv run --project integrations/alpaca-paper --locked --extra test mypy \
  integrations/alpaca-paper/src
```
