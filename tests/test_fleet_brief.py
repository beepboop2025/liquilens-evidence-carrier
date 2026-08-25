from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import pytest
from referencing import Registry, Resource

import liquilens_evidence.evidence_carrier as carrier_module
from liquilens_evidence.evidence_carrier import issue_evidence_carrier
from liquilens_evidence.fleet_brief import (
    FLEET_BRIEF_PRODUCTS,
    FLEET_BRIEF_SCHEMA,
    FleetBriefError,
    VerifiedFleetBrief,
    issue_fleet_brief,
    verify_fleet_brief,
)
from liquilens_evidence.protocol_resources import load_protocol_json

ROOT = Path(__file__).resolve().parents[1]
EVALUATED_AT = datetime(2026, 8, 25, tzinfo=UTC)
EVALUATED_AT_TEXT = "2026-08-25T00:00:00Z"
ENDPOINTS = {
    "liquilens": "https://liquilens.in/protocol/",
    "seiche": "https://api.seiche.info/v2/world-markets",
    "undertow": "https://liquilens-undertow.com/evidence",
    "palimpsest": "https://palimpsest.info/evidence",
}


def _carrier(
    product: str,
    *,
    rights_status: str = "licensed",
    claim_status: str = "structural",
    permissions: list[str] | None = None,
) -> dict:
    descriptor = json.loads((ROOT / "examples/descriptor.json").read_text())
    descriptor["producer"] = {
        "name": product,
        "version": "0.15.0-conformance",
        "endpoint": ENDPOINTS[product],
    }
    descriptor["subject"]["name"] = f"{product} native evidence"
    descriptor["subject"]["identifiers"] = {"fixture": f"{product}-carrier"}
    descriptor["claim"]["summary"] = f"Independent {product} conformance evidence"
    descriptor["claim"]["status"] = claim_status
    descriptor["sources"][0]["source_id"] = f"{product}:conformance:v1"
    descriptor["sources"][0]["publisher"] = product
    descriptor["rights"]["status"] = rights_status
    if permissions is not None:
        descriptor["rights"]["permissions"] = permissions
    descriptor["payload"] = {"native_product": product, "score": "not_aggregated"}
    return issue_evidence_carrier(**descriptor)


def _all_full() -> dict[str, dict]:
    return {product: _carrier(product) for product in FLEET_BRIEF_PRODUCTS}


def _schema_validator() -> jsonschema.Draft202012Validator:
    full = load_protocol_json("liquilens-evidence-carrier-v1.schema.json")
    reference = load_protocol_json(
        "liquilens-evidence-carrier-reference-v1.schema.json"
    )
    brief = load_protocol_json("liquilens-fleet-brief-v1.schema.json")
    registry = Registry().with_resource(full["$id"], Resource.from_contents(full))
    registry = registry.with_resource(
        reference["$id"], Resource.from_contents(reference)
    )
    return jsonschema.Draft202012Validator(
        brief,
        registry=registry,
        format_checker=jsonschema.FormatChecker(),
    )


def test_issue_is_deterministic_four_sectioned_and_schema_valid() -> None:
    carriers = _all_full()
    first = issue_fleet_brief(carriers=carriers, evaluated_at=EVALUATED_AT)
    second = issue_fleet_brief(carriers=carriers, evaluated_at=EVALUATED_AT)

    assert first == second
    assert first["schema"] == FLEET_BRIEF_SCHEMA
    assert first["brief_id"].startswith("fleet_brief_")
    assert set(first["sections"]) == set(FLEET_BRIEF_PRODUCTS)
    assert {section["state"] for section in first["sections"].values()} == {"full"}
    assert "score" not in first
    assert first["authority"] == {
        "financial_authority": "none",
        "can_execute": False,
        "can_recommend": False,
        "is_credit_rating": False,
    }
    _schema_validator().validate(first)

    verified = verify_fleet_brief(first, evaluated_at=EVALUATED_AT)
    assert verified.brief == first
    assert verified.states == {product: "full" for product in FLEET_BRIEF_PRODUCTS}


def test_brief_issuer_never_mints_a_native_carrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    already_issued = _carrier("liquilens")

    def forbidden_mint(**_values: object) -> dict:
        raise AssertionError("fleet brief attempted to mint a producer carrier")

    monkeypatch.setattr(carrier_module, "issue_evidence_carrier", forbidden_mint)
    brief = issue_fleet_brief(
        carriers={"liquilens": already_issued},
        evaluated_at=EVALUATED_AT,
    )
    assert brief["sections"]["liquilens"]["carrier_id"] == already_issued["carrier_id"]


