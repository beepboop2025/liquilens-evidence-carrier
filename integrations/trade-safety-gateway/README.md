# LiquiLens Trade Safety Gateway

This integration is a read-only sandbox adapter for AI trading copilots. It
accepts one exact Trade Safety Request and one operator policy, reads a small set
of fixed public evidence endpoints, and issues the deterministic SHA-256-only
receipt defined by `liquilens_evidence.trade_safety`.

It is **not** an order gateway. It has no broker credentials, broker preview,
order-submission route, recommendation route, automatic resize behavior,
custody, trade settlement, or real-money authority. An optional x402 route can
settle a payment for access to the same assessment; payment cannot alter the
policy, evidence, outcome, receipt authority, or order path. Every response carries
`X-Trade-Safety-Mode: sandbox`, `X-Trade-Safety-Authority: read-only`, and
`X-Trade-Safety-Execution: disabled`. Live-mode assessments deterministically
return `unavailable` because the evidence is context-only, the receipt is not
tenant-authenticated, and the broker-preview reference is `not_applicable`.

## HTTP and MCP surface

- `GET /healthz` reports process health without probing or masking source state.
- `GET /v1/capabilities` describes static limits, sources, and authority.

Anonymous POST requests share a budget within each gateway worker: 16 requests
in flight, 20 new requests per second, a burst of 40, and a 20-second total
deadline covering body upload and processing. Rate exhaustion returns HTTP 429;
occupied capacity or an expired deadline returns HTTP 503. These responses carry
`Retry-After: 1`, `Cache-Control: no-store`, and the usual read-only headers.
MCP errors retain the JSON-RPC envelope. Retry with backoff and jitter; paid
requests, when separately enabled, must also follow the settlement journal's
reconciliation rules.

Health and discovery GET requests remain available when the POST budget is full.
No IP address or installation identity is stored by this control, and changing
forwarding, installation, or monitoring headers cannot bypass it. The limits are
per worker, not a fleet-wide quota or a substitute for edge network protection.
Operators can lower or raise the bounded budget with
`TRADE_SAFETY_MAX_IN_FLIGHT` (1–64), `TRADE_SAFETY_REQUESTS_PER_SECOND` (1–100), and
`TRADE_SAFETY_REQUEST_BURST` (1–200); invalid values fail startup. The effective
settings are exposed under `limits.admission` in the capability response.
- `POST /v1/check` accepts exactly `{"request": {...}, "policy": {...}}`.
- `POST /v1/x402/check` is registered only under a complete x402 configuration;
  it challenges and settles paid access to an otherwise identical assessment.
- `POST /mcp` supports `initialize`, `ping`, `tools/list`, and `tools/call`.
- MCP exposes only `assess_trade_safety` and `trade_safety_capabilities`.
- MCP negotiates initialization-based `2025-11-25` and stateless
  `2026-07-28`; the latter exposes `server/discover` metadata and requires
  matching `MCP-Protocol-Version`, `Mcp-Method`, and applicable `Mcp-Name`
  headers. Invalid versions or header/body mismatches return HTTP 400.
- After legacy initialization, every subsequent legacy POST must carry
  `MCP-Protocol-Version: 2025-11-25`; accepted notifications return HTTP 202
  without a JSON-RPC body.
- Browser-originated MCP requests accept only the canonical HTTPS issuer
  origin; non-browser clients may omit `Origin`, while any other origin returns
  HTTP 403. Accepted legacy `notifications/initialized` posts return HTTP 202
  with no body.
- FastAPI publishes generated OpenAPI at `/openapi.json` and Swagger UI at
  `/docs`.

There is deliberately no execution-shaped MCP tool.

## Server-owned policy admission

The protocol accepts operator-authored policies, but the public gateway does not
let an anonymous caller redefine its safety floor. Before any source request or
x402 verification, it admits the supplied policy against a server-owned envelope:

- Seiche and Undertow remain mandatory and `STRESS` remains a held regime;
- source-age ceilings cannot exceed 8 days for Seiche or 1 day for Undertow and
  LiquiLens;
