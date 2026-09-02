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

The current signed and published core release is `v0.18.0`. Annotated tag
object `42dd412ef27b470841b71b8bc73c0ed63a5e4a6b` targets the allowlisted
SSH-signed candidate `906ca033a96ea862ab813c64db2a6b01c5ce8c4f`, tree
`0065206e14a21bb01ce25caed60bf14c9570d12f`, which is an ancestor of protected
`main` at `a4ec5d444cfe5b22b388b2e19e79de0d0cb0427c`. The candidate passed
[preflight run 33593756967](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33593756967),
and [release run 33593840364](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33593840364)
published 19 assets at `2026-09-02T05:14:06Z`. The `SHA256SUMS` file covers the
other 18 assets, matches their strict provenance subjects, and hashes to
`71c2c884d16fd3315a21c263ec8254b0f9578c8150f4a424c296228668d89953`.
The official MCP Registry record is active/latest at 0.18.0 and pins the
published MCPB SHA-256
`f57ce3fb488b693e633d8bc66f980b616af09a8080722a11c50507496f39a2bb`.

This source checkpoint prepares `v0.19.0`; it is not yet tagged, published, or
registered. The candidate preserves all Trade Safety v1 schema bytes and adds a
deterministic adversarial corpus, a dependency-free TypeScript-compatible Node
raw-UTF8 verifier and authenticated paper-only order guard, OpenBB 0.2.0
hash-only receipt verification pinned to released core v0.18.0, and MCP corpus
discovery. It also prepares attested multi-architecture MCP and read-only
gateway OCI packaging; those are registry artifacts, not hosted deployment or
order authority. Until protected-main preflight and the controlled release finish,
`v0.18.0` remains the production pin. See
[`docs/RELEASE-0.19.0.md`](docs/RELEASE-0.19.0.md); no v0.19.0 publication or
deployment receipt is asserted here.
The two deterministic local candidate builds currently hash to
`11db11aefafcc6c4ba558877d1f9892fc708150b3afbaa28a741e74435b9a91a`;
this is prepared-byte identity, not publication proof.

Release `v0.18.0` adds authenticated sync/async paper-order gateways around the
low-level `before_order` verification boundary while keeping live submission
held and the MCP server read-only. It also publishes Trade Safety gateway
`0.1.1` with a patched Starlette floor and updated FastAPI/test constraints.
[Container run 33593840346](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33593840346)
published and smoke-tested the multi-platform CLI index at
`sha256:293a9ec61ad43f9bac22775936271b19651b486115ab53acbe7928cb177f8c4e`.
See [`docs/RELEASE-0.18.0.md`](docs/RELEASE-0.18.0.md) for the complete release,
Registry, attestation, and container receipt. GitHub currently reports the
Release record itself as `immutable: false`; version-tag ruleset `21288366`
blocks `v*` tag update and deletion with no bypass, and the current assets are
checksum- and transparency-attested, but they are not described here as
platform-enforced immutable assets.

The immutable annotated `v0.17.0` tag object
`cb85e527c2b74abf476fd9a01b73b2235ce976b7` targets protected-main merge
`edde9b92ad9851d2974b91326a8c3877f4386d3a`, but its
[release run 33585764285](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33585764285)
failed at the commit-signature gate before any artifact was built, attested, or
published. There is no v0.17.0 GitHub release or official MCP Registry record.
See [`docs/RELEASE-0.17.1.md`](docs/RELEASE-0.17.1.md) for the unchanged recovery
receipt and [`docs/RELEASE-0.17.0.md`](docs/RELEASE-0.17.0.md) for the unchanged
failed-attempt record.

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

# Signed v0.18.0 wheel; checksum verified against the release manifest
python -m pip install 'https://github.com/beepboop2025/liquilens-evidence-carrier/releases/download/v0.18.0/liquilens_evidence-0.18.0-py3-none-any.whl#sha256=9fbc7ee50f658e2a8d1d880f8f76d73dca8b07ef6f0747df33a7b9fc346495ef'
liquilens-evidence issue examples/descriptor.json > carrier.json
liquilens-evidence verify carrier.json --as-of 2026-08-24T12:00:00Z
liquilens-evidence convert carrier.json --format fdc3
```

Published release `v0.18.0` provides a wheel and checksum manifest. The Python
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
| Trade Safety request | `https://liquilens.in/protocol/liquilens-trade-safety-request-v1.schema.json` | Published v0.18.0 release asset and canonically hosted |
| Trade Safety policy | `https://liquilens.in/protocol/liquilens-trade-safety-policy-v1.schema.json` | Published v0.18.0 release asset and canonically hosted |
| Broker preview reference | `https://liquilens.in/protocol/liquilens-broker-preview-reference-v1.schema.json` | Published v0.18.0 release asset and canonically hosted |
| Trade Safety receipt | `https://liquilens.in/protocol/liquilens-trade-safety-receipt-v1.schema.json` | Published v0.18.0 release asset and canonically hosted |
| FDC3 Trade Safety receipt | `https://liquilens.in/protocol/fdc3/com.liquilens.trade-safety-receipt.schema.json` | Published v0.18.0 release asset and canonically hosted |
| FDC3 context | `https://liquilens.in/protocol/fdc3/com.liquilens.evidence.schema.json` | Published and hosted |
| OpenLineage facet | `https://liquilens.in/protocol/openlineage/liquilens-evidence-facet.schema.json` | Published and hosted |

