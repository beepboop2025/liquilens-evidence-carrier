# v0.17.0 failed release attempt

Status at 2026-09-02: **tagged, but not built, attested, published, registered,
or deployed as v0.17.0**.

The immutable annotated `v0.17.0` tag object is
`cb85e527c2b74abf476fd9a01b73b2235ce976b7`. It targets GitHub protected-main
merge `edde9b92ad9851d2974b91326a8c3877f4386d3a` (pull request #26). The tag
name is consumed and must not be deleted, force-moved, or recreated.

The latest signed, downloadable, and official MCP Registry-listed core release
remains v0.16.0. This document is a failure receipt, not a publication receipt.

## Failure receipt

[Release run 33585764285](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33585764285)
started from the `v0.17.0` tag and failed in `Verify signed tag, commit,
ancestry, and version`. The annotated tag signature passed. Verification of the
target merge commit then failed with `NO_PUBKEY B5690EEEBB952194`.

Because that gate failed:

- `Test and build release artifacts` was skipped;
- artifact attestation was skipped;
- `Publish public release` was skipped; and
- the `mcp-registry` job was skipped.

GitHub has no v0.17.0 release record, and the official MCP Registry has no
`io.github.beepboop2025/liquilens-evidence-carrier` version `0.17.0`. There are
therefore no v0.17.0 release checksums, attestations, downloadable artifacts, or
Registry activation receipts to cite.

## Candidate scope

- Four strict Trade Safety v1 contracts: exact-order request, operator policy,
  broker preview reference, and order-bound receipt.
- Deterministic Python issuance and verification with content-derived identity,
  hash-only paper conformance, tenant-local HMAC authentication, strict clocks,
  rights, request/account binding, policy replay, and an all-false authority
  boundary.
- CLI issue/verify commands, offline secretless MCP verification, JavaScript
  identity verification, FDC3 context/intent fragments, and golden vectors.
- A separately locked, fixed-upstream FastAPI sandbox. It has no broker
  credential, preview, order route, recommendation, resize, or execution tool;
  live mode deterministically fails closed.
- A staged distribution, broker/OMS enforcement, shadow-validation,
  commercialization, reliability, and adoption plan.

## Recovery boundary

Recovery uses a new `v0.17.1` identity and the independent gate in
[`RELEASE-0.17.1.md`](RELEASE-0.17.1.md). Passing a later workflow does not turn
the failed v0.17.0 attempt into a release. Version 0.17.1 must not reuse the
v0.17.0 tag, versioned bundle name, or release identity, and must not imply that
v0.17.0 reached the Registry; stable unversioned protocol filenames remain
intentionally reusable.

## Runtime boundary

A later recovery release may publish a deployable sandbox artifact without
activating a hosted gateway. Hosted Railway or customer deployment requires an
explicitly authorized project/environment, exact source/build identity, egress
policy, edge quota, health/freshness proof, and rollback target. Live order
activation additionally requires broker preview, executable real-money
evidence, scoped service identity, asymmetric or tenant-local keys, atomic
receipt consumption, backup/restore proof, compliance review, and owner
authorization.

## Immutable prior receipt

The v0.16.0 release record, embedded MCPB README, hashes, commit, workflow and
Registry URL remain frozen. The prepared v0.17.0 MCPB README is also frozen at
SHA-256 `ec252e147ed8e835ba4eaf3a2a4132ab70f3739b14eb0a0610766c3574b51767`;
its wording describes candidate bytes and cannot be upgraded into publication
proof. Preparing v0.17.1 rewrites neither prior record.
