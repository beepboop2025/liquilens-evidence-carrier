# Trade Safety golden paper fixture

These files form one synthetic, deterministic conformance vector evaluated at
`2026-09-02T12:00:00Z`. They are not current market evidence.

- `request.paper.json` is one exact paper order.
- `policy.paper.json` requires Seiche and Undertow and fails closed.
- `evidence.paper.json` contains synthetic context-only product sections.
- `broker-preview.paper.json` is explicitly not applicable in paper mode.
- `issuer.paper.json` identifies the fixture issuer.
- `receipt.paper.pass.json` is the expected hash-only paper receipt.

Re-issue and verify it from the repository root:

```bash
liquilens-evidence issue-trade-safety \
  --request examples/trade-safety/request.paper.json \
  --evidence examples/trade-safety/evidence.paper.json \
  --policy examples/trade-safety/policy.paper.json \
  --broker-preview examples/trade-safety/broker-preview.paper.json \
  --issuer examples/trade-safety/issuer.paper.json \
  --as-of 2026-09-02T12:00:00Z

liquilens-evidence verify-trade-safety \
  examples/trade-safety/receipt.paper.pass.json \
  --as-of 2026-09-02T12:00:30Z

node protocol/verify_hash_tree_v1.mjs \
  --artifact trade-safety-receipt \
  examples/trade-safety/receipt.paper.pass.json
```

A paper `pass` means only that this exact synthetic request satisfies this
operator-authored paper policy. The all-false authority object remains binding.
