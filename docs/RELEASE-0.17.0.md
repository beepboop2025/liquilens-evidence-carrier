# v0.17.0 release candidate

Status at 2026-09-02: **prepared source; not tagged, published, registered, or
deployed as v0.17.0**.

The latest signed core release remains v0.16.0. This document is the release
gate for candidate bytes and must not be read as a publication receipt.

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

## Required release gates

1. The exact candidate commit is reviewed, signed, merged to protected `main`,
   and passes all required CI jobs.
2. Root Python 3.11-3.13, schema, JavaScript, CLI, MCP, lint, type, package,
   browser, container-smoke, and locked gateway tests pass.
3. Protocol catalog hashes, package data, FDC3 assets, golden receipts,
   deterministic MCPB replay, Registry metadata, and source versions agree.
4. The signed annotated `v0.17.0` tag points to that signed protected-main
   commit and passes the release workflow's tag/commit/ancestry checks.
5. GitHub publishes checksum and provenance-attested wheel, source,
   integration-kit, gateway, schema and MCPB artifacts.
6. An anonymous download matches `SHA256SUMS`, `server.json`, and its GitHub
   attestation; the official MCP Registry reports active version `0.17.0`.
7. Canonical LiquiLens Pages publishes the exact tagged schema/FDC3/document
   bytes, then its Worker catalog is staged, verified, promoted, and retained
   with a tested rollback version.

## Runtime boundary

The release can publish a deployable sandbox artifact without activating a
hosted gateway. Hosted Railway or customer deployment requires an explicitly
authorized project/environment, exact source/build identity, egress policy,
edge quota, health/freshness proof, and rollback target. Live order activation
additionally requires broker preview, executable real-money evidence, scoped
service identity, asymmetric or tenant-local keys, atomic receipt consumption,
backup/restore proof, compliance review, and owner authorization.

## Immutable prior receipt

The v0.16.0 release record, embedded MCPB README, hashes, commit, workflow and
Registry URL remain frozen. Preparing v0.17.0 does not rewrite them.
