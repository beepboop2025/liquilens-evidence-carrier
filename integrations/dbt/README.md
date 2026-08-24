# LiquiLens Evidence dbt test

This dependency-free generic test rejects warehouse rows that have lost the
carrier/hash shape, inverted the event and knowledge clocks, exposed a payload
after redaction, widened blocked/unknown/restricted rights, or marked a row full
without redistribution permission, a license basis, and attribution.

Add this directory as a Git package or copy the macro into an existing project,
then attach the test to the flattened output of `liquilens-evidence convert
--format flat`:

```yaml
models:
  - name: financial_evidence
    data_tests:
      - liquilens_evidence_contract
```

The SQL gate does not recompute the cross-language record hash. Run
`liquilens-evidence verify` before warehouse loading and retain the raw carrier
for independent replay. Adapter-specific tests are still required for warehouses
that coerce timestamps or empty strings differently.