- notional, exit-cost, and venue-spread limits must be present and cannot exceed
  USD 100,000, 50 bps, and 20 bps respectively;
- missing evidence remains fail-closed, live still requires an executable quote
  and broker preview, and automatic resize remains forbidden; and
- an operator may tighten any limit or admit only exact canonical policy hashes.

Recognized `TRADE_SAFETY_POLICY_*` settings are documented by
`trade_safety_gateway.policy_guard`. Unknown, malformed, or looser settings fail
startup. `GET /v1/capabilities` publishes the active non-secret envelope.

| Setting | Format and tighten-only meaning |
|---|---|
| `TRADE_SAFETY_POLICY_REQUIRED_PRODUCTS` | Unique comma-separated subset of `seiche,undertow,liquilens`; it must include Seiche and Undertow |
| `TRADE_SAFETY_POLICY_HOLD_REGIMES` | Unique comma-separated subset of `CALM,EROSION,STRAIN,STRESS`; it must include `STRESS` |
| `TRADE_SAFETY_POLICY_MAX_SEICHE_AGE_SECONDS` | Positive integer no greater than `691200` |
| `TRADE_SAFETY_POLICY_MAX_UNDERTOW_AGE_SECONDS` | Positive integer no greater than `86400` |
| `TRADE_SAFETY_POLICY_MAX_LIQUILENS_AGE_SECONDS` | Positive integer no greater than `86400` |
| `TRADE_SAFETY_POLICY_MAX_NOTIONAL_USD` | Positive finite number no greater than `100000` |
| `TRADE_SAFETY_POLICY_MAX_EXIT_COST_BPS` | Positive finite number no greater than `50` |
| `TRADE_SAFETY_POLICY_MAX_VENUE_SPREAD_BPS` | Positive finite number no greater than `20` |
| `TRADE_SAFETY_POLICY_SHA256_ALLOWLIST` | Unique comma-separated lowercase canonical policy SHA-256 values |

## x402 access and agent discovery

x402 is disabled when every `TRADE_SAFETY_X402_*` setting is absent. A complete
configuration adds the paid route and publishes an exact x402 v2 offer with
HTTP Bazaar discovery metadata. The offer binds the canonical request body,
resource, price, asset, payee, ResourceInfo, and required extensions with a
dedicated quote key. The runtime verifies before assessment, settles only after
the protected response exists, and releases only the exact journaled bytes.

