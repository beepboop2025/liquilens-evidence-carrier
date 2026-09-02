# Changelog

All notable source and release changes are recorded here. A version is not
public merely because it appears in this file: the signed GitHub release,
checksums, attestations, and distribution receipts remain the publication
authority.

## [0.17.1] - 2026-09-02

- Recover the Trade Safety release under a new version identity without moving,
  deleting, or reusing the immutable failed `v0.17.0` tag.
- Carry forward the strict Trade Safety contracts, deterministic verifiers,
  offline MCPB, FDC3 assets, and locked read-only gateway with additional
  fail-closed release and runtime corrections.
- Require successful tag and allowlisted candidate-commit signature checks plus
  protected-main ancestry verification before any build, attestation, GitHub
  publication, Registry publication, or deployment claim.

Prepared source entry only. `v0.17.1` is not tagged, published, registered, or
deployed. The latest signed, downloadable, and Registry-listed core release
remains `v0.16.0`.

## [0.17.0] - 2026-09-02

- Add strict, exact-order Trade Safety request, operator-policy,
  broker-preview-reference, and receipt contracts.
- Add deterministic Python issuance/verification, hash-only and tenant-local
  HMAC integrity, semantic-freshness and rights gates, CLI commands, offline
  secretless MCP verification, and Python/JavaScript golden conformance vectors.
- Add FDC3 receipt/intent assets and a fixed-upstream, locked, read-only FastAPI
  sandbox whose public evidence can never produce a live pass.
- Add a detailed ecosystem adoption and staged broker/OMS enforcement plan while
  retaining all-false financial authority and one-time-consumption requirements.

The immutable annotated tag object
`cb85e527c2b74abf476fd9a01b73b2235ce976b7` targeted protected-main merge
`edde9b92ad9851d2974b91326a8c3877f4386d3a`, but
[release run 33585764285](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33585764285)
failed at the commit-signature gate. Artifact build, attestation, GitHub
publication, and Registry publication were skipped. No v0.17.0 GitHub release
or official MCP Registry record exists; this version is a failed release attempt,
not a production pin.

## [0.16.0] - 2026-08-29

- Assign a new release identity to the post-`v0.15.0` README and deterministic
  MCPB bytes instead of mutating the published `v0.15.0` artifact contract.
- Align Codex plugin discovery with the already-supported, local-only
  four-product Fleet Brief verifier.
- Enforce release-version parity across Python, protocol catalog, MCPB,
  official Registry metadata, and plugin surfaces.
- Replay deterministic MCPB construction before release publication and keep
  the existing offline, read-only, no-financial-authority boundary.

Published from signed commit
`410f7d91114fba715e9a9ae830faa775064a4502` by release workflow
[33261143612](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33261143612).
The checksum-pinned MCPB is active/latest in the official MCP Registry; other
distribution channels retain their own independently verified versions.

## [0.15.0] - 2026-08-25

- Published Fleet Brief v1, including local CLI and MCP verification.
- Published the checksum-pinned MCPB and active official MCP Registry record.
- Preserved evidence clocks, rights, product boundaries, and all-false
  execution, recommendation, and credit-rating authority.
