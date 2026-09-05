# Trade Safety x402 access

Status: source implementation for gateway `0.2.1`; disabled by default

Authority: paid access to a read-only sandbox assessment only

Activation: no hosted activation or revenue claim is made by this document

## Why this rail exists

An AI agent should be able to discover, buy, and consume one order-bound Trade
Safety assessment without opening an account. x402 supplies that access rail.
It does not supply trading authority.

The paid and safety domains stay deliberately separate:

| Domain | What it binds | What it cannot do |
|---|---|---|
| x402 access | exact HTTP resource, canonical request body, offer, required extensions, payer and settlement | change evidence, policy, outcome, receipt expiry, or broker authority |
| Trade Safety | exact proposed order, caller policy admitted inside the server floor, product evidence, clocks, deterministic outcome and issuer | charge a wallet, settle a payment, route or execute an order |
| Broker/OMS guard | fresh authenticated receipt, exact execution identity, one-time claim and private submit callback | infer permission from payment or from MCP discovery alone |

The current public gateway remains hash-only and paper/observation-oriented. A
payment can buy a `hold` or `unavailable` result just as legitimately as a
`pass`; it buys the evidence-bound answer, not a favorable answer.

## Wire flow

```text
client POST /v1/x402/check
  -> strict JSON and server policy preflight
  -> 402 PAYMENT-REQUIRED for the exact canonical body
  -> client returns PAYMENT-SIGNATURE
  -> fixed facilitator /verify
  -> durable pre-settlement claim
  -> Seiche + Undertow + conditional LiquiLens assessment
  -> persist exact response and mark settlement starting
  -> fixed facilitator /settle
  -> durably record settlement outcome
     -> success: 200 exact receipt bytes + PAYMENT-RESPONSE
     -> terminal failure: 402 empty JSON object body + PAYMENT-RESPONSE
     -> pending/ambiguous: 503 and offline reconciliation
```

The route follows x402 v2's authorization flow: verify, produce the protected
resource, settle, then respond. `PAYMENT-REQUIRED`, `PAYMENT-SIGNATURE`, and
`PAYMENT-RESPONSE` use the v2 base64-encoded JSON objects. Network identity is
one CAIP-2 value and the amount is a positive atomic-unit integer.
Malformed payment payloads return HTTP 400. A valid terminal failed settlement
is cached as a permanent non-repayable authorization and replays the same HTTP
402 plus `PAYMENT-RESPONSE`; `settlement_pending`, timeouts, invalid facilitator
responses, and uncertain network outcomes remain sticky HTTP 503 states and are
never automatically settled again.

The challenge carries two required extensions:

- `bazaar` publishes bounded HTTP input/output discovery metadata; and
- `liquilens` authenticates the method, resource, canonical body hash, offer,
  ResourceInfo, and complete required-extension set with a dedicated HMAC key.

A client may echo required extensions but cannot remove or replace them. A
different body, price, asset, payee, resource description, discovery schema, or
extension set invalidates that quote.

## Enabling the route

With no `TRADE_SAFETY_X402_*` variables, the route is not registered and
`/v1/capabilities` reports `x402_access.state: disabled`. Supplying any x402
setting requires the complete exact configuration; partial, empty, unknown, or
unsafe configuration fails startup.

`/v1/check` and the MCP assessment tool remain free and assessment-equivalent;
the application contains no quota. x402 is therefore an additional accountless
access channel, not an exclusive paywall. A commercial deployment must publish
and enforce an honest free quota or reviewed service difference at the edge.
Without that, a 402 challenge and Bazaar listing create no rational paid
conversion and must not be reported as traction.

| Environment variable | Requirement |
|---|---|
| `TRADE_SAFETY_X402_RESOURCE_URL` | HTTPS URL whose path is exactly `/v1/x402/check`; it must be the externally served resource |
| `TRADE_SAFETY_X402_FACILITATOR_URL` | Fixed HTTPS facilitator base; the adapter appends `/verify` and `/settle` |
| `TRADE_SAFETY_X402_NETWORK` | One exact CAIP-2 network identifier |
| `TRADE_SAFETY_X402_AMOUNT` | Positive canonical integer in the asset's atomic units |
| `TRADE_SAFETY_X402_ASSET` | Exact asset identifier; EVM networks require a 20-byte hex address |
| `TRADE_SAFETY_X402_ASSET_NAME` | Bounded asset name placed in offer metadata |
| `TRADE_SAFETY_X402_ASSET_VERSION` | Bounded asset authorization version |
| `TRADE_SAFETY_X402_ASSET_TRANSFER_METHOD` | Optional; only `eip3009` on an EVM network is implemented. `permit2` and unknown methods fail startup rather than being advertised |
| `TRADE_SAFETY_X402_PAY_TO` | Exact receiver; EVM networks require a 20-byte hex address |
| `TRADE_SAFETY_X402_QUOTE_BINDING_KEY_B64` | Dedicated 32-128 byte random key in canonical base64; never reuse a receipt, broker, wallet, or API key |
| `TRADE_SAFETY_X402_JOURNAL_PATH` | Absolute SQLite file path under an existing service-owned `0700` directory |

