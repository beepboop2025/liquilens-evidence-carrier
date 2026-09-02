# Changelog

All notable source and release changes are recorded here. A version is not
public merely because it appears in this file: the signed GitHub release,
checksums, attestations, and distribution receipts remain the publication
authority.

## [Unreleased]

- Prepare independently versioned Trade Safety gateway `0.1.3` against the
  unchanged core `0.19.0` protocol: consume Seiche
  `seiche.risk-context.v1` and Undertow
  `trade_safety_exit_context` instead of their legacy convenience views.
- Bind both native context digests to one canonical Trade Safety request hash;
  retain Seiche clocks/staleness/attestation state and Undertow
  rights/PIT/source/deployed digests without promoting either to execution
  authority.
- Fail typed unavailable on producer-unavailable, malformed, mismatched,
  tampered, stale, incomplete-rights, or unverified-proof responses, while
  keeping observe/paper-only and no execution, routing, custody, or settlement.

## [0.19.0] - 2026-09-02

- Add a deterministic adversarial Trade Safety v1 corpus spanning pass, limit,
  hold, unavailable, numeric-lexeme mismatch, >53-bit integers, duplicate keys,
  invalid UTF-8, tamper, HMAC failure, future request/receipt clocks, account,
  policy, issuer and key binding, replay, and live rejection.
- Add a zero-runtime-dependency, TypeScript-compatible Node ESM verifier and
  authenticated paper-only order guard whose authoritative boundary is raw
  UTF-8 bytes and whose only submit callback remains live-disabled.
- Add OpenBB extension 0.2.0 offline hash-only receipt verification pinned to
  the immutable v0.18.0 core wheel; HMAC secrets and order submission remain
  outside the analytics router.
- Expose the conformance corpus through the protocol catalog and offline MCP
  resources, and add repeatable Node/package gates to CI and release preflight.
- Publish read-only Trade Safety gateway 0.1.2 against core 0.19.0 without a
  broker route, hosted deployment, or live-money activation.
- Publish attested amd64/arm64 core CLI and gateway OCI indexes. The gateway
  remains a read-only, hash-only sandbox; the separately controlled v0.19.0
  dedicated MCP image workflow has not been dispatched.

Published from allowlisted SSH-signed release commit
`8f5738c9e77cc95b9a68543d478b9521f5595d61`, tree
`acca6fa7aab75ebc91bf044e153c6468cd6f9c0c`, after green
[preflight run 33630656569](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33630656569).
Signed annotated tag object `c3239bfc7c4d3c4b7fc5ce26e0f602962e7d4337`
and [release run 33630790150](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33630790150)
published 23 assets at `2026-09-02T12:36:19Z`. The `SHA256SUMS` SHA-256 is
`c6d52cbf8794db6e478e3b2ea9e1ed8eee7757137650892a6a96fcbb839bb6bc`;
the root wheel, MCPB, and gateway 0.1.2 wheel SHA-256 values are respectively
`1adccb72376f50456fd16a979e372f802ae73ba35b766633bc3d8bd4ab5abcc8`,
`11db11aefafcc6c4ba558877d1f9892fc708150b3afbaa28a741e74435b9a91a`,
and `e3c685a300aadaafa406ccf38b2d8c56107e7145f6a075d0909a9c74a715f285`.
[Attestation 44695012](https://github.com/beepboop2025/liquilens-evidence-carrier/attestations/44695012)
covers the 22 non-manifest assets, and the official MCP Registry version 0.19.0
is active/latest. [Core container run 33630789998](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33630789998)
published and smoke-tested index
`sha256:bdbfed2afa87f25e8ef88dffeb4ba7ab198854705528c0de5abe31552a170b9a`;
[attestation 44695462](https://github.com/beepboop2025/liquilens-evidence-carrier/attestations/44695462)
binds it to the tagged source. [Gateway run 33630790011](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33630790011)
published and smoke-tested index
`sha256:b5c43013da1fdddd9e6e56cab0e4f0f562e39ab25cc640869c5008e3457218e3`;
[attestation 44695195](https://github.com/beepboop2025/liquilens-evidence-carrier/attestations/44695195)
binds it to the same source. No hosted gateway or live-order activation is
claimed. Canonical Trade Safety schema hosting remains independently proven by
LiquiLens Pages run
[33592149926](https://github.com/beepboop2025/liquilens-site/actions/runs/33592149926)
at site revision `3ec660175c81c5b282715ee400eea2f771dc2610`.

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
