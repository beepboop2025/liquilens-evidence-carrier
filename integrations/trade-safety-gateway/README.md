# LiquiLens Trade Safety Gateway

This integration is a read-only sandbox adapter for AI trading copilots. It
accepts one exact Trade Safety Request and one operator policy, reads a small set
of fixed public evidence endpoints, and issues the deterministic SHA-256-only
receipt defined by `liquilens_evidence.trade_safety`.

It is **not** an order gateway. It has no broker credentials, broker preview,
order-submission route, recommendation route, automatic resize behavior,
custody, settlement, or real-money authority. Every response carries
`X-Trade-Safety-Mode: sandbox`, `X-Trade-Safety-Authority: read-only`, and
`X-Trade-Safety-Execution: disabled`. Live-mode assessments deterministically
return `unavailable` because the evidence is context-only, the receipt is not
tenant-authenticated, and the broker-preview reference is `not_applicable`.

## HTTP and MCP surface

- `GET /healthz` reports process health without probing or masking source state.
- `GET /v1/capabilities` describes static limits, sources, and authority.
- `POST /v1/check` accepts exactly `{"request": {...}, "policy": {...}}`.
- `POST /mcp` supports `initialize`, `ping`, `tools/list`, and `tools/call`.
- MCP exposes only `assess_trade_safety` and `trade_safety_capabilities`.
- MCP negotiates initialization-based `2025-11-25` and stateless
  `2026-07-28`; the latter exposes `server/discover` metadata.
- FastAPI publishes generated OpenAPI at `/openapi.json` and Swagger UI at
  `/docs`.

There is deliberately no execution-shaped MCP tool.

## Fixed evidence adapters

| Product | Fixed request | Receipt state and boundary |
| --- | --- | --- |
| Seiche | `GET https://api.seiche.info/api/trade-safety/risk-context`, schema `seiche.risk-context.v1` | `context_only`; the native cache-only projection, conservative evidence clocks, rights state, staleness counts, attestation boundary, and projection digest are validated and retained. Seiche is not order-bound upstream, so the receipt explicitly binds its exact projection digest to the canonical Trade Safety request hash. |
| Undertow | `POST https://api.seiche.info/undertow/mcp`, tool `trade_safety_exit_context` | `context_only`; the gateway sends the canonical Trade Safety request hash with an exact observe/paper BTC/USD sell rung. Undertow must echo that binding and provide a verified PIT head/chain, deployed SHA, approved derived-metadata rights, complete six-venue coverage, coherent clocks, and an intact context digest. |
| LiquiLens | `GET https://api.liquilens.in/api/failure-radar/institution/{quoted_slug}` | Called only for a validated `request.order.instrument.identifiers.liquilens_institution_slug`; only latest period end and historical-evidence status/eligibility are projected. Otherwise `not_applicable`. |

Undertow JSON-RPC text content is ignored. The gateway consumes
`structuredContent` only; Seiche is consumed from its fixed REST representation.
Both native objects use exact-key validation, have their unkeyed canonical
digests recomputed, and are checked for cross-field consistency after the digest
check. The exact uncompressed HTTP entity is hashed independently. Source,
observation/as-of, knowledge, producer retrieval, gateway retrieval, native
expiry, and local receipt-expiry clocks remain separate.

Undertow completeness is roster-scoped, not a claim about the whole market.
The gateway requires exact set equality with its declared six-CEX roster and
cross-checks quote conversion, required depth band, per-venue basis-point and
dollar costs, best/worst venue, spread, peg, proof, rights, and expiry before
using a result. Off-roster CEXs, DEXs, and agent-native venues remain unmeasured
by construction; adding or changing a venue requires a reviewed contract update
rather than silent acceptance.

## Fail-closed and network boundary

- The request and policy—including their cross-reference, current lifetime, and
  the optional institution slug—are validated before any outbound call.
- Destinations, methods, MCP tool names, and URL bases are constants. Request
  bodies cannot provide a URL.
- Redirects, environment proxies, cookies, compression, non-JSON bodies,
  non-200 responses, malformed/duplicate-key JSON, MCP errors, and missing
  `structuredContent` are rejected.
