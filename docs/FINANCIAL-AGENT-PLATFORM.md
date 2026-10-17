# Financial agent integration reference

Current source interfaces and their operational boundaries. Source availability
does not establish deployment, account activation or customer adoption.

## Current implementation map

| Capability | Source status | Operating requirements |
| --- | --- | --- |
| Three-product evidence APIs/MCP and portable carrier | Existing contracts/integrations | Current rights, coverage and source/service reliability for the customer's workload |
| Trade Safety gateway; Python/TypeScript guards | Existing; public gateway is read-only | Customer policy and private issuer integration |
| Alpaca paper adapter with durable journal | Existing | Authorized customer installation and observed broker outcomes |
| Deterministic BTC/USD paper copilot | Existing, including additional account controls | Current source admission and authorized account activation |
| Runtime-neutral private agent tools | Implemented using the same guard/journal | Authorized installation and external pilot |
| Authenticated private agent REST host and reference client | Source integration: three-product assessment, fixed account binding, scoped tokens, durable intents, account controls and outcome reconciliation | Release qualification, current source admission, authorized installation and observed recurring use |
| Private MCP execution bridge | Implemented over the authenticated paper host; assessment/status by default, explicit submission exposure | Customer runtime installation and paper-account qualification |
| Customer-owned live Alpaca connector | Separate disabled integration candidate; exact limit orders, account limits, durable reservation, STOP, cancellation and reconciliation | Qualified live receipt issuer, entitled execution-grade sources, verified broker preview, customer account mandate, deployment/recovery qualification and specific activation |

The [agent guide](../integrations/alpaca-paper/AGENT-TOOLS.md) includes an offline
walkthrough. No caller argument selects keys, clocks, broker URLs, enable flags
or alternate policy definitions. Synthetic tests do not prove source admission,
account activation or actual broker fills.

The [private host guide](../integrations/trading-copilot/AGENT-HOST.md) provides
installation, client commands, endpoint contracts and recovery behavior. The
default source profile supports exactly $1,000 BTC/USD paper proposals. Its
development status must stay explicit in API/MCP marketplace copy; public
research endpoints do not acquire private execution capabilities.

The [live connector guide](../integrations/trading-copilot/LIVE-CONNECTOR.md)
describes the separately installed customer-account interface. Its local
preflight does not issue a broker preview or qualify public research evidence
for live trading. Source implementation, broker qualification and activation
remain distinct; no actual live broker calls or fills were produced by this work.
