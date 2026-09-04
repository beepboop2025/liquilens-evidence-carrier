# Trade Safety Gateway 0.1.3 publication receipt

Status verified on 2026-09-04: **signed gateway-only tag and attested OCI index
published successfully on 2026-09-02**. This is a historical source and
artifact receipt. It is not a GitHub Release, hosted gateway, paid-route
activation, customer deployment, broker integration, or financial authority.

## Signed source identity

The signed annotated tag `trade-safety-gateway-v0.1.3` has tag-object SHA
`757c18928c8036910ab50c80ec073679d7434abf`, tagger time
`2026-09-02T15:54:18Z`, and annotation
`LiquiLens Trade Safety Gateway 0.1.3`. It targets allowlisted SSH-signed commit
`fa8e25ae8e0e992611706b8d66e951342d594243`, tree
`7680694bf3397a0844f2388fb29067ff402f066d`, with parent
`4ba4bd6555896ad8c71bc1b32a62494ce0c4fe7a` and subject
`ci: add signed gateway-only release lane`.

Local `git tag -v` and `git verify-commit` verification succeeded for the
`liquilens-evidence-carrier-release` ED25519 identity with fingerprint
`SHA256:yhoa/PIDMM6M/ZennILp8jtRJy5pArncJRARbQssTMI`.

## Workflow and OCI identity

[Trade Safety Gateway Container run 33651560380](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33651560380)
was triggered by that exact tag and target commit at
`2026-09-02T15:54:22Z`; it completed successfully at
`2026-09-02T15:55:57Z`. Publish job `100319581407` ran successfully from
`15:54:26Z` through `15:55:56Z`. The pull-request-only smoke job was
intentionally skipped; the publish job pulled and smoke-tested the exact
published digest.

The run published the Linux amd64/arm64 OCI index

```text
ghcr.io/beepboop2025/liquilens-trade-safety-gateway@sha256:9b8f704547ecf6c43039b34149d6cca842de5d66cba13c040199cf5f3f216d61
```

At publication time, the run attached these aliases to that digest:

- `0.1.3`;
- `sha-fa8e25ae8e0e992611706b8d66e951342d594243`; and
- `signed-tag-757c18928c8036910ab50c80ec073679d7434abf`.

It emitted no `latest` or `core-*` alias. Index annotations bind gateway
version `0.1.3`, core version `0.19.0`, the target commit, signed tag and tag
object, Apache-2.0 licensing, and authority
`read-only-hash-only-sandbox`. The workflow requested maximum build provenance
and an SBOM.

[Provenance attestation 44751184](https://github.com/beepboop2025/liquilens-evidence-carrier/attestations/44751184)
binds the OCI subject digest above to the repository. Its transparency entry is
[Rekor index 2687448855](https://search.sigstore.dev?logIndex=2687448855). The
registry-side attestation manifest is
`sha256:82de8f5ff24edf99263c22b18e69d07c91e5a5b707fd98e83fce7061c83176ae`;
that value is an attestation-manifest digest, not the image-index digest.

## Verification boundary

`gh release view trade-safety-gateway-v0.1.3` reports no GitHub Release object.
The verification token available on 2026-09-04 did not carry `read:packages`,
so this receipt preserves publication-time aliases from the successful workflow
log and does not claim they were independently re-resolved from the registry on
the later verification date. The signed tag, exact workflow result, image
digest, and provenance attestation remain separately addressable evidence.

Gateway 0.1.3 remained a deployable read-only, hash-only sandbox. Nothing in
this receipt proves a public endpoint, x402 payment, payer, customer, revenue,
order-path enforcement, or protected trade.
