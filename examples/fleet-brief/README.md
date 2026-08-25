# Fleet Brief v1 conformance examples

These fixtures are deterministic and synthetic. They demonstrate native
carrier ownership and all five brief states without network access or any
cross-product score.

- `liquilens.carrier.json` is fully disclosable.
- `seiche.carrier.json` produces `metadata_only`.
- `undertow.carrier.json` carries an explicit unavailable claim.
- `palimpsest.carrier.json` has blocked rights and therefore produces a
  `rejected` identity-only section.
- `mixed-states.fleet-brief.json` contains those four outcomes.
- `missing-states.fleet-brief.json` supplies only LiquiLens and records the
  other three products as missing.

Regenerate the JSON with:

```bash
uv run --locked --extra dev --python 3.13 \
  python scripts/generate_fleet_brief_examples.py
```

Verify either brief at its fixed evaluation clock:

```bash
liquilens-evidence verify-brief \
  examples/fleet-brief/mixed-states.fleet-brief.json \
  --as-of 2026-08-25T00:00:00Z
```

The rejected Palimpsest section is the negative disclosure fixture: it contains
only product name, state, native carrier ID/hash, and `rights_blocked`. It does
not reproduce the source metadata or payload from the supplied native carrier.
