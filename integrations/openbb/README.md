# LiquiLens Evidence Carrier for OpenBB

This standalone OpenBB router extension verifies an already-supplied LiquiLens
Evidence Carrier without fetching market data. It is deliberately not an OpenBB
data-provider extension.

The extension exposes `obb.liquilens.verify(data=...)` for evidence carriers and
`obb.liquilens.verify_trade_safety(data=...)` for hash-only Trade Safety v1
receipts. Both commands are offline and return bounded status metadata without
returning the supplied carrier, request, policy, or evidence payload.

## Install

Use an isolated environment with Python 3.11 or newer and an existing OpenBB
installation. The repository can be installed directly without a package-index
publication:

```bash
python -m pip install \
  "git+https://github.com/beepboop2025/liquilens-evidence-carrier.git@main#subdirectory=integrations/openbb"
openbb-build
```

For a reproducible installation after the release is published, pin the signed,
repository-protected v0.19.0 source tag. The release controller creates this tag
only from the final reviewed candidate after preflight succeeds:

```bash
python -m pip install \
  "git+https://github.com/beepboop2025/liquilens-evidence-carrier.git@v0.19.0#subdirectory=integrations/openbb"
```

The package pins the LiquiLens verifier dependency to the immutable v0.18.0
GitHub release wheel and SHA-256 digest. That is the first released core with
the authenticated paper-order guard; this OpenBB command remains secret-free
and read-only.

## Python interface

```python
from openbb import obb

result = obb.liquilens.verify(
    data={
        "carrier": carrier_json,
        "evaluated_at": "2026-08-24T12:00:00Z",
    }
)
print(result.results)
```

Verify a hash-only Trade Safety receipt at a required deterministic clock:

```python
result = obb.liquilens.verify_trade_safety(
    data={
        "receipt": receipt_json,
        "evaluated_at": "2026-09-02T12:00:30Z",
    }
)
print(result.results)
```

HMAC receipts fail closed here because sending tenant secrets through a general
analytics router would widen the trust boundary. Authenticate and enforce those
receipts only in the tenant-local Python or TypeScript paper-order guard.

`verify_trade_safety` requires `evaluated_at` so notebook and audit replay is
deterministic. The carrier-only `verify` command may omit its evaluation time
and use the current UTC instant.

OpenBB supplies an already-parsed Python object, so this command is a convenient
offline status verifier, not a lossless raw-UTF8 boundary. Use the packaged
Node verifier on original bytes when duplicate keys, invalid UTF-8, or exact
numeric lexemes must be proven.

## Boundary

- Offline and read-only: the router accepts JSON already supplied by its caller.
- Not a provider: it does not source, normalize, or entitle financial data.
- Fail-closed: malformed or tampered carriers return `ok=false` with an export
  disposition of `reject`; unverified identifiers are not echoed.
- No rights widening: a verified carrier can still be `metadata_only` or
  `reject`, and the router never returns its payload.
- No authority: every response states that execution, recommendation, and
  credit-rating authority are false.
- No telemetry: the extension emits no analytics or outbound requests.

The core protocol and verifier are documented at
<https://liquilens.in/protocol/>. The OpenBB extension architecture is documented
at <https://docs.openbb.co/odp/python/developer/extension_types/router>.
