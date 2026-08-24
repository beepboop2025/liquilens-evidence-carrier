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

from liquilens_evidence.evidence_carrier import (
    EVIDENCE_CARRIER_OPENLINEAGE_FACET_SCHEMA_URL,
    EVIDENCE_CARRIER_REFERENCE_SCHEMA_URL,
    EVIDENCE_CARRIER_SCHEMA_URL,
    EvidenceCarrierError,
    EvidenceExportPolicy,
    ExportDisposition,
    VerifiedEvidenceCarrier,
    issue_evidence_carrier,
    to_arrow_metadata,
    to_cloudevent,
    to_csl_json,
    to_fdc3_context,
    to_flat_row,
    to_jsonld,
    to_openfigi_mapping_jobs,
    to_openlineage_facet,
    to_otel_log,
    verify_evidence_carrier,
)
from liquilens_evidence.protocol_resources import load_protocol_json, protocol_path

ROOT = Path(__file__).resolve().parents[1]
EVALUATED_AT = datetime(2026, 8, 24, 12, tzinfo=UTC)


def _descriptor(
    *,
    rights_status: str = "allowed",
    permissions: list[str] | None = None,
    claim_status: str = "observed",
    expires_at: str = "2026-08-24T13:00:00Z",
) -> dict:
    return {
        "producer": {
            "name": "seiche",
            "version": "0.11.0",
            "endpoint": "https://api.seiche.info/v2/world-markets",
        },
        "subject": {
            "kind": "financial_instrument",
            "name": "US Treasury 10Y",
            "identifiers": {
                "figi": "BBG001S6N5B5",
                "isin": "US91282CJZ59",
                "ticker": "T",
                "currency": "USD",
            },
        },
        "claim": {
            "kind": "funding_context",
            "summary": "Observed USD funding context for a Treasury instrument",
            "status": claim_status,
        },
        "clocks": {
            "event_time": "2026-08-24T09:00:00Z",
            "knowledge_time": "2026-08-24T09:02:00Z",
            "as_of": "2026-08-24T09:05:00Z",
            "expires_at": expires_at,
        },
        "sources": [
            {
                "source_id": "nyfed:sofr:2026-08-24",
                "publisher": "Federal Reserve Bank of New York",
                "title": "Secured Overnight Financing Rate",
                "url": "https://www.newyorkfed.org/markets/reference-rates/sofr",
                "retrieved_at": "2026-08-24T09:01:00Z",
                "content_sha256": "a" * 64,
            }
        ],
        "rights": {
            "status": rights_status,
            "permissions": permissions
            or ["ingest", "derive", "display", "redistribute"],
            "license": "official-publication-terms-reviewed",
            "license_url": "https://www.newyorkfed.org/markets/data-policy",
            "attribution": "Federal Reserve Bank of New York",
            "jurisdictions": ["global"],
        },
        "payload": {
            "metric": "sofr",
            "value": "5.31",
            "unit": "percent",
        },
        "extensions": {"seiche": {"availability": "observed"}},
    }


def _issue(**changes) -> dict:
    descriptor = _descriptor(**changes)
    return issue_evidence_carrier(**descriptor)


def test_public_descriptor_example_is_issuable() -> None:
    descriptor = json.loads((ROOT / "examples/descriptor.json").read_text())
    carrier = issue_evidence_carrier(**descriptor)
    verified = verify_evidence_carrier(
        carrier,
        evaluated_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    assert verified.disposition is ExportDisposition.FULL
    assert carrier == issue_evidence_carrier(**descriptor)


def test_issue_verify_and_json_schema_are_stable() -> None:
    first = _issue()
    second = _issue()
    assert first == second
    assert first["carrier_id"].startswith("evidence_")
    assert len(first["record_hash"]) == 64
    assert first["authority"] == {
        "financial_authority": "none",
        "can_execute": False,
        "can_recommend": False,
        "is_credit_rating": False,
    }

    schema = load_protocol_json("liquilens-evidence-carrier-v1.schema.json")
    jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    ).validate(first)
    verified = verify_evidence_carrier(first, evaluated_at=EVALUATED_AT)
    assert verified.disposition is ExportDisposition.FULL
    assert verified.reason_codes == ()
    assert verified.export_view() == first


