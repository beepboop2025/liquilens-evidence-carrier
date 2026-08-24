# LiquiLens Evidence Carrier

[![CI](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/workflows/ci.yml/badge.svg)](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/liquilens-evidence.svg)](https://pypi.org/project/liquilens-evidence/)
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
python -m pip install liquilens-evidence
liquilens-evidence issue examples/descriptor.json > carrier.json
liquilens-evidence verify carrier.json --as-of 2026-08-24T12:00:00Z
liquilens-evidence convert carrier.json --format fdc3
```

The Python runtime has no third-party dependencies. A Node.js verifier is also
included for cross-language `liquilens-hash-tree-v1` identity checks:

```bash
node protocol/verify_hash_tree_v1.mjs --artifact evidence-carrier carrier.json
```

## Canonical contracts

| Contract | Canonical URL |
|---|---|
| Full carrier | `https://liquilens.in/protocol/liquilens-evidence-carrier-v1.schema.json` |
| Redacted reference | `https://liquilens.in/protocol/liquilens-evidence-carrier-reference-v1.schema.json` |
| FDC3 context | `https://liquilens.in/protocol/fdc3/com.liquilens.evidence.schema.json` |
| OpenLineage facet | `https://liquilens.in/protocol/openlineage/liquilens-evidence-facet.schema.json` |

The current protocol is v1 and the initial public implementation release is
`0.13.5`. Pin production integrations to a signed tag or release checksum; use
the canonical URLs for schema identity and discovery.

## Integration kit

- [`docs/EVIDENCE-CARRIER-V1.md`](docs/EVIDENCE-CARRIER-V1.md) defines the
  contract, rights routing, transports, and failure modes.
- [`integrations/fdc3`](integrations/fdc3) contains the custom financial-desktop
  context schema.
- [`integrations/openlineage`](integrations/openlineage) contains the custom
  lineage facet schema.
- [`integrations/dbt`](integrations/dbt) contains a dependency-free warehouse
  data test that rejects stripped evidence semantics.
- [`protocol/verify_hash_tree_v1.mjs`](protocol/verify_hash_tree_v1.mjs) verifies
  content identities without trusting Python number formatting.

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
