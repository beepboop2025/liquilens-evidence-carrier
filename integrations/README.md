# Evidence Carrier integration kit

The adapters in `liquilens_evidence.evidence_carrier` are the implementation. This
directory contains machine contracts and downstream gates that can be adopted
without a negotiated marketplace listing:

- `fdc3/` validates the namespaced financial-desktop context;
- `openlineage/` validates the custom lineage facet; and
- `dbt/` rejects warehouse rows that strip clocks, rights, or redaction state.

FDC3 App Directory registration, a Bloomberg/LSEG application listing, a
certified Power BI connector, an Excel AppSource listing, and public cloud-data
marketplace listings remain separate deployment and provider-review actions.
The kit does not label those gated surfaces as live.