Payment identity and transaction provenance stay in the private settlement
journal, never in the Trade Safety receipt or adoption telemetry. A settled
payment does not turn context-only evidence into an executable quote and does
not make a safety result fresh forever. See
[`TRADE-SAFETY-X402.md`](https://github.com/beepboop2025/liquilens-evidence-carrier/blob/main/docs/TRADE-SAFETY-X402.md) for configuration,
the local `liquilens-trade-safety-x402-reconcile` operator command, state
recovery, and activation gates. The command never calls the facilitator;
operators must independently establish settled or definitely-unsettled state
before changing an uncertain journal entry.

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
- The caller policy is admitted against the immutable server safety floor before
  any outbound call or payment challenge/verification.
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
- Requests carrying both notional and a non-null quantity do not reach Undertow
  until a broker-normalized economic-order digest and reference-price contract
  can prove both fields describe the same exposure.

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

The server listens on `0.0.0.0:8080`. Free sandbox mode accepts no API key or
broker secret. Enabling x402 requires its dedicated quote-binding secret but
still accepts no wallet private key or broker credential. Adoption telemetry is
also off by default; an operator can explicitly select a private append-only
JSONL path as described in
[`TRADE-SAFETY-TRACTION.md`](https://github.com/beepboop2025/liquilens-evidence-carrier/blob/main/docs/TRADE-SAFETY-TRACTION.md).

## Container build

Build from the evidence-carrier repository root so the image contains the exact
root Trade Safety implementation:

```bash
docker build -f integrations/trade-safety-gateway/Dockerfile \
  --build-arg SOURCE_REVISION="$(git rev-parse HEAD)" \
  --build-arg CREATED="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --build-arg ISSUER_ENDPOINT=https://your-sandbox.example/v1/check \
  -t liquilens-trade-safety-gateway:0.2.2 .
docker run --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop ALL --security-opt no-new-privileges \
  -p 8080:8080 liquilens-trade-safety-gateway:0.2.2
```

The Dockerfile pins its base image by multi-platform digest, installs from the
committed gateway lock, uses a non-root numeric user, and exposes source/build
identity through health and capabilities. Production deployment should also
apply egress policy allowing only `api.seiche.info:443` and
`api.liquilens.in:443`; application allowlisting is not a substitute for a
network policy. Enabling x402 additionally requires egress to the one configured
facilitator and a private persistent volume for its journal. With x402 variables
absent, a read-only container runs only the free routes; with those variables
present but no writable private journal volume, startup fails. A public edge
must additionally apply a bounded request quota; the application does not trust
client-supplied IP headers as a rate-limit key.

The release workflow publishes an attested multi-architecture OCI package to
GHCR. That registry artifact is distribution, not a hosted deployment or an
order-authorizing service; it retains the same read-only, hash-only sandbox
boundary and must be operated by the adopter. The independent gateway-tag lane
publishes OCI only; it builds a wheel as validation evidence but does not publish
that wheel, an sdist, or a GitHub Release.

Gateway `0.2.2` has an independent release identity so publishing it does not
move or reuse the immutable core `v0.19.0` tag. After the release commit has
landed on `origin/main`, an allowlisted release owner may create and push the
annotated, signed tag `trade-safety-gateway-v0.2.2`. The workflow rejects a
lightweight or unsigned tag, an unsigned target commit, a target outside
`origin/main`, a gateway/tag version mismatch, or any core `VERSION` other than
`0.19.0`. It also rejects pre-existing gateway version, source-commit, or signed
tag-object OCI tags before publishing. The gateway-only lane emits no floating
`latest` or `core-*` alias; its multi-architecture image retains maximum build
provenance, an SBOM, a separate GitHub provenance attestation, and the signed
Git tag object in its OCI metadata. Conversely, a core `v*` release may update
only `core-*`, `core-sha-*`, and `latest`; it cannot move the gateway-owned
semantic version, source-commit, or signed-tag-object aliases.

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
- server-owned policy admission before network and payment work;
- x402 challenge, exact offer/body binding, verification, settlement, durable
  replay/reconciliation, terminal failure transport, cookie-free HTTP,
  age-bounded tombstone retention, stale-response refusal, transfer-method
  truthfulness, configuration rotation, and local operator CLI input/output
  safety;
- opt-in closed-schema telemetry without request or payment identity; and
- MCP tool inventory/calls, OpenAPI, sandbox headers, strict JSON, and size
  limits.

This directory is an implementation artifact only. Building or testing it does
not deploy or register a public service.

## Optional installation measurement

All public checks and MCP tools work without an account or tracking header.
An integration may opt into repeat-installation measurement by generating one
random UUIDv4, keeping it across runs, and sending it as
`X-Liquilens-Client-Id`. Never send an account, email, wallet or institution ID.
The gateway records only a keyed hash; an installation is not a verified person.

```python
import os
import uuid
from pathlib import Path

identity_path = Path(".liquilens-installation-id")
try:
    descriptor = os.open(identity_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    pass
else:
    with os.fdopen(descriptor, "w") as handle:
        handle.write(str(uuid.uuid4()))
headers = {"X-Liquilens-Client-Id": identity_path.read_text().strip()}
# Add these optional headers to your existing REST or MCP HTTP client.
```

Operator smoke tests must additionally send
`X-Liquilens-Traffic-Class: synthetic`. See
[the measurement contract](../../docs/TRADE-SAFETY-TRACTION.md) for private
stdout configuration, cohort limits and the scorecard command.
