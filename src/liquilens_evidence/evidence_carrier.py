"""Portable, fail-closed evidence carriers for downstream financial workflows.

The carrier is deliberately transport-neutral.  It can travel through financial
desktop contexts, event buses, observability systems, lineage catalogs, Arrow
metadata, spreadsheets, and citation managers without changing its temporal or
rights semantics.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

from .canonical import canonical_hash_bytes

EVIDENCE_CARRIER_SCHEMA_VERSION = "1.0"
EVIDENCE_CARRIER_CANONICALIZATION = "liquilens-hash-tree-v1"
EVIDENCE_CARRIER_SCHEMA_URL = (
    "https://liquilens.in/protocol/liquilens-evidence-carrier-v1.schema.json"
)
EVIDENCE_CARRIER_REFERENCE_SCHEMA_URL = (
    "https://liquilens.in/protocol/"
    "liquilens-evidence-carrier-reference-v1.schema.json"
)
EVIDENCE_CARRIER_OPENLINEAGE_FACET_SCHEMA_URL = (
    "https://liquilens.in/protocol/openlineage/"
    "liquilens-evidence-facet.schema.json"
)
EVIDENCE_CARRIER_REFERENCE_SCHEMA = "liquilens.evidence-carrier-reference.v1"
EVIDENCE_CARRIER_MAX_BYTES = 1_048_576

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CARRIER_ID_RE = re.compile(r"^evidence_[0-9a-f]{24}$")
_PRODUCTS = frozenset({"liquilens", "seiche", "undertow", "palimpsest"})
_CLAIM_STATUSES = frozenset(
    {"observed", "derived", "structural", "research", "restricted", "unavailable"}
)
_RIGHTS_STATUSES = frozenset(
    {"licensed", "allowed", "metadata_only", "restricted", "unknown", "blocked"}
)
_RIGHTS_PERMISSIONS = frozenset({"ingest", "derive", "display", "redistribute"})
_VERIFIED_CARRIER_SEAL = object()
_FDC3_IDENTIFIER_NAMES = {
    "currency": "CURRENCY_ISOCODE",
    "cusip": "CUSIP",
    "figi": "FIGI",
    "isin": "ISIN",
    "lei": "LEI",
    "mic": "MIC",
    "miccode": "MIC",
    "permid": "PERMID",
    "sedol": "SEDOL",
    "ticker": "ticker",
}


class EvidenceCarrierError(ValueError):
    """Raised when a carrier is malformed, tampered with, or unsafe to export."""


class ExportDisposition(StrEnum):
    """The maximum disclosure permitted for one verified carrier."""

    FULL = "full"
    METADATA_ONLY = "metadata_only"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class EvidenceExportPolicy:
    """Versioned disclosure policy; it never grants financial authority."""

    version: str = "liquilens-evidence-export-strict-v1"
    expired_disposition: ExportDisposition = ExportDisposition.METADATA_ONLY
    missing_redistribution_disposition: ExportDisposition = (
        ExportDisposition.METADATA_ONLY
    )

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("policy version is required")
        if not isinstance(self.expired_disposition, ExportDisposition):
            raise TypeError("expired_disposition must be an ExportDisposition")
        if not isinstance(
            self.missing_redistribution_disposition, ExportDisposition
        ):
            raise TypeError(
                "missing_redistribution_disposition must be an ExportDisposition"
            )
        if self.expired_disposition is ExportDisposition.FULL:
            raise ValueError("an export policy cannot disclose expired payloads in full")
        if self.missing_redistribution_disposition is ExportDisposition.FULL:
            raise ValueError(
                "an export policy cannot widen missing redistribution permission"
            )


STRICT_EXPORT_POLICY = EvidenceExportPolicy()


@dataclass(frozen=True, slots=True)
class VerifiedEvidenceCarrier:
    """Immutable verification result with a policy-bounded export view."""

    carrier_json: str
    disposition: ExportDisposition
    reason_codes: tuple[str, ...]
    policy_version: str
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal is not _VERIFIED_CARRIER_SEAL:
            raise TypeError(
                "VerifiedEvidenceCarrier values must come from verify_evidence_carrier"
            )

    @property
    def carrier(self) -> dict[str, Any]:
        value = json.loads(self.carrier_json)
        if not isinstance(value, dict):  # pragma: no cover - constructor invariant
            raise TypeError("verified carrier root changed shape")
        return value

    def export_view(self) -> dict[str, Any]:
        """Return the most detailed view allowed by the verified policy."""

        if self.disposition is ExportDisposition.REJECT:
            reasons = ", ".join(self.reason_codes) or "policy_rejected"
            raise EvidenceCarrierError(f"carrier export is blocked: {reasons}")
        carrier = self.carrier
        if self.disposition is ExportDisposition.FULL:
            return carrier
        return {
            "schema": EVIDENCE_CARRIER_REFERENCE_SCHEMA,
            "carrier_id": carrier["carrier_id"],
            "record_hash": carrier["record_hash"],
            "canonicalization": carrier["canonicalization"],
            "producer": carrier["producer"],
            "subject": carrier["subject"],
            "claim": carrier["claim"],
            "clocks": carrier["clocks"],
            "sources": carrier["sources"],
            "rights": carrier["rights"],
            "authority": carrier["authority"],
            "payload_disclosed": False,
            "reason_codes": list(self.reason_codes),
            "policy_version": self.policy_version,
        }


def _json_copy(value: Any) -> Any:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise EvidenceCarrierError("carrier values must be finite JSON") from error
    if len(encoded) > EVIDENCE_CARRIER_MAX_BYTES:
        raise EvidenceCarrierError(
            f"carrier exceeds {EVIDENCE_CARRIER_MAX_BYTES} encoded bytes"
        )
    return json.loads(encoded)


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EvidenceCarrierError(f"{field_name} must be an object")
    copied = _json_copy(value)
    if not isinstance(copied, dict):  # pragma: no cover - Mapping input invariant
        raise TypeError("mapping normalized to a non-object")
    return copied


def _exact_keys(
    value: Mapping[str, Any],
    field_name: str,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    allowed = required | (optional or set())
    missing = required - set(value)
    extra = set(value) - allowed
    if missing:
        raise EvidenceCarrierError(
            f"{field_name} is missing fields: {', '.join(sorted(missing))}"
        )
    if extra:
        raise EvidenceCarrierError(
            f"{field_name} has unsupported fields: {', '.join(sorted(extra))}"
        )


def _string(value: Mapping[str, Any], field_name: str, parent: str) -> str:
    item = value.get(field_name)
    if not isinstance(item, str) or not item.strip():
        raise EvidenceCarrierError(f"{parent}.{field_name} must be a non-blank string")
    return item


def _utc(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise EvidenceCarrierError(f"{field_name} must be a UTC timestamp ending in Z")
    try:
        instant = datetime.fromisoformat(value)
    except ValueError as error:
        raise EvidenceCarrierError(f"{field_name} is not a valid timestamp") from error
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise EvidenceCarrierError(f"{field_name} must be timezone-aware")
    return instant.astimezone(UTC)


def _https_url(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise EvidenceCarrierError(f"{field_name} must be an HTTPS URL")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username is not None:
        raise EvidenceCarrierError(f"{field_name} must be an HTTPS URL without userinfo")
    return value


def _sha256(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise EvidenceCarrierError(f"{field_name} must be lowercase SHA-256")
    return value


def _string_list(
    value: Any,
    field_name: str,
    *,
    allowed: frozenset[str] | None = None,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise EvidenceCarrierError(f"{field_name} must be a non-empty string array")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise EvidenceCarrierError(f"{field_name} must contain non-blank strings")
    if len(value) != len(set(value)):
        raise EvidenceCarrierError(f"{field_name} must not contain duplicates")
    if allowed is not None and not set(value) <= allowed:
        raise EvidenceCarrierError(f"{field_name} contains an unsupported value")
    return tuple(value)


def _carrier_payload(carrier: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in carrier.items()
        if key not in {"carrier_id", "record_hash"}
    }


def _carrier_digest(carrier: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_hash_bytes(_carrier_payload(carrier))).hexdigest()


def _more_restrictive(
    current: ExportDisposition, candidate: ExportDisposition
) -> ExportDisposition:
    rank = {
        ExportDisposition.FULL: 0,
        ExportDisposition.METADATA_ONLY: 1,
        ExportDisposition.REJECT: 2,
    }
    return candidate if rank[candidate] > rank[current] else current


def _validate_shape(carrier: Mapping[str, Any]) -> None:
    _exact_keys(
        carrier,
        "carrier",
        {
            "schema_version",
            "canonicalization",
            "carrier_id",
            "record_hash",
            "producer",
            "subject",
            "claim",
            "clocks",
            "sources",
            "rights",
            "authority",
            "payload",
            "extensions",
        },
    )
    if carrier["schema_version"] != EVIDENCE_CARRIER_SCHEMA_VERSION:
        raise EvidenceCarrierError("unsupported evidence carrier schema_version")
    if carrier["canonicalization"] != EVIDENCE_CARRIER_CANONICALIZATION:
        raise EvidenceCarrierError("unsupported evidence carrier canonicalization")
    if not isinstance(carrier["carrier_id"], str) or _CARRIER_ID_RE.fullmatch(
        carrier["carrier_id"]
    ) is None:
        raise EvidenceCarrierError("carrier_id has an invalid shape")
    _sha256(carrier["record_hash"], "record_hash")

    producer = _mapping(carrier["producer"], "producer")
    _exact_keys(producer, "producer", {"name", "version", "endpoint"})
    if _string(producer, "name", "producer") not in _PRODUCTS:
        raise EvidenceCarrierError("producer.name is not a fleet product")
    _string(producer, "version", "producer")
    _https_url(producer["endpoint"], "producer.endpoint")

    subject = _mapping(carrier["subject"], "subject")
    _exact_keys(subject, "subject", {"kind", "name", "identifiers"})
    _string(subject, "kind", "subject")
    _string(subject, "name", "subject")
    identifiers = _mapping(subject["identifiers"], "subject.identifiers")
    if not identifiers:
        raise EvidenceCarrierError("subject.identifiers must not be empty")
    for key, item in identifiers.items():
        if not isinstance(key, str) or not key.strip() or any(char.isspace() for char in key):
            raise EvidenceCarrierError("subject identifier keys must be non-blank tokens")
        if not isinstance(item, str) or not item.strip():
            raise EvidenceCarrierError("subject identifier values must be non-blank strings")

    claim = _mapping(carrier["claim"], "claim")
    _exact_keys(claim, "claim", {"kind", "summary", "status"})
    _string(claim, "kind", "claim")
    _string(claim, "summary", "claim")
    if claim["status"] not in _CLAIM_STATUSES:
        raise EvidenceCarrierError("claim.status is unsupported")

    clocks = _mapping(carrier["clocks"], "clocks")
    _exact_keys(
        clocks,
        "clocks",
        {"event_time", "knowledge_time", "as_of"},
        {"expires_at"},
    )
    event_time = _utc(clocks["event_time"], "clocks.event_time")
    knowledge_time = _utc(clocks["knowledge_time"], "clocks.knowledge_time")
    as_of = _utc(clocks["as_of"], "clocks.as_of")
    if not event_time <= knowledge_time <= as_of:
        raise EvidenceCarrierError(
            "carrier clocks must satisfy event_time <= knowledge_time <= as_of"
        )
    if clocks.get("expires_at") is not None:
        expires_at = _utc(clocks["expires_at"], "clocks.expires_at")
        if expires_at <= knowledge_time:
            raise EvidenceCarrierError("clocks.expires_at must follow knowledge_time")

    sources = carrier["sources"]
    if not isinstance(sources, list) or not sources:
        raise EvidenceCarrierError("sources must be a non-empty array")
    source_ids: set[str] = set()
    for index, source_value in enumerate(sources):
        source = _mapping(source_value, f"sources[{index}]")
        _exact_keys(
            source,
            f"sources[{index}]",
            {"source_id", "publisher", "title", "url", "retrieved_at", "content_sha256"},
        )
        source_id = _string(source, "source_id", f"sources[{index}]")
        if source_id in source_ids:
            raise EvidenceCarrierError("source_id values must be unique")
        source_ids.add(source_id)
        _string(source, "publisher", f"sources[{index}]")
        _string(source, "title", f"sources[{index}]")
        _https_url(source["url"], f"sources[{index}].url")
        retrieved_at = _utc(
            source["retrieved_at"], f"sources[{index}].retrieved_at"
        )
        if retrieved_at < event_time:
            raise EvidenceCarrierError(
                f"sources[{index}].retrieved_at cannot precede event_time"
            )
        if retrieved_at > knowledge_time:
            raise EvidenceCarrierError(
                f"sources[{index}].retrieved_at cannot follow knowledge_time"
            )
        _sha256(source["content_sha256"], f"sources[{index}].content_sha256")

    rights = _mapping(carrier["rights"], "rights")
    _exact_keys(
        rights,
        "rights",
        {
            "status",
            "permissions",
            "license",
            "license_url",
            "attribution",
            "jurisdictions",
        },
    )
    if rights["status"] not in _RIGHTS_STATUSES:
        raise EvidenceCarrierError("rights.status is unsupported")
    _string_list(
        rights["permissions"], "rights.permissions", allowed=_RIGHTS_PERMISSIONS
    )
    _string_list(rights["jurisdictions"], "rights.jurisdictions")
    for field_name in ("license", "license_url", "attribution"):
        item = rights[field_name]
        if item is not None and (not isinstance(item, str) or not item.strip()):
            raise EvidenceCarrierError(f"rights.{field_name} must be null or non-blank")
    if rights["license_url"] is not None:
        _https_url(rights["license_url"], "rights.license_url")

    authority = _mapping(carrier["authority"], "authority")
    expected_authority = {
        "financial_authority": "none",
        "can_execute": False,
        "can_recommend": False,
        "is_credit_rating": False,
    }
    if authority != expected_authority:
        raise EvidenceCarrierError("authority must retain the all-false boundary")
    _mapping(carrier["payload"], "payload")
    _mapping(carrier["extensions"], "extensions")


def issue_evidence_carrier(
    *,
    producer: Mapping[str, Any],
    subject: Mapping[str, Any],
    claim: Mapping[str, Any],
    clocks: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
    rights: Mapping[str, Any],
    payload: Mapping[str, Any],
    extensions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Issue one deterministic carrier without granting export permission."""

    carrier: dict[str, Any] = {
        "schema_version": EVIDENCE_CARRIER_SCHEMA_VERSION,
        "canonicalization": EVIDENCE_CARRIER_CANONICALIZATION,
        "carrier_id": "evidence_" + "0" * 24,
        "record_hash": "0" * 64,
        "producer": _mapping(producer, "producer"),
        "subject": _mapping(subject, "subject"),
        "claim": _mapping(claim, "claim"),
        "clocks": _mapping(clocks, "clocks"),
        "sources": [_mapping(source, "source") for source in sources],
        "rights": _mapping(rights, "rights"),
        "authority": {
            "financial_authority": "none",
            "can_execute": False,
            "can_recommend": False,
            "is_credit_rating": False,
        },
        "payload": _mapping(payload, "payload"),
        "extensions": _mapping(extensions or {}, "extensions"),
    }
    carrier = _json_copy(carrier)
    _validate_shape(carrier)
    digest = _carrier_digest(carrier)
    carrier["carrier_id"] = f"evidence_{digest[:24]}"
    carrier["record_hash"] = digest
    _validate_shape(carrier)
    return carrier


