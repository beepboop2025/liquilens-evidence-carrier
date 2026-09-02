# LiquiLens Trade Safety Gateway

This integration is a read-only sandbox adapter for AI trading copilots. It
accepts one exact Trade Safety Request and one operator policy, reads a small set
of fixed public evidence endpoints, and issues the deterministic SHA-256-only
receipt defined by `liquilens_evidence.trade_safety`.

It is **not** an order gateway. It has no broker credentials, broker preview,
order-submission route, recommendation route, automatic resize behavior, or
real-money authority. Every response carries `X-Trade-Safety-Mode: sandbox`,
`X-Trade-Safety-Authority: read-only`, and
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
| Seiche | `POST https://api.seiche.info/mcp`, tool `funding_stress_now` | `context_only`; only the validated regime, index, coverage, source clock, and exact response hash are projected. |
| Undertow | `POST https://api.seiche.info/undertow/mcp`, tool `exit_cost` | `context_only`; BTC aliases, USD, and exact published rungs `$1k/$10k/$100k/$1m` only. A mismatched nearest rung is unavailable evidence. |
| LiquiLens | `GET https://api.liquilens.in/api/failure-radar/institution/{quoted_slug}` | Called only for a validated `request.order.instrument.identifiers.liquilens_institution_slug`; only latest period end and historical-evidence status/eligibility are projected. Otherwise `not_applicable`. |

Upstream JSON-RPC text content is ignored. The gateway consumes
`structuredContent` only, validates the expected schemas and safety-relevant
fields, and hashes the exact uncompressed response entity bytes. Source,
generated/as-of, knowledge, retrieval, and local-expiry clocks remain separate.
The local expiry is explicitly identified as a gateway clock, not an upstream
claim.

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
- Independent eligible sources are fetched concurrently. A source or contract
  error becomes that product's `unavailable` evidence section, so the protocol
  emits a valid unavailable receipt instead of silently dropping the source.
- The service does not log request bodies, response bodies, secrets, account
  data, or source exceptions. The bundled command disables access logs.
- Every evidence section and the broker-preview reference carries
  `trade_safety_request_hash(request)`. No nearest-rung result is promoted to
  exact-order evidence.

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
  -t liquilens-trade-safety-gateway:0.1.2 .
docker run --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop ALL --security-opt no-new-privileges \
  -p 8080:8080 liquilens-trade-safety-gateway:0.1.2
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

## Focused tests

The tests inject byte-exact fake upstream responses and cover:

- unsupported and invalid sizes never calling Undertow;
- source errors and nearest-rung mismatches failing closed;
- exact response hashing and exact-order request-hash binding;
- paper receipts passing and limiting under operator policy;
- live receipts never passing and carrying a not-applicable broker preview;
- conditional, minimal LiquiLens projection;
- MCP tool inventory/calls, OpenAPI, sandbox headers, strict JSON, and size
  limits.

This directory is an implementation artifact only. Building or testing it does
not deploy or register a public service.
