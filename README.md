# LiquiLens Evidence Carrier

[![CI](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/workflows/ci.yml/badge.svg)](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/workflows/ci.yml)
[![Nix](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/workflows/nix.yml/badge.svg)](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/workflows/nix.yml)
[![Release](https://img.shields.io/github/v/release/beepboop2025/liquilens-evidence-carrier)](https://github.com/beepboop2025/liquilens-evidence-carrier/releases/tag/v0.14.0)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

LiquiLens Evidence Carrier is a transport-neutral contract for moving financial
evidence through files, warehouses, event buses, observability systems, data
catalogs, FDC3 desktops, notebooks, citations, and AI agents without dropping
provenance, rights, freshness, or authority boundaries.

The carrier is infrastructure for inspection and reproducibility. It is not an
order, recommendation, credit rating, market-data entitlement, or endorsement
by Bloomberg, LSEG, FactSet, FINOS, or any other platform.

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
python -m pip install https://github.com/beepboop2025/liquilens-evidence-carrier/releases/download/v0.14.0/liquilens_evidence-0.14.0-py3-none-any.whl
liquilens-evidence issue examples/descriptor.json > carrier.json
liquilens-evidence verify carrier.json --as-of 2026-08-24T12:00:00Z
liquilens-evidence convert carrier.json --format fdc3
```

The release publishes a wheel and checksum manifest. The Python runtime has no
third-party dependencies. A Node.js verifier is also
included for cross-language `liquilens-hash-tree-v1` identity checks:

```bash
node protocol/verify_hash_tree_v1.mjs --artifact evidence-carrier carrier.json
```

## Run directly with Nix

Nix users can run the v0.14.0 verifier from this public repository without a
Python environment or package-registry account:

```bash
nix run github:beepboop2025/liquilens-evidence-carrier -- --help
nix run github:beepboop2025/liquilens-evidence-carrier -- verify carrier.json
```

Run the offline stdio server through the dedicated app:

```bash
nix run github:beepboop2025/liquilens-evidence-carrier#mcp -- \
  --root /absolute/path/to/evidence
```

For an immutable v0.14.0 flake source, replace the repository reference above
with
`github:beepboop2025/liquilens-evidence-carrier/225c3e2fc96efb0ca78256e3ec96ac25901c10c7`.
The committed `flake.lock` pins NixOS 26.05 inputs. Nix may fetch those public
build inputs before launch; the resulting verifier and MCP server themselves
remain dependency-free at runtime, offline, read-only, and non-authoritative.

## Canonical contracts

| Contract | Canonical URL |
|---|---|
| Full carrier | `https://liquilens.in/protocol/liquilens-evidence-carrier-v1.schema.json` |
| Redacted reference | `https://liquilens.in/protocol/liquilens-evidence-carrier-reference-v1.schema.json` |
| FDC3 context | `https://liquilens.in/protocol/fdc3/com.liquilens.evidence.schema.json` |
| OpenLineage facet | `https://liquilens.in/protocol/openlineage/liquilens-evidence-facet.schema.json` |

The current protocol is v1 and the current public implementation release is
`0.14.0`. Pin production integrations to a signed tag or release checksum; use
the canonical URLs for schema identity and discovery.

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

The server exposes two read-only tools:

- `verify_carrier` verifies the content identity, clocks, rights, and export
  disposition of one explicit JSON path below the configured root.
- `project_carrier` applies an existing rights-aware projection (`fdc3`,
  `cloudevent`, `otel`, `openlineage`, `jsonld`, `csl`, `flat`, or `arrow`).

It never fetches network data, expands restricted rights, recommends, rates
credit, or executes a financial action. The GitHub release also carries a
checksum-pinned
[`liquilens-evidence-carrier-mcp-0.14.0.mcpb`](https://github.com/beepboop2025/liquilens-evidence-carrier/releases/download/v0.14.0/liquilens-evidence-carrier-mcp-0.14.0.mcpb)
bundle for compatible desktop clients. Registry identity:
`io.github.beepboop2025/liquilens-evidence-carrier`.

<!-- mcp-name: io.github.beepboop2025/liquilens-evidence-carrier -->

## Integration kit

- [`docs/EVIDENCE-CARRIER-V1.md`](docs/EVIDENCE-CARRIER-V1.md) defines the
  contract, rights routing, transports, and failure modes.
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
- uses: beepboop2025/liquilens-evidence-carrier@v0.14.0
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

The four schemas in this repository are byte-identical to the artifacts in the
signed LiquiLens Lab Engine `v0.13.5` release. Their SHA-256 values are recorded
in [`protocol/catalog.json`](protocol/catalog.json). This public repository is
the redistribution boundary for the carrier kit; private research code and
datasets are not included.

Code, schemas, documentation, and integration assets in this repository are
licensed under [Apache‑2.0](LICENSE). Provider data carried inside an evidence
object retains its own rights and license; this repository's license does not
grant rights to third-party data or product trademarks.
