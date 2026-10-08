# Private paper execution tools

`AlpacaPaperAgentTools` connects a customer's agent runtime to the existing
receipt guard and durable Alpaca paper adapter. It is a Python dispatcher with
MCP `tools/list` and `tools/call` result shapes for an authenticated host. It
does not start a server or alter the public evidence MCP.

```text
Any agent -> exact proposed request -> operator evidence/policy service
                  |                          |
                  |                  authenticated pass receipt
                  +--------------------------+
                                             |
                            private paper execution tools
                                             |
                             existing guard + durable journal
                                             |
                                   Alpaca paper account
```

The evidence service can compose Seiche funding conditions, Undertow exit
liquidity and applicable LiquiLens institution context. Each retains its own
clocks, rights and missingness. A public hash-only receipt cannot authorize
execution. The private issuer must enforce the operator's policy, account
exposure/open-order checks and instrument scope before issuing a receipt. The
[copilot](../trading-copilot/README.md) supplies existing operator-owned native
and scoped paper profiles; neither is a live-money issuer.

## Run the offline walkthrough

From the complete reviewed checkout:

```sh
uv run --project integrations/alpaca-paper --locked \
  python examples/paper_agent_tools.py --scratch-dir /absolute/scratch/demo
uv run --project integrations/alpaca-paper --locked \
  python examples/paper_agent_tools.py --scratch-dir /absolute/scratch/demo --timeout
```

Both use explicitly synthetic evidence, a fixed test clock and an in-process
broker double. They make no network requests. Each demonstrates discovery,
submission, an identical repeat, status and reconciliation. The timeout run
produces one simulated submission followed by a lookup. A temporary SQLite
journal under `--scratch-dir` is removed when the walkthrough finishes. These
outputs are verification traffic, not adoption, actual paper orders or fills.

## Register an operator-owned lane

Inside the customer's private service, construct the lane from trusted
configuration. This fragment assumes the operator supplies `binding`,
`operator_hmac_key`, `paper_api_key`, `paper_secret`, `state_dir` and the
`operator_enabled()` callback. None of those values is a tool input.

```python
from datetime import UTC, datetime
from liquilens_alpaca_paper import (
    AlpacaPaperAgentTools,
    PaperAgentToolProtocolError,
    SQLiteAlpacaPaperSubmissionJournal,
)

journal = SQLiteAlpacaPaperSubmissionJournal(state_dir / "submissions.sqlite3")
lane = AlpacaPaperAgentTools(
    binding=binding,
    submission_journal=journal,
    hmac_key=operator_hmac_key,
    api_key=paper_api_key,
    secret_key=paper_secret,
    clock=lambda: datetime.now(UTC),
    execution_enabled=lambda: (
        operator_enabled()
        and not (state_dir / "STOP").exists()
        and not (state_dir / "STOP").is_symlink()
    ),
)

# After authenticating and routing to this principal's fixed lane:
tool_catalog = lane.list_tools()
try:
    result = lane.call_tool(tool_name, decoded_arguments)
except PaperAgentToolProtocolError:
    # The host maps this to JSON-RPC -32602 with a fixed, non-secret message.
    raise
# Close the caller-owned journal on shutdown, not after each call.
```

The default callback disables submissions. A truthy string or callback failure
also disables them. It is checked again after blocking account I/O and receipt
claim, immediately before the adapter starts submission. STOP cannot recall an
order already in flight.

| Tool | Inputs | Effect |
| --- | --- | --- |
| `paper_execution_capabilities` | none | Describe the lane; no account read or order permission |
| `submit_paper_order` | exact `request`, authenticated `receipt` | Existing guard, account check and durable single attempt |
| `paper_order_status` | exact `request` | Local submission record; no current fill assertion |
| `reconcile_paper_order` | exact `request` | Broker lookup if needed and local journal update; never submission |

Inputs use the existing [request](../../protocol/liquilens-trade-safety-request-v1.schema.json)
and [receipt](../../protocol/liquilens-trade-safety-receipt-v1.schema.json) contracts.
Canonical runtime validation applies even if the caller ignores tool schemas.
Unknown tools/argument shapes raise `PaperAgentToolProtocolError`; execution
failures return `isError: true`. Text and `structuredContent` carry the same
bounded result.

Results include the `2026-07-28` completion metadata and a private, zero-TTL tool
catalog. The host owns version negotiation and any older-client adaptation; it
also owns the transport lifecycle and authorization, not this dispatcher.

## Host responsibilities and limits

- Authenticate the caller, authorize each tool, then select the fixed
  account/tenant/agent/runtime/strategy/policy lane. Never select it from an
  untrusted account ID alone. Read and reconciliation enforce the same binding.
- Keep the host, issuer, keys and broker client outside the untrusted agent's
  process and filesystem permissions. A Python object or tool annotation does
  not isolate credentials from arbitrary code in the same process.
- Reject duplicate JSON keys at transport decoding. Apply body/rate limits,
  deadlines and operator audit retention in the host. The dispatcher also
  detaches and bounds arguments to 2 MiB.
- Use one serialized service owner for each managed account and retain its
  journal across restarts. Concurrent calls to one instance fail busy. SQLite
  prevents exact receipt/request replay across instances; this interface does
  not provide a distributed account exposure lock. Do not run competing order
  services or use a new request ID to retry an uncertain order.
- The issuer owns portfolio, daily loss, cash, position, pending-order and
  permission checks. This interface adds no strategy and must not replace the
  copilot's additional account controls.
- A timeout stays uncertain. Reconcile while disabled if needed. Failed lookup,
  including not found, does not prove absence. Existing uncertainty blocks fresh
  submissions. Repeating a recorded request returns `already_recorded`, even
  after expiry/restart; this is historical information, not authorization.
- `fill_status` remains `not_observed`. Use a separately validated observation
  service for fills. No raw broker response or exception body goes to an agent.

The tool follows MCP's [structured-result and error conventions](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).
The adapter's conservative timeout behavior is consistent with
[Alpaca's order guidance](https://docs.alpaca.markets/us/docs/working-with-orders).
Transport conformance, authenticated hosting, real-account activation and live
trading remain separate work; this dispatcher proves none of those by itself.