def test_payload_or_authority_tampering_fails_identity_or_boundary() -> None:
    carrier = _issue()
    changed = copy.deepcopy(carrier)
    changed["payload"]["value"] = "99.99"
    with pytest.raises(EvidenceCarrierError, match="record_hash"):
        verify_evidence_carrier(changed, evaluated_at=EVALUATED_AT)

    changed = copy.deepcopy(carrier)
    changed["authority"]["can_execute"] = True
    with pytest.raises(EvidenceCarrierError, match="all-false"):
        verify_evidence_carrier(changed, evaluated_at=EVALUATED_AT)


def test_clock_and_source_retrieval_order_fail_issuance() -> None:
    descriptor = _descriptor()
    descriptor["clocks"]["event_time"] = "2026-08-24T10:00:00Z"
    with pytest.raises(EvidenceCarrierError, match="event_time <= knowledge_time"):
        issue_evidence_carrier(**descriptor)

    descriptor = _descriptor()
    descriptor["sources"][0]["retrieved_at"] = "2026-08-24T09:03:00Z"
    with pytest.raises(EvidenceCarrierError, match="cannot follow knowledge_time"):
        issue_evidence_carrier(**descriptor)

    descriptor = _descriptor()
    descriptor["sources"][0]["retrieved_at"] = "2026-08-24T08:59:00Z"
    with pytest.raises(EvidenceCarrierError, match="cannot precede event_time"):
        issue_evidence_carrier(**descriptor)


def test_expired_and_metadata_only_carriers_retain_reference_but_not_payload() -> None:
    expired = verify_evidence_carrier(
        _issue(expires_at="2026-08-24T10:00:00Z"),
        evaluated_at=EVALUATED_AT,
    )
    assert expired.disposition is ExportDisposition.METADATA_ONLY
    assert expired.reason_codes == ("evidence_expired",)
    view = expired.export_view()
    assert view["payload_disclosed"] is False
    assert "payload" not in view
    assert view["carrier_id"] == expired.carrier["carrier_id"]
    assert view["record_hash"] == expired.carrier["record_hash"]

    metadata = verify_evidence_carrier(
        _issue(rights_status="metadata_only"), evaluated_at=EVALUATED_AT
    )
    assert metadata.disposition is ExportDisposition.METADATA_ONLY
    assert "rights_metadata_only" in metadata.reason_codes
    assert to_flat_row(metadata)["payload_json"] == ""


def test_carrier_is_not_exportable_before_its_knowledge_time() -> None:
    verified = verify_evidence_carrier(
        _issue(), evaluated_at=datetime(2026, 8, 24, 9, 1, tzinfo=UTC)
    )
    assert verified.disposition is ExportDisposition.REJECT
    assert verified.reason_codes == ("evidence_not_yet_known",)
    with pytest.raises(EvidenceCarrierError, match="not_yet_known"):
        verified.export_view()

    descriptor = _descriptor(permissions=["ingest"])
    descriptor["rights"]["license"] = None
    descriptor["rights"]["license_url"] = None
    descriptor["rights"]["attribution"] = None
    verified = verify_evidence_carrier(
        issue_evidence_carrier(**descriptor),
        evaluated_at=datetime(2026, 8, 24, 9, 1, tzinfo=UTC),
    )
    assert verified.disposition is ExportDisposition.REJECT
    assert "evidence_not_yet_known" in verified.reason_codes
    assert "redistribution_not_permitted" in verified.reason_codes


