# v0.17.1 recovery release candidate

Status at 2026-09-02: **prepared source; not tagged, published, registered, or
deployed as v0.17.1**.

The latest signed, downloadable, and official MCP Registry-listed core release
remains v0.16.0. Version 0.17.1 is a fresh release identity for the Trade Safety
source after the immutable v0.17.0 attempt failed before build or publication.
This document is a release gate for candidate bytes, not a publication receipt.

## Recovery lineage

- Failed tag object: `cb85e527c2b74abf476fd9a01b73b2235ce976b7`
- Failed tag target: `edde9b92ad9851d2974b91326a8c3877f4386d3a`
- Failed workflow: [33585764285](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33585764285)
- Failure boundary: target-commit signature verification, before build,
  attestation, GitHub release creation, or MCP Registry publication
- Public state: no v0.17.0 GitHub release and no official Registry version
  0.17.0

The v0.17.0 tag and its prepared MCPB README remain immutable historical
records. They are not aliases for v0.17.1 and must not be repointed or rewritten.

Commit `32c97e5c89241db85132099b42e63a391274f441` demonstrates the required
release-signing identity: it has an allowlisted SSH signature from
`liquilens-evidence-carrier-release` and is an ancestor of protected `main`.
It is identity evidence only: its source metadata is version 0.17.0, so it must
not be a v0.17.1 tag target. The final v0.17.1 tag must target a new allowlisted
SSH-signed recovery commit containing the complete 0.17.1 tree after that
commit's pull request lands. It must not target GitHub's automatically generated
GPG merge commit.

## Candidate scope

- Four strict Trade Safety v1 contracts: exact-order request, operator policy,
  broker preview reference, and order-bound receipt.
- Deterministic Python issuance and verification with content-derived identity,
  hash-only paper conformance, tenant-local HMAC authentication, strict clocks,
  rights, request/account binding, policy replay, and an all-false authority
  boundary.
- CLI issue/verify commands, offline secretless MCP verification, JavaScript
  identity verification, FDC3 context/intent assets, and golden vectors.
- A deterministic offline MCPB containing the protocol verifier and FDC3 assets,
  but not the networked gateway.
- A separately locked, fixed-upstream FastAPI sandbox with no broker credential,
  preview, order route, recommendation, resize, or execution tool; live mode
  deterministically fails closed.
- Fail-closed runtime, schema, gateway, and release-recovery corrections made
  after the v0.17.0 attempt.

## Required release gates

1. Select one reviewed candidate commit after all source and documentation
   changes are complete. Sign that candidate commit with the allowlisted SSH
   release identity, merge its pull request, and confirm the exact candidate is
   an ancestor of protected `main`. A dirty worktree, unsigned branch head, or
   GitHub-generated merge commit is not the release target.
2. Run the manual `Release preflight` workflow with the exact candidate SHA and
   version `0.17.1`. Require a green receipt proving the candidate's allowlisted
   SSH signature, protected-main ancestry, version parity, non-placeholder MCPB
   digest, deterministic MCPB replay, release-critical package/runtime checks,
   and local/remote tag absence before creating the immutable tag. Dispatch it
   only on protected `main`; the workflow rejects every other controller ref.
3. Verify the candidate commit's SSH signature with the release workflow's
   `ops/release/liquilens-evidence-carrier.allowed_signers` file. Separately
   verify protected-main ancestry; do not require GitHub's GPG merge commit to
   satisfy the SSH allowlist.
4. Pass the matching successful preflight run ID to
   `scripts/create_release_tag.py --push --push-key <private-key-path>`. The
   repository-enforced creation ruleset `22065439` blocks ordinary account and
   integration tag creation; only the reviewed deploy-key class can create a
   matching ref. The controller rechecks both active tag rulesets, the sole
   write deploy key, live signature, ancestry, metadata and tag absence; binds
   the current protected-main workflow run to the exact candidate and version;
   then creates and pushes one signed annotated `v0.17.1` tag. Do not create the
   tag with a standalone `git tag` command. The tag-triggered workflow must
   independently verify tag signature, candidate-commit signature, ancestry,
   and version parity before any build step runs. See
   [`TAG-CREATION-POLICY.md`](../ops/release/TAG-CREATION-POLICY.md).
5. Pass root Python 3.11-3.13, schema, JavaScript, CLI, MCP, lint, type, package,
   browser, container-smoke, deterministic MCPB, and locked gateway tests.
6. Confirm protocol hashes, package data, FDC3 assets, golden receipts, Registry
   metadata, release README, and every machine-readable version agree on 0.17.1.
7. Publish checksum- and provenance-attested wheel, source, integration-kit,
   gateway, schema, JavaScript verifier, FDC3, and MCPB artifacts only from the
   successful tagged workflow.
8. Anonymously retrieve the GitHub assets, verify `SHA256SUMS` and attestations,
   run the extracted offline MCPB, and confirm the official MCP Registry reports
   active version 0.17.1 with the exact published bundle identity.
9. Publish the exact tagged schema/FDC3/document bytes to canonical LiquiLens
   Pages. Stage and verify any Worker catalog separately before promotion, with
   an explicit rollback version.

## Runtime and deployment boundary

The release may publish a deployable sandbox artifact without activating a
hosted gateway. Hosted Railway or customer deployment requires an explicitly
authorized project and environment, exact source/build identity, egress policy,
edge quota, health and semantic-freshness proof, and a rollback target. Live
order activation additionally requires broker preview, executable real-money
evidence, scoped service identity, tenant-local key management, atomic receipt
consumption, backup/restore proof, compliance review, and owner authorization.

No v0.17.1 tag object, successful tagged release run, checksum manifest,
attestation, GitHub release URL, Registry record, or deployment receipt exists
yet. The signed recovery commit and a green preflight run are readiness evidence,
not publication proof. Add publication facts only after they have been
independently verified.

## Immutable prior records

- [`RELEASE-0.16.0.md`](RELEASE-0.16.0.md) remains the current publication
  receipt.
- [`RELEASE-0.17.0.md`](RELEASE-0.17.0.md) remains the failed-attempt receipt.
- `mcpb/release-readmes/0.17.0.md` remains byte-frozen and is not reused by the
  v0.17.1 bundle.
