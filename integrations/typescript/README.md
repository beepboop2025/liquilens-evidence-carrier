# `@liquilens/trade-safety`

This zero-runtime-dependency ESM package verifies LiquiLens Trade Safety v1
receipts and places an authenticated, paper-only gate in front of one submit
callback. It never fetches evidence, submits to a broker by itself, recommends a
trade, sizes an order, or enables live routing.

The authoritative APIs accept `Uint8Array`, not parsed objects. Normal
`JSON.parse()` loses the distinction between JSON integer and float lexemes such
as `1000` and `1000.0`; `liquilens-hash-tree-v1` intentionally preserves that
distinction. Input is decoded as fatal UTF-8, duplicate keys are rejected, and
the raw artifact is capped at 1 MiB.

```js
import {
  InMemoryReceiptConsumer,
  PaperTradeSafetyOrderGateway,
} from "@liquilens/trade-safety";

const gateway = new PaperTradeSafetyOrderGateway(paperBrokerSubmit, {
  binding: operatorApprovedBinding,
  hmacKey: new TextEncoder().encode(process.env.PAPER_RECEIPT_HMAC_KEY),
  receiptConsumer: new InMemoryReceiptConsumer(), // demo only
});

await gateway.submit(requestBytes, receiptBytes);
```

Use an atomic, durable, operator-owned `ReceiptConsumer` in any shared or
multi-process paper environment. The bundled in-memory consumer is bounded and
serializes claims inside one Node process, but cannot coordinate across hosts or
survive a restart.

The gateway owns the evaluation clock, requires HMAC authentication, verifies
the deterministic policy decision and exact request lexemes, pins account,
tenant, operator, agent, runtime, policy, issuer, and HMAC-key identity, then
claims the one-use receipt before invoking the callback. `live` is rejected
unconditionally even if a receipt says `pass`.

Run the committed cross-language corpus without installing packages:

```bash
python scripts/generate_trade_safety_conformance.py --check
node --test integrations/typescript/test/*.test.mjs
```

There is intentionally no `package-lock.json`: this private release-tarball
package has no dependencies, does not run an install/build step, and executes
the committed ESM files directly. Release CI uses a pinned Node action, runs the
tests, and archives the exact `npm pack` output as an attested GitHub asset.
