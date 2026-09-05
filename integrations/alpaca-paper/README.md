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
from pathlib import Path

from liquilens_alpaca_paper import (
    AlpacaPaperTradeSafetyGateway,
    SQLiteAlpacaPaperSubmissionJournal,
)
from liquilens_evidence import TradeSafetyExecutionBinding

# Keep this file on a durable, backed-up local filesystem. The parent directory
# must already exist; the journal secures its SQLite, WAL, and SHM files to 0600.
submission_journal = SQLiteAlpacaPaperSubmissionJournal(
    Path("/var/lib/operator/liquilens/alpaca-paper-submissions.sqlite3")
)

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
    submission_journal=submission_journal,
    hmac_key=os.environ["TRADE_SAFETY_HMAC_KEY"].encode(),
    api_key=os.environ["ALPACA_PAPER_API_KEY"],
    secret_key=os.environ["ALPACA_PAPER_SECRET_KEY"],
    clock=lambda: datetime.now(UTC),
)

# This is the only order action the agent receives.
submission = gateway.submit(proposed_request, trade_safety_receipt)
```

The journal is both the atomic one-time receipt consumer and the submission
state machine. Its `receipt_id`, canonical request hash, submission id, and
deterministic Alpaca `client_order_id` are permanently unique. It uses SQLite
WAL mode with `synchronous=FULL`; the broker call cannot begin until the
`submitting` transition commits. Keep one journal for the full lifetime of the
operator service, monitor `submission_journal.counts()`, back up the database
with a SQLite-aware snapshot procedure, and close it during an orderly
shutdown.

The legacy `receipt_consumer=` constructor lane remains compatible for local
tests and demonstrations, but it does not provide restart-safe submission
state. Do not use `InMemoryReceiptConsumer` for an operator service.

## Durable state and crash recovery

| Persisted state | What it proves | Allowed next action |
| --- | --- | --- |
| `claimed` | The receipt is consumed; no broker call began | Reconcile locally to `not_submitted` |
| `submitting` | The one allowed broker attempt began; acceptance may be unknown | Broker lookup only |
| `submitted` | A response with the exact client id and a broker order id was persisted | Broker lookup only |
| `uncertain` | The broker call or post-call persistence failed | Broker lookup only |
| `reconciled` | The final lookup/local resolution was persisted | Read the record; never submit again |

On process startup, call `gateway.recovery_candidates()` and reconcile every
returned request hash. A lingering `claimed` record is resolved locally because
the journal proves the call never began. Every other candidate is looked up by
its deterministic client order id:

```python
for candidate in gateway.recovery_candidates():
    result = gateway.reconcile(candidate.request_hash)
    record = result.submission
    operator_log.info(
        "alpaca paper submission reconciled",
        extra={
            "submission_id": record.submission_id,
            "state": record.state,
            "resolution": record.reconciliation_resolution,
        },
    )
```

If lookup is unavailable, `reconcile` raises
`AlpacaPaperReconciliationUnavailable` and leaves the durable state unchanged
for a later lookup. It never calls `submit_order`. Do not delete or recycle
journal rows to regain capacity: they are the permanent replay boundary. Move
to a new, explicitly migrated journal only under an operator-reviewed retention
and identity-continuity procedure.

## Ambiguous submissions

If the broker call raises after the durable `submitting` transition, the adapter
raises `AlpacaPaperSubmissionUncertain`. Never submit again. Its
`client_order_id` is deterministically derived from the request hash
(`llts-<sha256>`), so the operator can call `gateway.reconcile(request_hash)`
without creating a second order. A failure while persisting the response also
remains sticky as `submitting`; this is intentionally treated as uncertain.

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
