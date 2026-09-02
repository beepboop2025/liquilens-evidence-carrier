# Trade Safety paper-order guard

The Python order guard turns a verified Trade Safety Receipt into a fail-closed
paper-order boundary. It wraps a broker SDK, OMS client, agent tool, or job
runner without reproducing Seiche, Undertow, or LiquiLens calculations.

```python
from liquilens_evidence import (
    InMemoryReceiptConsumer,
    PaperTradeSafetyOrderGateway,
    TradeSafetyExecutionBinding,
    TradeSafetyOrderAuthorization,
    trade_safety_policy_hash,
)


APPROVED_POLICY_SHA256 = trade_safety_policy_hash(operator_approved_policy)


binding = TradeSafetyExecutionBinding(
    account_id="paper-account-01",
    tenant_id="tenant-01",
    operator_id="operator-01",
    agent_id="copilot-01",
    runtime="operator-sidecar/1.0",
    strategy_id="strategy-01",
    policy_id="paper-default",
    policy_version="1.0.0",
    policy_hash=APPROVED_POLICY_SHA256,
    issuer_name="tenant-trade-safety-gateway",
    issuer_version="1.0.0",
    issuer_endpoint="https://tenant.example/trade-safety",
    hmac_key_id="tenant-paper-key-v1",
)


def private_paper_submit(auth: TradeSafetyOrderAuthorization) -> str:
    # This closure and the broker credential remain in the isolated sidecar.
    return paper_broker.submit(
        account_id=auth.binding.account_id,
        client_order_id=auth.request["request_id"],
        order=auth.order,
    )


guarded_broker = PaperTradeSafetyOrderGateway(
    private_paper_submit,
    binding=binding,
    receipt_consumer=InMemoryReceiptConsumer(),  # local paper/demo use
    hmac_key=TENANT_HMAC_KEY,  # loaded inside the isolated sidecar
)

order_id = guarded_broker.submit(
    exact_trade_safety_request,
    exact_trade_safety_receipt,
)
```

## What the boundary enforces

- The operator-side gateway owns the UTC clock. The agent-facing `submit` method
  cannot supply or backdate evaluation time.
- The full proposed request and canonical hash must match the receipt.
- Account, tenant, operator, agent, runtime, strategy, exact policy hash, issuer,
  and HMAC key identity must match immutable operator configuration.
- The agent-facing gateway requires an authenticated HMAC receipt; hash-only
  receipts can be checked offline but cannot reach its broker callback.
- Only an enforced `pass` may reach the protected paper submit callable.
- `limit`, `hold`, `unavailable`, missing, malformed, future, expired, replayed,
  and mismatched receipts stop before broker code runs.
- Live and observation modes are rejected unconditionally.
- Sync and async gateways use separate native consumer interfaces so durable
  async I/O is never forced onto the event loop as a synchronous call.

`before_order(..., evaluated_at=...)` remains available only as a low-level
deterministic hook for trusted adapters, conformance tests, and audit replay. Do
not expose it as an agent tool. Agent-facing code should expose only the gateway
method with its operator-owned clock.

## Replay and uncertain broker outcomes

The bundled consumers are bounded local paper/demo stores. A production paper
adapter should use an operator-owned atomic store. Claiming a receipt before the
broker call guarantees that this gateway does not invoke its submit callback
twice with that receipt; it does **not** prove that a broker accepted exactly one
order. Timeouts and crashes can leave execution state uncertain. Use stable
client-order IDs and reconcile with the broker before any resubmission.

Live routing remains held until the adapter contract includes all of the
following:

- a durable `claim -> submitted | uncertain -> reconciled` state machine;
- a stable broker idempotency/client-order key bound into the signed request;
- broker-side lookup and reconciliation after timeout or process failure;
- process and credential isolation from arbitrary agent code;
- reviewed key rotation, recovery, rollback, and owner-authorized activation.

Python's private attributes and seals are programming invariants, not a sandbox.
The agent process must not possess or introspect the sidecar's HMAC key, raw
broker client, submit callback, or credential. MCP and FDC3 improve discovery;
they do not replace this isolation or enforce the order path by themselves.