def test_carrier_is_not_exportable_before_its_as_of_time() -> None:
    descriptor = _descriptor()
    descriptor["clocks"]["as_of"] = "2026-08-24T13:00:00Z"
    verified = verify_evidence_carrier(
        issue_evidence_carrier(**descriptor), evaluated_at=EVALUATED_AT
    )
    assert verified.disposition is ExportDisposition.REJECT
    assert verified.reason_codes == ("evidence_as_of_in_future",)
    with pytest.raises(EvidenceCarrierError, match="as_of_in_future"):
        verified.export_view()


def test_policy_and_verified_result_cannot_be_forged_accidentally() -> None:
    with pytest.raises(TypeError, match="expired_disposition"):
        EvidenceExportPolicy(expired_disposition="reject")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="must come from verify_evidence_carrier"):
        VerifiedEvidenceCarrier(
            carrier_json="{}",
            disposition=ExportDisposition.FULL,
            reason_codes=(),
            policy_version="forged",
            _seal=object(),
        )


@pytest.mark.parametrize("status", ["restricted", "unknown", "blocked"])
def test_unsafe_rights_block_every_adapter(status: str) -> None:
    verified = verify_evidence_carrier(
        _issue(rights_status=status), evaluated_at=EVALUATED_AT
    )
    assert verified.disposition is ExportDisposition.REJECT
    with pytest.raises(EvidenceCarrierError, match="export is blocked"):
        to_fdc3_context(verified)
    with pytest.raises(EvidenceCarrierError, match="export is blocked"):
        to_cloudevent(verified)


def test_missing_redistribution_and_unavailable_claim_are_metadata_only() -> None:
    verified = verify_evidence_carrier(
        _issue(permissions=["ingest", "derive", "display"]),
        evaluated_at=EVALUATED_AT,
    )
    assert verified.disposition is ExportDisposition.METADATA_ONLY
    assert verified.reason_codes == ("redistribution_not_permitted",)

    unavailable = verify_evidence_carrier(
        _issue(claim_status="unavailable"), evaluated_at=EVALUATED_AT
    )
    assert unavailable.disposition is ExportDisposition.METADATA_ONLY
    assert unavailable.reason_codes == ("claim_unavailable",)


def test_incomplete_rights_basis_cannot_disclose_the_payload() -> None:
    missing_license = _descriptor()
    missing_license["rights"]["license"] = None
    missing_license["rights"]["license_url"] = None
    verified = verify_evidence_carrier(
        issue_evidence_carrier(**missing_license), evaluated_at=EVALUATED_AT
    )
    assert verified.disposition is ExportDisposition.METADATA_ONLY
    assert verified.reason_codes == ("rights_license_missing",)
    assert "payload" not in verified.export_view()

    missing_attribution = _descriptor()
    missing_attribution["rights"]["attribution"] = None
    verified = verify_evidence_carrier(
        issue_evidence_carrier(**missing_attribution), evaluated_at=EVALUATED_AT
    )
    assert verified.disposition is ExportDisposition.METADATA_ONLY
    assert verified.reason_codes == ("rights_attribution_missing",)
    assert to_flat_row(verified)["payload_json"] == ""


