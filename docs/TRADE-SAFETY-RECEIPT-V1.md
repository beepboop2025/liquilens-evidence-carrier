# LiquiLens Trade Safety Receipt v1

## Outcome

Trade Safety Receipt v1 gives an AI trading copilot or trading agent one
deterministic, short-lived answer to a narrow question:

> Given this exact proposed order, this operator-authored policy, and these
> independently labelled Seiche, Undertow, and conditional LiquiLens inputs at
> this evaluation time, which fail-closed policy outcome applies?

The four outcomes are `pass`, `limit`, `hold`, and `unavailable`. None is a
trade recommendation or execution authorization. A receipt never routes an
order, contacts a broker, chooses a security, allocates capital, or silently
changes a quantity. The caller and its accountable operator remain responsible
for every downstream action.

The machine contracts are:

- [`liquilens-trade-safety-request-v1.schema.json`](../protocol/liquilens-trade-safety-request-v1.schema.json)
- [`liquilens-trade-safety-policy-v1.schema.json`](../protocol/liquilens-trade-safety-policy-v1.schema.json)
- [`liquilens-broker-preview-reference-v1.schema.json`](../protocol/liquilens-broker-preview-reference-v1.schema.json)
- [`liquilens-trade-safety-receipt-v1.schema.json`](../protocol/liquilens-trade-safety-receipt-v1.schema.json)
- [`com.liquilens.trade-safety-receipt.schema.json`](../integrations/fdc3/com.liquilens.trade-safety-receipt.schema.json)
- [`trade-safety-intents.json`](../integrations/fdc3/trade-safety-intents.json)

## System boundary

```text
agent + operator + tenant/account scope
                  |
                  | exact proposed order + policy reference
                  v
        Trade Safety Request v1
                  |
          canonical request_hash
                  |
       +----------+----------+
       |          |          |
    Seiche     Undertow   LiquiLens
    funding    exit cost  conditional institution context
       |          |          |
       +----- request-bound evidence -----+
                  |                       |
          tenant/account-bound            |
           broker preview                 |
                  |                       |
                  +-----------------------+
                                          v
                           deterministic policy evaluation
                                          |
                                          v
                           Trade Safety Receipt v1
                                          |
                           verify identity, clocks,
                           binding, policy, HMAC, decision
                                          |
                                          v
                         caller-owned workflow and controls

                         (there is no execution edge here)
```

Seiche and Undertow are mandatory baseline dependencies. LiquiLens is present
as an explicit section on every receipt but is conditional: it is
`not_applicable` or `context_only` unless the order has a covered institution or
borrower relationship and a separately reviewed evidence profile is eligible
for that policy. A consumer must not convert optional LiquiLens context into a
universal risk score.

## Authority boundary

Every receipt contains this immutable object:

```json
{
  "financial_authority": "operator_policy_check_only",
  "can_execute": false,
  "can_recommend": false,
  "can_allocate_capital": false,
  "is_credit_rating": false,
  "is_executable_quote": false
}
```

Changing any value invalidates both the schema and the content identity. The
boundary remains all-false for a valid HMAC receipt. Authentication answers
“did a tenant-local issuer with this shared key issue these bytes?” It does not
answer “should the trade happen?”

## Request contract and exact order binding

A request has exactly nine root fields: `schema`, `request_id`, `created_at`,
`expires_at`, `mode`, `agent`, `order`, `policy_ref`, and `extensions`.

The agent block binds the proposed order to an `agent_id`, accountable
`operator_id`, `tenant_id`, `account_id`, runtime, optional strategy, and its
declared authorization scopes. Paper requests require `orders:paper`; live
requests require `orders:live`. A scope is necessary input to the policy check,
not a credential and not proof that a broker granted the same capability.

The order block binds all core execution-sensitive values:

- asset class, symbol, and available instrument identifiers;
- side and order type;
- notional amount and three-letter uppercase currency;
- optional quantity;
- limit and stop prices where applicable;
- optional venue; and
- time in force.

