# Consented decision and outcome records

This source integration lets a customer share a bounded part of their **paper**
agent's decision and execution history with a named reviewer. It connects the
existing exact-order request, verified evidence receipt, durable submission
journal and last broker observation. It supplies a practical starting point for
execution-quality review and measuring use of the pre-trade workflow.

This feature is an authenticated export from the customer-owned paper host.
It is not a deployed central analytics service or a live-account integration.
Public research MCP/API requests still reveal no private orders or fills.

## What is retained

After an assessment, submission, status or reconciliation operation, the host
retains a changed snapshot in `audit.sqlite3`. Identical snapshots are deduplicated.
The snapshot contains:

- The exact assessment/request hash and order instrument, side, notional and type.
- Receipt and policy hashes, original expiry and source-policy outcome.
- Each product's source hash, observation/knowledge/retrieval clocks, rights status
  and eligibility flags. Product facts and source payloads are excluded.
- Durable submission state, attempt count and whether a broker ID was observed.
- Last validated broker status, terminal flag, cumulative filled quantity and
  average fill price, plus the host observation time.

Tenant, operator, account, agent and strategy identifiers become keyed references.
Raw account IDs, intent labels, broker order IDs, credentials and receipt HMACs
are excluded. These records remain sensitive customer data, not anonymized data.
The export's content hashes detect accidental changes; they are not independent
broker signatures. HTTPS and the separately provisioned host token establish the
transport's origin. Verification of a historical receipt uses its issuance time
and does not extend its execution validity.

Existing journals remain the execution authority. Outcome export performs no
source refresh, broker lookup, submission, reconciliation or cancellation. A
source-policy pass, a successful HTTP response and a broker acknowledgment each
remain distinct from a fill.

## Customer provisioning

First configure the private host as described in [AGENT-HOST.md](AGENT-HOST.md).
The customer must authorize the named recipient and purpose before running:

```sh
liquilens-agent-host grant-outcomes \
  --state-dir /absolute/private/agent-state \
  --consent-id pilot-quality-review-001 \
  --recipient-id liquilens-quality-review \
  --expires-at 2026-11-15T00:00:00Z \
  --traffic-class unknown
```

Use an expiry appropriate to the agreed pilot. This owner filesystem command
creates `outcome-consent.json` and `agent-outcomes.token`, both mode 0600, and
adds a token digest with **only** `outcomes:read` to `agent-auth.json`. It never
prints the token. Restart the host to load the new token scope, then provision
that token to the named recipient through the customer's private secret channel.
The grant begins at provisioning time and expires at the supplied timestamp;
it cannot authorize older assessments. Existing execution activation is unaffected.

The grant binds tenant, operator, account, agent, recipient, token digest,
purpose and record window. The supported purpose is `execution_quality_review`.
`allow_training` and `allow_cross_customer_aggregation` must both be false.
Customer authorization does not grant third-party market-data redistribution
rights. This owner configuration records an operational sharing instruction;
it is not independent evidence of a signed customer agreement.

Traffic class is explicitly `operator`, `synthetic`, `customer` or `unknown`.
`customer` is an operator declaration, not proof of an independent customer,
AI authorship, retention, payment or revenue. Use `synthetic` for fixtures and
`operator` for internal probes. Never relabel existing probe traffic as customers.

One grant is supported per host in this first version. Provisioning refuses to
overwrite an existing grant or token. Rotation requires an owner-reviewed update
of the grant, token digest and recipient secret, followed by a host restart.
Do not copy this host's token or state to another customer's account.

## Export and resume

`POST /v1/outcomes/export` requires the dedicated token and exactly:

```json
{"consent_id":"pilot-quality-review-001","after":0,"through":null,"limit":25}
```

The maximum page size is 25 snapshots. The reference client is:

```sh
umask 077
liquilens-agent outcomes --url https://customer-host.example \
  --token-file /absolute/private/agent-outcomes.token \
  --consent-id pilot-quality-review-001 > page-001.json
```

The returned `through` fixes the journal watermark. While `has_more` is true,
request the next page with `--after <next_after>` and the **same**
`--through <through>`. Save each response durably before advancing the cursor.
An identical page can be retried; export never causes another order. New records
arriving during download are excluded from that watermark. For a later incremental
poll, send the completed `next_after` and omit `--through`. Cursor gaps are normal
because duplicate and out-of-window records do not appear.

For an offline report, supply every saved page from cursor zero to the final page
for one watermark, in order:

```sh
uv run --project integrations/trading-copilot --locked \
  python -m liquilens_trading_copilot.outcomes page-001.json page-002.json
```

The summarizer rejects missing/reordered pages, changed retry pages, mixed
grants/watermarks and content-hash mismatches. Identical retry pages are ignored.
It selects the latest snapshot for each assessment: cumulative partial fills
are never added together. Counts distinguish assessed, source-policy passed,
submission attempted, broker acknowledged, partial fill, full fill and terminal
without fill. `no_broker_observation` means unknown execution outcome; it does
not mean no trade. An assessment may pass source checks and still have no
submission because the host is disabled or an account check failed.

The report leaves independent customers, live trades, turnover and PnL unknown.
It does not prove economic impact or causal improvement from our evidence.
`export_pages_complete` refers only to the requested page sequence;
`coverage_complete=false` remains explicit.

## Revoke, recover and interpret limits

```sh
liquilens-agent-host revoke-outcomes --state-dir /absolute/private/agent-state
```

The host rereads the private grant on every page. Revocation, expiry, a missing
file, unsafe permissions, a symlink or a mismatched binding denies the next
export without a restart. An already admitted response may finish. Revocation
cannot recall copies already delivered; recipient retention and deletion must
be agreed separately. The local operational order journal remains intact.

Snapshots survive restart alongside existing audit state and belong in the same
encrypted backup/restore boundary. This release adds one table; it neither
rewrites historical execution tables nor reconstructs missing history. A process
interruption between an execution-journal update and outcome capture can leave
a gap. Status/reconcile on the **same assessment** captures the current durable
state after recovery; it cannot reconstruct every intermediate state. Export
does not perform that repair or any trading operation. A malformed snapshot
fails the page rather than silently disappearing.

This is a polling observation history, not a complete broker event stream. Host
observation time is distinct from broker execution time (`broker_event_time` is
null). Orders outside this host are outside coverage. Agent identity comes from
the customer's binding; we do not independently prove that AI made the decision.
The current paper reader refuses regressing cumulative fills or changed terminal
states. Trade corrections, busts and a resumable authenticated broker event stream
require additional qualification before a live observation service can be claimed.

Official order-identity and lifecycle references:
[Alpaca client order IDs](https://docs.alpaca.markets/us/docs/working-with-orders)
and [authenticated trade updates](https://docs.alpaca.markets/us/docs/websocket-streaming).

## Verification

```sh
uv run --project integrations/trading-copilot --locked --extra test \
  pytest integrations/trading-copilot/tests/test_outcomes.py \
  integrations/trading-copilot/tests/test_agent_host.py
```

These tests use synthetic broker doubles and fixed source fixtures. They cover
assessment → receipt → attempt → partial fill → terminal outcome, restart,
timeout/no-resubmit, cancellation/rejection, scoped tokens, active consent,
revocation between pages, bounded pagination, source clocks and redaction.
Passing fixtures are not evidence of a connected broker account or customer use.
