# Trade Safety traction measurement

## Scope

The gateway has a small, first-party telemetry primitive for answering whether
agents progress through a Trade Safety assessment and the x402 access funnel.
It is disabled unless an operator explicitly injects a sink. It adds no Google
Analytics, cookies, browser fingerprinting, or outbound analytics dependency.

This is operational adoption telemetry, not financial evidence and not an
authorization control. It must never influence an assessment outcome, payment
decision, receipt, or broker action.

## Privacy boundary

Each record contains only:

- schema and finite event name;
- UTC observation time rounded to a whole second;
- gateway service version and source revision;
- `rest`, `mcp`, or `x402` transport;
- a coarse duration bucket; and
- the event's finite, low-cardinality properties.

The emitter cannot accept a raw request, order, account, tenant or agent ID, IP
address, wallet or payment payload, transaction hash, URL, evidence, exception
text, prompt, bearer token, institution, instrument, or free-form reason. Unknown
event names, properties and values are rejected before serialization. There is
no session, device, or caller ID in the default v1 stream. The optional v2
installation extension below stores only a keyed hash of a caller-supplied
random UUID, never the raw UUID or an IP address.

## Event contract

| Event | Required properties | Meaning |
|---|---|---|
| `assessment_accepted` | none | A syntactically and semantically admitted assessment reached evaluation. |
| `assessment_rejected` | `reason` | Admission stopped before an outcome. |
| `assessment_outcome` | `outcome` | An admitted assessment produced `pass`, `limit`, `hold`, or `unavailable`. |
| `mcp_activation` | `operation`, `outcome` | An MCP transport rejection or initialize/list/named gateway tool operation completed. |
| `x402_offered` | none | The gateway constructed and returned a priced-access offer to ASGI. |
| `x402_verify_failed` | `reason` | Payment proof verification did not pass. |
| `x402_settle_failed` | `reason` | Verification passed but settlement did not complete safely. |
| `x402_settled` | none | The settlement adapter reported success. |
| `x402_release_failed` | `delivery`, `reason` | Settlement may be complete, but safe delivery failed. |
| `x402_released` | `delivery`, `outcome` | The gateway handed a settled assessment response to ASGI; `delivery` distinguishes `initial` from `replay`. |

Allowed assessment rejection reasons are `policy_not_admitted`,
`invalid_request`, and `internal_error`. Verification and settlement use only
the applicable subset of `payment_missing`, `payment_malformed`,
`offer_mismatch`, `facilitator_unavailable`, `payment_rejected`,
`settlement_failed`, `settlement_uncertain`, `replay_in_progress`,
`authorization_retired`, `capacity_exhausted`, and `internal_error`. Release
failure uses `response_expired`, `response_invalid`, `response_retired`, or
`response_too_large`. Provider strings and exceptions must be mapped to these
codes, never copied.

An x402 integration should preserve this order:

```text
x402_offered
   -> x402_verify_failed
   -> assessment_outcome -> x402_settle_failed
                         -> x402_settled -> x402_release_failed
                                        -> x402_released(initial)
   -> prior x402_settled ----------------> x402_released(replay)
```

The failure branches are alternatives; they are not a promise that every retry
can be joined to its earlier offer. Without a correlation ID, counts are
aggregate transitions within a reporting window.

## Enabling a local sink

The application factory supports an explicit local path or an explicit stdout
sink. With both settings absent, construction is a null configuration and no
path is opened or written:

```python
telemetry = telemetry_from_env(SERVICE_VERSION, SERVICE_REVISION)
assert not telemetry.enabled
```

An operator opts in with an absolute local path:

```sh
export TRADE_SAFETY_TELEMETRY_PATH=/var/lib/liquilens/traction.jsonl
```

The directory must already exist and should be owned by the gateway service.
An empty value, relative path, missing parent, symlink, non-regular existing
target, wrong owner, or group/world-accessible existing target fails startup;
it does not silently disable telemetry. Alternatively,
`TRADE_SAFETY_TELEMETRY_STDOUT=1` emits bounded records to private provider logs,
prefixed with `TRADE_SAFETY_TRACTION `. Configuring both sinks is an error.
No `*_ENABLED`, OpenTelemetry, or GA setting activates either sink.

The sink creates and opens the file with mode `0600` during startup, rejects
symlinks, hard links, read-only targets, and insecure existing files, caps each
line at 2,048 bytes, and performs one `O_APPEND` write per JSON record. Eager
opening proves that an opted-in destination is writable before traffic arrives.
A later sink error returns `False` and cannot change the gateway response. Tests
should inject `InMemoryTelemetrySink`; production code must not select it
automatically. Event schema validation is identical in enabled and disabled
modes, so enabling the sink cannot reveal a previously silent invalid event.

`GET /healthz`, `GET /v1/capabilities`, and the MCP capabilities tool expose
only `telemetry.state` (`disabled`, `ready`, or `degraded`) and the monotonic
`delivery_failures` count. They never expose the configured path or exception
text. A runtime append failure changes `ready` to `degraded`; it does not change
an assessment, payment, or receipt.

The sink does not upload, rotate, aggregate, or delete data. Operators must use
local rotation and deletion with a documented retention period. Start with at
most 30 days of raw JSONL, keep only non-identifying daily aggregates after
that, and shorten retention when the operational question can be answered with
less. The per-line cap is not a disk-usage cap. Alert on sink write failures and
filesystem growth outside the request path.

## Measurement ladder

Report each layer separately. Never collapse all activity into “users,”
“customers,” or “traction.”

