# Core 0.20.0 candidate

This source prepares an unpublished core 0.20.0 candidate. The current signed
and published core release remains v0.19.0; its recorded checksums, tag,
attestations and distribution receipts are unchanged.

The candidate adds two offline MCP resources:

- `liquilens-evidence://research/network-schema`: the Seiche research-network
  response schema, including source rights, missingness and pagination.
- `liquilens-evidence://research/network-routing`: explicit research routes
  across Palimpsest, Seiche, LiquiLens, Undertow, NarcoScope and Market Brief.

These are additive discovery resources. Existing carrier and Trade Safety v1
schemas and the four verification/projection tools retain their meanings.
Existing documents require no migration. The offline server performs no fetch,
and reading the new resources grants no publication, training or financial
authority. The separately deployed producers own their rights and freshness
decisions.

The source version and deterministic MCPB digest must pass the existing
metadata preflight, offline contract tests, package checks, and reproducibility
check before the controlled signed-tag workflow can publish this version.
A source merge, local bundle, or passing PR check is not release acceptance.
Independent consumer channels retain their existing released pins until their
own release receipts establish an upgrade.
