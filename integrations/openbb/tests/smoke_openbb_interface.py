"""Exercise the generated OpenBB Python interface, not only the raw router."""

import json
from pathlib import Path

from liquilens_evidence.evidence_carrier import issue_evidence_carrier
from openbb import obb
from openbb_core.api.rest_api import app

ROOT = Path(__file__).resolve().parents[3]


def main() -> None:
    descriptor = json.loads((ROOT / "examples" / "descriptor.json").read_text())
    carrier = issue_evidence_carrier(**descriptor)
    result = obb.liquilens.verify(
        data={
            "carrier": carrier,
            "evaluated_at": "2026-08-24T12:00:00Z",
        }
    )
    payload = result.results
    if not payload.ok:
        raise SystemExit(f"OpenBB verification failed: {payload!r}")
    if payload.carrier_id != carrier["carrier_id"]:
        raise SystemExit("OpenBB result returned the wrong carrier identity")
    if payload.data_provider or payload.network_access or payload.telemetry:
        raise SystemExit("OpenBB result widened the integration boundary")
    if payload.authority.can_execute or payload.authority.can_recommend:
        raise SystemExit("OpenBB result widened financial authority")
    paths = [
        route.path for route in app.routes if route.path == "/api/v1/liquilens/verify"
    ]
    if paths != ["/api/v1/liquilens/verify"]:
        raise SystemExit(f"OpenBB REST route was not registered: {paths!r}")

    receipt = json.loads(
        (ROOT / "examples/trade-safety/receipt.paper.pass.json").read_text()
    )
    trade_safety = obb.liquilens.verify_trade_safety(
        data={
            "receipt": receipt,
            "evaluated_at": "2026-09-02T12:00:30Z",
        }
    ).results
    if not trade_safety.ok or trade_safety.can_submit_order:
        raise SystemExit("OpenBB Trade Safety verification widened its boundary")
    if trade_safety.receipt_id != receipt["receipt_id"]:
        raise SystemExit("OpenBB Trade Safety result returned the wrong identity")
    print(
        "obb.liquilens.verify ok: "
        f"{payload.carrier_id} {payload.export_disposition} {paths[0]}"
    )


if __name__ == "__main__":
    main()