Price fields are deliberately unambiguous:

| Order type | `limit_price` | `stop_price` |
|---|---:|---:|
| `market` | `null` | `null` |
| `limit` | required | `null` |
| `stop` | `null` | required |
| `stop_limit` | required | required |
| `other` | `null` | `null` |

Any execution-material value absent from the core shape—multi-leg composition,
reduce-only, post-only, leverage, account sub-routing, auction instructions, or
an order-type-specific parameter—must be placed in a namespaced
`extensions` entry before hashing. An evaluator that does not understand a
present extension must fail closed. It must not drop the extension and evaluate
a simpler order.

Example observe request:

```json
{
  "schema": "liquilens.trade-safety-request.v1",
  "request_id": "copilot/acme/order-01891",
  "created_at": "2026-09-02T06:00:00Z",
  "expires_at": "2026-09-02T06:02:00Z",
  "mode": "observe",
  "agent": {
    "agent_id": "treasury-copilot-7",
    "operator_id": "operator-42",
    "tenant_id": "acme-treasury",
    "account_id": "paper-account-3",
    "runtime": "acme-agent-runtime/4.2.0",
    "strategy_id": null,
    "authorization_scope": ["orders:observe"]
  },
  "order": {
    "instrument": {
      "asset_class": "equity",
      "symbol": "EXAMPLE",
      "identifiers": {"FIGI": "BBG000EXAMPLE"}
    },
    "side": "sell",
    "order_type": "limit",
    "notional": {"amount": 25000, "currency": "USD"},
    "quantity": 100,
    "limit_price": 250,
    "stop_price": null,
    "venue": null,
    "time_in_force": "DAY"
  },
  "policy_ref": {
    "policy_id": "acme-standard-safety",
    "version": "2026-09-02"
  },
  "extensions": {}
}
```

`created_at < expires_at` is mandatory. Issuance also requires
`created_at <= evaluated_at < expires_at`. A consumer must create a new request
after changing any bound field; copying a receipt to a changed order is a
binding failure, not a `pass`.

## Modes

| Mode | Required scope | `decision.enforced` | Integrity and use |
|---|---|---:|---|
| `observe` | a non-empty declared scope | `false` | Hash or HMAC; research/diagnostic output only |
| `paper` | `orders:paper` | `true` | Hash or HMAC; simulated workflow only |
| `live` | `orders:live` | `true` | A hash-only receipt is always `unavailable`; any non-unavailable result requires tenant-local HMAC authentication, real-money-eligible required evidence, an Undertow section carrying an eligible executable quote, and an unexpired broker preview bound to the same tenant account and exact request |

The word `enforced` means the deterministic operator policy was applied in the
requested paper/live mode. It does not mean the receipt library enforced an
order at a venue.

## Policy contract

The policy is embedded in the receipt and independently bound by `policy_hash`.
It has no permissive switches for missing data, quote requirements, or silent
resizing:

```json
{
  "schema": "liquilens.trade-safety-policy.v1",
  "policy_id": "acme-standard-safety",
  "version": "2026-09-02",
  "required_products": ["seiche", "undertow"],
  "max_evidence_age_seconds": {
    "seiche": 900,
    "undertow": 60,
    "liquilens": 86400
  },
  "hold_regimes": ["STRESS"],
  "max_notional_usd": 100000,
  "max_exit_cost_bps": 35,
  "max_venue_spread_bps": 20,
  "missing_evidence": "fail_closed",
  "live_requires_executable_quote": true,
  "live_requires_broker_preview": true,
  "auto_resize": false,
  "extensions": {}
}
```

`required_products` always includes `seiche` and `undertow`. It may also include
`liquilens`, but only an `eligible` LiquiLens section can satisfy that dependency
for a non-unavailable live result. The current conditional or contextual LiquiLens
evidence must not be relabelled to make a policy pass.

