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

The current Alpaca integration has **no compatible broker-preview adapter**.
Alpaca's [Broker API estimation endpoint](https://docs.alpaca.markets/us/reference/get-v1-trading-accounts-account_id-orders-estimation)
returns indicative estimates for notional market orders; its documented contract
excludes crypto and non-market orders. It cannot qualify the quantity-sized limit
orders supported here, and it belongs to the Broker API rather than this
customer-key Trading API connector. A CLI dry run or our local account preflight
does not replace that missing broker contract.

## Initialize and inspect without contacting a broker

From a reviewed complete checkout:

```sh
uv sync --project integrations/trading-copilot --locked
uv run --project integrations/trading-copilot --locked liquilens-live init \
  --state-dir /absolute/private/live-state
uv run --project integrations/trading-copilot --locked liquilens-live capabilities \
  --state-dir /absolute/private/live-state
uv run --project integrations/trading-copilot --locked liquilens-live doctor \
  --state-dir /absolute/private/live-state
```

`doctor` reads existing local files without creating state or contacting an
account. It reports binding, limits, private credential/key presence, STOP,
configured activation and unresolved journal entries. It never prints credentials,
account identifiers, receipts or complete orders. Its `live_ready` remains false
and its exit code is 2 while the independent issuer, quote entitlement, compatible
broker preview and account qualification are unverified. Even valid-looking keys
and a configured activation acknowledgment do not establish those facts. The
stable `alpaca_limit_order_broker_preview_unavailable` requirement identifies the
current provider incompatibility above. The legacy `external_requirements` array
remains an aggregate for compatibility. Additive `engineering_requirements`
identifies missing issuer/preview implementations; `qualification_requirements`
identifies unverified entitlement, account and activation gates. None is waived.

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

## Read the configured account without activation

With an existing private state directory, configured `binding.account_id`, valid
operator limits and separately provisioned customer credentials, run:

```sh
uv run --project integrations/trading-copilot --locked liquilens-live check-account \
  --state-dir /absolute/private/live-state
```

This command works with `live_enabled=false` and with STOP present. It requires
only the configured account ID from the binding and the `api_key`/`secret_key`
fields from the existing owner-only secrets file. It does not require an issuer,
receipt key, signed receipt or activation acknowledgment. Do not populate those
fields with placeholders to perform an account check.

The transport pins `https://api.alpaca.markets` and allows only
[`GET /v2/account`](https://docs.alpaca.markets/us/reference/getaccount-1),
[`GET /v2/orders?status=open&limit=1`](https://docs.alpaca.markets/us/reference/getallorders-1)
and [`GET /v2/positions`](https://docs.alpaca.markets/us/reference/getallopenpositions).
It sends no order, cancellation, transfer, request body, redirect or retry. Account
ID/USD binding, ACTIVE status, all three trading-block flags, finite cash/equity,
daily loss, absence of open orders, unique long-only positions and gross exposure
must pass the shared connector controls. This read does not assess a proposed
order's size, available quantity or receipt.

The redacted JSON reports checks, position count, configured activation, STOP and
stable failure reasons. It prints no account ID, symbols, balances, credentials or
upstream response/error text. Exit 0 means this momentary account check passed;
exit 2 means it did not complete or failed a control. `account_qualified=true`
never changes `live_ready=false`, the null managed endpoint or the unavailable
issuer/preview flags. `doctor` remains an offline check and does not consume or
persist this observation.

No state directory, lock, journal or receipt is created, repaired or updated, and
no activation occurs. Existing journals are not inspected by this operation;
use the local recovery commands separately. Other account users can change the
broker snapshot between these GET requests. A passing check cannot authorize a
later order, establish broker-preview support, or certify live trading readiness.
This implementation is tested with synthetic HTTP doubles, not a provisioned live
account; it does not change the published paper-host source pin.

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
liquilens-live orders --state-dir /absolute/private/live-state --unresolved-only --limit 50
liquilens-live export --state-dir /absolute/private/live-state --limit 100
liquilens-live reconcile --state-dir /absolute/private/live-state --request-hash <saved-hash>
liquilens-live cancel --state-dir /absolute/private/live-state --request-hash <saved-hash>
```

`status`, `orders` and `export` read an existing local journal without requiring
broker credentials, a receipt key or activation. They remain useful if credentials
have been revoked or removed. `orders` lists saved hashes needed for crash
recovery; `--unresolved-only` selects pending and uncertain attempts. `export`
writes a sanitized metadata page to stdout, including bounded observations and
hashes rather than complete orders, account bindings, requests or receipts.
Each page is limited to 1–100 records. If `truncated=true`, pass `next_after` as
`--after-hash` with the same filter to retrieve the following page. Retain exported
metadata privately; it is a last-observed account record, never proof of a fresh
broker state or authorization to retry.

Local reads use a shared existing operator lock and read-only SQLite. They do not
create a missing database or repair a hot journal. A concurrent writer produces
`local_journal_busy`; leftover recovery files produce
`local_journal_recovery_required`. Stop the writer and perform reviewed database
recovery before reading again; do not remove recovery files to bypass the check.

`reconcile` reads the same client order ID at the
broker and validates order identity and monotonic fills. A `cancel` acknowledgment
means cancellation was requested; reconcile again to observe the terminal state.
Cancellation and reconciliation remain possible under STOP. Neither submits a
replacement order. A timeout, malformed response, 404 or a crash after reservation
keeps the outcome unresolved and blocks fresh submissions.

Observed limit fills must respect the submitted side and limit price. A
contradictory fill is rejected without clearing the existing pending/uncertain
record. The transport allows only exact operation/route pairs, including
single-order cancellation by UUID; bulk order cancellation and position
liquidation routes are excluded.

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
