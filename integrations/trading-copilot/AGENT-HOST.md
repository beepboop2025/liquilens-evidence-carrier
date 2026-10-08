# Private financial agent host

This source integration connects an external agent to operator-owned evidence
checks, account limits, paper submission and broker reconciliation. It accepts
proposals from any runtime that can call HTTPS. LIQUILENS PRIVATE LIMITED is the
publisher; each installation has one operator, agent identity and paper account.

The public LiquiLens, Seiche and Undertow APIs/MCP remain research services. This
private host is a separately installed REST service, disabled by default. It is
not a public MCP authorization server or a deployed managed execution service.
The signed core Carrier package does not include this integration.

## Supported workflow

The default `liquilens.paper-funding-exit.v1` profile accepts **exactly $1,000
BTC/USD BUY or SELL, market, IOC**. It retrieves Seiche's funding observations,
LiquiLens's corporate funding observations and Undertow's exact hypothetical
SELL liquidation scenario. A BUY assessment's exit scenario is not a buy quote.
See the [profile contract](README.md#default-private-profile) for source clocks,
rights, pressure bands and limitations. Fresh retrieval never refreshes a source
observation. Missing, restricted, stale or mismatched evidence can block a call.

The host applies the existing configurable cash, exposure, daily-loss, minimum
size, open-order and daily-attempt limits. It does not run the copilot's trend
strategy. The external agent chooses its proposal; a source-policy pass supplies
no order authority. Separate operator enablement and submit permission remain
required. Live money, cash transfers, other instruments and arbitrary order
sizes are unsupported by this profile.

## Install and initialize

From this reviewed source checkout, with Python 3.11–3.14 and uv:

```sh
uv sync --project integrations/trading-copilot --locked --extra test
uv run --project integrations/trading-copilot --locked liquilens-agent-host init \
  --state-dir /absolute/private/agent-state
```

On the development Mac, use an SSDWorkspace state directory and run commands
through `ssd-workspace run`. Keep production state on durable local storage.
Initialization creates an owner-only directory and mode-0600 files:

| File | Purpose |
| --- | --- |
| `config.json` | Fixed agent/account/policy and account limits; initially disabled with no account ID |
| `paper.env` | Explicit paper broker credentials and generated private HMAC key |
| `agent-auth.json` | SHA-256 token digests, fixed agent identity and allowed scopes |
| `agent-read.token` | Read capabilities/status and request assessments |
| `agent-execution.token` | Also submit paper orders and reconcile broker outcomes |

Provision the account ID and paper credentials locally. Never send broker keys,
HMAC keys or token files to a public agent prompt, MCP listing or source control.
The host ignores ambient broker environment variables. The client receives only
its designated agent token. Token rotation/revocation requires changing the auth
file and restarting the host; it does not cancel already submitted orders.

Keep `enabled=false` for initial source assessment.

First inspect local setup without contacting sources or the broker:

```sh
uv run --project integrations/trading-copilot --locked liquilens-agent-host doctor \
  --state-dir /absolute/private/agent-state
```

The report identifies missing account identity, credentials, receipt key, token
scopes, file permissions, profile and account limits without printing their
private values. It reads only local files and does not initialize, chmod or
change them. Exit code 2 means local setup has blockers; exit code 0 means those
local checks passed. `ready_for_order` remains false: current source eligibility,
broker-account qualification and deployment/recovery are separate requirements.
Execution enablement and STOP are reported independently from setup completeness.

After provisioning the local setup, run the host explicitly:

```sh
uv run --project integrations/trading-copilot --locked liquilens-agent-host serve \
  --state-dir /absolute/private/agent-state --port 8766
```

The CLI binds `127.0.0.1`, one worker, without access logging or forwarded-header
trust. Remote clients need an operator-managed TLS reverse proxy, an explicit
`--allowed-host` value and private network admission. Do not expose the raw
loopback HTTP service through a public tunnel. Browser origins, query parameters,
duplicate authorization headers and oversized/ambiguous JSON are rejected.
There is a bounded 60-request/minute host-wide admission limit.

## Connect an agent

Use the installed reference client or the same JSON contract in another runtime.
Persist the business intent ID **before** sending the proposal:

```sh
uv run --project integrations/trading-copilot --locked liquilens-agent capabilities \
  --token-file /absolute/private/agent-state/agent-read.token
uv run --project integrations/trading-copilot --locked liquilens-agent assess \
  --token-file /absolute/private/agent-state/agent-read.token \
  --intent-id treasury-review-20261008-001 --side sell --notional-usd 1000
```

An assessment returns its stable `assessment_id`, exact paper request, independent
product evidence, source-policy result and expiry. The HMAC signature stays
private. Repeating the same intent and proposal returns the original assessment,
including its original expiry. Changing a proposal under that intent is rejected.
An expired proposal needs a genuinely new decision and new intent; a new intent
must never be used to retry an uncertain submission.

Persist the assessment ID before any submission. After operator activation,
invoke `liquilens-agent submit --assessment-id <saved-id>` with the execution token
file. Status uses the read token; reconciliation uses the execution token. Each
is a separate explicit command. The client accepts HTTPS remotely, follows no
redirects, uses no environment proxy and never retries a request automatically.

| Method and path | Scope | JSON input |
| --- | --- | --- |
| GET `/v1/capabilities` | `read` | None |
| POST `/v1/assessments` | `assess` | `intent_id`, `side`, `notional_usd` |
| POST `/v1/orders/submit` | `submit` | `assessment_id` |
| POST `/v1/orders/status` | `read` | `assessment_id` |
| POST `/v1/orders/reconcile` | `reconcile` | `assessment_id` |

All requests need `Authorization: Bearer <agent-token>`; POSTs require
`application/json`. Caller-selected accounts, policy, evidence, receipts, clocks,
broker endpoints and enable flags are rejected. A token grants access only to
this host's fixed binding. Configure separate installations for distinct tenants.

## Connect an MCP-compatible agent

The private stdio bridge exposes capabilities, assessment and saved-order status
with the read token. Add this entry to your existing client's `mcpServers` map,
replacing the absolute checkout and token paths locally:

```json
{
  "liquilens-paper": {
    "command": "uv",
    "args": ["run", "--project", "/absolute/reviewed-checkout/integrations/trading-copilot",
             "--locked", "liquilens-agent-mcp", "--token-file",
             "/absolute/private/agent-state/agent-read.token"]
  }
}
```

The host must already be running. To expose submission and reconciliation, the
operator must explicitly add `--allow-submit` and use the execution token file.
This changes only the bridge catalog; host activation, scope checks, source
policy, account controls and replay protection still apply. Broker and HMAC keys
remain in the host. An MCP client capable of arbitrary filesystem access still
needs OS isolation from the host's secret directory. Do not put secrets into the
configuration, a public server listing, prompts or tool arguments.

The bridge supports MCP 2025-11-25 initialization and 2026-07-28 per-request
negotiation, private zero-TTL discovery, strict JSON input, bounded messages and
execution error results. Notifications cannot invoke orders. The connector never
turns an uncertain outcome into an automatic retry.

For a separately gated customer-owned live connector, see
[LIVE-CONNECTOR.md](LIVE-CONNECTOR.md). Paper host tokens never authorize it.

## Interpret outcomes and recover

HTTP success is not financial success. Inspect `tool_error`, `status`,
`submission_state`, `reconciliation_resolution` and `fill_status`. A submission
acknowledgment does not prove a fill. `fill_status` is `not_observed`, `partial`
or `filled`; quantities, prices and terminal state come from the independently
validated `order_observation`. Its `broker_observed_at` is a historical read time,
explicitly labeled `last_observed`. Status does no broker network read.

After a timeout, disconnection or error, retain the same IDs. Read status and
reconcile; never create a replacement order as a retry. The SQLite reservation
and journal survive restart. An HTTP disconnection cannot release an in-flight
submission lane. The host refuses new submissions while any known intent lacks
a terminal broker observation, including a reservation made before a late STOP.
Contradictory broker IDs/sides, regressing fills and changed terminal states fail
closed. A `not_submitted` resolution still requires operator investigation; there
is no automatic journal deletion or attempt-budget reset.

Create `STOP` in the state directory to disable new submissions. The dispatcher
rechecks it after the SDK account read, before submitting. STOP does not cancel an
already in-flight or accepted order. Set `enabled=false` and restart for durable
configuration disablement. Keep the same state directory for recovery, and never
run the copilot or a second host against this account in another directory. The
local process lock serializes owners of one state directory; it cannot coordinate
other hosts, external trading clients or manual account activity.

Back up both SQLite stores and configuration together while the service is
stopped. Keep credentials separately protected. Changing the execution binding
with existing host state is refused. This prototype supports 10,000 retained
assessments; an operator must design a reviewed archival migration before that
limit, without deleting unresolved intents.

## Verify and pilot

```sh
uv run --project integrations/trading-copilot --locked --extra test \
  pytest integrations/trading-copilot/tests integrations/alpaca-paper/tests
```

Tests exercise both source profiles, authentication, account limits, late STOP,
timeout, restart, client cancellation and broker reconciliation using synthetic
source responses and broker doubles. They contact no broker and establish no
real fills, current source admission, returns or customer adoption.

An external pilot should record the customer's runtime, covered workflow,
integration time, recurring use, evidence refusals and separately observed broker
outcomes. Keep verification traffic excluded from adoption. Follow the
[platform delivery map](../../docs/FINANCIAL-AGENT-PLATFORM.md) for the live-money,
treasury and commercial work that still needs separate implementation and proof.