The age budget is explicit per product. `hold_regimes` accepts `CALM`,
`EROSION`, `STRAIN`, and `STRESS`; it is the operator who chooses which observed
Seiche regimes cause a hold. All numeric policy limits are optional through
`null`, but a present limit must be positive. If a USD-denominated policy limit
is active and the request notional is not USD, evaluation is `unavailable`—the
protocol does not invent an FX conversion.

Neither `live_requires_executable_quote` nor
`live_requires_broker_preview` can be disabled. `auto_resize` is permanently
false. A `limit` result identifies an exceeded
operator constraint and sets `resubmit_required` to true. A smaller order is a
new exact order and needs a new request, new product evidence, and new receipt.

## Evidence contract

All three product keys are always present. Omission is a malformed receipt, not
an empty or calm observation.

| Product | Role in this protocol | Baseline status |
|---|---|---|
| Seiche | System funding and bounded capital-market regime context; normative fact `regime` | Required by every policy |
| Undertow | Requested-size exit liquidity and venue-friction context; normative facts `requested_size_usd`, `published_rung_used_usd`, `worst_sell_cost_bps`, `venue_spread_bps` | Required by every policy |
| LiquiLens | Covered institution/borrower balance-sheet context | Conditional; normally `not_applicable` or `context_only` until an eligible policy profile applies |

Each section contains its product, state, evidence class, canonical source URL,
optional source schema and source hash, clocks, rights status, eligibility and
quote flags, limitations, and product-native facts. Facts stay separated by
product; the protocol does not flatten them into one universal risk score.

Every section also contains `request_hash`, including `unavailable` and
`not_applicable` sections. It must equal the canonical hash of the exact
embedded request. This prevents evidence computed for one side, size, account,
venue, or price from being attached to another request.

Evidence states mean:

| State | Meaning |
|---|---|
| `eligible` | Complete, rights-usable evidence that may participate in the requested mode; it still does not imply a pass |
| `context_only` | Bounded research context; source hash and clocks are present, but it cannot satisfy a required real-money live dependency |
| `unavailable` | The product or required evidence cannot safely participate; never substitute zero, false, calm, or another product's value |
| `not_applicable` | The product does not apply to this request; source hash and evidence clocks are null and facts are empty |

Evidence classes are `observed`, `derived`, `structural`, `research`,
`restricted`, and `unavailable`. Rights status is independently labelled as
`licensed`, `allowed`, `metadata_only`, `restricted`, `unknown`, or `blocked`.
Required evidence with restricted, unknown, or blocked rights fails closed.

Usable `eligible` and `context_only` sections require `source_sha256`, `as_of`,
`knowledge_time`, and `expires_at`. Clock order is
`as_of <= knowledge_time <= retrieved_at < expires_at`. The evaluator checks
the per-product age budget at `evaluated_at`; schema validation alone cannot
establish freshness.

For every non-`unavailable` decision, receipt expiry is no later than the
earliest request expiry, requested receipt TTL, required-product expiry,
required-product `as_of + max_evidence_age_seconds`, or—when live—the verified
broker-preview expiry. The protocol TTL is capped at 3,600 seconds. A verifier
must independently reject a receipt whose declared expiry exceeds that semantic
ceiling, even when its content hash or HMAC is otherwise valid.

Only the Undertow section may set `executable_quote` true, and then it must also
be `eligible` and `real_money_eligible`. This flag is reserved for a reviewed,
tenant-local integration carrying an actual executable quote alongside
Undertow's analysis. Public Undertow depth or exit-cost research is not an
executable quote and must set the flag false. The receipt itself always has
`authority.is_executable_quote = false`.

## Broker preview reference

Live evaluation adds one independently labelled
`liquilens.broker-preview-reference.v1` object. It is a reference to a
tenant's broker-generated preview, not a broker order, acceptance,
acknowledgement, fill, entitlement, or credential. Its `account_id` and
`request_hash` must exactly equal the embedded request's agent account and
canonical request hash.