def test_all_five_section_states_are_explicit_and_rights_aware() -> None:
    mixed = issue_fleet_brief(
        carriers={
            "liquilens": _carrier("liquilens"),
            "seiche": _carrier("seiche", rights_status="metadata_only"),
            "undertow": _carrier("undertow", claim_status="unavailable"),
            "palimpsest": _carrier("palimpsest", rights_status="restricted"),
        },
        evaluated_at=EVALUATED_AT,
    )
    assert {key: value["state"] for key, value in mixed["sections"].items()} == {
        "liquilens": "full",
        "seiche": "metadata_only",
        "undertow": "unavailable",
        "palimpsest": "rejected",
    }
    assert "payload" not in mixed["sections"]["seiche"]["evidence"]
    assert "payload" not in mixed["sections"]["undertow"]["evidence"]

    rejected = mixed["sections"]["palimpsest"]
    assert rejected["evidence"] is None
    assert rejected["reason_codes"] == ["rights_restricted"]
    assert "sources" not in rejected
    assert "payload" not in rejected
    assert "palimpsest:conformance:v1" not in json.dumps(rejected)
    _schema_validator().validate(mixed)

    with_missing = issue_fleet_brief(
        carriers={"liquilens": _carrier("liquilens")},
        evaluated_at=EVALUATED_AT,
    )
    assert with_missing["sections"]["seiche"] == {
        "product": "seiche",
        "state": "missing",
        "carrier_id": None,
        "record_hash": None,
        "reason_codes": ["carrier_missing"],
        "evidence": None,
    }
    _schema_validator().validate(with_missing)


@pytest.mark.parametrize("rights_status", ["restricted", "unknown", "blocked"])
def test_unsafe_rights_never_leak_source_metadata_or_payload(
    rights_status: str,
) -> None:
    brief = issue_fleet_brief(
        carriers={"liquilens": _carrier("liquilens", rights_status=rights_status)},
        evaluated_at=EVALUATED_AT,
    )
    section = brief["sections"]["liquilens"]
    assert section["state"] == "rejected"
    assert section["evidence"] is None
    encoded = json.dumps(section, sort_keys=True)
    assert "source_id" not in encoded
    assert "native_product" not in encoded

    leaked = copy.deepcopy(brief)
    leaked_section = leaked["sections"]["liquilens"]
    leaked_section["evidence"] = {"sources": [], "payload": {"leak": True}}
    with pytest.raises(FleetBriefError, match="cannot disclose evidence"):
        verify_fleet_brief(leaked, evaluated_at=EVALUATED_AT)


def test_mismatch_duplicates_unknown_keys_and_tampering_fail_closed() -> None:
    with pytest.raises(FleetBriefError, match="issued by seiche"):
        issue_fleet_brief(
            carriers={"liquilens": _carrier("seiche")},
            evaluated_at=EVALUATED_AT,
        )
    with pytest.raises(FleetBriefError, match="unsupported producers"):
        issue_fleet_brief(
            carriers={"not-a-product": _carrier("liquilens")},  # type: ignore[dict-item]
            evaluated_at=EVALUATED_AT,
        )

    brief = issue_fleet_brief(carriers=_all_full(), evaluated_at=EVALUATED_AT)
    changed = copy.deepcopy(brief)
    changed["sections"]["seiche"]["evidence"]["payload"]["native_product"] = "x"
    with pytest.raises(FleetBriefError, match="record_hash"):
        verify_fleet_brief(changed, evaluated_at=EVALUATED_AT)

    changed = copy.deepcopy(brief)
    changed["unexpected"] = True
    with pytest.raises(FleetBriefError, match="unsupported fields"):
        verify_fleet_brief(changed, evaluated_at=EVALUATED_AT)

    mixed = issue_fleet_brief(
        carriers={"seiche": _carrier("seiche", rights_status="metadata_only")},
        evaluated_at=EVALUATED_AT,
    )
    changed = copy.deepcopy(mixed)
    changed["sections"]["seiche"]["reason_codes"] = ["claim_unavailable"]
    changed["sections"]["seiche"]["evidence"]["reason_codes"] = ["claim_unavailable"]
    with pytest.raises(FleetBriefError, match="strict policy"):
        verify_fleet_brief(changed, evaluated_at=EVALUATED_AT)

    rejected = issue_fleet_brief(
        carriers={
            "liquilens": _carrier("liquilens", rights_status="blocked"),
            "seiche": _carrier("seiche", rights_status="blocked"),
        },
        evaluated_at=EVALUATED_AT,
    )
    duplicated = copy.deepcopy(rejected)
    duplicated["sections"]["seiche"]["carrier_id"] = duplicated["sections"][
        "liquilens"
    ]["carrier_id"]
    duplicated["sections"]["seiche"]["record_hash"] = duplicated["sections"][
        "liquilens"
    ]["record_hash"]
    with pytest.raises(FleetBriefError, match="multiple sections"):
        verify_fleet_brief(duplicated, evaluated_at=EVALUATED_AT)

    invented = copy.deepcopy(rejected)
    invented["sections"]["liquilens"]["reason_codes"].append("invented_rejection")
    with pytest.raises(FleetBriefError, match="unknown reason code"):
        verify_fleet_brief(invented, evaluated_at=EVALUATED_AT)