Do not place the quote key, facilitator credential, wallet key, or payee secret
in a repository, image, command-line argument, log, discovery response, or
telemetry record. The receiving address and offer are intentionally public; a
wallet private key is never accepted by this gateway.

The bundled upstream and facilitator HTTP clients use fixed origins, no
redirects, no environment proxy, a reject-all cookie policy, identity encoding,
bounded response bytes, and short timeouts. A response `Set-Cookie` can never
influence a later agent, payer, evidence request, verify, or settle call. The
facilitator does not add provider-specific authentication. An authenticated
provider requires a separately reviewed injected `Facilitator` implementation;
do not put credentials in the facilitator URL.

x402 v2 permits `payer` to be absent from successful verify and settle
responses. For the exact EVM authorization profile, the gateway derives a
missing verify-time payer from the facilitator-validated authorization's
`from` field; a present value must match it. A missing settle-time payer reuses
that already verified, journal-bound identity, while a present mismatch remains
settlement-uncertain. Other network profiles currently require the facilitator
to return a valid payer during verification and must prove that compatibility
before activation.

## Persistent state and recovery

The local SQLite journal is part of the payment correctness boundary, not a
cache that may be casually deleted. Its directory must be mounted read-write
into the otherwise read-only container, backed up consistently, restored before
traffic, and excluded from public artifacts. The database and its WAL/SHM files
must be private service-owned regular files with verified `0600` permissions.

This implementation supports exactly one active writer/replica over one local
journal. The supported gateway runtime holds an exclusive advisory lock at
`JOURNAL_PATH.runtime.lock` for its full lifetime; a second gateway or any local
reconciliation command fails closed while that lock is held. The private volume
must preserve POSIX advisory-lock semantics. Two replicas with separate volumes
could still accept the same authorization independently. Horizontal activation
requires a shared transactional journal with equivalent claim, tombstone,
settlement, reconciliation, and maintenance-quiescence semantics; a load
balancer's sticky routing is not an idempotency guarantee.

Startup also checks journal compatibility. An empty development-era schema can
receive additive columns, but a non-empty incompatible journal fails with
`journal_migration_required`. Do not replace it with an empty file: preserve an
encrypted backup, stop paid traffic, reconcile every potentially settled row,
and use a separately reviewed offline migration that retains full-payload
bindings, exact response digests, settlement results, and terminal tombstones.
This repository intentionally does not guess missing payment identity from
legacy rows.

The journal distinguishes:

- `processing`: verification passed but settlement is known not to have begun;
- `settling`: the irreversible facilitator call may have begun and automatic
  retry is forbidden; and
- `settled`: a terminal record whose reason identifies a successful cached
  entitlement, terminal failed settlement, retired response, or retired
  authorization. A `payment_settlement_failed` record replays the facilitator's
  same failed `PAYMENT-RESPONSE` and never exposes the protected assessment.

Only definitely pre-settlement work can be aborted or lease-recovered.
`settling` is sticky until operator reconciliation proves the result. A retry
must never blindly charge again. Retiring response bytes must retain a terminal
payment tombstone so the same authorization cannot be verified or settled a
second time. Offer or quote-key rotation applies to new quotes and must not
revoke an already-settled exact response.

After exact response retention ends, retries receive
`settled_response_retired`; the tombstone prevents verification or settlement
of that authorization again. An operator-retired pre-settlement claim receives
`payment_authorization_retired` and likewise requires a newly signed payment.

Hot rows and exact response caches use a separate capacity from all terminal
identities. Terminal tombstones are not deleted on a client-declared payment
expiry because the opaque scheme and network finality rules are not trusted as
a universal safe-deletion clock. The default terminal cap is 1,000,000 rows. At
that cap, new payment identities fail closed (`journal_terminal_capacity`,
reported as telemetry reason `capacity_exhausted`) while existing replays remain
protected. Monitor the redacted local `status` output and perform an offline
capacity expansion or tombstone-preserving archive migration before exhaustion;
never clear the journal to restore availability.

The journal contains bounded payer, transaction, payment-offer, response, and
reconciliation metadata. Treat it as confidential financial operations data.
Define a documented retention period, legal basis, deletion/tombstone policy,
encrypted backup location, restore test, and restricted operator access before
activation. The configured facilitator receives the PaymentPayload,
PaymentRequirements, payer authorization, asset, amount, receiver, network, and
resource; its own retention, privacy, sanctions, availability, and incident
terms require review.

### Local reconciliation command

The gateway package installs a local-only operator command. It opens the same
private journal as the configured runtime and never contacts the facilitator:

```sh
liquilens-trade-safety-x402-reconcile status --limit 100

# Use only after the facilitator or chain independently proves settlement.
liquilens-trade-safety-x402-reconcile reconcile-settled PAYMENT_ID \
  --response /private/operator/confirmed-settlement.json

# Use only after independent evidence proves settlement never happened.
liquilens-trade-safety-x402-reconcile retire-unsettled PAYMENT_ID \
  --confirm-not-settled

# Remove response/payment material older than the policy while retaining
# permanent anti-replay tombstones. Run bounded batches while fully offline.
liquilens-trade-safety-x402-reconcile retire-terminal-responses \
  --older-than-days 30 --limit 100 --confirm-replay-loss
```

`status` exposes redacted counts and uncertain records; it omits raw payment
payloads, signatures, payer/payee details, amounts, protected response bytes,
and facilitator bodies. A settlement-response file must be an absolute,
service-owner-controlled regular file with mode `0600`, strict JSON, and no
more than 64 KiB. Omitting `--response` is valid only when the journal already
captured a final facilitator result. The command emits bounded JSON but no
secrets; store its output as operational metadata, not settlement evidence.

No reconciliation or retention mutation is available on the serving gateway's
`X402AccessGate`; it returns `reconciliation_runtime_active` even to in-process
callers. The CLI constructs an explicit maintenance-mode gate only after it has
acquired the exclusive journal lock.

No mutation is an investigation shortcut. Reconcile external state first,
record the evidence in the restricted incident system, fully stop the paid
gateway process, and establish that its process-lifetime runtime lock has been
released. This drains a live facilitator call from the local process; after a
crash, it does not prove what the remote facilitator or chain completed. Only
then run exactly one transition and confirm the journal state. The CLI
mechanically refuses to open the journal while the supported gateway runtime is
active (`reconciliation_runtime_active`). `reconcile-settled` makes the original
exact response replayable; `retire-unsettled` permanently rejects that
authorization and requires the caller to obtain a new quote and sign a new
payment. If the journal already contains a valid successful facilitator result,
retirement is mechanically refused; the operator must use `reconcile-settled`
so a crash cannot erase durable evidence that the caller paid.

The retention command is age-filtered, oldest-first, and bounded to 100 rows per
run. It retires successful cached receipts to `settled_response_retired` and
cached terminal payment failures to `payment_authorization_retired`; raw payment
payloads, payer, requirements, protected response, settlement response,
`PAYMENT-RESPONSE`, and identity JSON are cleared while the payment key,
payload digest, body/resource binding, and terminal tombstone remain. Replay
does not refresh the retention clock. Capacity pressure may retire successful
cached responses earlier than this age, so size the hot-cache limit if offering
a minimum replay-duration service level.

SQLite `secure_delete` is required and the offline command requires a successful
WAL truncate checkpoint before reporting success. This is best-effort removal
from the active database, not a claim of forensic erasure from encrypted
backups, filesystem/cloud snapshots, replicas, or SSD-remapped blocks. Those
copies need their own documented expiry and deletion proof; do not run an
unbounded `VACUUM` in the request or default retention path.

If a cached Trade Safety receipt has expired, the gateway returns
`settled_response_expired` without its receipt bytes and without re-assessing or
re-settling the authorization. It retains the original `PAYMENT-RESPONSE` header
so the buyer receives settlement proof. The caller needs a fresh request and a
new quote; payment durability never extends market-evidence freshness.

## Deployment gates

Before enabling a public paid route, record and independently verify:

1. exact source SHA, gateway `0.2.1` artifact digest, issuer endpoint, external
   resource URL, x402 network, asset, atomic price and receiving address;
2. facilitator compatibility with x402 v2 verify/settle response schemas,
   authentication method if any, timeout behavior, and settlement-pending
   reconciliation procedure;
3. private persistent-volume ownership, permissions, backup, restore, WAL,
   advisory-lock, single-writer, maintenance-quiescence, crash recovery,
   age-retirement, and backup/snapshot expiry tests;
4. egress policy limited to Seiche, LiquiLens, and the chosen facilitator;
5. public edge body/header limits, rate limits, TLS, and no response caching;
6. test-wallet challenge, rejected payment, successful settlement, exact replay,
   expired receipt, service restart, quote rotation, and uncertain-settlement
   evidence; and
7. independent facilitator/on-chain reconciliation matching local settled
   counts before reporting revenue or payers.

Do not call a branch, image, configured environment, successful health probe, or
402 challenge a deployment, payment, customer, or protected trade. Visitor-visible
activation needs a live challenge plus a bounded owner-authorized settlement
canary; broker protection additionally needs a broker-adjacent receipt guard.
The independently versioned gateway-tag workflow distributes an OCI image only;
its wheel build is validation evidence, not a published wheel, sdist, or GitHub
Release.

## Primary protocol references

- [x402 protocol specification v2](https://github.com/x402-foundation/x402/blob/main/specs/x402-specification-v2.md)
- [x402 Bazaar discovery extension](https://github.com/x402-foundation/x402/blob/main/specs/extensions/bazaar.md)
- [CAIP-2 blockchain ID specification](https://chainagnostic.org/CAIPs/caip-2)
