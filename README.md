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

This source tree is versioned for `v0.16.0`; a source version alone is not
publication proof. At the 2026-08-29 preparation checkpoint, the latest signed,
downloadable, and Registry-listed release was immutable `v0.15.0`. See
[`docs/RELEASE-0.16.0.md`](docs/RELEASE-0.16.0.md) for the candidate boundary
and release gates.

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
# Source checkout (0.16.0 bytes; verify publication state separately)
uv sync --locked
uv run liquilens-evidence --help

# Verified at the 2026-08-29 checkpoint: signed v0.15.0 release
python -m pip install https://github.com/beepboop2025/liquilens-evidence-carrier/releases/download/v0.15.0/liquilens_evidence-0.15.0-py3-none-any.whl
liquilens-evidence issue examples/descriptor.json > carrier.json
liquilens-evidence verify carrier.json --as-of 2026-08-24T12:00:00Z
liquilens-evidence convert carrier.json --format fdc3
```

The release publishes a wheel and checksum manifest. The Python runtime has no
third-party dependencies. A Node.js verifier is also
included for cross-language `liquilens-hash-tree-v1` identity checks:

```bash
node protocol/verify_hash_tree_v1.mjs --artifact evidence-carrier carrier.json
node protocol/verify_hash_tree_v1.mjs --artifact fleet-brief fleet-brief.json
```

## Canonical contracts

| Contract | Canonical URL |
|---|---|
| Full carrier | `https://liquilens.in/protocol/liquilens-evidence-carrier-v1.schema.json` |
| Redacted reference | `https://liquilens.in/protocol/liquilens-evidence-carrier-reference-v1.schema.json` |
| Four-product fleet brief | `https://liquilens.in/protocol/liquilens-fleet-brief-v1.schema.json` |
| FDC3 context | `https://liquilens.in/protocol/fdc3/com.liquilens.evidence.schema.json` |
| OpenLineage facet | `https://liquilens.in/protocol/openlineage/liquilens-evidence-facet.schema.json` |

The current protocol is v1. The `v0.16.0` source candidate changes release and
discovery metadata, not protocol semantics or evidence authority. The immutable
`v0.15.0` release is published at commit
`0d852c06b1a4b0be566c8b4586c9c4c8b8f8f31c` with checksums, attestations, and
an active official MCP Registry record. Production integrations should use
`v0.16.0` only after verifying its signed release workflow and exact checksums;
otherwise pin `v0.15.0`. Use the canonical URLs for schema identity and
discovery.

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

The server exposes three read-only tools:

- `verify_carrier` verifies the content identity, clocks, rights, and export
  disposition of one explicit JSON path below the configured root.
- `project_carrier` applies an existing rights-aware projection (`fdc3`,
  `cloudevent`, `otel`, `openlineage`, `jsonld`, `csl`, `flat`, or `arrow`).
- `verify_fleet_brief` verifies one local four-product brief at its exact
  recorded evaluation clock without returning embedded evidence bodies.

It never fetches network data, expands restricted rights, recommends, rates
credit, or executes a financial action. The published `v0.15.0` GitHub release
carries the checksum-pinned
[`liquilens-evidence-carrier-mcp-0.15.0.mcpb`](https://github.com/beepboop2025/liquilens-evidence-carrier/releases/download/v0.15.0/liquilens-evidence-carrier-mcp-0.15.0.mcpb)
bundle for compatible desktop clients. Registry identity:
[`io.github.beepboop2025/liquilens-evidence-carrier`](https://registry.modelcontextprotocol.io/v0/servers/io.github.beepboop2025%2Fliquilens-evidence-carrier/versions/0.15.0).

<!-- mcp-name: io.github.beepboop2025/liquilens-evidence-carrier -->

## Integration kit

- [`docs/EVIDENCE-CARRIER-V1.md`](docs/EVIDENCE-CARRIER-V1.md) defines the
  contract, rights routing, transports, and failure modes.
- [`docs/FLEET-BRIEF-V1.md`](docs/FLEET-BRIEF-V1.md) defines deterministic,
  rights-aware four-product briefs and their five explicit section states.
- [`CHANGELOG.md`](CHANGELOG.md) and
  [`docs/RELEASE-0.16.0.md`](docs/RELEASE-0.16.0.md) distinguish prepared
  source bytes from published release and Registry state.
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
- uses: beepboop2025/liquilens-evidence-carrier@v0.15.0
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
the Fleet Brief v1 schema is additive. This public repository is the
redistribution boundary for the carrier kit; private research code and datasets
are not included.

Code, schemas, documentation, and integration assets in this repository are
licensed under [Apache‑2.0](LICENSE). Provider data carried inside an evidence
object retains its own rights and license; this repository's license does not
grant rights to third-party data or product trademarks.