def verify_evidence_carrier(
    value: Mapping[str, Any],
    *,
    evaluated_at: datetime | None = None,
    policy: EvidenceExportPolicy = STRICT_EXPORT_POLICY,
) -> VerifiedEvidenceCarrier:
    """Verify identity, clocks, rights, and the maximum safe disclosure."""

    if not isinstance(policy, EvidenceExportPolicy):
        raise TypeError("policy must be an EvidenceExportPolicy")
    carrier = _mapping(value, "carrier")
    _validate_shape(carrier)
    digest = _carrier_digest(carrier)
    if carrier["record_hash"] != digest:
        raise EvidenceCarrierError("record_hash does not match the carrier payload")
    if carrier["carrier_id"] != f"evidence_{digest[:24]}":
        raise EvidenceCarrierError("carrier_id does not match record_hash")

    instant = evaluated_at or datetime.now(UTC)
    if not isinstance(instant, datetime):
        raise TypeError("evaluated_at must be a datetime")
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")
    instant = instant.astimezone(UTC)

    reasons: list[str] = []
    disposition = ExportDisposition.FULL
    knowledge_time = _utc(carrier["clocks"]["knowledge_time"], "clocks.knowledge_time")
    as_of = _utc(carrier["clocks"]["as_of"], "clocks.as_of")
    if instant < knowledge_time:
        disposition = _more_restrictive(disposition, ExportDisposition.REJECT)
        reasons.append("evidence_not_yet_known")
    elif instant < as_of:
        disposition = _more_restrictive(disposition, ExportDisposition.REJECT)
        reasons.append("evidence_as_of_in_future")
    rights = carrier["rights"]
    rights_status = rights["status"]
    if rights_status in {"restricted", "unknown", "blocked"}:
        disposition = _more_restrictive(disposition, ExportDisposition.REJECT)
        reasons.append(f"rights_{rights_status}")
    else:
        if rights_status == "metadata_only":
            disposition = _more_restrictive(
                disposition, ExportDisposition.METADATA_ONLY
            )
            reasons.append("rights_metadata_only")
        if "redistribute" not in rights["permissions"]:
            disposition = _more_restrictive(
                disposition, policy.missing_redistribution_disposition
            )
            reasons.append("redistribution_not_permitted")
        if rights_status in {"licensed", "allowed"}:
            if rights["license"] is None and rights["license_url"] is None:
                disposition = _more_restrictive(
                    disposition, ExportDisposition.METADATA_ONLY
                )
                reasons.append("rights_license_missing")
            if rights["attribution"] is None:
                disposition = _more_restrictive(
                    disposition, ExportDisposition.METADATA_ONLY
                )
                reasons.append("rights_attribution_missing")

    if carrier["claim"]["status"] in {"restricted", "unavailable"}:
        disposition = _more_restrictive(
            disposition, ExportDisposition.METADATA_ONLY
        )
        reasons.append(f"claim_{carrier['claim']['status']}")

    expires_at_value = carrier["clocks"].get("expires_at")
    if expires_at_value is not None and instant >= _utc(
        expires_at_value, "clocks.expires_at"
    ):
        disposition = _more_restrictive(disposition, policy.expired_disposition)
        reasons.append("evidence_expired")

    serialized = json.dumps(
        carrier,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return VerifiedEvidenceCarrier(
        carrier_json=serialized,
        disposition=disposition,
        reason_codes=tuple(dict.fromkeys(reasons)),
        policy_version=policy.version,
        _seal=_VERIFIED_CARRIER_SEAL,
    )


def _view(verified: VerifiedEvidenceCarrier) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(verified, VerifiedEvidenceCarrier):
        raise TypeError("verified must be a VerifiedEvidenceCarrier")
    return verified.carrier, verified.export_view()


def _view_schema_url(verified: VerifiedEvidenceCarrier) -> str:
    return (
        EVIDENCE_CARRIER_SCHEMA_URL
        if verified.disposition is ExportDisposition.FULL
        else EVIDENCE_CARRIER_REFERENCE_SCHEMA_URL
    )


def to_fdc3_context(verified: VerifiedEvidenceCarrier) -> dict[str, Any]:
    """Project a carrier into an FDC3-compatible custom context object."""

    carrier, view = _view(verified)
    identifiers: dict[str, str] = {}
    for source_name, value in carrier["subject"]["identifiers"].items():
        lowered = source_name.lower()
        target_name = _FDC3_IDENTIFIER_NAMES.get(lowered)
        if target_name is None:
            target_name = (
                source_name
                if "." in source_name or source_name.isupper()
                else f"com.liquilens.{source_name}"
            )
        if target_name in identifiers and identifiers[target_name] != value:
            raise EvidenceCarrierError(
                f"subject identifiers conflict after FDC3 mapping: {target_name}"
            )
        identifiers[target_name] = value
    identifiers["liquilensEvidenceId"] = carrier["carrier_id"]
    return {
        "type": "com.liquilens.evidence",
        "name": carrier["claim"]["summary"],
        "id": identifiers,
        "evidenceSchema": _view_schema_url(verified),
        "evidence": view,
    }


def to_cloudevent(verified: VerifiedEvidenceCarrier) -> dict[str, Any]:
    """Project a carrier into CloudEvents 1.0 structured JSON."""

    carrier, view = _view(verified)
    identifiers = carrier["subject"]["identifiers"]
    subject = next(
        (
            identifiers[key]
            for key in ("lei", "figi", "isin", "ticker")
            if key in identifiers
        ),
        carrier["carrier_id"],
    )
    return {
        "specversion": "1.0",
        "id": carrier["carrier_id"],
        "source": carrier["producer"]["endpoint"],
        "type": f"com.liquilens.evidence.{carrier['claim']['kind']}.v1",
        "subject": subject,
        "time": carrier["clocks"]["knowledge_time"],
        "datacontenttype": "application/json",
        "dataschema": _view_schema_url(verified),
        "liquilensdisposition": verified.disposition.value,
        "liquilensrecordhash": carrier["record_hash"],
        "data": view,
    }


def to_otel_log(verified: VerifiedEvidenceCarrier) -> dict[str, Any]:
    """Map the economic and observation clocks to the OTel logical log model."""

    carrier, view = _view(verified)
    warn = verified.disposition is not ExportDisposition.FULL
    return {
        "timestamp": carrier["clocks"]["event_time"],
        "observed_timestamp": carrier["clocks"]["knowledge_time"],
        "event_name": f"liquilens.evidence.{carrier['claim']['kind']}",
        "severity_text": "WARN" if warn else "INFO",
        "body": view,
        "resource": {
            "service.name": carrier["producer"]["name"],
            "service.version": carrier["producer"]["version"],
        },
        "attributes": {
            "liquilens.evidence.carrier_id": carrier["carrier_id"],
            "liquilens.evidence.record_hash": carrier["record_hash"],
            "liquilens.evidence.rights_status": carrier["rights"]["status"],
            "liquilens.evidence.disposition": verified.disposition.value,
            "liquilens.evidence.schema_url": _view_schema_url(verified),
        },
    }


def to_openlineage_facet(verified: VerifiedEvidenceCarrier) -> dict[str, Any]:
    """Return a namespaced custom OpenLineage facet payload."""

    carrier, view = _view(verified)
    return {
        "_producer": carrier["producer"]["endpoint"],
        "_schemaURL": EVIDENCE_CARRIER_OPENLINEAGE_FACET_SCHEMA_URL,
        "carrierSchemaURL": _view_schema_url(verified),
        "carrier": view,
        "disposition": verified.disposition.value,
    }


def to_jsonld(verified: VerifiedEvidenceCarrier) -> dict[str, Any]:
    """Project a carrier into a compact W3C PROV-O JSON-LD graph."""

    carrier, view = _view(verified)
    return {
        "@context": {
            "prov": "http://www.w3.org/ns/prov#",
            "schema": "https://schema.org/",
            "dcterms": "http://purl.org/dc/terms/",
            "liquilens": "https://liquilens.in/ns/evidence#",
        },
        "@id": f"urn:liquilens:{carrier['carrier_id']}",
        "@type": "prov:Entity",
        "schema:name": carrier["claim"]["summary"],
        "schema:about": carrier["subject"],
        "prov:generatedAtTime": carrier["clocks"]["knowledge_time"],
        "prov:wasDerivedFrom": [
            {
                "@id": source["url"],
                "@type": "prov:Entity",
                "schema:name": source["title"],
                "schema:publisher": source["publisher"],
            }
            for source in carrier["sources"]
        ],
        "dcterms:rights": carrier["rights"],
        "liquilens:evidenceSchema": _view_schema_url(verified),
        "liquilens:evidence": view,
    }


def to_arrow_metadata(verified: VerifiedEvidenceCarrier) -> dict[bytes, bytes]:
    """Return Arrow/Parquet-compatible schema metadata without importing PyArrow."""

    carrier, view = _view(verified)
    return {
        b"liquilens.evidence.schema": _view_schema_url(verified).encode(),
        b"liquilens.evidence.carrier_id": carrier["carrier_id"].encode(),
        b"liquilens.evidence.record_hash": carrier["record_hash"].encode(),
        b"liquilens.evidence.disposition": verified.disposition.value.encode(),
        b"liquilens.evidence.carrier": json.dumps(
            view,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8"),
    }


def to_csl_json(verified: VerifiedEvidenceCarrier) -> dict[str, Any]:
    """Return a CSL-JSON dataset citation that retains the evidence identity."""

    carrier, _view_value = _view(verified)
    issued = _utc(carrier["clocks"]["event_time"], "clocks.event_time")
    accessed = _utc(carrier["clocks"]["knowledge_time"], "clocks.knowledge_time")
    return {
        "id": carrier["carrier_id"],
        "type": "dataset",
        "title": carrier["claim"]["summary"],
        "author": [{"literal": carrier["producer"]["name"]}],
        "version": carrier["producer"]["version"],
        "issued": {"date-parts": [[issued.year, issued.month, issued.day]]},
        "accessed": {"date-parts": [[accessed.year, accessed.month, accessed.day]]},
        "URL": carrier["producer"]["endpoint"],
        "note": (
            f"LiquiLens evidence carrier {carrier['carrier_id']} "
            f"sha256:{carrier['record_hash']}"
        ),
    }


def to_flat_row(verified: VerifiedEvidenceCarrier) -> dict[str, str]:
    """Flatten a carrier for Excel, CSV, SQL, and dataframe ingestion."""

    carrier, view = _view(verified)
    return {
        "carrier_id": carrier["carrier_id"],
        "record_hash": carrier["record_hash"],
        "producer": carrier["producer"]["name"],
        "producer_version": carrier["producer"]["version"],
        "subject_kind": carrier["subject"]["kind"],
        "subject_name": carrier["subject"]["name"],
        "subject_identifiers_json": json.dumps(
            carrier["subject"]["identifiers"], separators=(",", ":"), sort_keys=True
        ),
        "claim_kind": carrier["claim"]["kind"],
        "claim_status": carrier["claim"]["status"],
        "claim_summary": carrier["claim"]["summary"],
        "event_time": carrier["clocks"]["event_time"],
        "knowledge_time": carrier["clocks"]["knowledge_time"],
        "as_of": carrier["clocks"]["as_of"],
        "expires_at": carrier["clocks"].get("expires_at") or "",
        "rights_status": carrier["rights"]["status"],
        "rights_permissions_json": json.dumps(
            carrier["rights"]["permissions"], separators=(",", ":"), sort_keys=True
        ),
        "rights_license": carrier["rights"]["license"] or "",
        "rights_license_url": carrier["rights"]["license_url"] or "",
        "rights_attribution": carrier["rights"]["attribution"] or "",
        "redistribution_permitted": str(
            "redistribute" in carrier["rights"]["permissions"]
        ).lower(),
        "export_disposition": verified.disposition.value,
        "evidence_schema_url": _view_schema_url(verified),
        "payload_json": (
            json.dumps(view.get("payload", {}), separators=(",", ":"), sort_keys=True)
            if verified.disposition is ExportDisposition.FULL
            else ""
        ),
        "evidence_json": json.dumps(
            view, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ),
    }


def to_openfigi_mapping_jobs(verified: VerifiedEvidenceCarrier) -> list[dict[str, Any]]:
    """Build public OpenFIGI mapping jobs without making a network request."""

    carrier, _view_value = _view(verified)
    identifiers = carrier["subject"]["identifiers"]
    jobs: list[dict[str, Any]] = []
    normalized_identifiers = {
        key.lower().replace("_", ""): value for key, value in identifiers.items()
    }
    for key, id_type in (
        ("figi", "ID_BB_GLOBAL"),
        ("isin", "ID_ISIN"),
        ("sedol", "ID_SEDOL"),
        ("cusip", "ID_CUSIP"),
    ):
        if key in normalized_identifiers:
            jobs.append(
                {"idType": id_type, "idValue": normalized_identifiers[key]}
            )
    if "ticker" in normalized_identifiers:
        ticker_job: dict[str, Any] = {
            "idType": "TICKER",
            "idValue": normalized_identifiers["ticker"],
        }
        for field_name, aliases in (
            ("micCode", ("mic", "miccode")),
            ("currency", ("currency", "currencyisocode")),
            ("exchCode", ("exch", "exchcode")),
        ):
            value = next(
                (
                    normalized_identifiers[alias]
                    for alias in aliases
                    if alias in normalized_identifiers
                ),
                None,
            )
            if value is not None:
                ticker_job[field_name] = value
        jobs.append(ticker_job)
    if not jobs:
        raise EvidenceCarrierError(
            "subject has no FIGI, ISIN, SEDOL, CUSIP, or ticker mapping identifier"
        )
    return jobs
