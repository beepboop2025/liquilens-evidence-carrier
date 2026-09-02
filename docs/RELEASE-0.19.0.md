# v0.19.0 release candidate

Status at 2026-09-02: **prepared source; not tagged, published, registered, or
deployed as v0.19.0**.

This candidate preserves the exact bytes and identities of every Trade Safety
v1 schema. It adds a deterministic adversarial conformance corpus, a
dependency-free TypeScript-compatible Node raw-UTF8 verifier and authenticated
paper-only order guard, an offline OpenBB Trade Safety verifier pinned to the
released v0.18.0 core wheel, MCP corpus discovery, and release gates for those
assets. Gateway package 0.1.2 pins this candidate core but adds no execution
route. Candidate workflows package both the dedicated MCP server and gateway as
attested amd64/arm64 OCI indexes after the signed release; the gateway image is
explicitly a read-only, hash-only sandbox, not a hosted or order-authorizing
service.

There is no v0.19.0 tag object, GitHub release, official MCP Registry record,
OCI deployment, hosted gateway, or live-order activation receipt yet. A local
candidate digest proves only reproducible prepared bytes; it does not prove the
future release URL exists.
Two byte-identical deterministic local MCPB builds produced SHA-256
`692f19b3b202fe9a6a8601532e0728f36e406665dfddd09643a1d737d2b5ef74`.

## Candidate gates

- Reproduce the corpus and pass every Python and Node conformance case.
- Run the full locked Python suite, strict typing/linting, MCP response tests,
  OpenBB tests, and read-only gateway tests.
- Build two byte-identical MCPB artifacts and bind `server.json` to that digest.
- Build and inspect the root, OpenBB, gateway, and TypeScript package artifacts.
- Build, smoke, label, and attest the multi-architecture MCP and gateway OCI
  indexes without deploying either image to a hosted runtime.
- Merge through protected `main`, then run the exact-SHA preflight before a
  controlled signed tag can be created.
- Publish checksums and provenance attestations before any channel is called
  live.

Live routing stays fail-closed. It is outside this candidate and still requires
broker idempotency, uncertain-outcome reconciliation, durable atomic claims,
eligible real-money evidence, compliance review, and owner authorization.
