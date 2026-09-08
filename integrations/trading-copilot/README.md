# LiquiLens trading copilot

An operator-owned, deterministic BTC/USD **paper** trading runner. It combines
Seiche funding observations, LiquiLens corporate research and Undertow exit
context with an exact-order HMAC receipt and the durable Alpaca paper adapter.
Riptide supplies separately audited, display-only defensive research and event
availability. Its native score does not become a BTC signal or order permission.
There is no live-money mode, LLM decision maker or demonstrated profitable
strategy. The public Trade Safety gateway remains unchanged and read-only.

## Try an offline decision first

From a source checkout, Python 3.11 or later can run the demo without installing
dependencies, creating an account or reading credentials:

```sh
PYTHONPATH=integrations/trading-copilot/src \
  python3 -B -m liquilens_trading_copilot.demo --format markdown
```

With the locked copilot environment already prepared, the same feature is also
available through its CLI:

```sh
uv run --project integrations/trading-copilot --locked --no-sync --offline \
  liquilens-trading-copilot demo --format markdown
uv run --project integrations/trading-copilot --locked --no-sync --offline \
  liquilens-trading-copilot demo --scenario stale-bars > synthetic-decision.json
```

Both entry points use the existing `strategy.propose` function with unchanged
defaults and explicitly invented bars, portfolio and funding regime. The fixed
scenario clock is **2000-01-02**, not the current market clock. JSON is the default;
Markdown includes the full JSON, input hash, decision metrics, reason explanations,
countercase and remaining evidence gates. Shell redirection writes only the file
you select. The demo itself performs no network or broker calls and does not read
or write configuration, secrets, accounts or journals.

| `--scenario` | What to inspect |
| --- | --- |
| `candidate` (default) | Synthetic positive momentum produces a $1,000 candidate; execution remains blocked. |
| `reduction` | Negative momentum can reduce a synthetic holding without opening a short. |
| `stale-bars` | Old complete bars cause HOLD; report generation cannot refresh them. |
| `loss-halt` | The daily-loss halt freezes both sides and leaves the synthetic holding open. |
| `funding-stress` | A synthetic STRESS input blocks new exposure. |
| `small-residual` | A holding below the $1,000 order rung remains HOLD without rounding up. |

These are educational decision examples, not historical evaluation, performance
evidence or a production readiness check. Every report has a separate demo schema,
`synthetic=true`, `order_authorized=false` and `receipt_issued=false`. Required
Seiche, LiquiLens, Undertow and operator/broker gates remain **not evaluated**.
No demo result can be submitted as a trading request or authorization receipt.
The CLI rejects operator config, secret-file and state-directory arguments for
`demo`. It does not enable the runner.

