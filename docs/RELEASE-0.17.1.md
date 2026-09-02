# v0.17.1 release receipt

Status verified on 2026-09-02: **signed, published, attested, and active/latest
in the official MCP Registry**. The separately built multi-platform CLI OCI
index is also published and attested. This is a source/artifact publication
receipt; it is not a hosted Trade Safety gateway or canonical-site deployment
receipt.

## Release identity

| Field | Verified value |
|---|---|
| Version | `0.17.1` |
| Candidate commit | `a74274236e177404c2d254541e6a4110a4ce8a0d` |
| Candidate tree | `d044ccb2fe71a849b01f67c5b88bb07b7f8bcc50` |
| Annotated tag | `v0.17.1` |
| Tag object | `8844ee4556d59472a587cb9ceb412112c23543db` |
| Tag target | `a74274236e177404c2d254541e6a4110a4ce8a0d` |
| Tagger time | `2026-09-02T04:05:44Z` |
| Protected `main` / preflight controller | `9a79c3e0c907fd0d698c934ab426ea0a8106303a` |
| Candidate/tag signing principal | `liquilens-evidence-carrier-release` |
| Allowed ED25519 fingerprint | `SHA256:yhoa/PIDMM6M/ZennILp8jtRJy5pArncJRARbQssTMI` |
| Preflight | [run 33589423934](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33589423934) |
| Tagged release | [run 33589489958](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33589489958) |
| GitHub release | [`v0.17.1`](https://github.com/beepboop2025/liquilens-evidence-carrier/releases/tag/v0.17.1) |

Repository-local verification with
`ops/release/liquilens-evidence-carrier.allowed_signers` accepted both the
candidate commit and annotated tag as allowlisted SSH signatures. The candidate
is an ancestor of protected `main`; the GitHub-generated merge commit is not the
tag target. The signed tag message binds the candidate and successful preflight
run. The release was published at `2026-09-02T04:06:20Z` and is neither a draft
nor a prerelease.

## Preflight and release gates

The manual preflight ran from protected `main` with candidate
`a74274236e177404c2d254541e6a4110a4ce8a0d` and version `0.17.1`. Job
`100120202773` completed successfully. Its receipt recorded:

- `allowlisted_ssh` candidate-commit verification and protected-main ancestry;
- exact version parity across source, package, protocol, MCPB, Registry, plugin,
  and release metadata;
- local and remote absence of `v0.17.1` before tag creation;
- the full root suite, integration-kit validation, ten locked gateway tests,
  and release package builds; and
- two byte-identical deterministic MCPB builds with SHA-256
  `4d6c409f2c69588fad6fe13bf2f78ed1b72d3555d81082d5da638d037b0307a1`.

The tag-triggered signed-release job `100120390698` independently rechecked the
tag signature, candidate signature, protected-main ancestry, and version before
building. It then passed tests, package validation, checksum generation,
attestation, anonymous-download verification, and GitHub release publication.
Registry job `100120495621` subsequently published version 0.17.1 successfully.

## Published checksums

The downloaded `SHA256SUMS` file has SHA-256
`666924e261c7760bc598713598390be6b1ca7d0854b5746811fb990cf951cf46`.
Strict verification passed for all 18 entries:

| Release asset | SHA-256 |
|---|---|
| `com.liquilens.evidence.schema.json` | `9519474a4d0bf3a77834320d9aa43a88d5df96d49f691110154050212c7511b7` |
| `com.liquilens.trade-safety-receipt.schema.json` | `6c013eef85134e17b649e67c75227a698b76b7d97c7048edb3e8cd703563620b` |
| `liquilens-broker-preview-reference-v1.schema.json` | `89069649379ca759382dcf3f9237e58b069e7fddeeecae6cffa686bbe7351422` |
| `liquilens-evidence-carrier-0.17.1.tar.gz` | `3febc702920579b51e9579e90f86c86704aa574d1c90bc748d108730106958f4` |
| `liquilens-evidence-carrier-mcp-0.17.1.mcpb` | `4d6c409f2c69588fad6fe13bf2f78ed1b72d3555d81082d5da638d037b0307a1` |
| `liquilens-evidence-carrier-reference-v1.schema.json` | `d54043bf11359749597bff7495b0fffe6ff8453a35144cee4a2bd69711fec7e8` |
| `liquilens-evidence-carrier-v1.schema.json` | `7f8494d8470853dc88665ea32c1dccb40cc58c55b07e9267aa28c81f83c1ccd3` |
| `liquilens-evidence-facet.schema.json` | `e4c6035452d75be280a7b717f85da87319a078dbf5563e62ac3a3cb83486e9a5` |
| `liquilens-fleet-brief-v1.schema.json` | `aaf95337ff973dfbdda97e8ac63975a61b199e43854927404055fbeb52fc6058` |
| `liquilens-trade-safety-policy-v1.schema.json` | `d9171e61c2d378eec545a14bbab0d1ca54302397c809eeeeaae55fb9154ae8d1` |
| `liquilens-trade-safety-receipt-v1.schema.json` | `c2232ae5f80eb42edf7562ae5f5e44ccb9866a13717b697b4d41c28e74b25abe` |
| `liquilens-trade-safety-request-v1.schema.json` | `73af15f84b09b0772368095a01d0f076b9334dd8bbdf9637015aed86e35a47f5` |
| `liquilens_evidence-0.17.1-py3-none-any.whl` | `dec2751fa2f20d09a1a77b5f25ae99f28fa49484ea1bf5ede7ca2bcdd86610ea` |
| `liquilens_evidence-0.17.1.tar.gz` | `f69750534dcc69cb796f83502be5727c4c4fc98a165b583152bc2165622044f5` |
| `liquilens_trade_safety_gateway-0.1.0-py3-none-any.whl` | `7bdb42358adc3746ca771a3ba3b39db9cffdbd1c13682eaa01b1dc58735c03c6` |
| `liquilens_trade_safety_gateway-0.1.0.tar.gz` | `a9068ea949459776be3008daf8ac1e576e2dfeb17b01eba8d655c99e51919282` |
| `trade-safety-intents.json` | `e35efa5568c0328e96871010ff2d52afe767d65deaa1cadd13f759391047a0a2` |
| `verify_hash_tree_v1.mjs` | `a3b318276a5d2580ef255ecf56b8e95a39de17f1c4629e152075c2c0074cce4a` |

## Artifact provenance

One GitHub/Sigstore SLSA provenance statement covers exactly the same 18 asset
names and digests as `SHA256SUMS`. Strict `gh attestation verify` checks passed
with repository, signer workflow, tag ref, and candidate source digest pinned.

- Attestation record:
  [44596593](https://github.com/beepboop2025/liquilens-evidence-carrier/attestations/44596593)
- Signer: `.github/workflows/release.yml@refs/tags/v0.17.1`
- Source digest: `a74274236e177404c2d254541e6a4110a4ce8a0d`
- Invocation:
  [run 33589489958, attempt 1](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33589489958/attempts/1)
- Rekor log index:
  [`2683247539`](https://search.sigstore.dev?logIndex=2683247539)

## MCPB and official Registry

The published MCPB was downloaded, checksum-verified, extracted, and started
directly from its bundled `src` tree. `server/discover` and `tools/list`
succeeded for protocol `2026-07-28`, exposing the four read-only tools without
network access or financial authority. The bundle contains the JavaScript hash
verifier and both FDC3 Trade Safety assets; it excludes the networked gateway.
Its embedded README has SHA-256
`8422e21dc715443c22c8d18e1991fa8427136292a06ee45068db4a1a26029c9e`
and is byte-identical to `mcpb/release-readmes/0.17.1.md`.

The official record is
[`io.github.beepboop2025/liquilens-evidence-carrier` version 0.17.1](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.beepboop2025%2Fliquilens-evidence-carrier/versions/0.17.1).
It was published at `2026-09-02T04:06:37.161326Z`, reports `active` and
`isLatest: true`, and pins the GitHub MCPB URL plus file SHA-256
`4d6c409f2c69588fad6fe13bf2f78ed1b72d3555d81082d5da638d037b0307a1`.
Its stdio metadata declares `financialAuthority: none`, `networkAccess: false`,
and protocol revisions `2026-07-28` and `2025-11-25`.

## OCI container receipt

[Container run 33589489966](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33589489966)
published and smoke-tested:

`ghcr.io/beepboop2025/liquilens-evidence-carrier@sha256:bd9b92f25fa8666ea1f43afc4047261ad82213f3c121da87f4dcb9f2e401776d`

Live manifest inspection resolved that exact OCI index with `linux/amd64` and
`linux/arm64` images. Strict provenance verification bound the subject digest,
tag ref, candidate source digest, and `container.yml` signer. The corresponding
[attestation record 44596849](https://github.com/beepboop2025/liquilens-evidence-carrier/attestations/44596849)
names the candidate and
[run attempt](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33589489966/attempts/1).
The dedicated MCP container remains independently versioned at v0.15.0; it is
not silently relabelled as v0.17.1.

## Canonical hosting and gateway boundary

The GitHub release makes all Trade Safety contracts, FDC3 assets, verifier, and
gateway distributions downloadable. It does not deploy a gateway. During the
2026-09-02 post-release check, each of these canonical identities returned HTTP
404:

- `https://liquilens.in/protocol/liquilens-trade-safety-request-v1.schema.json`
- `https://liquilens.in/protocol/liquilens-trade-safety-policy-v1.schema.json`
- `https://liquilens.in/protocol/liquilens-broker-preview-reference-v1.schema.json`
- `https://liquilens.in/protocol/liquilens-trade-safety-receipt-v1.schema.json`
- `https://liquilens.in/protocol/fdc3/com.liquilens.trade-safety-receipt.schema.json`

Hosted Railway or customer deployment still requires an explicitly authorized
project/environment, exact source and build identity, egress policy, edge quota,
health and semantic-freshness proof, and rollback. Live-order activation further
requires broker preview, executable real-money evidence, scoped service
identity, tenant-local keys, atomic one-time receipt consumption, backup/restore
proof, compliance review, and owner authorization. No release artifact grants
execution, recommendation, allocation, credit-rating, or broker authority.

## Immutable recovery lineage

The `v0.17.0` annotated tag object
`cb85e527c2b74abf476fd9a01b73b2235ce976b7` still targets protected-main merge
`edde9b92ad9851d2974b91326a8c3877f4386d3a`. Its
[release run 33585764285](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33585764285)
failed at target-commit signature verification before build, attestation,
GitHub release, or Registry publication. No v0.17.0 GitHub release or official
Registry version exists. The tag, [`RELEASE-0.17.0.md`](RELEASE-0.17.0.md), and
`mcpb/release-readmes/0.17.0.md` remain immutable historical records and were
not repointed or rewritten for this release.
