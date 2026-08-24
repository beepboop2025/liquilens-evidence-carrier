# LiquiLens Evidence Carrier for OpenBB

This standalone OpenBB router extension verifies an already-supplied LiquiLens
Evidence Carrier without fetching market data. It is deliberately not an OpenBB
data-provider extension.

The single command is exposed as `obb.liquilens.verify(data=...)` in the Python
interface and `POST /api/v1/liquilens/verify` through OpenBB's REST interface.
It verifies carrier identity, clocks, rights, and the strict export disposition,
then returns only bounded status metadata. It never returns the carrier payload.

## Install

Use an isolated environment with Python 3.11 or newer and an existing OpenBB
installation. The repository can be installed directly without a package-index
publication:

```bash
python -m pip install \
  "git+https://github.com/beepboop2025/liquilens-evidence-carrier.git@main#subdirectory=integrations/openbb"
openbb-build
```

For a reproducible installation, pin the reviewed extension source snapshot:

```bash
python -m pip install \
  "git+https://github.com/beepboop2025/liquilens-evidence-carrier.git@f4e9d6fb6bb20abbe6fc4625bd8c0f3279b48674#subdirectory=integrations/openbb"
```

The package also pins the LiquiLens verifier dependency to the immutable v0.14.0
GitHub release wheel and its SHA-256 digest.

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

Omit `evaluated_at` to evaluate policy at the current UTC instant. Supplying it
explicitly makes a research notebook or audit replay deterministic.

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
