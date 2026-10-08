# Customer-owned live broker connector

This is a separate integration candidate for customers embedding execution in
their own authenticated service. It contains real Alpaca live API calls but is
disabled on initialization. It has been tested with synthetic receipts and HTTP
broker doubles, not activated or qualified against a live account. The existing
paper host and public evidence MCP cannot invoke it.

## Supported contract

- A dedicated, customer-owned USD Alpaca account and one durable state directory.
- Long-only quantity-sized limit orders: DAY for equities/ETFs, IOC for crypto.
  Symbols and per-order, gross-exposure, daily-loss and daily-attempt limits are
  operator configuration. No shorting, market orders, leverage, transfer, order
  replacement or automatic retry is supported.
- A current HMAC-authenticated **live** Trade Safety Receipt binding the exact
  proposal, account, tenant, agent, strategy, policy and issuer. The receipt must
  include execution-eligible evidence, an executable quote and a verified broker
  preview reference. Current public research observations cannot be promoted to
  these states by relabeling them.
- Separate explicit account activation. A valid receipt does not activate the
  connector or represent customer authorization by itself.

The connector consumes an independently qualified customer receipt issuer. It
does not implement or certify that issuer, supply licensed executable quotes,
or manufacture broker previews. Its `preview` command is a local enforcement
preflight with live account reads; it is **not a broker-issued preview** and
cannot satisfy the receipt's broker-preview requirement. Where a customer's
broker/source combination cannot supply those inputs, live submission remains
blocked. Do not relax those checks to turn the demo into live trading.

## Initialize and inspect without contacting a broker

From a reviewed complete checkout:

```sh
uv sync --project integrations/trading-copilot --locked
uv run --project integrations/trading-copilot --locked liquilens-live init \
  --state-dir /absolute/private/live-state
uv run --project integrations/trading-copilot --locked liquilens-live capabilities \
  --state-dir /absolute/private/live-state
```

Initialization writes owner-only `live-config.json` and `live-secrets.json`, never
overwriting existing files. Supply your account and complete trusted issuer
binding locally. Keep the live API credentials and the issuer's HMAC verification
key in the private secrets file. Never pass these files to an LLM or put them in
source control. The development Mac's state directory must resolve to permanent
SSD storage. Each customer provisions their own account and source rights.

Keep `live_enabled=false` during integration. A separately authorized activation
requires setting both `live_enabled=true` and
`activation_acknowledgment="LIVE-ACCOUNT:<the exact account id>"` in the private
configuration. This task did not set either value or provision any credentials.
Use separate accounts/credentials/state for paper and live. Do not share the
account with another order service or manual trading: the local lock cannot
coordinate another machine or protect against external account mutations.

## Explicit operations

```sh
liquilens-live preview --state-dir /absolute/private/live-state \
  --request exact-request.json --receipt authenticated-live-receipt.json
```

Preview checks the exact receipt and current account without submitting. After
independent qualification and account activation, `submit` takes the same files.
Persist the request hash before calling it. Read `state`, `observation` and
`resubmit_allowed`; command success is not a fill or financial success.

```sh
liquilens-live status --state-dir /absolute/private/live-state --request-hash <saved-hash>
liquilens-live reconcile --state-dir /absolute/private/live-state --request-hash <saved-hash>
liquilens-live cancel --state-dir /absolute/private/live-state --request-hash <saved-hash>
```

`status` reads local history. `reconcile` reads the same client order ID at the
broker and validates order identity and monotonic fills. A `cancel` acknowledgment
means cancellation was requested; reconcile again to observe the terminal state.
Cancellation and reconciliation remain possible under STOP. Neither submits a
replacement order. A timeout, malformed response, 404 or a crash after reservation
keeps the outcome unresolved and blocks fresh submissions.

Create `STOP` in the live state directory to block new submissions. It cannot
recall an in-flight order. The connector rechecks activation, STOP and receipt
expiry after account reads and immediately before POST. Each request ID and
receipt is single-use; repeats of an exact saved request return its prior record.
SQLite commits the reservation with full synchronization before contacting the
broker. Account identity/limits cannot silently change around an existing journal.
Back up the state while stopped; protect keys separately. Do not delete records
to clear uncertainty or reset limits. The 10,000-order journal limit requires a
reviewed archival migration, retaining unresolved orders and replay identities.

## Embed in a platform

```python
from liquilens_trading_copilot.live_connector import (
    AlpacaLiveTransport, LiveAccountConnector, LiveLimits,
)

# Trusted operator configuration only; never take these values from tool inputs.
broker = AlpacaLiveTransport(api_key=customer_key, secret_key=customer_secret)
connector = LiveAccountConnector(
    state_dir=private_state, binding=trusted_binding,
    hmac_key=private_issuer_key,
    limits=LiveLimits(("BTC/USD",), 1000, 5000, 100, max_daily_attempts=2),
    broker=broker, activated=operator_activation,
)
# Authenticate the customer and select this fixed lane before invoking methods.
preview = connector.preview(exact_request, verified_issuer_receipt)
# Submission is a separately authorized operation, never an automatic next step.
# result = connector.submit(exact_request, verified_issuer_receipt)
broker.close()
```

The platform owns user authentication, per-operation authorization, customer
mandates, network isolation, custody of credentials, external account concurrency,
entitled evidence and deployment/recovery qualification. Agents receive bounded
outcomes, not broker credentials. There is no public live MCP endpoint.

Primary broker contracts: [orders](https://docs.alpaca.markets/us/docs/working-with-orders),
[create an order](https://docs.alpaca.markets/us/reference/postorder), and
[paper/live separation](https://docs.alpaca.markets/us/docs/paper-trading).
Account availability and data entitlements require customer-specific verification.
