# v0.18.0 order-guard release receipt

Status verified on 2026-09-02: **signed, published, attested, and active/latest
in the official MCP Registry**. The separately built multi-platform CLI OCI
index is also published, attested, and smoke-tested by digest. This is a
source/artifact publication receipt; it is not a hosted Trade Safety gateway or
live-order activation receipt.

## Release identity

| Field | Verified value |
|---|---|
| Version | `0.18.0` |
| Candidate commit | `906ca033a96ea862ab813c64db2a6b01c5ce8c4f` |
| Candidate tree | `0065206e14a21bb01ce25caed60bf14c9570d12f` |
| Annotated tag | `v0.18.0` |
| Tag object | `42dd412ef27b470841b71b8bc73c0ed63a5e4a6b` |
| Tag target | `906ca033a96ea862ab813c64db2a6b01c5ce8c4f` |
| Tagger time | `2026-09-02T05:13:31Z` |
| Protected `main` / preflight controller | `a4ec5d444cfe5b22b388b2e19e79de0d0cb0427c` |
| Candidate/tag signing principal | `liquilens-evidence-carrier-release` |
| Allowed ED25519 fingerprint | `SHA256:yhoa/PIDMM6M/ZennILp8jtRJy5pArncJRARbQssTMI` |
| Preflight | [run 33593756967](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33593756967) |
| Tagged release | [run 33593840364](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33593840364) |
| GitHub release | [`v0.18.0`](https://github.com/beepboop2025/liquilens-evidence-carrier/releases/tag/v0.18.0) |

Repository-local verification with
`ops/release/liquilens-evidence-carrier.allowed_signers` accepted both the
candidate commit and annotated tag as allowlisted SSH signatures. The candidate
is an ancestor and direct second parent of protected `main`; the merge tree and
candidate tree are identical. The signed tag message binds the candidate and
successful preflight run. GitHub Release ID `380991186` was published at
`2026-09-02T05:14:06Z` and is neither a draft nor a prerelease.

Active ruleset `21288366`, `Immutable version tags`, rejects update and deletion
of `refs/tags/v*` with no bypass. GitHub's Release API nevertheless reports
`immutable: false` for this Release record. The tag is platform-protected and
the current asset bytes are checksum- and transparency-attested, but the assets
must not be described as protected by GitHub's immutable-release feature.

## Preflight and release gates

The manual preflight ran from protected `main` with candidate
`906ca033a96ea862ab813c64db2a6b01c5ce8c4f` and version `0.18.0`. Job
`100132900209` completed successfully. Its receipt recorded:

- `allowlisted_ssh` candidate-commit verification and protected-main ancestry;
- exact version parity across source, package, protocol, MCPB, Registry, plugin,
  gateway, and release metadata;
- local and remote absence of `v0.18.0` before tag creation;
- the full root suite, integration-kit validation, ten locked gateway tests,
  release builds, and dependency compatibility checks; and
- two byte-identical deterministic MCPB builds with SHA-256
  `f57ce3fb488b693e633d8bc66f980b616af09a8080722a11c50507496f39a2bb`.

The tag-triggered signed-release job `100133138430` independently rechecked the
tag signature, candidate signature, protected-main ancestry, and version before
building. It then passed tests, package validation, checksum generation,
attestation, anonymous-download verification, and GitHub release publication.
Registry job `100133241374` subsequently published version 0.18.0 successfully.

## Published checksums

The downloaded `SHA256SUMS` file has SHA-256
`71c2c884d16fd3315a21c263ec8254b0f9578c8150f4a424c296228668d89953`.
Strict verification passed for all 18 entries:

| Release asset | SHA-256 |
|---|---|
| `com.liquilens.evidence.schema.json` | `9519474a4d0bf3a77834320d9aa43a88d5df96d49f691110154050212c7511b7` |
| `com.liquilens.trade-safety-receipt.schema.json` | `6c013eef85134e17b649e67c75227a698b76b7d97c7048edb3e8cd703563620b` |
| `liquilens-broker-preview-reference-v1.schema.json` | `89069649379ca759382dcf3f9237e58b069e7fddeeecae6cffa686bbe7351422` |
| `liquilens-evidence-carrier-0.18.0.tar.gz` | `d4bfbac68f108436f674dd499dfb0f473164cb5ee7742297847e4bb1e72bf3cf` |
| `liquilens-evidence-carrier-mcp-0.18.0.mcpb` | `f57ce3fb488b693e633d8bc66f980b616af09a8080722a11c50507496f39a2bb` |
| `liquilens-evidence-carrier-reference-v1.schema.json` | `d54043bf11359749597bff7495b0fffe6ff8453a35144cee4a2bd69711fec7e8` |
| `liquilens-evidence-carrier-v1.schema.json` | `7f8494d8470853dc88665ea32c1dccb40cc58c55b07e9267aa28c81f83c1ccd3` |
| `liquilens-evidence-facet.schema.json` | `e4c6035452d75be280a7b717f85da87319a078dbf5563e62ac3a3cb83486e9a5` |
| `liquilens-fleet-brief-v1.schema.json` | `aaf95337ff973dfbdda97e8ac63975a61b199e43854927404055fbeb52fc6058` |
| `liquilens-trade-safety-policy-v1.schema.json` | `d9171e61c2d378eec545a14bbab0d1ca54302397c809eeeeaae55fb9154ae8d1` |
| `liquilens-trade-safety-receipt-v1.schema.json` | `c2232ae5f80eb42edf7562ae5f5e44ccb9866a13717b697b4d41c28e74b25abe` |
| `liquilens-trade-safety-request-v1.schema.json` | `73af15f84b09b0772368095a01d0f076b9334dd8bbdf9637015aed86e35a47f5` |
| `liquilens_evidence-0.18.0-py3-none-any.whl` | `9fbc7ee50f658e2a8d1d880f8f76d73dca8b07ef6f0747df33a7b9fc346495ef` |
| `liquilens_evidence-0.18.0.tar.gz` | `13e7d30894584acfdb626ab0a8a977eeb85c0cab23256350c48c8f9871f17712` |
| `liquilens_trade_safety_gateway-0.1.1-py3-none-any.whl` | `103cde79c006074eaabe5083fec212ba237fcf3a42f01b0600e0faf0328a05a8` |
| `liquilens_trade_safety_gateway-0.1.1.tar.gz` | `8639491c22de61d218efb1fc3dce291eb6c95ed033cc824ad722e6ff026ab43a` |
| `trade-safety-intents.json` | `e35efa5568c0328e96871010ff2d52afe767d65deaa1cadd13f759391047a0a2` |
| `verify_hash_tree_v1.mjs` | `a3b318276a5d2580ef255ecf56b8e95a39de17f1c4629e152075c2c0074cce4a` |

## Artifact provenance

One GitHub/Sigstore SLSA provenance statement covers exactly the same 18 asset
names and digests as `SHA256SUMS`. Strict `gh attestation verify` checks passed
for every subject with repository, signer workflow, tag ref, candidate source
digest, and GitHub-hosted runner pinned.

- Attestation record:
  [44605007](https://github.com/beepboop2025/liquilens-evidence-carrier/attestations/44605007)
- Signer: `.github/workflows/release.yml@refs/tags/v0.18.0`
- Source digest: `906ca033a96ea862ab813c64db2a6b01c5ce8c4f`
- Invocation:
  [run 33593840364, attempt 1](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33593840364/attempts/1)
- Rekor log index:
  [`2683533042`](https://search.sigstore.dev?logIndex=2683533042)

## Clean Python and order-guard verification

The published root wheel was downloaded and matched SHA-256
`9fbc7ee50f658e2a8d1d880f8f76d73dca8b07ef6f0747df33a7b9fc346495ef`.
A no-dependency clean-environment install imported version 0.18.0 from
`site-packages` and exposed the synchronous/asynchronous order gateways,
execution binding, replay consumers, and low-level hooks. A fresh authenticated
paper receipt passed once, a second atomic claim failed as `receipt_replay`, and
a fully valid live `pass` receipt remained blocked as `mode_not_supported`
before the submit callback.

The separately published gateway 0.1.1 wheel matched SHA-256
`103cde79c006074eaabe5083fec212ba237fcf3a42f01b0600e0faf0328a05a8`.
Its wheel metadata requires core `liquilens-evidence==0.18.0`, FastAPI
`>=0.141.1,<0.142`, direct Starlette `>=1.3.1,<2`, and test-only pytest
`>=9.0.3,<10`. The package remains a read-only sandbox and is not an order
gateway.

## MCPB and official Registry

The published MCPB was downloaded, checksum-verified, extracted, and started
directly from its bundled `src` tree. `server/discover`, `tools/list`, and local
paper-receipt verification succeeded for protocol `2026-07-28`. All four tools
declare read-only, closed-world annotations. The bundle contains the JavaScript
hash verifier, FDC3 Trade Safety assets, schemas, and `order_guard.py`; it
excludes the networked gateway. Its embedded README has SHA-256
`3cc9705a2c1aa0471342199f54509b2aa66a02a2c84d89287732a89cd026018a`
and is byte-identical to `mcpb/release-readmes/0.18.0.md`. That frozen README's
pre-publication wording remains a temporally correct record of the bytes when
prepared; it is not rewritten after publication.

The official record is
[`io.github.beepboop2025/liquilens-evidence-carrier` version 0.18.0](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.beepboop2025%2Fliquilens-evidence-carrier/versions/0.18.0).
It was published at `2026-09-02T05:14:26.182966Z`, reports `active` and
`isLatest: true`, and pins the GitHub MCPB URL plus file SHA-256
`f57ce3fb488b693e633d8bc66f980b616af09a8080722a11c50507496f39a2bb`.
Its stdio metadata declares `financialAuthority: none`, `networkAccess: false`,
and protocol revisions `2026-07-28` and `2025-11-25`. Version 0.17.1 remains
active with its original bytes and now reports `isLatest: false`.

## OCI container receipt

[Container run 33593840346](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33593840346)
published and smoke-tested by digest:

`ghcr.io/beepboop2025/liquilens-evidence-carrier@sha256:293a9ec61ad43f9bac22775936271b19651b486115ab53acbe7928cb177f8c4e`

Live registry inspection resolved that exact OCI index with:

- `linux/amd64` manifest
  `sha256:7c0eaaa336cd9a58069d8894bfb825e0a6ecfa336d761316b37a420c020c4337`;
- `linux/arm64` manifest
  `sha256:fc91b09df670dd41b74ff73f4a3e518051e106bdf165f3536122009176539bdd`;
- version `0.18.0`, revision
  `906ca033a96ea862ab813c64db2a6b01c5ce8c4f`, Apache-2.0, source,
  documentation, vendor, and Artifact Hub annotations on the index and both
  platform configurations; and
- one SPDX SBOM and one SLSA v1 provenance layer bound to each platform
  manifest.

[Attestation record 44605376](https://github.com/beepboop2025/liquilens-evidence-carrier/attestations/44605376)
binds the exact index digest to `container.yml@refs/tags/v0.18.0`, candidate
source digest `906ca033a96ea862ab813c64db2a6b01c5ce8c4f`, and
[run 33593840346, attempt 1](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33593840346/attempts/1).
Its Rekor log index is
[`2683545937`](https://search.sigstore.dev?logIndex=2683545937). The workflow's
digest-pinned amd64 pull and full container smoke both passed. The dedicated MCP
container remains independently versioned at v0.15.0; it is not silently
relabelled as v0.18.0.

## Canonical hosting and gateway boundary

The five stable Trade Safety schema and FDC3 identities are hosted independently
of the core package. LiquiLens Pages
[run 33592149926](https://github.com/beepboop2025/liquilens-site/actions/runs/33592149926)
succeeded at `2026-09-02T04:49:12Z` for site revision
`3ec660175c81c5b282715ee400eea2f771dc2610`, with HTTP 200 and exact-byte checks
for the request, policy, broker-preview, receipt, and FDC3 receipt schemas. This
is canonical schema-hosting proof, not a hosted Trade Safety gateway or
live-order activation receipt.

Hosted gateway or customer deployment still requires an explicitly authorized
project/environment, exact source and build identity, egress policy, edge quota,
health and semantic-freshness proof, and rollback. Live-order activation further
requires broker preview, executable real-money evidence, scoped service
identity, tenant-local keys, atomic one-time receipt consumption, durable
idempotency and uncertain-outcome reconciliation, backup/restore proof,
compliance review, and owner authorization. No release artifact grants
execution, recommendation, allocation, credit-rating, or broker authority.

## Immutable release history

The complete v0.17.1 publication receipt remains
[`RELEASE-0.17.1.md`](RELEASE-0.17.1.md). Its annotated tag object
`8844ee4556d59472a587cb9ceb412112c23543db`, candidate
`a74274236e177404c2d254541e6a4110a4ce8a0d`, release assets, attestation,
Registry record, OCI digest, and MCPB README were rechecked and remain unchanged.

The `v0.17.0` annotated tag object
`cb85e527c2b74abf476fd9a01b73b2235ce976b7` still targets protected-main merge
`edde9b92ad9851d2974b91326a8c3877f4386d3a`. Its
[release run 33585764285](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33585764285)
failed at target-commit signature verification before build, attestation,
GitHub release, or Registry publication. No v0.17.0 GitHub release or official
Registry version exists. Neither v0.17.x release document nor any frozen MCPB
README was rewritten for v0.18.0.