def test_transport_projections_preserve_identity_clocks_and_rights() -> None:
    verified = verify_evidence_carrier(_issue(), evaluated_at=EVALUATED_AT)
    carrier = verified.carrier

    fdc3 = to_fdc3_context(verified)
    assert fdc3["type"] == "com.liquilens.evidence"
    assert fdc3["evidenceSchema"] == EVIDENCE_CARRIER_SCHEMA_URL
    assert fdc3["id"]["FIGI"] == "BBG001S6N5B5"
    assert fdc3["id"]["ISIN"] == "US91282CJZ59"
    assert fdc3["id"]["CURRENCY_ISOCODE"] == "USD"
    assert fdc3["id"]["liquilensEvidenceId"] == carrier["carrier_id"]

    event = to_cloudevent(verified)
    assert event["specversion"] == "1.0"
    assert event["dataschema"] == EVIDENCE_CARRIER_SCHEMA_URL
    assert event["time"] == carrier["clocks"]["knowledge_time"]
    assert event["data"]["record_hash"] == carrier["record_hash"]

    otel = to_otel_log(verified)
    assert otel["timestamp"] == carrier["clocks"]["event_time"]
    assert otel["observed_timestamp"] == carrier["clocks"]["knowledge_time"]
    assert otel["attributes"]["liquilens.evidence.rights_status"] == "allowed"

    facet = to_openlineage_facet(verified)
    assert facet["_schemaURL"] == EVIDENCE_CARRIER_OPENLINEAGE_FACET_SCHEMA_URL
    assert facet["carrierSchemaURL"] == EVIDENCE_CARRIER_SCHEMA_URL
    assert facet["carrier"]["carrier_id"] == carrier["carrier_id"]

    jsonld = to_jsonld(verified)
    assert jsonld["@type"] == "prov:Entity"
    assert jsonld["prov:generatedAtTime"] == carrier["clocks"]["knowledge_time"]
    assert jsonld["prov:wasDerivedFrom"][0]["@id"].startswith("https://")

    arrow = to_arrow_metadata(verified)
    assert arrow[b"liquilens.evidence.record_hash"] == carrier[
        "record_hash"
    ].encode()
    embedded = json.loads(arrow[b"liquilens.evidence.carrier"])
    assert embedded["carrier_id"] == carrier["carrier_id"]

    citation = to_csl_json(verified)
    assert citation["type"] == "dataset"
    assert carrier["record_hash"] in citation["note"]

    row = to_flat_row(verified)
    assert row["event_time"] == carrier["clocks"]["event_time"]
    assert row["evidence_schema_url"] == EVIDENCE_CARRIER_SCHEMA_URL
    assert json.loads(row["payload_json"])["metric"] == "sofr"


def test_fdc3_and_openlineage_machine_contracts_validate() -> None:
    verified = verify_evidence_carrier(_issue(), evaluated_at=EVALUATED_AT)
    carrier_schema = load_protocol_json("liquilens-evidence-carrier-v1.schema.json")
    registry = Registry().with_resource(
        EVIDENCE_CARRIER_SCHEMA_URL, Resource.from_contents(carrier_schema)
    )
    reference_schema = load_protocol_json(
        "liquilens-evidence-carrier-reference-v1.schema.json"
    )
    registry = registry.with_resource(
        EVIDENCE_CARRIER_REFERENCE_SCHEMA_URL,
        Resource.from_contents(reference_schema),
    )
    fdc3_schema = json.loads(
        (ROOT / "integrations/fdc3/com.liquilens.evidence.schema.json").read_text()
    )
    jsonschema.Draft202012Validator(
        fdc3_schema,
        format_checker=jsonschema.FormatChecker(),
        registry=registry,
    ).validate(to_fdc3_context(verified))

    lineage_schema = json.loads(
        (
            ROOT
            / "integrations/openlineage/liquilens-evidence-facet.schema.json"
        ).read_text()
    )
    jsonschema.Draft202012Validator(
        lineage_schema,
        format_checker=jsonschema.FormatChecker(),
        registry=registry,
    ).validate(to_openlineage_facet(verified))