- Request bodies are capped at 64 KiB; source bodies at 1 MiB; gateway responses
  at 512 KiB. Each upstream operation has a five-second total timeout and a
  two-second connect timeout.
- Independent eligible sources are fetched concurrently. A source error,
  producer-declared unavailable state, stale native clock, digest failure,
  request-hash mismatch, proof failure, or rights mismatch becomes that
  product's typed `unavailable` evidence section. Because Seiche and Undertow
  are mandatory products, the receipt then fails closed.
- The service does not log request bodies, response bodies, secrets, account
  data, or source exceptions. The bundled command disables access logs.
- Every evidence section and the broker-preview reference carries
  `trade_safety_request_hash(request)`. Undertow also receives and echoes it;
  Seiche's native projection digest and request hash receive a separate local
  binding digest inside the hash-sealed receipt. No nearest-rung result is
  promoted to exact-order evidence.
- Observe and paper are the only modes sent to Undertow. Live and buy requests
  never reach its sell-side paper-context tool; they produce typed unavailable
  evidence instead.

Public-source access does not imply real-money or redistribution rights. All
three adapters therefore declare `metadata_only`, `context_only`,
`real_money_eligible: false`, and `executable_quote: false` where applicable.

## Local development

From this directory, install the repository protocol package and the gateway in
one environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ../.. -e '.[test]'
pytest
liquilens-trade-safety-gateway
```

The server listens on `0.0.0.0:8080`. No API key or broker secret is accepted or
required.

## Container build

Build from the evidence-carrier repository root so the image contains the exact
root Trade Safety implementation:

```bash
docker build -f integrations/trade-safety-gateway/Dockerfile \
  --build-arg SOURCE_REVISION="$(git rev-parse HEAD)" \
  --build-arg CREATED="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --build-arg ISSUER_ENDPOINT=https://your-sandbox.example/v1/check \
  -t liquilens-trade-safety-gateway:0.1.3 .
docker run --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop ALL --security-opt no-new-privileges \
  -p 8080:8080 liquilens-trade-safety-gateway:0.1.3
```

The Dockerfile pins its base image by multi-platform digest, installs from the
committed gateway lock, uses a non-root numeric user, and exposes source/build
identity through health and capabilities. Production deployment should also
apply egress policy allowing only `api.seiche.info:443` and
`api.liquilens.in:443`; application allowlisting is not a substitute for a
network policy. A public edge must additionally apply a bounded request quota;
the application does not trust client-supplied IP headers as a rate-limit key.

The release workflow publishes an attested multi-architecture OCI package to
GHCR. That registry artifact is distribution, not a hosted deployment or an
order-authorizing service; it retains the same read-only, hash-only sandbox
boundary and must be operated by the adopter.

Gateway `0.1.3` has an independent release identity so publishing it does not
move or reuse the immutable core `v0.19.0` tag. After the release commit has
landed on `origin/main`, an allowlisted release owner may create and push the
annotated, signed tag `trade-safety-gateway-v0.1.3`. The workflow rejects a
lightweight or unsigned tag, an unsigned target commit, a target outside
`origin/main`, a gateway/tag version mismatch, or any core `VERSION` other than
`0.19.0`. It also rejects pre-existing gateway version, source-commit, or signed
tag-object OCI tags before publishing. The gateway-only lane emits no floating
`latest` or `core-*` alias; its multi-architecture image retains maximum build
provenance, an SBOM, a separate GitHub provenance attestation, and the signed
Git tag object in its OCI metadata.

## Focused tests

The tests inject byte-exact fake upstream responses and cover:

- unsupported and invalid sizes never calling Undertow;
- source errors, producer-unavailable results, stale clocks, and digest
  mismatches failing closed;
- incomplete/off-roster Undertow venue maps and inconsistent conversion, depth,
  cost, rights, PIT, deployed-SHA, or authority fields failing closed;
- exact response hashing, native-digest retention, and exact-order request-hash
  binding across both products;
- paper receipts passing and limiting under operator policy;
- live receipts never passing and carrying a not-applicable broker preview;
- conditional, minimal LiquiLens projection;
- MCP tool inventory/calls, OpenAPI, sandbox headers, strict JSON, and size
  limits.

This directory is an implementation artifact only. Building or testing it does
not deploy or register a public service.
