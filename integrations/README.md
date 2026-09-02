# Evidence Carrier integration kit

The adapters in `liquilens_evidence.evidence_carrier` are the implementation. This
directory contains machine contracts and downstream gates that can be adopted
without a negotiated marketplace listing:

- `fdc3/` validates the namespaced financial-desktop context;
- `typescript/` provides a dependency-free raw-UTF8 Trade Safety verifier,
  TypeScript declarations, and authenticated paper-only order guard;
- `openbb/` provides offline carrier and hash-only Trade Safety receipt
  verification without accepting authentication secrets;
- `trade-safety-gateway/` implements the fixed-upstream, read-only public
  sandbox; it has no broker credentials or execution route;
- `openlineage/` validates the custom lineage facet;
- `dbt/` rejects warehouse rows that strip clocks, rights, or redaction state;
  and
- `vscode/` packages the browser verifier as offline editor diagnostics for
  desktop, remote, virtual, and web workspaces.

FDC3 App Directory registration, a Bloomberg/LSEG application listing, a
certified Power BI connector, an Excel AppSource listing, and public cloud-data
marketplace listings remain separate deployment and provider-review actions.
The kit does not label those gated surfaces as live.

The Trade Safety request, policy, broker-preview and receipt schemas live in
`../protocol/`. The FDC3 directory also contains a receipt context and intent
fragments. These are workflow and interoperability contracts, not evidence that
an App Directory, broker, OMS, or runtime has accepted the integration.