The five Trade Safety identities above are stable schema `$id` values. LiquiLens
Pages [run 33592149926](https://github.com/beepboop2025/liquilens-site/actions/runs/33592149926)
succeeded at 2026-09-02T04:49:12Z for site revision
`3ec660175c81c5b282715ee400eea2f771dc2610`; its post-deploy gate retrieved all
five URLs over HTTPS and matched their exact bytes to the hashes in
[`protocol/catalog.json`](protocol/catalog.json). This is schema-hosting proof,
not a hosted Trade Safety gateway or live-order activation receipt.

The current contracts are v1. Release `v0.17.1` added Trade Safety without
changing the previously published Carrier or Fleet Brief semantics. Release
`v0.18.0` preserves the v1 schema bytes and adds a Python enforcement adapter;
it does not create a new protocol identity. The v0.18.0 signed release workflow is
[run 33593840364](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33593840364),
the wheel SHA-256 is
`9fbc7ee50f658e2a8d1d880f8f76d73dca8b07ef6f0747df33a7b9fc346495ef`, and
the MCPB SHA-256 is
`f57ce3fb488b693e633d8bc66f980b616af09a8080722a11c50507496f39a2bb`.
Production integrations can pin `v0.18.0`; separately released container,
skill, plugin, browser, and package-manager channels retain their own verified
versions. The canonical URLs are now available for public schema discovery.

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

Python broker and agent runtimes can place the fail-closed, paper-only
[`before_order` guard](docs/TRADE-SAFETY-ORDER-GUARD.md) around their only
submit callable. Its agent-facing gateway requires tenant-authenticated HMAC
receipts, so a missing, expired, mismatched, cross-account, or non-pass receipt
never reaches broker code. A configured claim store blocks receipt replay; use a
durable operator-owned store outside local paper/demo runs. Live submission
remains held until the broker idempotency and uncertain-outcome reconciliation
gates are complete.

TypeScript and Node consumers can use the zero-runtime-dependency
[`@liquilens/trade-safety` candidate](integrations/typescript/README.md). Its
authoritative APIs consume raw UTF-8 bytes so `1000` and `1000.0` retain their
different protocol identities, and it rejects malformed UTF-8, duplicate keys,
tamper, cross-context use, expiry, replay, and every live request before the
paper submit callback. The committed corpus and threat model are documented in
[`TRADE-SAFETY-CONFORMANCE.md`](docs/TRADE-SAFETY-CONFORMANCE.md).

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

The published `v0.18.0` release exposes four read-only tools:

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
credit, or executes a financial action. The published `v0.18.0` GitHub release
carries the checksum-pinned
[`liquilens-evidence-carrier-mcp-0.18.0.mcpb`](https://github.com/beepboop2025/liquilens-evidence-carrier/releases/download/v0.18.0/liquilens-evidence-carrier-mcp-0.18.0.mcpb)
bundle for compatible desktop clients. Registry identity:
[`io.github.beepboop2025/liquilens-evidence-carrier`](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.beepboop2025%2Fliquilens-evidence-carrier/versions/0.18.0).

<!-- mcp-name: io.github.beepboop2025/liquilens-evidence-carrier -->

## Integration kit

- [`docs/EVIDENCE-CARRIER-V1.md`](docs/EVIDENCE-CARRIER-V1.md) defines the
  contract, rights routing, transports, and failure modes.
- [`docs/FLEET-BRIEF-V1.md`](docs/FLEET-BRIEF-V1.md) defines deterministic,
  rights-aware four-product briefs and their five explicit section states.
- [`docs/TRADE-SAFETY-RECEIPT-V1.md`](docs/TRADE-SAFETY-RECEIPT-V1.md) defines
  strict order, policy, evidence, broker-preview, receipt and verification
  semantics; the companion adoption plan separates discovery from enforcement.
- [`CHANGELOG.md`](CHANGELOG.md), the
  [`v0.18.0 publication receipt`](docs/RELEASE-0.18.0.md), and the unchanged
  [`v0.17.1 recovery receipt`](docs/RELEASE-0.17.1.md) separate current release
  facts from immutable history and independently versioned channels.
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
- uses: beepboop2025/liquilens-evidence-carrier@v0.18.0
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
