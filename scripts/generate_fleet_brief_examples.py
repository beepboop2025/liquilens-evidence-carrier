"""Regenerate deterministic Fleet Brief v1 conformance examples offline."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from liquilens_evidence import issue_evidence_carrier, issue_fleet_brief

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "examples" / "fleet-brief"
EVALUATED_AT = datetime(2026, 8, 25, tzinfo=UTC)
ENDPOINTS = {
    "liquilens": "https://liquilens.in/protocol/",
    "seiche": "https://api.seiche.info/v2/world-markets",
    "undertow": "https://liquilens-undertow.com/evidence",
    "palimpsest": "https://palimpsest.info/evidence",
}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def _carrier(
    descriptor: dict[str, Any],
    product: str,
    *,
    rights_status: str = "licensed",
    claim_status: str = "structural",
) -> dict[str, Any]:
    native = copy.deepcopy(descriptor)
    native["producer"] = {
        "name": product,
        "version": "0.15.0-conformance",
        "endpoint": ENDPOINTS[product],
    }
    native["subject"] = {
        "kind": "protocol_artifact",
        "name": f"{product} native conformance evidence",
        "identifiers": {"fixture": f"{product}-fleet-brief-v1"},
    }
    native["claim"] = {
        "kind": "fleet_brief_conformance",
        "summary": f"Independent {product} conformance evidence",
        "status": claim_status,
    }
    native["sources"][0]["source_id"] = f"{product}:fleet-brief:v1"
    native["sources"][0]["publisher"] = f"{product} conformance fixture"
    native["rights"]["status"] = rights_status
    native["payload"] = {
        "native_product": product,
        "aggregation_permitted": False,
    }
    return issue_evidence_carrier(**native)


def main() -> int:
    descriptor = json.loads((ROOT / "examples" / "descriptor.json").read_text())
    OUTPUT.mkdir(parents=True, exist_ok=True)
    carriers = {
        "liquilens": _carrier(descriptor, "liquilens"),
        "seiche": _carrier(descriptor, "seiche", rights_status="metadata_only"),
        "undertow": _carrier(descriptor, "undertow", claim_status="unavailable"),
        "palimpsest": _carrier(descriptor, "palimpsest", rights_status="blocked"),
    }
    for product, carrier in carriers.items():
        _write_json(OUTPUT / f"{product}.carrier.json", carrier)

    mixed = issue_fleet_brief(carriers=carriers, evaluated_at=EVALUATED_AT)
    missing = issue_fleet_brief(
        carriers={"liquilens": carriers["liquilens"]},
        evaluated_at=EVALUATED_AT,
    )
    _write_json(OUTPUT / "mixed-states.fleet-brief.json", mixed)
    _write_json(OUTPUT / "missing-states.fleet-brief.json", missing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
