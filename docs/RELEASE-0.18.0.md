# v0.18.0 order-guard release candidate

Status at 2026-09-02: **prepared source; not tagged, published, registered, or
deployed as v0.18.0**.

The latest signed, downloadable, and official MCP Registry-listed core release
remains v0.17.1. This document is a release gate for candidate bytes, not a
publication receipt. No v0.18.0 tag object, successful tagged release run,
checksum manifest, attestation, GitHub release, Registry record, or container
receipt exists yet.

The deterministic candidate MCPB referenced by `server.json` has SHA-256
`f57ce3fb488b693e633d8bc66f980b616af09a8080722a11c50507496f39a2bb`.
Two local builds from these bytes must remain identical, but this prepared hash
does not prove that the future GitHub release URL exists.

## Candidate scope

- Add synchronous and asynchronous paper-only order gateways around one private
  broker or OMS submit callback.
- Require an operator-owned current clock, tenant-authenticated HMAC receipt,
  exact request and policy binding, exact account/tenant/operator/agent/runtime/
  strategy/issuer/key binding, an enforced `pass`, and atomic replay rejection
  before the callback can run.
- Reject hash-only, observation, live, expired, future, malformed, mismatched,
  and replayed receipts before broker code. Live submission remains held until
  durable idempotency and uncertain-outcome reconciliation are implemented and
  owner-authorized.
- Keep the offline MCP server read-only. The deterministic MCPB contains the
  Python library, JavaScript verifier, schemas, and FDC3 assets, but exposes no
  order or execution tool and excludes the networked gateway.
- Refresh the separately packaged Trade Safety gateway from version 0.1.0 to
  0.1.1, pin FastAPI 0.141.1-compatible dependencies, and require pytest 9 so
  its lock resolves beyond the known vulnerable Starlette and pytest ranges.
- Preserve every Trade Safety schema as v1. No protocol `$id`, schema byte, or
  protocol artifact hash changes in this candidate.

## Published baseline and immutable history

The complete v0.17.1 publication receipt remains
[`RELEASE-0.17.1.md`](RELEASE-0.17.1.md). Its annotated tag, release assets,
MCPB README, Registry record, and OCI digest are not changed or relabelled by
this candidate. The failed v0.17.0 tag and its records also remain immutable;
v0.17.0 must not be deleted, moved, recreated, or reused.

The v0.18.0 tag must target the final allowlisted SSH-signed candidate commit
containing this complete tree after its pull request lands. That exact candidate
must be an ancestor of protected `main`. GitHub's automatically generated GPG
merge commit is not the tag target unless it is independently the allowlisted
candidate, which normal merge flow does not make it.

## Required release gates

1. Review one complete v0.18.0 candidate, sign its commit with the allowlisted
   SSH release identity, merge its pull request, and prove that exact candidate
   is an ancestor of protected `main`.
2. Dispatch `Release preflight` on protected `main` with the exact candidate SHA
   and version `0.18.0`. Require a green receipt for signature, ancestry,
   machine-version parity, non-placeholder MCPB digest, tag absence, full tests,
   both locked environments, package builds, and two byte-identical MCPB builds.
3. Pass the successful preflight run ID to `scripts/create_release_tag.py` first
   without `--push`. Review its protected-main controller, active workflow,
   repository tag rulesets, sole write-enabled deploy key, candidate, version,
   and tag-absence receipts.
4. Use the same controller with `--push --push-key <private-key-path>` to create
   and push one signed annotated `v0.18.0` tag. Do not create the tag with a
   standalone `git tag` or ordinary-account push.
5. Let the tag trigger the Release and Container workflows. Do not manually
   dispatch them as a substitute for the signed-tag event.
6. Verify the GitHub release asset list, `SHA256SUMS`, provenance attestations,
   exact MCPB digest, official Registry 0.18.0 active/latest record, OCI index
   digest and platforms, and anonymous smoke tests before calling v0.18.0 live.
7. Record those publication facts in a separate post-release documentation
   commit. Do not rewrite `mcpb/release-readmes/0.18.0.md` after publication or
   any v0.17.x artifact.

The preflight and controlled tag creation must occur while protected `main`
still points at the controller commit bound to the successful preflight. If
`main` advances, rerun preflight and the dry controller check; do not reuse the
superseded receipt.

## Canonical hosting boundary

The stable Trade Safety v1 schema and FDC3 identities are hosted independently
of this candidate. LiquiLens Pages run
[33592149926](https://github.com/beepboop2025/liquilens-site/actions/runs/33592149926)
succeeded at 2026-09-02T04:49:12Z for site revision
`3ec660175c81c5b282715ee400eea2f771dc2610`, with exact-byte checks for the
request, policy, broker-preview, receipt, and FDC3 receipt schemas. This is
schema-hosting proof, not a hosted Trade Safety gateway; it does not publish
v0.18.0 or activate a gateway.

Hosted gateway or customer deployment still requires an explicitly authorized
project and environment, exact source/build identity, egress policy, edge
quota, health and semantic-freshness proof, credential and key isolation,
durable replay/idempotency state, backup/restore proof, rollback, compliance
review, and owner authorization. No package, MCPB, receipt, schema, or `pass`
grants execution, recommendation, allocation, credit-rating, or broker
authority.
