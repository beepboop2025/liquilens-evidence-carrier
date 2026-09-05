# Trade Safety Infrastructure and Adoption Plan

Status: staged delivery contract; core published, gateway `0.2.2` source candidate

Owner: Liquidity Lab

Initial products: Seiche, Undertow, LiquiLens
Initial operating boundary: observation and paper trading; live trading fails closed

## 1. Objective

Make Seiche and Undertow the default context and liquidity-safety dependency for
AI trading copilots, trading agents, algorithms, and human order workflows.
LiquiLens supplies conditional institution and counterparty context when the
instrument, issuer, lender, or account exposure is inside its supported domain.

The system cannot force every independent agent on the internet to use it. It
can make adoption compelling, cheap, interoperable, and—inside participating
brokers, OMS/EMS systems, funds, and agent runtimes—technically mandatory.

The enforceable product is not another trading model. It is a deterministic
pre-trade safety gateway that issues a short-lived receipt bound to one exact
order and one operator-owned policy.

### Current state as of 2026-09-04

Implemented in the gateway `0.2.2` source candidate:

- fixed Seiche, Undertow, and conditional LiquiLens native-contract adapters;
- strict request, policy, source, rights, clock, proof, and receipt validation;
- a server-owned safety envelope that callers can tighten but cannot weaken;
- dormant x402 v2 paid access with exact request/offer/extension binding,
  Bazaar discovery, durable replay and reconciliation state; and
- opt-in privacy-minimized MCP, assessment, and x402 funnel telemetry.

Not yet claimed: a merged or published `0.2.2` artifact, a hosted paid gateway,
an owner-authorized settlement canary, active design partners, unique agents or
payers, broker/OMS enforcement, or a live-money route. Existing product-level
x402 experiments are separate surfaces and do not prove this gateway is active.

## 2. Non-negotiable boundaries

1. Seiche supplies funding and system context. It does not choose securities.
2. Undertow supplies position-sized liquidity and exit-risk context. Its
   current public reading is an estimate, not an executable quote.
3. LiquiLens supplies institution evidence only where applicable. Its current
   construction-PIT diagnostics are not real-money eligible.
4. The customer's policy maps evidence to `pass`, `limit`, `hold`, or
   `unavailable`. The products do not author a universal risk appetite.
5. Missing, stale, restricted, unsupported, or malformed evidence fails closed.
6. A `pass` means only that an exact request satisfies an exact operator policy.
   It is not advice, approval, an order, a credit rating, or a promise of fill.
7. No public gateway holds broker credentials or submits an order.
8. A live receipt requires authenticated integrity, real-money-eligible product
   evidence, an executable Undertow quote, and a broker-owned exact-order
   preview. Current public product contracts do not satisfy those gates.
9. Every execution-material order mutation invalidates the receipt.
10. Rights and evidence clocks remain product-specific; they are never averaged
    into a universal confidence score.

## 3. Target architecture

```text
AI copilot / agent / algorithm / human OMS
                    |
                    | exact proposed order + operator policy
                    v
         Trade Safety Gateway / sidecar
          |            |              |
          |            |              +--> Broker WhatIf / margin preview
          |            +-----------------> Undertow exit/liquidity context
          +------------------------------> Seiche funding/system context
                          |
                          +---------------> LiquiLens institution context
                                            when policy says applicable
                    |
                    v
       deterministic, order-bound evaluation
                    |
                    v
       short-lived authenticated safety receipt
                    |
                    v
  broker/OMS adapter verifies exact order, account, policy,
  evidence clocks, receipt expiry, signature, and one-time use
                    |
        reject / resubmit / human review / submit

Every stage emits trace and audit metadata. The broker retains its own controls
and direct authority over market access.
```

### Control plane versus agent plane

- MCP, A2A, FDC3, OpenBB, and natural-language explanations are the agent and
  human workflow plane.
- REST/OpenAPI, SDK middleware, OMS/FIX adapters, and broker wrappers are the
  deterministic control plane.