For current public research, open [Market Brief](https://beepboop2025.github.io/market-brief/)
separately, inspect its source clocks and gaps, then use its AI research handoff.
Its public brief does not clear trading gates. For the distinct private paper
workflow and source-checkout dependency installation, see
[Private setup and commands](#private-setup-and-commands). The signed core Carrier
wheel alone does not install this copilot integration.

### Export all six examples for a public browser

The standalone entry point can export every existing scenario in one bounded,
deterministic JSON file. It still imports no operator, evidence-service or broker
module. The browser displays these Python-generated reports and does not
implement its own trading strategy.

After committing the reviewed generator changes, run from the source checkout:

```sh
copilot_source_ref="$(git rev-parse HEAD)"
PYTHONPATH=integrations/trading-copilot/src python3 -B \
  -m liquilens_trading_copilot.demo --all --source-ref "$copilot_source_ref" \
  > /tmp/copilot-demo-pack.json
python3 -c 'import hashlib,pathlib; print(hashlib.sha256(pathlib.Path("/tmp/copilot-demo-pack.json").read_bytes()).hexdigest())'
```

`python3 -m liquilens_trading_copilot.demo_pack --source-ref <full-commit-SHA>`
is the equivalent dedicated export entry point with the same `PYTHONPATH`.
`--all` cannot be combined with `--scenario` or Markdown output. Existing
single-scenario commands and the installed operator CLI remain unchanged.

The `liquilens.copilot-demo-pack.v1` envelope contains six complete existing
`liquilens.copilot-offline-demo.v1` reports, their fixed order, unchanged
`StrategyConfig` defaults and explicit false execution flags. Its provenance
includes the actual strategy-file SHA-256, hashes of all synthetic inputs and
generator sources, and a fixed-origin GitHub source link pinned to the supplied
40-character commit. Git blob comparisons must match the actual `strategy.py`,
`demo.py` and `demo_pack.py` bytes. Local Git runs without inherited credentials,
custom configuration, replacement objects or permission to fetch missing blobs.
This verifies local source identity, not a remote publication or signature.

Omitting `--source-ref` produces a local preview with `source_verified=false`
and null source reference/link; it must not be published as a verified pack.
Files modified after the supplied commit cause verification to fail. The source
commit does not depend on the generated artifact: commit code first and keep
the generated pack outside the Carrier repository.

Input and generator hashes use the canonicalization and field scope documented
inside `provenance`. Python and JavaScript can serialize numeric JSON differently,
so a public loader must independently pin and verify the SHA-256 of the complete
file bytes, not recompute those hashes by serializing parsed data in JavaScript.
Require the exact schema, six unique scenario IDs, verified pinned source and
false authorization/receipt/submission flags at both envelope and report levels.
Render an allowlisted view as text and fail closed on malformed or partial packs.

The pack has no current-time stamp and is capped at 256 KiB. Its year-2000 clocks
belong to invented examples. Source, receipt and broker policies remain
unevaluated; the exported strategy defaults do not become order permission.

## Default private profile

`init` selects `liquilens.paper-funding-exit.v1`, policy version `1.0.0`, and
creates a disabled configuration. All three evidence products are required.
This profile has its own source scope and cadence rules:

| Source | Native input and use | Required freshness |
| --- | --- | --- |
| [Seiche money-market desk](https://api.seiche.info/api/money-markets) | `policy_corridor` SOFR, EFFR and IORB rates, plus SOFR−IORB and EFFR−IORB spreads. Validate source identities, observation dates, exact-date joins, no forward fill and basis-point arithmetic. | Each selected observation younger than 8 days; capture clocks checked when present. |
| [LiquiLens corporate transmission](https://api.liquilens.in/api/public-signals/corporate-transmission) | Schema/method v2: current CP spread and weekly rollover. Preserve other channel/leg periods, unavailable channels and historical eligibility flags as background research. | Both CP legs younger than 8 days; stale, withheld or missing current legs block. |
| [Undertow MCP](https://api.seiche.info/undertow/mcp) | `trade_safety_exit_context` for a distinct, exact $1,000 hypothetical BTC/USD SELL scenario associated with the original paper order. Preserve and revalidate its native request, hashes, clocks, rights and authority. | Native observation ceiling 300 seconds and native expiry. |

The private funding pressure is `max(SOFR−IORB, abs(EFFR−IORB))`, in basis
points: **CALM ≤5; EROSION >5–15; STRAIN >15–25; STRESS >25**. These are
experimental operator bands, not Seiche's full-composite regime or calibrated
return forecasts. Native display rounding permits at most 0.1005 bp arithmetic
difference; classification uses the more conservative displayed/recomputed
pressure. Original observation clocks and response hashes remain in the receipt.

LiquiLens CP spread **above 50 bp** triggers a separate strategy HOLD after a
passing evidence assessment. Its corporate state is not relabeled as Seiche's
regime. Quarterly research legs retain their own dates and do not acquire the
current CP freshness claim. The aggregate is not an institution or BTC rating;
the observed board has four of six channels readable. The private eight-day
CP rule does not change the native gateway's one-day institution policy.

The original order must be **BTC/USD, BUY or SELL, $1,000 USD notional, market,
IOC**, with `quantity`, venue and limit/stop prices unset. Undertow always
assesses a separate SELL liquidation scenario, including for a BUY candidate.
Its estimate is neither buy execution cost, a fill nor guaranteed future
liquidity. Arbitrary sizes, quantity-based orders, other assets and live orders
are outside this profile. Its exact policy is pinned; changing its policy fields
requires a separately implemented/versioned profile.

The existing `native_gateway_v1` path remains available through
`init --profile native_gateway_v1` in a separate state directory. It preserves
the full-composite freshness checks and native sell-only contract; optional
institution context belongs to that path. A bare `diagnose` without a config
also uses the native path. Pass the initialized config to diagnose this private
profile.

## Riptide defensive research

The scoped runner also reads Riptide's fixed public `/risk` and `/events`
endpoints. Risk research covers configured public Telegram previews: its
`breach_stress` is a 0–100 keyword-density index, with native bands CALM below
25, EROSION from 25 to below 45, STRAINED from 45 to below 70, and ACUTE from
70. It is not a probability, market-risk score or price forecast. The private
reader requires the original scan to be at most 24 hours old, with complete
positive channel coverage and scanned posts; it rejects stale, partial or
unavailable scans. The HTTP response generation time does not refresh the scan.

Each report preserves observation time, coverage, native run ID and response
hash. The public projection does not expose a verifiable private journal proof
or the exact monitored-channel identities, so these are not claimed. Event
availability and source-policy quarantine are reported independently. A
quarantined empty event list cannot establish no event risk. SPY allocation
weights and Riptide's separately labeled BTC toy strategy are not inputs.

Riptide remains `financial_authority=none` and
`influences_order_decision=false`. Its research is stored separately and linked
to a candidate's request hash in the private audit. The authenticated trade
permission receipt still has exactly three evidence products. Neither a high
nor a low Riptide index changes position weights, clears an evidence refusal,
or supplies permission to submit. Research is also accessible without an
Alpaca account:

```sh
uv run --project integrations/trading-copilot --locked \
  liquilens-trading-copilot research
```

## Strategy and submission controls

| Setting | Default |
| --- | --- |
| Bars | Complete, consecutive hourly BTC/USD bars; minimum 30; newest close at most 7,200 seconds old |
| Momentum | Mean of last 5 closes / mean of last 30 closes − 1; neutral when absolute value <0.003 |
| Volatility target | 20% annualized; population standard deviation of hourly log returns, annualized using 365.25 days |
| Position target | Equity × min(10%, target/observed volatility); EROSION halves it |
| Entry rebalance tolerance | 1% of equity; negative-momentum reductions do not use this tolerance |
| Candidate size | $1,000; minimum $1,000, with no rounding up of smaller cash/holding remainders |
| Attempt limit | Two durable reservations per UTC day, with one reserved for reductions; at most one entry and at most two total attempts |
| Loss halt | Loss ≥2% against broker `last_equity`, its prior-close basis |
| Outstanding orders | Zero allowed |
| Receipt limits | Estimated hypothetical exit cost ≤25 bp; venue spread ≤15 bp; STRAIN/STRESS held |

Positive momentum can buy toward the target; negative momentum can reduce
existing BTC holdings. The runner never shorts or uses leverage. It does not
sell solely because a positive-momentum target shrank. Missing inputs, invalid
bars or zero/invalid volatility produce HOLD. Loss and evidence holds can leave
a paper position open: this is not a stop-loss or automatic liquidation system.

The position target limits entries; it is not a continuously maintained exposure
cap. Price appreciation can move a holding above the target or configured ceiling.
Decision metrics report both conditions without forcing a sale. Negative momentum
can propose a $1,000 reduction even when the holding is below the 1% rebalance
tolerance. Holdings below $1,000 remain HOLD with
`residual_below_minimum_order_notional`; the runner neither rounds up nor creates
a smaller order outside the evidence profile. These policy changes were chosen
to address reproduced behavior, without optimizing historical returns.

`reserved_daily_exit_attempts` defaults to one, including when loading an older
configuration without the field. With the default two total attempts, buy/sell,
sell/buy and sell/sell are permitted sequences; buy/buy is blocked. A failed,
cancelled or uncertain attempt still counts. Setting the total to one while
retaining the default reserve makes the runner exit-only; an explicit zero reserve
opts out of reserving exit capacity. Reservations remain limited to one intent per
account, strategy and completed bar. Cycle records include the UTC daily budget.

Before submission the runner fetches the credential-bound paper account,
checks portfolio limits, obtains required evidence and persists the assessment.
It then rereads the portfolio, recomputes the candidate using the receipt's
funding regime, checks STOP and reserves a durable intent. The private HMAC
receipt binds the original request, identity and policy; the scoped receipt and
native liquidation association are reverified. The Alpaca adapter independently
checks binding, authentication, expiry and replay state before its SDK call.
Receipts last at most 30 seconds and cannot outlive their source/request bounds.

The private audit also retains bounded strategy bars, the strategy configuration
and source-code hash, and the original source receipt when the evidence profile
supplies one. The final account/strategy recheck is recorded before submission.
Receipt capture accepts the explicit receipt contract and rejects credential or
transport-header fields; it does not export the environment or HTTP client.
Failure to persist any of these pre-submission records stops the cycle before
the broker submission call. `observed_at` is the local decision-observation clock,
not a reconstructed source publication clock. Original evidence clocks, request
hashes, expiry and authority remain unchanged. These private records do not prove
that a complete historical evidence chain existed for earlier market bars.

## Private setup and commands

From the repository root:

```sh
uv sync --project integrations/trading-copilot --locked --extra test
uv run --project integrations/trading-copilot --locked \
  liquilens-trading-copilot init --state-dir /absolute/private/operator-state
```

`init` creates mode-0600 `config.json` and `paper.env` inside a mode-0700 directory
owned by the invoking user. It refuses to overwrite either file. Set the actual
paper account ID in `config.json.account_id`. Privately fill these literal keys
in `paper.env`; retain the generated HMAC value across restarts:

```text
ALPACA_PAPER_API_KEY=
ALPACA_PAPER_SECRET_KEY=
COPILOT_PAPER_HMAC_KEY=<generated by init>
```

Do not commit or paste credentials, or source this file as shell code. The parser
accepts only these three keys, requires private file ownership/permissions and
does not evaluate shell syntax. The broker endpoint is fixed to
`https://paper-api.alpaca.markets`; generic/live credential names and a custom
broker host are not accepted. The account must match `account_id`, be active in
USD and have clear trading/block flags.

```sh
uv run --project integrations/trading-copilot --locked \
  liquilens-trading-copilot diagnose \
  --config /absolute/private/operator-state/config.json \
  --env-file /absolute/private/operator-state/paper.env
```

Scoped `diagnose` makes four public GETs, to Seiche, LiquiLens and Riptide. It reports
credential presence, not validity, and does not assess Undertow, read the broker
or authorize an order. Even `pending_order_specific_checks` is not a submission
pass. Once the required evidence and account setup are ready, set
`config.json.enabled=true` to permit a bounded cycle:

```sh
uv run --project integrations/trading-copilot --locked \
  liquilens-trading-copilot once \
  --config /absolute/private/operator-state/config.json \
  --env-file /absolute/private/operator-state/paper.env
```

Replace `once` with `reconcile` to perform uncertain-submission lookups and
observe existing orders without submitting. Reconciliation is available while
execution is disabled. Use `status --config ...` for the private audit state;
its output may include the latest full audit record and should remain private.

```sh
uv run --project integrations/trading-copilot --locked \
  liquilens-trading-copilot stop \
  --config /absolute/private/operator-state/config.json
```

`stop` persistently creates `<state_dir>/STOP`. It is checked before submission
and again after the adapter's blocking account lookup and before its authorized
submission hook. It does not cancel an order already submitted/in flight or
liquidate holdings. Resume requires explicitly removing that file and keeping
`enabled=true`. A stop or error after intent reservation retains that intent;
removing STOP alone does not resolve it. Unresolved reservations continue to
block new exposure and require reconciliation or operator investigation. There
is no automatic resume command. The stop hooks couple to the pinned adapter
version and are covered by the integration pipeline tests.

## Durable state and service layout

Keep one state directory per managed account and use it for every runner
instance. One advisory lock covers a cycle. `audit.sqlite3` stores evidence,
intents and subsequent order observations; `alpaca-submissions.sqlite3` stores
the adapter's durable submission/recovery journal. Back up both consistently,
including any required SQLite WAL state. Do not erase reservations or journal
rows to retry or regain a daily slot.

The `intent_directions` sidecar records buy/sell direction without rewriting the
original four-column `intents` table. Existing reservations without a direction
conservatively consume entry capacity as well as total capacity. Backups must
retain both tables together. The schema stays readable by the older runner, but
that runner does not enforce the new reserved-exit policy; reverting code also
reverts that policy. Do not mix runner versions for a managed account.

A failed or uncertain attempt retains its reservation. Ambiguous submission is
resolved by lookup, never blind resubmission; an unavailable lookup remains
unresolved. Any known nonterminal or unresolved order blocks another submission,
even if the broker's open-order list is temporarily empty. Accepted is distinct
from filled. Later validated broker status,
filled quantity and average price are recorded separately, without rewriting
the original safety receipt. Filled quantities and terminal observations cannot
regress. External/manual account activity can still race the final read; the
broker retains its cash, holdings and order checks.

The supplied `deploy/liquilens-paper-copilot.service` runs as the unprivileged
`liquilens-copilot` user with this deployment layout:

```text
source: /opt/liquilens-trading-copilot/current
binary: /opt/liquilens-trading-copilot/current/integrations/trading-copilot/.venv/bin/liquilens-trading-copilot
state:  /var/lib/liquilens-trading-copilot
```

Prepare the locked environment from the complete reviewed checkout, including
its sibling packages, and initialize state as the service user. The oneshot
unit passes the private config/env-file paths. Its timer runs five minutes after
boot and 15 minutes after the previous service finishes. The config's
`cycle_interval_seconds=900` is descriptive for another scheduler; it does not
change the unit. **The service/timer is not enabled pending prerequisites.**

At the 2026-09-06 validation boundary, current scoped Seiche/CP observations were
usable, but the exact Undertow response remained unavailable with
`rights_manifest_not_approved`. Paper account ID and credentials were also
missing. Keep execution disabled until these actual prerequisites are resolved;
installing the runner or getting healthy endpoints does not clear them. No real
paper account order, fill or profit has been established by the synthetic tests
or the read-only scoped assessment.

## Local verification

```sh
uv run --project integrations/trading-copilot --locked --extra test \
  pytest integrations/trading-copilot/tests
uv run --project integrations/trading-copilot --locked --extra test \
  ruff check integrations/trading-copilot/src integrations/trading-copilot/tests
uv build --project integrations/trading-copilot
```

Tests exercise native-shaped synthetic responses and a mocked Alpaca SDK,
including the actual paper adapter. They do not demonstrate a real broker fill
or strategy performance. This package uses source-tree sibling dependencies;
building its wheel does not publish those dependencies to PyPI. Paper fills,
when observed, remain simulations and do not establish equivalent live results.
