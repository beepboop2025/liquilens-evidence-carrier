# LiquiLens financial agent platform

Updated 2026-10-08. Product direction for LIQUILENS PRIVATE LIMITED, grounded in
carrier source `3e52f324342634fdb2b77acd73a86cd6a87be602` and this private paper
agent-tool change. This is a strategy and implementation map, not a claim of
deployed execution, customer adoption or industry-wide reliance.

## Intended business

Make LiquiLens, Seiche and Undertow reusable infrastructure for financial AI
agents: obtain evidence, check a proposed action, apply the customer's policy,
execute through an authorized connector, and retain the result for review and
reconciliation. Support both customers building their own agents and a managed
execution service for customers who delegate that work.

The user-supplied *Banker bots* article discusses agents changing banking work
and potentially moving deposits more readily. The opportunity inferred from it
is recurring decisions across institutions, funding and execution. The article
does not establish demand for our products or validate an autonomous strategy.
The earlier treasury review kit is one application on this infrastructure.

| Product | Contribution | Boundary |
| --- | --- | --- |
| LiquiLens | Institution, counterparty and corporate funding evidence | Dated disclosures are not current balances, credit approval or payment authority |
| Seiche | Funding and money-market conditions | Missing/stale data stay explicit; a regime is not a trading instruction |
| Undertow | Exit-cost and venue-liquidity evidence for covered instruments/sizes | Estimates and partial books are not executable quotes or guaranteed fills |
| Shared carrier/gateway | Exact-proposal evidence-policy evaluation | Independent source clocks, rights, identity, policy and expiry |
| Private execution service | Exact checked action for an authorized account | Permissions, submission, fills and reconciliation are separate states |

Require the products relevant to each workflow. Do not add an irrelevant bank
score to every trade merely to force all three products into it. Dependence
should come from coverage, reliability, integration and an auditable history.
Customers can make our guard mandatory inside their participating order route;
we cannot require unaffiliated agents or the financial industry to use it.

## Current implementation map

| Capability | Source status | Next proof needed |
| --- | --- | --- |
| Three-product evidence APIs/MCP and portable carrier | Existing contracts/integrations | Current rights, coverage and source/service reliability for the customer's workload |
| Trade Safety gateway; Python/TypeScript guards | Existing; public gateway is read-only | Customer policy and private issuer integration |
| Alpaca paper adapter with durable journal | Existing | Authorized customer installation and observed broker outcomes |
| Deterministic BTC/USD paper copilot | Existing, including additional account controls | Current source admission and authorized account activation |
| Runtime-neutral private agent tools | Added here, using the same guard/journal | Authenticated host integration and external pilot |
| Live-money execution | Unsupported here | Broker/account mandate, execution-grade sources, full account controls, deployment/recovery qualification and specific activation |
| Transfers, credit, collateral, settlement | Possible later workflows | Dedicated action contracts, connectors and domain validation |

The [agent guide](../integrations/alpaca-paper/AGENT-TOOLS.md) includes an offline
walkthrough. No caller argument selects keys, clocks, broker URLs, enable flags
or alternate policy definitions. Synthetic tests do not prove source admission,
account activation or actual broker fills.

## Delivery sequence for evidence and execution

1. **External trading-agent integration.** Connect an existing customer runtime
   to the gateway and private paper tools. Measure integration effort and
   unsupported source/instrument cases. Keep exact binding, policy, rights,
   replay protection and uncertainty recovery in the route.
2. **Recurring operation.** Independently observe broker acceptance, terminal
   state and fills. Demonstrate timeout/restart recovery and operator stop.
   Evaluate whole-account exposure and pending orders alongside receipts.
3. **Narrow live service.** Choose one broker, instrument scope and customer
   mandate. Add entitled execution-grade data, account controls, monitoring and
   operational recovery. Developing the platform is not permission to activate
   a live account. Implement a separately qualified live adapter; do not weaken
   the paper-only guard into a bypass.
4. **Treasury integration.** Reuse identity, provenance and audit contracts with
   cash balances, rates/fees, transfer and settlement connectors. Deposit
   movement needs explicit account authority. Quarterly institution evidence
   cannot substitute for a current cash balance.
5. **Other financial workflows.** Add portfolio, collateral and credit paths
   when a recurring customer problem warrants their dedicated contracts. Each
   needs independent validation of its evidence and actions.

A second agent runtime is a useful portability check. A trading API alone does
not implement the rest of financial operations.

## Commercial validation

First customer hypothesis: teams already operating financial agents that need
attributable evidence and enforceable controls. Second: treasury platforms
needing institution and funding context. Evidence/API access and a private
execution service can have different contracts, prices and service levels.
Demand and pricing remain unvalidated; this change activates no payment rail.

Suggested pilot **targets, not traction**: three external design partners, one
supported workflow integrated within a working day, one independent recurring
integration over four weeks, and one paid renewal or committed contract. Validate
these targets with customers before using them in revenue/capacity forecasts.

Measure separately:

- Attributed external organizations and recurring workflows, excluding demos,
  probes, verification and unclassified requests.
- Eligible coverage by workflow/instrument, source observation freshness,
  rights, latency and unavailable-source time.
- Receipt outcomes and false blocks investigated with customers; fewer blocks
  alone do not establish better safety.
- Authorized submission attempts, broker acknowledgments, observed fills and
  unresolved cases, each with its own evidence and denominator.
- Retained organizations, contractual revenue, cash received and cost to serve.
  Tool calls and payment endpoints do not establish these measures.

The earlier [adoption plan](TRADE-SAFETY-ADOPTION-PLAN.md) provides the gateway
roadmap. Use this document's source map for the newer durable adapter and agent
interface. No customer outreach or account activation is part of this change.

## Concurrent work and release ownership

Work is isolated on `codex/agent-execution-tools-20261008` in the carrier repo.
It does not change active Seiche recovery/release, product data, payment details,
distribution work or the separate treasury-review PR. Qualify this change in
the carrier release lane. Broker secrets do not belong in public MCP
registration; existing copilot timers remain under their operator's ownership.