- An agent can ignore an MCP suggestion. It cannot bypass a gateway if the raw
  order tool and broker credential are unavailable to it.

## 4. Canonical contracts

### 4.1 Trade safety request

The request binds:

- agent, operator, tenant, account, runtime, strategy, and authorization scope;
- instrument class, symbol and canonical identifiers;
- side, order type, quantity, notional, currency, prices, venue and time in
  force;
- observation, paper, or live mode;
- creation and expiry clocks; and
- exact policy identity and namespaced extensions.

### 4.2 Product evidence sections

Every receipt contains independent Seiche, Undertow, and LiquiLens sections.
Each section retains:

- product and source schema identity;
- exact request hash;
- source byte hash and canonical URL;
- evidence class and rights status;
- as-of, knowledge, retrieval and expiry clocks;
- `real_money_eligible` and `executable_quote` booleans;
- typed availability state; and
- explicit limitations plus a small allowlisted fact projection.

`not_applicable` is different from `unavailable`. An inapplicable LiquiLens
section does not block a policy that does not require LiquiLens. An unavailable
required Seiche or Undertow section does.

### 4.3 Broker preview reference

A live receipt must include a short-lived broker preview bound to the same
request hash and account ID. It records provider, preview identity, retrieval
and expiry clocks, source digest, limitations, and privacy-minimized facts. It
does not contain a broker credential or become an order.

### 4.4 Policy

The customer owns policy identity, version, required products, evidence-age
budgets, held Seiche regimes, notional/exit-cost/venue-spread limits, and any
namespaced extensions. Core invariants cannot be disabled:

- missing evidence fails closed;
- live requires an executable Undertow quote;
- live requires a broker preview; and
- automatic order resizing is prohibited.

### 4.5 Receipt

The receipt binds the full request, request hash, policy and policy hash, all
evidence sections, broker preview, deterministic outcome, issuer, expiry,
authority boundary, content-derived identity and integrity profile.

- `sha256` is tamper-evident but unauthenticated and cannot authorize live use.
- `hmac-sha256` authenticates a tenant-local gateway and verifier. Public-key
  signing is a planned compatible profile after key discovery and rotation are
  specified.

## 5. Initial safety semantics

Evaluation order is deterministic:

1. Validate strict unique-key finite JSON and exact schemas.
2. Verify request/policy identities and every request hash binding.
3. Verify independent product rights, state, clocks, expiry and policy age.
4. In live mode, require real-money eligibility, executable quote, authenticated
   receipt, and broker preview.
5. Apply explicit policy holds.
6. Apply notional, exit-cost, and venue-spread limits.
7. Produce one outcome and stable reason codes.

Outcome precedence is `unavailable` before `hold`, `hold` before `limit`, and
`limit` before `pass`. A failed dependency cannot be hidden by a favorable
reading from another product.

## 6. Product adapter plan

### Seiche adapter

Current gateway source: fixed cache-only REST projection
`GET /api/trade-safety/risk-context`, schema `seiche.risk-context.v1`.

Near-term work:

- add authenticated snapshot identity without changing the projection's
  context-only authority;
- normalize ready/warn/hold/unavailable and freshness reason codes;
- bind public payload hashes to existing notary/attestation identity;
- reject invalid subscriber credentials instead of silently downgrading when a
  requested fidelity tier is mandatory;
- add scoped service identities and state-change webhooks; and
- keep broad context separate from executable market claims.

### Undertow adapter

Current gateway source: hosted MCP tool `trade_safety_exit_context`, restricted
to observe/paper BTC/USD sells and exact published rungs of USD 1,000, 10,000,
100,000, or 1,000,000.

The read-only sandbox adapter treats the current committed roster—Binance,
Bitfinex, Coinbase, Gemini, Kraken, and OKX—as an explicit coverage contract.
It uses exit-cost and venue-spread facts only when every roster venue is present
and measured at the exact rung. A partial or extra venue map is unavailable,
and complete roster coverage is not represented as complete market coverage.

Near-term work:

- reject boolean, non-finite, non-positive, out-of-grid and beyond-depth sizes;
- stop silently snapping unsupported requests to a nearest rung;
- publish typed output schemas with source quorum, rights, clocks and expiry;
- add buy-side, fee, urgency, venue-constraint and uncertainty semantics;
- build post-trade expected-versus-realized calibration receipts; and
- retain `paper_only`, `reduce_only`, and `real_orders=false` until forward
  shadow evidence supports a narrowly reviewed live cap.

### LiquiLens adapter

Initial use is optional and context-only. A supplied institution slug can attach
the exact public institution record, period end, historical-evidence status and
real-money eligibility state. Current scores or grades do not decide a trade.

Near-term work:

- map issuer, counterparty, lender and account exposures to stable identifiers;
- preserve complete public-availability and revision history;
- finish forward design-partner validation and promotion governance; and
- add private-tenant evidence delivery without disclosing restricted data.

### Broker adapter

Start with paper environments and deterministic preview endpoints:

- Alpaca paper account/order wrapper;
- Interactive Brokers WhatIf composition;
- QuantConnect/LEAN brokerage-model wrapper; and
- one institution-owned OMS/FIX canary.

The adapter exposes only a wrapped order action to the agent. Raw create-order
tools and credentials stay outside the agent's tool inventory.

## 7. Distribution plan

One canonical schema and conformance suite feed every channel:

| Channel | Role | Enforcement |
|---|---|---|
| REST/OpenAPI | deterministic assessment and verification | yes, when placed on order path |
| x402 v2 + Bazaar | accountless paid access and machine discovery for one exact assessment | access control only; never order authority |
| MCP | agent discovery, assessment and explanation | no by itself |
| A2A | long-running portfolio/scenario tasks and artifacts | no by itself |
| Python SDK | decorators, middleware, receipt verifier | yes in wrapped clients |
| TypeScript SDK | tool middleware and web/Node clients | yes in wrapped clients |
| OpenBB provider/MCP | fastest financial-copilot distribution | workflow only initially |
| QuantConnect/LEAN | custom data, risk model, brokerage wrapper | yes in wrapper |
| FDC3 | institutional desktop context and intent | workflow only |
| FIX/OMS/EMS | order-path validation before submission | strongest enforcement |
| RFC 9727 catalog | automated discovery and version metadata | discovery |
| CloudEvents/OTel/OpenLineage | audit transport, trace and lineage | evidence, not decision |

Every adapter must call the canonical service or verifier. It must not reproduce
Seiche, Undertow, or LiquiLens calculations.

## 8. Delivery phases and release gates

### Phase 0 — contract and conformance

Deliver:

- request, policy, broker-preview and receipt schemas;
- dependency-free Python issuer/verifier;
- hash-only and HMAC profiles;
- CLI and offline read-only MCP verification;
- FDC3 context/intent assets;
- golden pass/limit/hold/unavailable and tamper vectors; and
- detailed protocol and threat-model documentation.

Gate: Python, schema, Node canonicalization, CLI, MCP, lint, type and packaging
tests pass from the exact commit.

### Phase 1 — public sandbox

Deliver:

- bounded hosted `/v1/check`, `/healthz`, `/v1/capabilities`, OpenAPI and MCP;
- fixed upstream allowlist and no redirects/user URLs;
- Seiche/Undertow parallel fetch and conditional LiquiLens fetch;
- strict source-shape, byte, timeout, rights and freshness checks; and
- explicit `sandbox`, `paper_only`, `no_broker_credentials` headers/metadata.

Gate: unsupported sizes never call Undertow; source errors produce unavailable;
no public request can produce a live pass; load and abuse tests pass.

### Phase 2 — official SDKs and first adapters

Deliver Python and TypeScript SDKs, OpenBB provider, LEAN custom data/risk model,
Alpaca paper wrapper, IBKR preview composer, FDC3 app record, examples and
framework-neutral `before_order` middleware.

