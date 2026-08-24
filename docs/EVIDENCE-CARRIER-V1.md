# LiquiLens Evidence Carrier v1

## Outcome

The Evidence Carrier makes LiquiLens-family evidence portable without making it
more authoritative. One verified object can accompany a terminal context,
spreadsheet row, event, log, data-lineage facet, Arrow/Parquet table, citation,
or agent response. The carrier preserves the fleet's two-clock rule, source
hashes, redistribution status, product/version identity, and all-false financial
authority boundary.

This is infrastructure for inspection and reproducibility. It is not an order,
recommendation, credit rating, market-data entitlement, or substitute for a
platform's listing or vendor approval.

## System boundary

```text
LiquiLens / Seiche / Undertow / Palimpsest
                  |
                  | issue one content-addressed carrier
                  v
        LiquiLens Evidence Carrier v1
                  |
        verify identity + time + rights
                  |
       +----------+-----------+------------+-------------+
       |          |           |            |             |
      FDC3    CloudEvents   OTel logs   OpenLineage   Arrow/CSL/CSV
    desktops   event buses  operations   data catalogs  files/research
```

LiquiLens Lab owns the carrier because it already owns shared temporal and
evidence contracts. Product repositories remain responsible for their source
adapters, provider rights, freshness windows, and public endpoints. Consumers
must not infer a stronger maturity or authority from the transport used.

## Contract

Every carrier requires:

- a fleet producer and exact producer version;
- a subject with at least one durable identifier;
- a short claim classification and summary;
- `event_time`, `knowledge_time`, and `as_of`, ordered as
  `event_time <= knowledge_time <= as_of`;
- one or more HTTPS sources, each with retrieval time and SHA-256, where every
  retrieval satisfies `event_time <= retrieved_at <= knowledge_time`;
- explicit rights status, permissions, license state, attribution, and
  jurisdictions;
- the all-false execution, recommendation, and credit-rating boundary;
- a finite JSON payload and optional namespaced extensions; and
- a cross-language `liquilens-hash-tree-v1` identity.

The JSON Schema is
[`protocol/liquilens-evidence-carrier-v1.schema.json`](../protocol/liquilens-evidence-carrier-v1.schema.json).
The existing Python and Node hash-tree implementations both verify carrier
identity, so a JavaScript desktop integration does not need to trust Python's
JSON number formatting.

## Rights and freshness routing

The default export policy has three dispositions:

| Input state | Disposition | Payload |
|---|---|---|
| `licensed` or `allowed`, redistribution permission, license basis, attribution, unexpired | `full` | Included |
| `metadata_only`, missing redistribution/license/attribution, unavailable/restricted claim, or expired | `metadata_only` | Omitted |
| rights `restricted`, `unknown`, or `blocked` | `reject` | No export |

A `liquilens.evidence-carrier-reference.v1` metadata-only projection retains the carrier ID, record hash, source identity,
clocks, subject, rights, and reason codes. It does not reuse the original hash as
if the redacted view were the original object. Restricted and unknown rights
fail closed even when a target transport would technically accept the bytes.
The reference has its own machine contract at
[`protocol/liquilens-evidence-carrier-reference-v1.schema.json`](../protocol/liquilens-evidence-carrier-reference-v1.schema.json).
Every adapter labels the emitted view with either the full-carrier or reference
schema URL; a redacted reference is never described as a full carrier.

## Adapter semantics

### FDC3 financial desktops

`to_fdc3_context(...)` emits a namespaced `com.liquilens.evidence` context. It
maps external standards to FDC3 names such as `FIGI`, `ISIN`, and
`CURRENCY_ISOCODE`, namespaces other private identifiers, and adds
`liquilensEvidenceId`. This lets an FDC3 desktop agent route `ViewResearch` or
`ViewAnalysis` workflows without a bilateral schema fork. A production app
still needs a real FDC3 listener and an App Directory record in the user's
desktop environment.

### Bloomberg-recognizable identity

`to_openfigi_mapping_jobs(...)` emits public OpenFIGI mapping jobs for FIGI,
ISIN, SEDOL, CUSIP, or ticker identifiers. It does not call Bloomberg services,
use Terminal data, or claim Terminal distribution. The output can be used by a
Terminal-licensed analyst, BQuant notebook, or independent pipeline subject to
their own entitlements and OpenFIGI limits.

### Event and operations infrastructure

`to_cloudevent(...)` emits CloudEvents 1.0 structured JSON, so existing event
buses can route evidence without a LiquiLens-specific transport. `to_otel_log(...)`
maps the economic occurrence to the OpenTelemetry event timestamp and platform
knowledge to observed timestamp. This makes freshness and evidence failures
visible in the same operational systems that already page data-platform teams.

### Data lineage and files

`to_openlineage_facet(...)` returns a custom facet whose `_schemaURL` identifies
the facet wrapper; the nested carrier retains its separate carrier schema URL.
`to_arrow_metadata(...)` returns byte keys and values suitable for Arrow schema
metadata and Parquet key/value metadata. Because metadata can be dropped by
some transformations, consumers should verify its presence at every materialized
boundary rather than assuming it survives a round trip.

### Research, spreadsheets, and documents

`to_flat_row(...)` produces stable scalar columns plus canonical JSON for Excel,
CSV, SQL, and dataframe ingestion. `to_csl_json(...)` makes the carrier citable
by reference managers. `to_jsonld(...)` expresses sources as W3C PROV-O entities,
allowing knowledge graphs to retain derivation rather than merely copying a URL.

## CLI

```bash
liquilens-evidence issue descriptor.json > carrier.json
liquilens-evidence verify carrier.json --as-of 2026-08-24T12:00:00Z
liquilens-evidence convert carrier.json --format fdc3
liquilens-evidence convert carrier.json --format cloudevent
liquilens-evidence convert carrier.json --format openfigi
```

The CLI rejects duplicate JSON keys, non-UTF-8 input, non-finite JSON, files over
1 MiB, path-like URLs, clock inversions, hash mismatches, and authority widening.
Verification also rejects export when the policy evaluation instant precedes the
carrier's `as_of` time. The CLI writes only to standard output; callers control
persistence and permissions.

## Reliability and scale

The carrier is stateless and deterministic. Issuance and verification are linear
in object size, capped at 1 MiB. No adapter performs network I/O. This makes the
library safe in batch jobs, notebook kernels, desktop apps, serverless handlers,
and CI gates without introducing a new availability dependency.

For high-volume streams, carry the full object on first observation and the
content-addressed reference on repeated events. For warehouse tables, attach
the carrier to schema metadata and retain `carrier_id` and `record_hash` as
ordinary columns so metadata loss is detectable.

## Failure modes

The highest-risk failure is semantic stripping: a downstream process retains a
number while dropping clocks, rights, or source hashes. Conformance tests should
therefore reject naked values. Other expected failures are expired evidence,
identifier ambiguity, lost Arrow metadata, unsupported custom FDC3 context,
source-hash drift, and a producer endpoint becoming unavailable. All leave an
explicit disposition or verification error; none silently upgrades evidence.

## Rollout

1. Adopt the carrier in read-only exports and historical fixtures.
2. Add product-specific source adapters with golden vectors for all four products.
3. Require carrier verification in public SDK and agent responses.
4. Add FDC3 app listeners, spreadsheet functions, and warehouse packages against
   the same vectors.
5. Publish platform listings only after provider rights, exact endpoint identity,
   privacy/support terms, and platform review are proven.

The design should be revisited if payloads routinely approach 1 MiB, detached
signatures become mandatory, a platform cannot preserve nested JSON, or product
rights need per-field rather than per-carrier routing.
