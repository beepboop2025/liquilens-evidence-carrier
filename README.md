# LiquiLens Evidence Carrier

[![CI](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/workflows/ci.yml/badge.svg)](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/beepboop2025/liquilens-evidence-carrier)](https://github.com/beepboop2025/liquilens-evidence-carrier/releases)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

LiquiLens Evidence Carrier is a transport-neutral contract for moving financial
evidence through files, warehouses, event buses, observability systems, data
catalogs, FDC3 desktops, notebooks, citations, and AI agents without dropping
provenance, rights, freshness, or authority boundaries.

The carrier is infrastructure for inspection and reproducibility. It is not an
order, recommendation, credit rating, market-data entitlement, or endorsement
by Bloomberg, LSEG, FactSet, FINOS, or any other platform.

This source tree is versioned for `v0.17.1`; a source version alone is not
publication proof. It is not published as `v0.17.1`. The immutable annotated
`v0.17.0` tag object
`cb85e527c2b74abf476fd9a01b73b2235ce976b7` targets protected-main merge
`edde9b92ad9851d2974b91326a8c3877f4386d3a`, but its
[release run 33585764285](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33585764285)
failed at the commit-signature gate before any artifact was built, attested, or
published. There is no v0.17.0 GitHub release or official MCP Registry record.
The latest signed, published core release therefore remains `v0.16.0`, released
on 2026-08-29 from reviewed commit
`410f7d91114fba715e9a9ae830faa775064a4502`. See
[`docs/RELEASE-0.16.0.md`](docs/RELEASE-0.16.0.md) for its exact receipts,
[`docs/RELEASE-0.17.0.md`](docs/RELEASE-0.17.0.md) for the failed attempt, and
[`docs/RELEASE-0.17.1.md`](docs/RELEASE-0.17.1.md) for the recovery candidate
boundary.

## Why it travels

One verified JSON object can be embedded in:

- FDC3 contexts and app-directory workflows;
- CloudEvents and OpenTelemetry logs;
- OpenLineage custom facets and data catalogs;
- Arrow or Parquet schema metadata;
- dbt warehouse tests, CSV, SQL, and spreadsheets;
- CSL-JSON citations and PROV-O knowledge graphs; and
- MCP or other agent responses.

Every full carrier preserves `event_time <= knowledge_time <= as_of`, source
hashes, explicit redistribution rights, a content-derived identity, and an
all-false execution/recommendation/credit-rating boundary. Restricted or
unknown rights fail closed; incomplete or expired evidence is redacted to a
separately identified reference rather than silently upgraded.

## Install and verify

```bash
# Source checkout (main may contain post-release documentation)
uv sync --locked
uv run liquilens-evidence --help

# Signed v0.16.0 wheel; checksum verified against the release manifest
python -m pip install 'https://github.com/beepboop2025/liquilens-evidence-carrier/releases/download/v0.16.0/liquilens_evidence-0.16.0-py3-none-any.whl#sha256=317c06b728a2b087eca3d51ba1cdf3f7570e4078334829959008ceb0a29dfd11'
liquilens-evidence issue examples/descriptor.json > carrier.json
liquilens-evidence verify carrier.json --as-of 2026-08-24T12:00:00Z
liquilens-evidence convert carrier.json --format fdc3
```

Published release `v0.16.0` provides a wheel and checksum manifest. The Python
runtime has no third-party dependencies. A Node.js verifier is also
included for cross-language `liquilens-hash-tree-v1` identity checks:

```bash
node protocol/verify_hash_tree_v1.mjs --artifact evidence-carrier carrier.json
node protocol/verify_hash_tree_v1.mjs --artifact fleet-brief fleet-brief.json
node protocol/verify_hash_tree_v1.mjs --artifact trade-safety-receipt receipt.json
```

## Canonical contract identities

| Contract | Canonical identity | Availability at this source checkpoint |
|---|---|---|
| Full carrier | `https://liquilens.in/protocol/liquilens-evidence-carrier-v1.schema.json` | Published and hosted |
| Redacted reference | `https://liquilens.in/protocol/liquilens-evidence-carrier-reference-v1.schema.json` | Published and hosted |
| Four-product fleet brief | `https://liquilens.in/protocol/liquilens-fleet-brief-v1.schema.json` | Published and hosted |
| Trade Safety request | `https://liquilens.in/protocol/liquilens-trade-safety-request-v1.schema.json` | v0.17.1 candidate; not hosted yet |
| Trade Safety policy | `https://liquilens.in/protocol/liquilens-trade-safety-policy-v1.schema.json` | v0.17.1 candidate; not hosted yet |
| Broker preview reference | `https://liquilens.in/protocol/liquilens-broker-preview-reference-v1.schema.json` | v0.17.1 candidate; not hosted yet |
| Trade Safety receipt | `https://liquilens.in/protocol/liquilens-trade-safety-receipt-v1.schema.json` | v0.17.1 candidate; not hosted yet |
| FDC3 Trade Safety receipt | `https://liquilens.in/protocol/fdc3/com.liquilens.trade-safety-receipt.schema.json` | v0.17.1 candidate; not hosted yet |
| FDC3 context | `https://liquilens.in/protocol/fdc3/com.liquilens.evidence.schema.json` | Published and hosted |
| OpenLineage facet | `https://liquilens.in/protocol/openlineage/liquilens-evidence-facet.schema.json` | Published and hosted |

The candidate identities above are stable schema `$id` values, not evidence of
public retrieval. Treat them as hosted only after the exact tagged bytes are
deployed to LiquiLens Pages and independently retrieved from those URLs.

The current contracts are v1. Source candidate `v0.17.1` adds Trade Safety
without changing the previously published Carrier or Fleet Brief semantics.
Release `v0.16.0` remains the production pin. Its signed release
workflow is [run 33261143612](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33261143612),
the wheel SHA-256 is
`317c06b728a2b087eca3d51ba1cdf3f7570e4078334829959008ceb0a29dfd11`, and
the MCPB SHA-256 is
`c44b13b2efc4622a8ecfc06848f32358982dd2a9458a271e1ed77d646791961a`.
Production integrations can pin `v0.16.0`; separately released container,
skill, plugin, browser, and package-manager channels retain their own verified
versions. Use the canonical URLs as schema identities; use them for public
discovery only after their availability row says they are hosted.

## Order-bound Trade Safety Receipts

`liquilens.trade-safety-receipt.v1` composes independent Seiche funding/system
context, Undertow position-sized exit context, optional LiquiLens institution
context, an operator-authored policy, and a broker-preview reference into one
short-lived receipt bound to one exact proposed order. Missing, stale,
restricted, mismatched, or future-dated inputs fail closed.

```bash
liquilens-evidence issue-trade-safety \
  --request examples/trade-safety/request.paper.json \
  --evidence examples/trade-safety/evidence.paper.json \
  --policy examples/trade-safety/policy.paper.json \
  --broker-preview examples/trade-safety/broker-preview.paper.json \
  --issuer examples/trade-safety/issuer.paper.json \
  --as-of 2026-09-02T12:00:00Z > receipt.json

liquilens-evidence verify-trade-safety receipt.json \
  --as-of 2026-09-02T12:00:30Z
```

A hash-only receipt supports observation and paper conformance. A live `pass`
requires tenant-local authenticated integrity, real-money-eligible required
evidence, an executable Undertow quote, and an unexpired broker preview bound to
the same request and account. Current public adapters satisfy none of those live
gates. A `pass` is not advice, broker approval, or an execution instruction;
the immutable authority object keeps execution, recommendation, allocation,
credit-rating, and executable-quote authority false. See
[`docs/TRADE-SAFETY-RECEIPT-V1.md`](docs/TRADE-SAFETY-RECEIPT-V1.md), the
[`adoption plan`](docs/TRADE-SAFETY-ADOPTION-PLAN.md), and the
[`read-only sandbox gateway`](integrations/trade-safety-gateway/README.md).

## Four-product fleet briefs

`liquilens.fleet-brief.v1` bundles already-issued native carriers without
flattening LiquiLens, Seiche, Undertow, and Palimpsest into one score. Each brief
contains exactly one rights-aware section per product and explicitly preserves
`full`, `metadata_only`, `unavailable`, `rejected`, or `missing` state.

```bash
liquilens-evidence issue-brief \
  --liquilens ./liquilens.carrier.json \
  --seiche ./seiche.carrier.json \
  --undertow ./undertow.carrier.json \
  --palimpsest ./palimpsest.carrier.json \
  --as-of 2026-08-25T00:00:00Z > fleet-brief.json

liquilens-evidence verify-brief fleet-brief.json \
  --as-of 2026-08-25T00:00:00Z
```

Issuance performs no discovery or network fetch. A product mismatch, duplicate,
unknown field, or tampered carrier fails closed. Rejected rights never disclose
source metadata or payload. See
[`docs/FLEET-BRIEF-V1.md`](docs/FLEET-BRIEF-V1.md) for the complete contract.

## Offline MCP server

The package includes a zero-third-party-dependency stdio server for agents that
need to inspect local carrier JSON. It implements current stateless MCP
`2026-07-28` (including `server/discover`) and the latest initialization-based
revision, `2025-11-25`, for existing clients.

```json
{
  "mcpServers": {
    "liquilens-evidence-carrier": {
      "command": "liquilens-evidence-mcp",
      "args": ["--root", "/absolute/path/to/evidence"]
    }
  }
}
```

The `v0.17.1` source candidate exposes four read-only tools:

- `verify_carrier` verifies the content identity, clocks, rights, and export
  disposition of one explicit JSON path below the configured root.
- `project_carrier` applies an existing rights-aware projection (`fdc3`,
  `cloudevent`, `otel`, `openlineage`, `jsonld`, `csl`, `flat`, or `arrow`).
- `verify_fleet_brief` verifies one local four-product brief at its exact
  recorded evaluation clock without returning embedded evidence bodies.
- `verify_trade_safety_receipt` verifies one local hash-only order-bound receipt.
  It accepts no secret; HMAC/live receipts fail closed and must be verified
  inside the tenant boundary.

It never fetches network data, expands restricted rights, recommends, rates
credit, or executes a financial action. The published `v0.16.0` GitHub release
carries the checksum-pinned
[`liquilens-evidence-carrier-mcp-0.16.0.mcpb`](https://github.com/beepboop2025/liquilens-evidence-carrier/releases/download/v0.16.0/liquilens-evidence-carrier-mcp-0.16.0.mcpb)
bundle for compatible desktop clients. Registry identity:
[`io.github.beepboop2025/liquilens-evidence-carrier`](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.beepboop2025%2Fliquilens-evidence-carrier/versions/0.16.0).

<!-- mcp-name: io.github.beepboop2025/liquilens-evidence-carrier -->

## Integration kit

- [`docs/EVIDENCE-CARRIER-V1.md`](docs/EVIDENCE-CARRIER-V1.md) defines the
  contract, rights routing, transports, and failure modes.
- [`docs/FLEET-BRIEF-V1.md`](docs/FLEET-BRIEF-V1.md) defines deterministic,
  rights-aware four-product briefs and their five explicit section states.
- [`docs/TRADE-SAFETY-RECEIPT-V1.md`](docs/TRADE-SAFETY-RECEIPT-V1.md) defines
  strict order, policy, evidence, broker-preview, receipt and verification
  semantics; the companion adoption plan separates discovery from enforcement.
- [`CHANGELOG.md`](CHANGELOG.md) and
  [`docs/RELEASE-0.16.0.md`](docs/RELEASE-0.16.0.md) record the published core
  release, exact receipts, and separately versioned distribution channels.
- [`integrations/fdc3`](integrations/fdc3) contains the custom financial-desktop
  context schema.
- [`integrations/openlineage`](integrations/openlineage) contains the custom
  lineage facet schema.
- [`dbt_project.yml`](dbt_project.yml) and [`macros`](macros) make the repository
  directly installable as a dbt package. The mirrored [`integrations/dbt`](integrations/dbt)
  directory remains available for integration-bundle consumers.
- [`protocol/verify_hash_tree_v1.mjs`](protocol/verify_hash_tree_v1.mjs) verifies
  content identities without trusting Python number formatting.

## Inherit verification in existing workflows

Pin the reusable action to an exact release tag:

```yaml
- uses: beepboop2025/liquilens-evidence-carrier@v0.16.0
  with:
    path: evidence/close.evidence.json
```

For local commit gates, add this repository to `.pre-commit-config.yaml`. The
published hook verifies files ending in `.evidence.json` or `.carrier.json` and
passes every matched file through `liquilens-evidence verify-files`.

## Use in another product

1. Issue the carrier at the boundary where the evidence and its rights are
   known.
2. Verify before every disclosure or conversion.
3. Preserve the raw carrier plus `carrier_id` and `record_hash` at materialized
   boundaries.
4. Treat missing carrier metadata as a failure, not as permission to use a naked
   number.
5. Add a product-specific adapter and golden vector; do not fork the core
   temporal or authority semantics.

## Provenance and license

Protocol artifact SHA-256 values are recorded in
[`protocol/catalog.json`](protocol/catalog.json). The original carrier,
reference, FDC3, and OpenLineage contracts retain their established identities;
the Fleet Brief and Trade Safety v1 schemas are additive. This public repository is the
redistribution boundary for the carrier kit; private research code and datasets
are not included.

Code, schemas, documentation, and integration assets in this repository are
licensed under [Apache‑2.0](LICENSE). Provider data carried inside an evidence
object retains its own rights and license; this repository's license does not
grant rights to third-party data or product trademarks.