The Python `before_order`, paper-only synchronous and asynchronous gateways,
sealed authorization object, immutable execution binding, and bounded local
replay consumers are implemented in `liquilens_evidence.order_guard`. The
agent-facing gateway owns its clock and pins account, tenant, operator, agent,
runtime, strategy, policy hash, issuer, and integrity-key identity. The
protected callback also requires tenant-authenticated HMAC integrity;
hash-only receipts remain conformance artifacts. Live routing remains held until
a durable broker-idempotency and uncertain-outcome reconciliation state machine
is specified and independently reviewed.

Gate: each consumer test installs from a pinned artifact in a clean environment
and proves a missing/expired/mismatched receipt blocks its paper order path.

### Phase 3 — shadow validation

Run with three to five design partners without live blocking. Capture proposed
orders, product evidence, broker previews, human overrides, simulated outcome,
and privacy-minimized realized execution metrics.

Gate: forward coverage, false-hold rate, unavailable rate, latency, calibration,
rights, incident response, deletion and restore criteria meet declared policy.

### Phase 4 — narrow live canary

One broker/account/asset/venue/policy at a time. The broker retains direct and
exclusive market-access controls. Start with a small cap, manual override,
automatic rollback and a signed release/activation/recovery receipt.

Gate: independent legal/compliance review, service identity, key rotation,
replay protection, one-time receipt consumption, backup/restore proof, exact
runtime identity, p95/p99 SLO, and owner-authorized activation.

### Phase 5 — institutional and ecosystem adoption

Publish conformance certification, reference OMS/FIX integration, case studies
from actual shadow/live evidence, service SLOs, enterprise policy management,
private deployment and audit export. Submit or update official MCP, OpenBB,
FDC3, QuantConnect and broker ecosystem listings without describing a
submission as acceptance.

### Phase 6 — coverage expansion

Add asset/venue coverage only after instrument identity, rights, clocks,
uncertainty, execution semantics and calibration pass the same gates. Likely
order: BTC/ETH paper, liquid US equities/ETFs, Treasury/rates workflows, major
FX, then licensed commodity and credit depth.

## 9. Deployment topology

Target operating model:

- GitHub: source and immutable release artifacts only;
- Hetzner: scheduled collection, sealed snapshot creation, builds, tests,
  conformance and release orchestration;
- Railway: stateless free/read-only public routes; an x402-enabled gateway must
  instead run as a stateful single writer with its private persistent journal
  volume, or use a separately reviewed shared transactional journal; and
- broker/customer environment: credentials, policy secrets, HMAC keys, receipt
  consumption and any order submission.

Release evidence keeps gate, snapshot, activation, public proof, recovery and
rollback as separate receipts. A healthy process or endpoint is not sufficient
proof of semantic freshness or exact release identity.

## 10. Reliability and security requirements

- Serve only sealed last-known-good snapshots; never rebuild a model on an order
  request.
- Bounded body, response, timeout, concurrency, retry and cache policies.
- Fixed upstream origins; no request-controlled URLs or redirects.
- OAuth/OIDC service identities with issuer, audience, scopes, revocation and
  rotation for enterprise use.
- No bearer credentials in query strings or logs.
- Exact order/account/tenant/policy binding and short expiry.
- Replay protection and one-time receipt consumption at the broker adapter.
- No automatic order resizing or hidden fallback to anonymous fidelity.
- Prompt-injected source strings remain untrusted data and cannot become tool
  instructions.
- Rights-restricted evidence is referenced or withheld, not republished.
- Independent monitoring for source clock, retrieval clock, snapshot identity,
  service health, outcome distribution and failed-closed rate.

## 11. Commercial model

### Free/open layer

- Apache-licensed schemas, verifier, conformance tests and client middleware;
- public sandbox with small quotas;
- free `/v1/check` and MCP assessment for adoption and evaluation;
- public Seiche/Undertow context and evidence health; and
- transparent status, methods, limitations and misses.

### Professional layer

- higher quotas, richer Undertow/Seiche views, replay, webhooks and team policy;
- accountless x402 access after an edge-enforced free quota, with the same
  safety outcome and no claim that payment buys favorable treatment;
- OpenBB/LEAN/copilot integrations; and
- post-trade calibration and audit export.