def test_metadata_only_transports_identify_and_validate_reference_schema() -> None:
    verified = verify_evidence_carrier(
        _issue(permissions=["ingest", "derive", "display"]),
        evaluated_at=EVALUATED_AT,
    )
    assert verified.disposition is ExportDisposition.METADATA_ONLY

    carrier_schema = load_protocol_json("liquilens-evidence-carrier-v1.schema.json")
    reference_schema = load_protocol_json(
        "liquilens-evidence-carrier-reference-v1.schema.json"
    )
    registry = Registry().with_resource(
        EVIDENCE_CARRIER_SCHEMA_URL, Resource.from_contents(carrier_schema)
    )
    registry = registry.with_resource(
        EVIDENCE_CARRIER_REFERENCE_SCHEMA_URL,
        Resource.from_contents(reference_schema),
    )
    jsonschema.Draft202012Validator(
        reference_schema,
        registry=registry,
        format_checker=jsonschema.FormatChecker(),
    ).validate(verified.export_view())

    event = to_cloudevent(verified)
    assert event["dataschema"] == EVIDENCE_CARRIER_REFERENCE_SCHEMA_URL
    arrow = to_arrow_metadata(verified)
    assert (
        arrow[b"liquilens.evidence.schema"]
        == EVIDENCE_CARRIER_REFERENCE_SCHEMA_URL.encode()
    )
    assert (
        to_otel_log(verified)["attributes"]["liquilens.evidence.schema_url"]
        == EVIDENCE_CARRIER_REFERENCE_SCHEMA_URL
    )
    assert (
        to_jsonld(verified)["liquilens:evidenceSchema"]
        == EVIDENCE_CARRIER_REFERENCE_SCHEMA_URL
    )
    assert (
        to_flat_row(verified)["evidence_schema_url"]
        == EVIDENCE_CARRIER_REFERENCE_SCHEMA_URL
    )

    fdc3_schema = json.loads(
        (ROOT / "integrations/fdc3/com.liquilens.evidence.schema.json").read_text()
    )
    fdc3_validator = jsonschema.Draft202012Validator(
        fdc3_schema,
        registry=registry,
        format_checker=jsonschema.FormatChecker(),
    )
    fdc3 = to_fdc3_context(verified)
    fdc3_validator.validate(fdc3)
    mismatched_fdc3 = copy.deepcopy(fdc3)
    mismatched_fdc3["evidenceSchema"] = EVIDENCE_CARRIER_SCHEMA_URL
    with pytest.raises(jsonschema.ValidationError):
        fdc3_validator.validate(mismatched_fdc3)
    lineage_schema = json.loads(
        (
            ROOT
            / "integrations/openlineage/liquilens-evidence-facet.schema.json"
        ).read_text()
    )
    lineage_validator = jsonschema.Draft202012Validator(
        lineage_schema,
        registry=registry,
        format_checker=jsonschema.FormatChecker(),
    )
    facet = to_openlineage_facet(verified)
    lineage_validator.validate(facet)
    mismatched_facet = copy.deepcopy(facet)
    mismatched_facet["carrierSchemaURL"] = EVIDENCE_CARRIER_SCHEMA_URL
    with pytest.raises(jsonschema.ValidationError):
        lineage_validator.validate(mismatched_facet)


@pytest.mark.parametrize(
    ("product", "endpoint"),
    [
        ("liquilens", "https://liquilens.in"),
        ("seiche", "https://api.seiche.info"),
        ("undertow", "https://liquilens-undertow.com"),
        ("palimpsest", "https://palimpsest.info"),
    ],
)
def test_all_four_product_producers_share_the_same_contract(
    product: str, endpoint: str
) -> None:
    descriptor = _descriptor()
    descriptor["producer"] = {
        "name": product,
        "version": "conformance-fixture-v1",
        "endpoint": endpoint,
    }
    carrier = issue_evidence_carrier(**descriptor)
    verified = verify_evidence_carrier(carrier, evaluated_at=EVALUATED_AT)
    assert verified.disposition is ExportDisposition.FULL
    assert to_cloudevent(verified)["source"] == endpoint
    assert to_fdc3_context(verified)["id"]["liquilensEvidenceId"] == carrier[
        "carrier_id"
    ]


