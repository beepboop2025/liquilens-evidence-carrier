"""Offline, read-only OpenBB router for LiquiLens evidence verification."""

from datetime import UTC, datetime
from typing import Any, Literal

from liquilens_evidence.evidence_carrier import (
    STRICT_EXPORT_POLICY,
    EvidenceCarrierError,
    verify_evidence_carrier,
)
from openbb_core.app.model.obbject import OBBject
from openbb_core.app.router import Router
from openbb_core.provider.abstract.data import Data
from pydantic import ConfigDict, Field, ValidationError


class EvidenceCarrierRequest(Data):
    """A carrier supplied by the caller and an optional UTC evaluation time."""

    model_config = ConfigDict(extra="forbid", strict=True)

    carrier: dict[str, Any] = Field(
        description="Complete LiquiLens Evidence Carrier JSON object to verify."
    )
    evaluated_at: str | None = Field(
        default=None,
        description=(
            "Optional policy evaluation timestamp in UTC, ending in Z. "
            "Defaults to the current UTC instant."
        ),
    )


class AuthorityBoundary(Data):
    """Explicit non-authority boundary carried by every response."""

    model_config = ConfigDict(extra="forbid", strict=True)

    financial_authority: Literal["none"] = "none"
    can_execute: Literal[False] = False
    can_recommend: Literal[False] = False
    is_credit_rating: Literal[False] = False


class EvidenceVerificationResult(Data):
    """Bounded verification result without carrier payload disclosure."""

    model_config = ConfigDict(extra="forbid", strict=True)

    ok: bool
    carrier_id: str | None = None
    record_hash: str | None = None
    export_disposition: Literal["full", "metadata_only", "reject"]
    reason_codes: list[str]
    policy_version: str
    evaluated_at: str | None
    error: str | None = None
    payload_disclosed: Literal[False] = False
    data_provider: Literal[False] = False
    network_access: Literal[False] = False
    telemetry: Literal[False] = False
    authority: AuthorityBoundary = Field(default_factory=AuthorityBoundary)


router = Router(
    prefix="",
    description=(
        "Offline, read-only verification of caller-supplied LiquiLens evidence "
        "carriers. This router does not fetch market data, execute trades, make "
        "recommendations, or grant redistribution rights."
    ),
)


def _parse_evaluated_at(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if not value.endswith("Z"):
        raise EvidenceCarrierError("evaluated_at must be a UTC timestamp ending in Z")
    try:
        instant = datetime.fromisoformat(value)
    except ValueError as error:
        raise EvidenceCarrierError("evaluated_at is not a valid timestamp") from error
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise EvidenceCarrierError("evaluated_at must be timezone-aware")
    return instant.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _authority_boundary() -> AuthorityBoundary:
    return AuthorityBoundary(
        financial_authority="none",
        can_execute=False,
        can_recommend=False,
        is_credit_rating=False,
    )


def _invalid_result(*, reason_code: str, error: Exception) -> OBBject:
    return OBBject(
        results=EvidenceVerificationResult(
            ok=False,
            export_disposition="reject",
            reason_codes=[reason_code],
            policy_version=STRICT_EXPORT_POLICY.version,
            evaluated_at=None,
            error=str(error),
            payload_disclosed=False,
            data_provider=False,
            network_access=False,
            telemetry=False,
            authority=_authority_boundary(),
        )
    )


@router.command(
    methods=["POST"],
    openapi_extra={
        "mcp_config": {
            "description": (
                "Verify one already-supplied LiquiLens Evidence Carrier offline. "
                "Never use this command as market data, a recommendation, a rating, "
                "or execution authority."
            )
        }
    },
)
async def verify(data: Data) -> OBBject:
    """Verify carrier identity, clocks, rights, and safe export disposition."""

    try:
        request = EvidenceCarrierRequest.model_validate(data.model_dump())
    except ValidationError:
        return _invalid_result(
            reason_code="invalid_request",
            error=EvidenceCarrierError(
                "request must contain only carrier and optional evaluated_at"
            ),
        )

    try:
        evaluated_at = _parse_evaluated_at(request.evaluated_at)
    except EvidenceCarrierError as error:
        return _invalid_result(reason_code="invalid_evaluation_time", error=error)

    try:
        verified = verify_evidence_carrier(
            request.carrier,
            evaluated_at=evaluated_at,
        )
    except (EvidenceCarrierError, TypeError, ValueError) as error:
        return _invalid_result(reason_code="invalid_carrier", error=error)

    carrier = verified.carrier
    return OBBject(
        results=EvidenceVerificationResult(
            ok=True,
            carrier_id=carrier["carrier_id"],
            record_hash=carrier["record_hash"],
            export_disposition=verified.disposition.value,
            reason_codes=list(verified.reason_codes),
            policy_version=verified.policy_version,
            evaluated_at=_utc_text(evaluated_at),
            error=None,
            payload_disclosed=False,
            data_provider=False,
            network_access=False,
            telemetry=False,
            authority=_authority_boundary(),
        )
    )