### Enterprise layer

- private gateway/sidecar, service identities, policy administration, private
  LiquiLens context, customer keys, SLO, retention/deletion controls, OMS/FIX
  adapters, on-prem/private-cloud operation and reviewed support.

The durable moat is the verified history of evidence, policy, decisions,
overrides, misses, and expected-versus-realized outcomes—not a generic LLM or a
claim that public data alone predicts returns.

The source gateway itself has no application-level quota, and its x402 route
currently returns the same assessment as free `/v1/check`. Therefore Bazaar
discovery or a configured 402 response is not a commercial conversion. A paid
deployment needs an honest, published free quota or another reviewed service
difference at the edge, followed by reconciled settlements and repeat use.

## 12. Adoption metrics

Do not count catalog impressions or anonymous bot opens as customers. Track:

- installed and active SDK/adapters;
- unique verified tenants and service identities;
- checks per day and percentage tied to paper/live order attempts;
- percentage of protected order routes that cannot bypass the verifier;
- pass/limit/hold/unavailable and human-override rates;
- stale/source-failure and unsupported-coverage rates;
- receipt verification and replay-rejection rates;
- p50/p95/p99 assessment latency;
- expected-versus-realized exit-cost coverage; and
- signed design partners, pilots, renewals and paid production accounts.

The leading milestone is one enforceable broker or OMS reference integration,
not a large number of optional MCP installs.

## 13. Immediate implementation sequence

1. Finish independent review of the gateway `0.2.2` x402 journal, policy floor,
   telemetry, packaging and recovery behavior; merge through protected main.
2. Publish the independently signed, attested `0.2.2` gateway artifact without
   describing artifact availability as hosted activation.
3. Deploy the free read-only sandbox with live mode hard-blocked and prove exact
   source/build identity, semantic freshness, rollback, edge limits and uptime.
4. Run one owner-authorized, low-value x402 settlement canary; reconcile local,
   facilitator and chain evidence before announcing paid availability.
5. Publish framework-neutral client examples and submit Bazaar/catalog metadata;
   measure activation and repeat calls with the privacy-safe scorecard in
   `TRADE-SAFETY-TRACTION.md`.
6. Ship the first broker-adjacent paper wrapper that makes a fresh verified
   receipt structurally unavoidable, then recruit three shadow design partners.
7. Add broker preview, asymmetric signing, protected-order telemetry and one
   narrow owner-authorized live canary only after all promotion gates pass.

This sequence makes the system useful immediately without pretending that the
current research products already possess execution authority.

## 14. Primary interoperability references

- [Model Context Protocol 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28)
- [x402 protocol specification v2](https://github.com/x402-foundation/x402/blob/main/specs/x402-specification-v2.md)
- [x402 Bazaar discovery extension](https://github.com/x402-foundation/x402/blob/main/specs/extensions/bazaar.md)
- [OpenAPI 3.2](https://spec.openapis.org/oas/v3.2.0.html)
- [RFC 9727 API Catalog](https://www.rfc-editor.org/rfc/rfc9727.html)
- [Agent2Agent v1.0](https://a2a-protocol.org/v1.0.0/specification/)
- [FINOS FDC3 2.2](https://fdc3.finos.org/docs/fdc3-standard)
- [OpenBB provider extensions](https://docs.openbb.co/odp/python/developer/extension_types/provider)
- [QuantConnect/LEAN risk management](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/risk-management/key-concepts)
- [Interactive Brokers order WhatIf](https://ibkrcampus.com/docs/web-api/v1/endpoints/orders/preview-order-what-if-order)
- [FIXatdl](https://www.fixtrading.org/standards/fixatdl-online/)
- [SEC market-access control guidance](https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0)
- [EU RTS 6 algorithmic-trading controls](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32017R0589)
- [CloudEvents](https://github.com/cloudevents/spec)
- [OpenTelemetry](https://opentelemetry.io/docs/specs/otel/)
- [OpenLineage](https://github.com/OpenLineage/OpenLineage/blob/main/spec/OpenLineage.md)