def test_evaluation_clock_is_mandatory_and_bound_into_identity() -> None:
    carrier = _carrier("liquilens")
    first = issue_fleet_brief(
        carriers={"liquilens": carrier}, evaluated_at=EVALUATED_AT
    )
    later_at = datetime(2026, 8, 26, tzinfo=UTC)
    later = issue_fleet_brief(carriers={"liquilens": carrier}, evaluated_at=later_at)
    assert first["record_hash"] != later["record_hash"]

    with pytest.raises(FleetBriefError, match="evaluation clock"):
        verify_fleet_brief(first, evaluated_at=later_at)
    with pytest.raises(ValueError, match="timezone-aware"):
        issue_fleet_brief(
            carriers={"liquilens": carrier},
            evaluated_at=datetime(2026, 8, 25),  # noqa: DTZ001 - rejection fixture
        )


def test_verified_result_cannot_be_forged() -> None:
    with pytest.raises(TypeError, match="must come from verify_fleet_brief"):
        VerifiedFleetBrief(brief_json="{}", _seal=object())


@pytest.mark.parametrize(
    ("name", "states"),
    [
        (
            "mixed-states.fleet-brief.json",
            {
                "liquilens": "full",
                "seiche": "metadata_only",
                "undertow": "unavailable",
                "palimpsest": "rejected",
            },
        ),
        (
            "missing-states.fleet-brief.json",
            {
                "liquilens": "full",
                "seiche": "missing",
                "undertow": "missing",
                "palimpsest": "missing",
            },
        ),
    ],
)
def test_committed_conformance_briefs_verify(name: str, states: dict[str, str]) -> None:
    brief = json.loads((ROOT / "examples" / "fleet-brief" / name).read_text())
    verified = verify_fleet_brief(brief, evaluated_at=EVALUATED_AT)
    assert verified.states == states
    _schema_validator().validate(brief)
    rejected = brief["sections"]["palimpsest"]
    if rejected["state"] == "rejected":
        assert rejected["evidence"] is None
        assert "sources" not in json.dumps(rejected)


def test_node_verifier_matches_python_fleet_brief_identity() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable for cross-language brief verification")
    path = ROOT / "examples" / "fleet-brief" / "mixed-states.fleet-brief.json"
    completed = subprocess.run(
        [
            node,
            str(ROOT / "protocol" / "verify_hash_tree_v1.mjs"),
            "--artifact",
            "fleet-brief",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["ok"] is True


def test_cli_issues_and_verifies_from_explicit_local_paths(tmp_path: Path) -> None:
    paths: dict[str, Path] = {}
    for product in ("liquilens", "seiche"):
        path = tmp_path / f"{product}.carrier.json"
        path.write_text(json.dumps(_carrier(product)), encoding="utf-8")
        paths[product] = path

    issued = subprocess.run(
        [
            sys.executable,
            "-m",
            "liquilens_evidence.evidence_cli",
            "issue-brief",
            "--liquilens",
            str(paths["liquilens"]),
            "--seiche",
            str(paths["seiche"]),
            "--as-of",
            EVALUATED_AT_TEXT,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert issued.returncode == 0, issued.stderr
    brief = json.loads(issued.stdout)
    assert brief["sections"]["undertow"]["state"] == "missing"
    brief_path = tmp_path / "fleet-brief.json"
    brief_path.write_text(json.dumps(brief), encoding="utf-8")

    verified = subprocess.run(
        [
            sys.executable,
            "-m",
            "liquilens_evidence.evidence_cli",
            "verify-brief",
            str(brief_path),
            "--as-of",
            EVALUATED_AT_TEXT,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stderr
    result = json.loads(verified.stdout)
    assert result["brief_id"] == brief["brief_id"]
    assert result["states"]["palimpsest"] == "missing"
    assert result["authority"]["can_recommend"] is False

    mismatch = subprocess.run(
        [
            sys.executable,
            "-m",
            "liquilens_evidence.evidence_cli",
            "issue-brief",
            "--undertow",
            str(paths["seiche"]),
            "--as-of",
            EVALUATED_AT_TEXT,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert mismatch.returncode == 2
    assert "undertow section received a carrier issued by seiche" in mismatch.stderr