| Layer | Current evidence | What may be reported | What must not be inferred |
|---|---|---|---|
| Reach | Registry/download/site/CDN data outside this emitter | Clearly sourced impressions, downloads, or catalog retrievals | Agent activation, human readership, or demand |
| Activation | `mcp_activation`, `assessment_accepted` | Aggregate successful operations and admitted assessments | Unique agents or integrations |
| Repeat integration | Repeated aggregate activations across comparable windows | Call volume recurrence, labelled non-unique | D1/D7 retention, a returning agent, or a distinct installed integration |
| Settlement | `x402_settled` plus independent facilitator reconciliation | Settled event count and reconciliation status | Unique payer, revenue, finality, or absence of refunds/chargebacks |
| Released assessment | `x402_released` with `delivery=initial` | Initial settled assessment responses handed to ASGI, by safe outcome | Network receipt, replay, fill, recommendation, protected order, or satisfied customer |
| Replay | `x402_released` with `delivery=replay` | Cached response replays handed to ASGI | Network receipt, a second settlement, unique agent, or additional revenue |
| Protected-order use | Not emitted by this read-only sandbox gateway | `0`/not implemented, until a broker-adjacent enforcement receipt is independently observed | Mandatory broker adoption or real-money protection |
| Payers | Not identifiable in this telemetry | Independent, privacy-reviewed aggregate payer count if the payment system can produce it | Treating settlements, wallets, or calls as unique paying customers |

### Weekly gateway scorecard

Use one fixed UTC window and show counts before ratios:

- admitted assessments: `assessment_accepted`;
- rejected assessments and reason mix: `assessment_rejected`;
- admission rate: accepted / (accepted + rejected);
- policy outcome mix: `assessment_outcome` by outcome;
- MCP operation success rate: successful / all `mcp_activation` events;
- x402 offer-to-settlement rate: settled / offered;
- verification and settlement failures by finite reason;
- initial-response handoff integrity: initial releases / settled, with release failures
  and the raw difference shown;
- replay volume, reported separately from initial release and settlement; and
- initial released outcome mix, reported separately from all assessment outcomes.

Ratios are only directional because offers and failures cannot be joined.
Initial response releases greater than settlements, or a sustained
settled-minus-initial-release gap not explained by `x402_release_failed`, is an
operational investigation signal rather than a growth result. Replay volume is
expected and must never be added to settlement or revenue counts. Compare
service version and source revision before attributing a change to product
behavior.

## Optional installation cohorts (v2)

For repeat-installation measurement, an operator may configure
`TRADE_SAFETY_TELEMETRY_IDENTITY_KEY` with a securely generated 32-byte secret
encoded as 64 lowercase hexadecimal characters. Keep it in the deployment's
secret store and stable across the intended reporting interval. It requires an
enabled sink. The key never appears in telemetry or public capabilities.

An integration may persist one random UUIDv4 locally and send it in
`X-Liquilens-Client-Id`. The header is optional; every free route works without
it. Do not use a user, account, email, wallet or institution identifier. The
gateway stores a domain-separated HMAC of the UUID. Duplicate or malformed ID
headers are ignored. A shared UUID represents a shared installation; a new UUID
looks like a new installation. These are **unverified installation counts**,
not people or authenticated customers.

Mark all operator QA with `X-Liquilens-Traffic-Class: synthetic`. Known monitor
user-agent names are classified as `automation`; all other requests remain
`unattributed`. Headers are self-declared, so this filter cannot prove that the
remaining traffic is external or human. No raw user agent or IP is stored.

V2 adds a server-generated event UUID for duplicate log-export removal, an
identity-key epoch, the installation hash (or null), and the finite traffic
class. Context is reset after each ASGI request, including errors.

To aggregate a private Railway JSONL export or raw prefixed telemetry lines:

```sh
python -m trade_safety_gateway.traction_report \
  --start 2026-09-05T00:00:00Z \
  --end 2026-09-13T00:00:00Z \
  --coverage-start 2026-09-05T00:00:00Z < gateway-private-logs.jsonl
```

Export the full declared coverage interval. Provider limits and retention can
truncate an export; the report labels coverage as operator-declared rather
than proven complete. It does not fill missing time with zeros. Mixed key
epochs, conflicting event duplicates and malformed telemetry refuse a report.

Only completed `pass`, `limit` and `hold` assessment outcomes count as useful
assessments. `unavailable`, MCP initialization, listing and capability checks
are separate; MCP wrapper events never double-count an assessment. Synthetic
and known automation events are excluded. Anonymous outcomes count as calls
but cannot produce installation retention.

D1/D7 report returned/eligible installations first observed in the reporting
window, using full UTC calendar days. Prior observed installations and the
first partial coverage day cannot enter a new cohort. A return day must finish
before its cohort becomes eligible. These are first-observed cohorts within
available logs, not lifetime acquisition or a guarantee of external usage.

## What this implementation cannot measure

It cannot prove network receipt by a caller or determine unique agents,
organizations, tenants, wallets, payers,
sessions, person-level cohorts or D1/D7 retention, attribution source, revenue, settlement
amount, protected-order volume, prevented loss, broker coverage, or whether an
assessment affected a later order. It also cannot prove that an event came from
the public edge rather than an authorized internal probe.

Those omissions are intentional. Reach needs source-specific distribution data;
unique payer and revenue claims need independent payment reconciliation;
protected-order claims need a fresh receipt enforced on the broker-adjacent
order path. None should be reconstructed by adding identity or payment payloads
to this telemetry stream. The v2 installation report does not change these
boundaries. It reports unknown people, payers and revenue as null, not zero.