A `verified` preview requires a provider, preview identifier, HTTPS source URL,
source hash, retrieval clock, and expiration clock. The preview must remain
unexpired at `evaluated_at`. An `unavailable` preview preserves the explicit
failure and any safe reference metadata. `not_applicable` carries null provider,
preview, source, hash, and expiration fields with empty facts; this state is
valid for observe or paper workflows but cannot satisfy live mode.

The preview is deliberately separate from Undertow evidence. Undertow measures
requested-size liquidity and may carry a reviewed executable-quote flag; the
broker preview proves that the tenant/account's own broker previewed the exact
request. Neither source grants this library authority to submit it.

## Deterministic outcomes

Evaluation precedence is `unavailable`, then `hold`, then `limit`, then `pass`.
Lower-precedence reasons are retained when a higher-precedence result applies.

| Condition | Outcome or validation result |
|---|---|
| A product section is missing | Receipt is structurally invalid; issue no receipt |
| Any section's `request_hash` differs | Reject issuance or verification |
| Required evidence is unavailable, not applicable, restricted, unknown, blocked, not yet known, expired, or older than policy | `unavailable` |
| Live receipt is hash-only | `unavailable` |
| Live required evidence is not real-money eligible | `unavailable` |
| Live Undertow executable quote is absent | `unavailable` |
| Live broker preview is unavailable, not applicable, expired, for another account, or bound to another request | `unavailable` or verification rejection |
| Undertow requested size does not match the USD order notional, or its published rung does not match the requested size | `unavailable` |
| A USD policy is applied to non-USD notional | `unavailable` |
| Seiche regime appears in operator `hold_regimes` | `hold` |
| Notional, exit-cost, or spread threshold is exceeded | `limit`; exact order is not resized and `resubmit_required` is true |
| No unavailable, hold, or limit reason applies | `pass` for this exact order only |

`unavailable` is not bearish and `hold` is not a prediction. `pass` means only
that this operator-authored policy was satisfied using the embedded evidence at
the receipt clock. It is not approval, advice, a guarantee of execution, or a
statement that the market will remain liquid.

## Content identity and authentication

All hashes use `liquilens-hash-tree-v1`, which preserves JSON types and sorts
object keys without flattening product boundaries. Consumers must use the
provided canonical hash-tree implementation. Plain sorted JSON, language-native
object hashing, and an unrelated JSON canonicalization scheme are not
interchangeable. In particular, integer and floating-point values are distinct
typed leaves.

Identity is computed as follows:

1. `request_hash` is SHA-256 over the canonical full request.
2. Each evidence section repeats that exact `request_hash`.
3. The broker preview repeats the same `request_hash` and exact request
   `account_id`.
4. `policy_hash` is SHA-256 over the canonical full policy.
5. For receipt identity, omit `receipt_id` and `record_hash`, set
   `integrity.signature` to `null`, canonicalize the remaining receipt, and take
   SHA-256.
6. `record_hash` is that lowercase digest and `receipt_id` is
   `trade_safety_` plus its first 24 hexadecimal characters.

The `sha256` integrity profile has null `key_id` and `signature`. It detects
mutation after a trusted retrieval but does not authenticate an issuer and can
never produce a non-unavailable live result.

The tenant-local `hmac-sha256` profile requires a non-empty `key_id` and signs
the ASCII lowercase `record_hash` with domain separation:

```text
HMAC-SHA256(
  tenant_key,
  b"liquilens.trade-safety-receipt.v1\n" + record_hash.encode("ascii")
).hexdigest()
```

Keys are never carried in the request, receipt, logs, FDC3 context, or product
evidence. Distribution, rotation, tenant/account binding, and revocation remain
operator responsibilities. Verification uses constant-time signature
comparison. A valid signature authenticates this receipt; it does not widen the
all-false authority object.

## Verification order

Consumers must verify before displaying or acting on an outcome:

1. Enforce bounded, finite JSON input and exact object keys.
2. Validate the request, policy, evidence, broker preview, receipt, and applicable transport
   schemas.
3. Check request, evidence, and receipt clocks against the consumer's current
   UTC time. Evidence age is measured from `as_of`, not from a later retrieval
   or processing clock; reject evidence and broker previews whose `retrieved_at`
   is still in the future.
4. Recompute `request_hash`; compare it with the receipt, all three evidence
   sections, and broker preview. Require the preview `account_id` to equal the
   request account.
5. Recompute `policy_hash` and confirm `request.policy_ref` matches the embedded
   policy identity.
6. Recompute `record_hash` and the derived `receipt_id`.
7. In live mode, require the broker preview to be `verified` and unexpired.
8. For HMAC receipts, resolve `key_id` inside the same tenant boundary and
   verify the domain-separated signature. Reject a missing or unexpected key.
9. Re-run the deterministic decision and require byte-for-byte semantic
   equality with the embedded decision.
10. Require the immutable authority object.
11. At an execution boundary, atomically consume `receipt_id` once and reject
    every replay. The receipt format is order-bound and short-lived, but its
    offline verifier is not a replay database.

Schema validation alone cannot perform hash comparison, clock ordering,
freshness evaluation, cross-object request/account equality, HMAC authentication, deterministic policy replay, or
same-tenant key lookup. Use the protocol verifier for those checks.

## FDC3 integration

`com.liquilens.trade-safety-receipt` is a proprietary FDC3 context that wraps
one full receipt. Its `liquilensTradeSafetyReceiptId` must identify the same
receipt carried in the payload. Receiving the context through an FDC3 desktop
does not verify it; the listener must still run the complete verifier.

`trade-safety-intents.json` supplies FDC3 2.2 App Directory fragments for:

- `liquilens.EvaluateTradeSafety`, which accepts an `fdc3.order` workflow
  context and returns a `com.liquilens.trade-safety-receipt`; and
- `liquilens.ViewTradeSafetyReceipt`, which displays or inspects an existing
  receipt without executing it.

FDC3 2.2 `fdc3.order` is an experimental reference context and may contain only
an order identifier. It is never the canonical Trade Safety Request. An intent
handler must resolve it into the complete request shape, bind the accountable
agent/operator/tenant/account and policy, and reject identifier-only or
under-specified orders. The intent is workflow routing, not RPC, OMS approval,
or order execution. A real application still needs its own App Directory
record, listener, tenant authentication, and operator-controlled deployment.

## Integration checklist

An agent, copilot, OMS adapter, or desktop integration should:

1. Pin the four core v1 schema URLs and a compatible protocol verifier.
2. Build the complete request once; reject missing order semantics and unknown
   execution-material extensions.
3. Compute `request_hash` before requesting product context.
4. Obtain Seiche and Undertow evidence for that exact request. Add LiquiLens
   only under the declared applicability and eligibility policy.
5. For live mode, obtain a tenant/account-local broker preview bound to the same
   `request_hash`; never put broker credentials in the protocol object.
6. Preserve every evidence and preview clock, source hash, rights status, limitation, and
   explicit unavailable state.
7. Issue a short-lived receipt without an order-execution callback.
8. Verify the full receipt in the consuming trust boundary. For live mode,
   authenticate with the correct tenant-local HMAC key.
9. Treat `limit` as a request to construct and re-evaluate a new exact order;
   never resize the old request in place.
10. Treat `hold`, `unavailable`, expiration, unknown extension semantics, and all
   verification errors as no-go states.
11. Keep a bounded audit record containing the request/receipt identities,
    issuer and product versions, outcome/reasons, clocks, and verification
    result—without secrets or restricted payloads.

The protocol is intentionally useful without becoming a hidden dependency or
execution chokepoint. Adoption should come from a stable open contract, thin
adapters, deterministic conformance tests, transparent limitations, and
operator choice.
