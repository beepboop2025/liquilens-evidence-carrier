# LiquiLens Fleet Brief v1

`liquilens.fleet-brief.v1` is a deterministic envelope over native Evidence
Carriers that have already been issued by LiquiLens, Seiche, Undertow, and
Palimpsest. It is not a shared model, a score, a forecast, or a data-fetching
service.

The canonical schema is
[`liquilens-fleet-brief-v1.schema.json`](https://liquilens.in/protocol/liquilens-fleet-brief-v1.schema.json).

## Contract boundaries

- A brief always has exactly one named section for each canonical product:
  `liquilens`, `seiche`, `undertow`, and `palimpsest`.
- Issuance accepts only supplied carrier objects. It does not fetch a current
  carrier, discover a latest file, or mint a carrier for a product.
- Every supplied carrier passes `verify_evidence_carrier` with
  `STRICT_EXPORT_POLICY` at the caller's mandatory `evaluated_at` instant.
- The carrier's native `producer.name` must match its section. Reused carrier
  identities, producer mismatches, unknown fields, duplicate JSON keys, and
  hash tampering fail closed.
- There is no aggregate score or fleet-wide conclusion. Product claims remain
  in separate sections.
- The root keeps the same all-false boundary: no execution, recommendation,
  credit-rating, or other financial authority.

## Section states

| State | Meaning | Section disclosure |
| --- | --- | --- |
| `full` | The native carrier is fully redistributable at `evaluated_at`. | The complete verified carrier. |
| `metadata_only` | Strict policy permits only the existing carrier-reference view. | The policy-redacted reference; no payload. |
| `unavailable` | A real native carrier has `claim.status = unavailable`. | The policy-redacted reference; no payload. |
| `rejected` | Strict verification rejects disclosure, including `restricted`, `unknown`, or `blocked` rights. | Carrier ID, record hash, and reason codes only. No subject, source metadata, rights metadata, or payload. |
| `missing` | No carrier was supplied for that product. | No fabricated identity or evidence; `carrier_missing` only. |

`missing` and `unavailable` are deliberately different. Missing means there was
no native artifact to evaluate. Unavailable means the product issued an
artifact that explicitly records evidence unavailability.

## Content identity

The brief uses `liquilens-hash-tree-v1`, the same typed canonical hash semantics
as the native carrier. SHA-256 covers the schema name, canonicalization,
evaluation clock, all four sections, their order-independent object keys, and
the all-false authority object. It excludes only `brief_id` and `record_hash`:

```text
record_hash = sha256(canonical_hash_bytes(brief without brief_id/record_hash))
brief_id    = "fleet_brief_" + record_hash[0:24]
```

Changing a state, reason, embedded reference, carrier, or evaluation time
therefore produces a different brief identity.

## Python API

```python
from datetime import UTC, datetime
from liquilens_evidence import issue_fleet_brief, verify_fleet_brief

brief = issue_fleet_brief(
    carriers={
        "liquilens": liquilens_carrier,
        "seiche": seiche_carrier,
        "undertow": None,
        "palimpsest": palimpsest_carrier,
    },
    evaluated_at=datetime(2026, 8, 25, tzinfo=UTC),
)

verified = verify_fleet_brief(
    brief,
    evaluated_at=datetime(2026, 8, 25, tzinfo=UTC),
)
```

Omitted product keys and explicit `None` values both create `missing` sections.
The verifier requires the same explicit instant recorded in the brief. To
evaluate rights or expiry at a later instant, issue a new brief.

## CLI

Issuance reads only the named local files. An omitted flag becomes `missing`;
stdin is intentionally unavailable for producer carrier inputs so every input
has an auditable path.

```bash
liquilens-evidence issue-brief \
  --liquilens ./liquilens.carrier.json \
  --seiche ./seiche.carrier.json \
  --undertow ./undertow.carrier.json \
  --palimpsest ./palimpsest.carrier.json \
  --as-of 2026-08-25T00:00:00Z > fleet-brief.json

liquilens-evidence verify-brief ./fleet-brief.json \
  --as-of 2026-08-25T00:00:00Z
```

The command performs no network access.

The cross-language verifier replays the same hash tree without parsing JSON
numbers through Python:

```bash
node protocol/verify_hash_tree_v1.mjs \
  --artifact fleet-brief ./fleet-brief.json
```

## MCP

The dependency-free MCP server exposes `verify_fleet_brief`. It is read-only,
offline, constrained to the configured root, and requires `path` plus the exact
`evaluated_at` clock. It returns identity, five-state section status, source
path/byte count, and the all-false authority boundary. It does not return the
brief's evidence bodies.

The schema is also available as the static resource
`liquilens-evidence://protocol/fleet-brief-schema`.

## Verification limits

For a `full` section, the verifier replays native carrier identity, clocks, and
strict policy. A metadata-only or unavailable section intentionally withholds
the native payload; verification can prove the brief's own content identity,
reference shape, product match, strict-policy marker, and safe rights status,
but cannot reconstruct the withheld native carrier bytes. A rejected section
is intentionally identity-only.

Content addressing detects accidental or malicious byte-level changes; it is
not a producer signature or proof that an untrusted party honestly selected
its inputs. Use release signatures or an application trust policy when issuer
authentication is required.
