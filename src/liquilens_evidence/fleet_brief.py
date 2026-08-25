"""Rights-aware fleet briefs over already-issued native Evidence Carriers.

The brief is an envelope, not a cross-product model. It preserves one section
for each canonical producer, records the exact policy-evaluation clock, and
never fetches evidence or issues a carrier for another product.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .canonical import canonical_hash_bytes
from .evidence_carrier import (
    EVIDENCE_CARRIER_CANONICALIZATION,
    EVIDENCE_CARRIER_MAX_BYTES,
    EVIDENCE_CARRIER_REFERENCE_SCHEMA,
    EVIDENCE_CARRIER_SCHEMA_VERSION,
    STRICT_EXPORT_POLICY,
    EvidenceCarrierError,
    ExportDisposition,
    _evaluate_disclosure,
    _validate_shape,
    verify_evidence_carrier,
)

FLEET_BRIEF_SCHEMA = "liquilens.fleet-brief.v1"
FLEET_BRIEF_SCHEMA_URL = (
    "https://liquilens.in/protocol/liquilens-fleet-brief-v1.schema.json"
)
FLEET_BRIEF_CANONICALIZATION = EVIDENCE_CARRIER_CANONICALIZATION
FLEET_BRIEF_PRODUCTS = ("liquilens", "seiche", "undertow", "palimpsest")
FLEET_BRIEF_MAX_BYTES = EVIDENCE_CARRIER_MAX_BYTES * 4 + 262_144

_FLEET_BRIEF_PRODUCT_SET = frozenset(FLEET_BRIEF_PRODUCTS)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CARRIER_ID_RE = re.compile(r"^evidence_[0-9a-f]{24}$")
_BRIEF_ID_RE = re.compile(r"^fleet_brief_[0-9a-f]{24}$")
_VERIFIED_FLEET_BRIEF_SEAL = object()
_AUTHORITY_BOUNDARY = {
    "financial_authority": "none",
    "can_execute": False,
    "can_recommend": False,
    "is_credit_rating": False,
}
_POLICY_REASON_CODES = frozenset(
    {
        "evidence_not_yet_known",
        "evidence_as_of_in_future",
        "rights_restricted",
        "rights_unknown",
        "rights_blocked",
        "rights_metadata_only",
        "redistribution_not_permitted",
        "rights_license_missing",
        "rights_attribution_missing",
        "claim_restricted",
        "claim_unavailable",
        "evidence_expired",
    }
)
_REJECTION_REASON_CODES = frozenset(
    {
        "evidence_not_yet_known",
        "evidence_as_of_in_future",
        "rights_restricted",
        "rights_unknown",
        "rights_blocked",
    }
)


class FleetBriefError(EvidenceCarrierError):
    """Raised when a fleet brief is malformed, inconsistent, or tampered with."""


class FleetBriefState(StrEnum):
    """The evidence availability disclosed for one canonical product."""

    FULL = "full"
    METADATA_ONLY = "metadata_only"
    UNAVAILABLE = "unavailable"
    REJECTED = "rejected"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class VerifiedFleetBrief:
    """Immutable result returned only by :func:`verify_fleet_brief`."""

    brief_json: str
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal is not _VERIFIED_FLEET_BRIEF_SEAL:
            raise TypeError(
                "VerifiedFleetBrief values must come from verify_fleet_brief"
            )

    @property
    def brief(self) -> dict[str, Any]:
        value = json.loads(self.brief_json)
        if not isinstance(value, dict):  # pragma: no cover - constructor invariant
            raise TypeError("verified fleet brief root changed shape")
        return value

    @property
    def states(self) -> dict[str, str]:
        sections = self.brief["sections"]
        return {product: sections[product]["state"] for product in FLEET_BRIEF_PRODUCTS}


def _json_copy(value: Any, field_name: str) -> Any:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise FleetBriefError(f"{field_name} must contain finite JSON") from error
    if len(encoded) > FLEET_BRIEF_MAX_BYTES:
        raise FleetBriefError(
            f"fleet brief exceeds {FLEET_BRIEF_MAX_BYTES} encoded bytes"
        )
    return json.loads(encoded)


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise FleetBriefError(f"{field_name} must be an object")
    copied = _json_copy(value, field_name)
    if not isinstance(copied, dict):  # pragma: no cover - Mapping invariant
        raise TypeError("mapping normalized to a non-object")
    return copied


def _exact_keys(value: Mapping[str, Any], field_name: str, required: set[str]) -> None:
    missing = required - set(value)
    extra = set(value) - required
    if missing:
        raise FleetBriefError(
            f"{field_name} is missing fields: {', '.join(sorted(missing))}"
        )
    if extra:
        raise FleetBriefError(
            f"{field_name} has unsupported fields: {', '.join(sorted(extra))}"
        )


def _evaluation_time(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("evaluated_at must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")
    return value.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _utc(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise FleetBriefError(f"{field_name} must be a UTC timestamp ending in Z")
    try:
        instant = datetime.fromisoformat(value)
    except ValueError as error:
        raise FleetBriefError(f"{field_name} is not a valid timestamp") from error
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise FleetBriefError(f"{field_name} must be timezone-aware")
    return instant.astimezone(UTC)


def _identity(value: Any, field_name: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise FleetBriefError(f"{field_name} has an invalid shape")
    return value


def _reason_codes(value: Any, field_name: str, *, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise FleetBriefError(f"{field_name} must be an array")
    if not allow_empty and not value:
        raise FleetBriefError(f"{field_name} must not be empty")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise FleetBriefError(f"{field_name} must contain non-blank strings")
    if len(value) != len(set(value)):
        raise FleetBriefError(f"{field_name} must not contain duplicates")
    return tuple(value)


def _brief_payload(brief: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in brief.items()
        if key not in {"brief_id", "record_hash"}
    }


def _brief_digest(brief: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_hash_bytes(_brief_payload(brief))).hexdigest()


def _missing_section(product: str) -> dict[str, Any]:
    return {
        "product": product,
        "state": FleetBriefState.MISSING.value,
        "carrier_id": None,
        "record_hash": None,
        "reason_codes": ["carrier_missing"],
        "evidence": None,
    }


def _section_from_carrier(
    product: str, carrier_value: Mapping[str, Any], evaluated_at: datetime
) -> dict[str, Any]:
    verified = verify_evidence_carrier(
        carrier_value,
        evaluated_at=evaluated_at,
        policy=STRICT_EXPORT_POLICY,
    )
    carrier = verified.carrier
    actual_product = carrier["producer"]["name"]
    if actual_product != product:
        raise FleetBriefError(
            f"{product} section received a carrier issued by {actual_product}"
        )

    state: FleetBriefState
    evidence: dict[str, Any] | None
    if verified.disposition is ExportDisposition.REJECT:
        state = FleetBriefState.REJECTED
        evidence = None
    elif carrier["claim"]["status"] == "unavailable":
        state = FleetBriefState.UNAVAILABLE
        evidence = verified.export_view()
    elif verified.disposition is ExportDisposition.METADATA_ONLY:
        state = FleetBriefState.METADATA_ONLY
        evidence = verified.export_view()
    else:
        state = FleetBriefState.FULL
        evidence = verified.export_view()

    return {
        "product": product,
        "state": state.value,
        "carrier_id": carrier["carrier_id"],
        "record_hash": carrier["record_hash"],
        "reason_codes": list(verified.reason_codes),
        "evidence": evidence,
    }


def issue_fleet_brief(
    *,
    carriers: Mapping[str, Mapping[str, Any] | None],
    evaluated_at: datetime,
) -> dict[str, Any]:
    """Bundle supplied native carriers without fetching or issuing for producers.

    Omitted keys and explicit ``None`` values become ``missing`` sections. Any
    supplied value is verified with the strict carrier policy at the mandatory
    ``evaluated_at`` clock before it can influence the brief.
    """

    if not isinstance(carriers, Mapping):
        raise TypeError("carriers must be a product-to-carrier mapping")
    unknown_products = set(carriers) - _FLEET_BRIEF_PRODUCT_SET
    if unknown_products:
        raise FleetBriefError(
            "carriers has unsupported producers: "
            + ", ".join(sorted(str(item) for item in unknown_products))
        )
    instant = _evaluation_time(evaluated_at)
    sections: dict[str, Any] = {}
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for product in FLEET_BRIEF_PRODUCTS:
        supplied = carriers.get(product)
        if supplied is None:
            sections[product] = _missing_section(product)
            continue
        if not isinstance(supplied, Mapping):
            raise FleetBriefError(f"carriers.{product} must be an object or null")
        section = _section_from_carrier(product, supplied, instant)
        carrier_id = section["carrier_id"]
        record_hash = section["record_hash"]
        if carrier_id in seen_ids or record_hash in seen_hashes:
            raise FleetBriefError("the same carrier cannot occupy multiple sections")
        seen_ids.add(carrier_id)
        seen_hashes.add(record_hash)
        sections[product] = section

    brief: dict[str, Any] = {
        "schema": FLEET_BRIEF_SCHEMA,
        "canonicalization": FLEET_BRIEF_CANONICALIZATION,
        "brief_id": "fleet_brief_" + "0" * 24,
        "record_hash": "0" * 64,
        "evaluated_at": _utc_text(instant),
        "sections": sections,
        "authority": dict(_AUTHORITY_BOUNDARY),
    }
    brief = _json_copy(brief, "fleet brief")
    digest = _brief_digest(brief)
    brief["brief_id"] = f"fleet_brief_{digest[:24]}"
    brief["record_hash"] = digest
    verify_fleet_brief(brief, evaluated_at=instant)
    return brief


def _validate_reference(
    value: Any,
    *,
    product: str,
    section_reasons: tuple[str, ...],
    state: FleetBriefState,
    evaluated_at: datetime,
) -> dict[str, Any]:
    reference = _mapping(value, f"sections.{product}.evidence")
    _exact_keys(
        reference,
        f"sections.{product}.evidence",
        {
            "schema",
            "carrier_id",
            "record_hash",
            "canonicalization",
            "producer",
            "subject",
            "claim",
            "clocks",
            "sources",
            "rights",
            "authority",
            "payload_disclosed",
            "reason_codes",
            "policy_version",
        },
    )
    if reference["schema"] != EVIDENCE_CARRIER_REFERENCE_SCHEMA:
        raise FleetBriefError(f"sections.{product}.evidence has an unsupported schema")
    if reference["payload_disclosed"] is not False:
        raise FleetBriefError(f"sections.{product}.evidence must not disclose payload")
    if reference["policy_version"] != STRICT_EXPORT_POLICY.version:
        raise FleetBriefError(f"sections.{product}.evidence did not use strict policy")
    reference_reasons = _reason_codes(
        reference["reason_codes"],
        f"sections.{product}.evidence.reason_codes",
        allow_empty=False,
    )
    if reference_reasons != section_reasons:
        raise FleetBriefError(f"sections.{product} reason codes do not match evidence")

    # Reuse the native carrier's dependency-free structural validator without
    # reconstructing or reissuing the withheld payload.
    carrier_shape = {
        "schema_version": EVIDENCE_CARRIER_SCHEMA_VERSION,
        "canonicalization": reference["canonicalization"],
        "carrier_id": reference["carrier_id"],
        "record_hash": reference["record_hash"],
        "producer": reference["producer"],
        "subject": reference["subject"],
        "claim": reference["claim"],
        "clocks": reference["clocks"],
        "sources": reference["sources"],
        "rights": reference["rights"],
        "authority": reference["authority"],
        "payload": {},
        "extensions": {},
    }
    try:
        _validate_shape(carrier_shape)
    except EvidenceCarrierError as error:
        raise FleetBriefError(f"sections.{product}.evidence: {error}") from error
    if reference["producer"]["name"] != product:
        raise FleetBriefError(f"sections.{product} producer does not match its key")
    if reference["rights"]["status"] in {"restricted", "unknown", "blocked"}:
        raise FleetBriefError(
            f"sections.{product} leaks metadata for rejected carrier rights"
        )
    disposition, expected_reasons = _evaluate_disclosure(
        carrier_shape,
        evaluated_at=evaluated_at,
        policy=STRICT_EXPORT_POLICY,
    )
    if disposition is not ExportDisposition.METADATA_ONLY:
        raise FleetBriefError(
            f"sections.{product} reference is not metadata-only under strict policy"
        )
    if reference_reasons != expected_reasons:
        raise FleetBriefError(
            f"sections.{product} reason codes do not match strict policy"
        )
    is_unavailable = reference["claim"]["status"] == "unavailable"
    if state is FleetBriefState.UNAVAILABLE and not is_unavailable:
        raise FleetBriefError(f"sections.{product} unavailable state lacks that claim")
    if state is FleetBriefState.METADATA_ONLY and is_unavailable:
        raise FleetBriefError(
            f"sections.{product} must surface an unavailable claim as unavailable"
        )
    return reference


def _validate_section(
    product: str,
    value: Any,
    *,
    evaluated_at: datetime,
) -> tuple[str | None, str | None]:
    section = _mapping(value, f"sections.{product}")
    _exact_keys(
        section,
        f"sections.{product}",
        {"product", "state", "carrier_id", "record_hash", "reason_codes", "evidence"},
    )
    if section["product"] != product:
        raise FleetBriefError(f"sections.{product}.product must match its key")
    try:
        state = FleetBriefState(section["state"])
    except (TypeError, ValueError) as error:
        raise FleetBriefError(f"sections.{product}.state is unsupported") from error
    reasons = _reason_codes(
        section["reason_codes"],
        f"sections.{product}.reason_codes",
        allow_empty=state is FleetBriefState.FULL,
    )

    if state is FleetBriefState.MISSING:
        if section["carrier_id"] is not None or section["record_hash"] is not None:
            raise FleetBriefError(
                f"sections.{product} missing state cannot have identity"
            )
        if section["evidence"] is not None:
            raise FleetBriefError(
                f"sections.{product} missing state cannot have evidence"
            )
        if reasons != ("carrier_missing",):
            raise FleetBriefError(f"sections.{product} missing reason is not canonical")
        return None, None

    carrier_id = _identity(
        section["carrier_id"], f"sections.{product}.carrier_id", _CARRIER_ID_RE
    )
    record_hash = _identity(
        section["record_hash"], f"sections.{product}.record_hash", _SHA256_RE
    )
    if state is FleetBriefState.REJECTED:
        if section["evidence"] is not None:
            raise FleetBriefError(
                f"sections.{product} rejected state cannot disclose evidence"
            )
        if not set(reasons) <= _POLICY_REASON_CODES:
            raise FleetBriefError(
                f"sections.{product} rejected state has an unknown reason code"
            )
        if not _REJECTION_REASON_CODES.intersection(reasons):
            raise FleetBriefError(
                f"sections.{product} rejected state lacks a strict rejection reason"
            )
        return carrier_id, record_hash

    if state is FleetBriefState.FULL:
        if reasons:
            raise FleetBriefError(f"sections.{product} full state cannot have reasons")
        evidence = _mapping(section["evidence"], f"sections.{product}.evidence")
        try:
            verified = verify_evidence_carrier(
                evidence,
                evaluated_at=evaluated_at,
                policy=STRICT_EXPORT_POLICY,
            )
        except EvidenceCarrierError as error:
            raise FleetBriefError(f"sections.{product}.evidence: {error}") from error
        carrier = verified.carrier
        if verified.disposition is not ExportDisposition.FULL:
            raise FleetBriefError(f"sections.{product} is not fully disclosable")
        if carrier["producer"]["name"] != product:
            raise FleetBriefError(f"sections.{product} producer does not match its key")
        if carrier["carrier_id"] != carrier_id or carrier["record_hash"] != record_hash:
            raise FleetBriefError(
                f"sections.{product} identity does not match evidence"
            )
        return carrier_id, record_hash

    reference = _validate_reference(
        section["evidence"],
        product=product,
        section_reasons=reasons,
        state=state,
        evaluated_at=evaluated_at,
    )
    if reference["carrier_id"] != carrier_id or reference["record_hash"] != record_hash:
        raise FleetBriefError(f"sections.{product} identity does not match evidence")
    return carrier_id, record_hash


def verify_fleet_brief(
    value: Mapping[str, Any], *, evaluated_at: datetime
) -> VerifiedFleetBrief:
    """Verify one content-addressed fleet brief at its explicit issuance clock."""

    instant = _evaluation_time(evaluated_at)
    brief = _mapping(value, "fleet brief")
    _exact_keys(
        brief,
        "fleet brief",
        {
            "schema",
            "canonicalization",
            "brief_id",
            "record_hash",
            "evaluated_at",
            "sections",
            "authority",
        },
    )
    if brief["schema"] != FLEET_BRIEF_SCHEMA:
        raise FleetBriefError("unsupported fleet brief schema")
    if brief["canonicalization"] != FLEET_BRIEF_CANONICALIZATION:
        raise FleetBriefError("unsupported fleet brief canonicalization")
    _identity(brief["brief_id"], "brief_id", _BRIEF_ID_RE)
    _identity(brief["record_hash"], "record_hash", _SHA256_RE)
    recorded_at = _utc(brief["evaluated_at"], "evaluated_at")
    if recorded_at != instant:
        raise FleetBriefError("evaluated_at does not match the brief evaluation clock")
    if brief["authority"] != _AUTHORITY_BOUNDARY:
        raise FleetBriefError("authority must retain the all-false boundary")

    sections = _mapping(brief["sections"], "sections")
    _exact_keys(sections, "sections", set(FLEET_BRIEF_PRODUCTS))
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for product in FLEET_BRIEF_PRODUCTS:
        carrier_id, record_hash = _validate_section(
            product, sections[product], evaluated_at=instant
        )
        if carrier_id is None or record_hash is None:
            continue
        if carrier_id in seen_ids or record_hash in seen_hashes:
            raise FleetBriefError("the same carrier cannot occupy multiple sections")
        seen_ids.add(carrier_id)
        seen_hashes.add(record_hash)

    digest = _brief_digest(brief)
    if brief["record_hash"] != digest:
        raise FleetBriefError("record_hash does not match the fleet brief payload")
    if brief["brief_id"] != f"fleet_brief_{digest[:24]}":
        raise FleetBriefError("brief_id does not match record_hash")
    serialized = json.dumps(
        brief,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return VerifiedFleetBrief(
        brief_json=serialized,
        _seal=_VERIFIED_FLEET_BRIEF_SEAL,
    )


__all__ = [
    "FLEET_BRIEF_CANONICALIZATION",
    "FLEET_BRIEF_MAX_BYTES",
    "FLEET_BRIEF_PRODUCTS",
    "FLEET_BRIEF_SCHEMA",
    "FLEET_BRIEF_SCHEMA_URL",
    "FleetBriefError",
    "FleetBriefState",
    "VerifiedFleetBrief",
    "issue_fleet_brief",
    "verify_fleet_brief",
]
