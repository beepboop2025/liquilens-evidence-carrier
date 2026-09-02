# v0.19.0 release receipt

Status verified on 2026-09-02: **signed, published, attested, and active/latest
in the official MCP Registry**. The core CLI and Trade Safety gateway OCI
indexes are also published, attested, and smoke-tested by digest. This is a
source/artifact publication receipt; it is not a hosted Trade Safety gateway or
live-order activation receipt.

## Release identity

| Field | Verified value |
|---|---|
| Version | `0.19.0` |
| Release commit | `8f5738c9e77cc95b9a68543d478b9521f5595d61` |
| Release tree | `acca6fa7aab75ebc91bf044e153c6468cd6f9c0c` |
| Annotated tag | `v0.19.0` |
| Tag object | `c3239bfc7c4d3c4b7fc5ce26e0f602962e7d4337` |
| Tag target | `8f5738c9e77cc95b9a68543d478b9521f5595d61` |
| Tagger time | `2026-09-02T12:35:32Z` |
| Commit/tag signing principal | `liquilens-evidence-carrier-release` |
| Allowed ED25519 fingerprint | `SHA256:yhoa/PIDMM6M/ZennILp8jtRJy5pArncJRARbQssTMI` |
| Preflight | [run 33630656569](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33630656569) |
| Tagged release | [run 33630790150](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33630790150) |
| GitHub release | [`v0.19.0`](https://github.com/beepboop2025/liquilens-evidence-carrier/releases/tag/v0.19.0) |

Repository-local allowlist verification accepted both the commit and annotated
tag signatures. Remote `main`, the tag target, and the release workflow source
all resolved to the same commit at verification time. GitHub Release ID
`381242008` was published at `2026-09-02T12:36:19Z` and is neither a draft nor a
prerelease.

Active ruleset `21288366`, `Immutable version tags`, rejects update and deletion
of `refs/tags/v*` with no bypass. GitHub's Release API nevertheless reports
`immutable: false`. The tag is platform-protected and the current asset bytes
are checksum- and transparency-attested, but the Release assets are not
described as protected by GitHub's immutable-release feature.

## Release gates and artifacts

The exact protected-main preflight passed its release-critical tests, corpus
replay, deterministic MCPB construction, package inspection, and signature
gates before tag creation. The tagged release then rechecked tag/commit
signatures, ancestry, and version before building and publishing.

The GitHub release contains 23 assets: `SHA256SUMS` plus 22 checksummed
artifacts. A fresh anonymous download passed strict checksum verification for
all 22 entries. The checksum manifest itself has SHA-256
`c6d52cbf8794db6e478e3b2ea9e1ed8eee7757137650892a6a96fcbb839bb6bc`.

| Selected release asset | SHA-256 |
|---|---|
| `liquilens-evidence-carrier-mcp-0.19.0.mcpb` | `11db11aefafcc6c4ba558877d1f9892fc708150b3afbaa28a741e74435b9a91a` |
| `liquilens-evidence-carrier-0.19.0.tar.gz` | `ba57d0a4581ccab33a99c9a0b514e3b784ad051c662e9e29fd18b19e8219f86b` |
| `liquilens_evidence-0.19.0-py3-none-any.whl` | `1adccb72376f50456fd16a979e372f802ae73ba35b766633bc3d8bd4ab5abcc8` |
| `liquilens_trade_safety_gateway-0.1.2-py3-none-any.whl` | `e3c685a300aadaafa406ccf38b2d8c56107e7145f6a075d0909a9c74a715f285` |
| `openbb_liquilens_evidence-0.2.0-py3-none-any.whl` | `1dea5f5284e45746f094c5051d11f8c2aca9b9e5e62bc4f7d41e10965d5fc82d` |
| `liquilens-trade-safety-0.1.0.tgz` | `06e4ef437d5a53ba013aa5abfdbddd16bb932c2d63e4ad3d3982f99b7af54395` |
| `liquilens-trade-safety-conformance-v1.json` | `12b2e15ddef4989e9b999636ea557576c16b965444ad9ab00cb3cfa1f68c0729` |

[Attestation 44695012](https://github.com/beepboop2025/liquilens-evidence-carrier/attestations/44695012)
contains SLSA provenance for the same 22 non-manifest subjects. Strict
verification of the downloaded MCPB enforced `release.yml`, tag ref
`refs/tags/v0.19.0`, source commit `8f5738c9e77cc95b9a68543d478b9521f5595d61`,
and a GitHub-hosted runner. Its Rekor log index is
[`2685496291`](https://search.sigstore.dev?logIndex=2685496291). `SHA256SUMS`
is the checksum index and is not itself one of those provenance subjects.

The embedded `mcpb/release-readmes/0.19.0.md` remains byte-frozen at SHA-256
`2d1b4dce5431451510d786f70a5a8e401180f4dd8e4820025e101444e5a97aa6`.
Its pre-publication language is a temporally correct description of the bytes
when prepared; post-release source documentation does not rewrite it.

## Official MCP Registry

The official record is
[`io.github.beepboop2025/liquilens-evidence-carrier` version 0.19.0](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.beepboop2025%2Fliquilens-evidence-carrier/versions/0.19.0).
It was published at `2026-09-02T12:36:47.695864Z`, reports `active` and
`isLatest: true`, and pins the GitHub MCPB URL plus file SHA-256
`11db11aefafcc6c4ba558877d1f9892fc708150b3afbaa28a741e74435b9a91a`.
Its stdio metadata declares `financialAuthority: none`, `networkAccess: false`,
and protocol revisions `2026-07-28` and `2025-11-25`.

## Core OCI index

[Container run 33630789998](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33630789998)
published and smoke-tested by digest:

`ghcr.io/beepboop2025/liquilens-evidence-carrier@sha256:bdbfed2afa87f25e8ef88dffeb4ba7ab198854705528c0de5abe31552a170b9a`

Live GHCR inspection resolved that exact OCI index with:

- `linux/amd64` manifest
  `sha256:1262b93244da0f6666e8ae55d19fda70a173f65d552064598ff9c44760b99c6b`;
- `linux/arm64` manifest
  `sha256:e408163fcc41ac7166e7ff86aaa55f042791259d3a58ea59ae0e81ffd175acbc`;
- version `0.19.0` and revision
  `8f5738c9e77cc95b9a68543d478b9521f5595d61`; and
- one SPDX SBOM and one SLSA v1 BuildKit provenance layer attached to each
  platform manifest.

[Attestation 44695462](https://github.com/beepboop2025/liquilens-evidence-carrier/attestations/44695462)
independently binds the index digest to `container.yml@refs/tags/v0.19.0`, the
same source commit, a GitHub-hosted runner, and
[run 33630789998, attempt 1](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33630789998/attempts/1).
Strict `gh attestation verify` passed; its Rekor log index is
[`2685516286`](https://search.sigstore.dev?logIndex=2685516286).

## Trade Safety gateway OCI index

[Gateway container run 33630790011](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33630790011)
published and smoke-tested by digest:

`ghcr.io/beepboop2025/liquilens-trade-safety-gateway@sha256:b5c43013da1fdddd9e6e56cab0e4f0f562e39ab25cc640869c5008e3457218e3`

Live GHCR inspection resolved that exact OCI index with:

- `linux/amd64` manifest
  `sha256:3ec553fcbbaef30e496b15895231ff628ddce7f72bcdf30817f2366171f426b2`;
- `linux/arm64` manifest
  `sha256:0d20d45473b6d389df116336a48c5bb19e7fc385dac4448f02e6593d8ce2e94f`;
- gateway version `0.1.2`, core version `0.19.0`, revision
  `8f5738c9e77cc95b9a68543d478b9521f5595d61`, and authority label
  `read-only-hash-only-sandbox`; and
- one SPDX SBOM and one SLSA v1 BuildKit provenance layer attached to each
  platform manifest.

[Attestation 44695195](https://github.com/beepboop2025/liquilens-evidence-carrier/attestations/44695195)
independently binds the index digest to
`gateway-container.yml@refs/tags/v0.19.0`, the same source commit, a
GitHub-hosted runner, and
[run 33630790011, attempt 1](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33630790011/attempts/1).
Strict `gh attestation verify` passed; its Rekor log index is
[`2685504149`](https://search.sigstore.dev?logIndex=2685504149).

## Hosting and authority boundary

The five stable Trade Safety schema/FDC3 identities remain canonically hosted
by LiquiLens Pages. [Run 33592149926](https://github.com/beepboop2025/liquilens-site/actions/runs/33592149926)
succeeded for site revision
`3ec660175c81c5b282715ee400eea2f771dc2610`; this is schema-hosting proof, not
a hosted gateway.

The gateway package and OCI image are deployable read-only verification
artifacts, but no production project/environment or public gateway URL was
activated by these workflows. Live-order activation remains outside this
release and still requires broker idempotency, uncertain-outcome
reconciliation, durable atomic claims, eligible real-money evidence, scoped
service identity, tenant-local keys, compliance review, and owner
authorization. No release artifact grants execution, recommendation,
allocation, credit-rating, market-data entitlement, or broker authority.

## Preserved history

The full v0.18.0 receipt remains
[`RELEASE-0.18.0.md`](RELEASE-0.18.0.md), and the v0.18.0 MCPB README remains
byte-frozen. The failed v0.17.0 tag and v0.17.1 recovery records are also left
unchanged; post-release documentation for v0.19.0 does not rewrite prior
release history.
