# Changelog

All notable source and release changes are recorded here. A version is not
public merely because it appears in this file: the signed GitHub release,
checksums, attestations, and distribution receipts remain the publication
authority.

## [0.18.0] - 2026-09-02

- Add synchronous and asynchronous authenticated paper-order gateways that bind
  one exact request, account, policy, issuer, execution identity, current clock,
  and one-time receipt claim before a private submit callback can run.
- Keep live and observation submission fail-closed, keep the MCP server
  read-only, and require durable idempotency plus uncertain-outcome
  reconciliation before any separately reviewed live adapter may be activated.
- Preserve all Trade Safety v1 schema identities and bytes; this is an additive
  Python enforcement boundary, not a new protocol version.
- Prepare the separately packaged Trade Safety gateway as `0.1.1`, with
  FastAPI `>=0.141.1,<0.142`, direct Starlette `>=1.3.1,<2`, and pytest
  `>=9.0.3,<10`. The regenerated lock resolves Starlette 1.6.0 and pytest 9.1.1,
  clearing three high, three moderate, and one low dependency-alert ranges.
- Record the independent canonical Trade Safety schema publication proven by
  LiquiLens Pages run
  [33592149926](https://github.com/beepboop2025/liquilens-site/actions/runs/33592149926)
  at site revision `3ec660175c81c5b282715ee400eea2f771dc2610`, without
  representing it as a hosted gateway or core v0.18.0 release.

Published from allowlisted SSH-signed candidate
`906ca033a96ea862ab813c64db2a6b01c5ce8c4f`, tree
`0065206e14a21bb01ce25caed60bf14c9570d12f`, after green
[preflight run 33593756967](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33593756967).
Signed annotated tag object `42dd412ef27b470841b71b8bc73c0ed63a5e4a6b`
and [release run 33593840364](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33593840364)
published 19 assets at `2026-09-02T05:14:06Z`. The `SHA256SUMS` SHA-256 is
`71c2c884d16fd3315a21c263ec8254b0f9578c8150f4a424c296228668d89953`;
the root wheel, MCPB, and gateway 0.1.1 wheel SHA-256 values are respectively
`9fbc7ee50f658e2a8d1d880f8f76d73dca8b07ef6f0747df33a7b9fc346495ef`,
`f57ce3fb488b693e633d8bc66f980b616af09a8080722a11c50507496f39a2bb`,
and `103cde79c006074eaabe5083fec212ba237fcf3a42f01b0600e0faf0328a05a8`.
[Attestation 44605007](https://github.com/beepboop2025/liquilens-evidence-carrier/attestations/44605007)
covers the 18 manifest subjects. The official MCP Registry version 0.18.0 is
active/latest. Separately,
[container run 33593840346](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33593840346)
published and smoke-tested the multi-platform CLI index at
`sha256:293a9ec61ad43f9bac22775936271b19651b486115ab53acbe7928cb177f8c4e`;
[attestation 44605376](https://github.com/beepboop2025/liquilens-evidence-carrier/attestations/44605376)
binds it to the same candidate. GitHub reports the Release record as
`immutable: false`; protected version tags and current checksum/transparency
receipts do not make the Release assets platform-enforced immutable.

## [0.17.1] - 2026-09-02

- Recover the Trade Safety release under a new version identity without moving,
  deleting, or reusing the immutable failed `v0.17.0` tag.
- Carry forward the strict Trade Safety contracts, deterministic verifiers,
  offline MCPB, FDC3 assets, and locked read-only gateway with additional
  fail-closed release and runtime corrections.
- Require successful tag and allowlisted candidate-commit signature checks plus
  protected-main ancestry verification before any build, attestation, GitHub
  publication, or Registry publication.

Published from allowlisted SSH-signed candidate
`a74274236e177404c2d254541e6a4110a4ce8a0d` after green
[preflight run 33589423934](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33589423934).
Signed annotated tag object `8844ee4556d59472a587cb9ceb412112c23543db`
and [release run 33589489958](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33589489958)
produced the GitHub release, checksummed and attested 18 manifest assets, and an
active/latest official MCP Registry 0.17.1 record. The wheel SHA-256 is
`dec2751fa2f20d09a1a77b5f25ae99f28fa49484ea1bf5ede7ca2bcdd86610ea`;
the MCPB SHA-256 is
`4d6c409f2c69588fad6fe13bf2f78ed1b72d3555d81082d5da638d037b0307a1`.
Separately, [container run 33589489966](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33589489966)
published the multi-platform CLI index at
`sha256:bd9b92f25fa8666ea1f43afc4047261ad82213f3c121da87f4dcb9f2e401776d`.
No hosted gateway or canonical-site deployment is claimed by these release
receipts.

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