def test_dbt_gate_contains_the_non_widening_invariants() -> None:
    macro = (
        ROOT
        / "integrations/dbt/macros/test_liquilens_evidence_contract.sql"
    ).read_text()
    assert "event_time > knowledge_time" in macro
    assert "export_disposition <> 'full'" in macro
    assert "rights_status in ('restricted', 'unknown', 'blocked')" in macro
    assert "redistribution_permitted <> 'true'" in macro
    assert "coalesce(rights_attribution, '') = ''" in macro


def test_openfigi_jobs_are_generated_without_network_access() -> None:
    descriptor = _descriptor()
    descriptor["subject"]["identifiers"].update(
        {"mic": "XNAS", "exch_code": "US"}
    )
    verified = verify_evidence_carrier(
        issue_evidence_carrier(**descriptor), evaluated_at=EVALUATED_AT
    )
    jobs = to_openfigi_mapping_jobs(verified)
    assert jobs[:2] == [
        {"idType": "ID_BB_GLOBAL", "idValue": "BBG001S6N5B5"},
        {"idType": "ID_ISIN", "idValue": "US91282CJZ59"},
    ]
    assert jobs[-1] == {
        "idType": "TICKER",
        "idValue": "T",
        "micCode": "XNAS",
        "currency": "USD",
        "exchCode": "US",
    }


def test_node_verifier_matches_python_carrier_identity(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable for cross-language carrier verification")
    carrier_path = tmp_path / "carrier.json"
    carrier_path.write_text(
        json.dumps(_issue(), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            node,
            str(protocol_path("verify_hash_tree_v1.mjs")),
            "--artifact",
            "evidence-carrier",
            str(carrier_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["ok"] is True


def test_cli_issues_verifies_and_converts(tmp_path: Path) -> None:
    descriptor_path = tmp_path / "descriptor.json"
    descriptor_path.write_text(json.dumps(_descriptor()), encoding="utf-8")
    issued = subprocess.run(
        [sys.executable, "-m", "liquilens_evidence.evidence_cli", "issue", str(descriptor_path)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert issued.returncode == 0, issued.stderr
    carrier = json.loads(issued.stdout)
    carrier_path = tmp_path / "carrier.json"
    carrier_path.write_text(json.dumps(carrier), encoding="utf-8")

    verified = subprocess.run(
        [
            sys.executable,
            "-m",
            "liquilens_evidence.evidence_cli",
            "verify",
            str(carrier_path),
            "--as-of",
            "2026-08-24T12:00:00Z",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["export_disposition"] == "full"

    converted = subprocess.run(
        [
            sys.executable,
            "-m",
            "liquilens_evidence.evidence_cli",
            "convert",
            str(carrier_path),
            "--format",
            "fdc3",
            "--as-of",
            "2026-08-24T12:00:00Z",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert converted.returncode == 0, converted.stderr
    assert json.loads(converted.stdout)["type"] == "com.liquilens.evidence"


def test_cli_rejects_duplicate_keys(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"producer":{},"producer":{}}', encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "liquilens_evidence.evidence_cli", "issue", str(duplicate)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "duplicate JSON object key" in completed.stderr


def test_cli_verifies_multiple_hook_files(tmp_path: Path) -> None:
    paths: list[Path] = []
    for name in ("opening.evidence.json", "close.carrier.json"):
        path = tmp_path / name
        path.write_text(json.dumps(_issue()), encoding="utf-8")
        paths.append(path)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "liquilens_evidence.evidence_cli",
            "verify-files",
            *(str(path) for path in paths),
            "--as-of",
            "2026-08-24T12:00:00Z",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    output = json.loads(completed.stdout)
    assert output["ok"] is True
    assert [row["input"] for row in output["verified"]] == [
        str(path) for path in paths
    ]


def test_composite_action_wrapper_verifies_without_install(tmp_path: Path) -> None:
    carrier_path = tmp_path / "close.evidence.json"
    carrier_path.write_text(json.dumps(_issue()), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/action_verify.py"),
            str(carrier_path),
            "--as-of",
            "2026-08-24T12:00:00Z",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["ok"] is True
