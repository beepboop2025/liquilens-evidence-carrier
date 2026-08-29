# v0.16.0 release preparation

Preparation state recorded 2026-08-29: **source candidate; at this checkpoint,
not tagged, published, or registered**.

This candidate gives the changed post-`v0.15.0` package bytes their own release
identity. `README.md` is embedded in the deterministic MCPB, so a documentation
change necessarily changes that artifact's SHA-256. The published `v0.15.0`
MCPB, wheel, tag, checksums, attestations, Registry record, OCI images, skill
tag, and plugin tag remain immutable. At the preparation checkpoint they were
the latest verified live channels; later status must be established from fresh
release and Registry receipts.

## Scope

- Version the Python package, protocol catalog, MCPB manifest, future Registry
  declaration, and Codex plugin together as `0.16.0`.
- Expose the existing local four-product Fleet Brief verification capability in
  plugin discovery metadata.
- Keep the MCP server offline and read-only. It still accepts only explicit
  local paths under the configured root, performs no discovery fetch, and has
  no trading, recommendation, rating, payment, credential, or platform
  authority.
- Make no schema, canonical identifier, evidence-clock, rights, or Fleet Brief
  state-semantics change.

## Prepared artifacts

- `liquilens_evidence-0.16.0-py3-none-any.whl`
- `liquilens_evidence-0.16.0.tar.gz`
- `liquilens-evidence-carrier-mcp-0.16.0.mcpb`
- `liquilens-evidence-carrier-0.16.0.tar.gz`
- `SHA256SUMS` plus GitHub build-provenance attestations

`server.json` is the release-candidate declaration for the exact MCPB filename,
future `v0.16.0` asset URL, and deterministic SHA-256. Its URL is intentionally
not described as live before the signed release creates and attests that asset.

## Release gates

1. Merge the reviewed candidate to protected `main` with CI green.
2. Verify the committed version surfaces, plugin/skill parity, protocol hashes,
   deterministic MCPB replay, MCP protocol responses, and full test matrix.
3. Create a signed `v0.16.0` tag on the reviewed commit. Do not move or reuse
   `v0.15.0`.
4. Let `.github/workflows/release.yml` rebuild from that signed commit, compare
   two MCPB builds byte-for-byte, publish checksums, and attest runnable/package
   artifacts.
5. Verify the anonymous release download and its SHA-256 against both
   `server.json` and `SHA256SUMS`.
6. Only then allow the dependent job to publish `0.16.0` to the official MCP
   Registry; verify it is active before describing it as latest.
7. Update separate owner-controlled container, skill, plugin, and external
   directory channels only after their own immutable build and retrieval
   receipts exist.

No step in source preparation, review, or CI alone is evidence that a public
release or Registry update has happened.
