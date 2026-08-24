"""Conservative example policy layered over the safe core default."""

from liquilens_evidence import EvidenceExportPolicy, ExportDisposition


def policy_for_claim(
    *, product: str, claim_kind: str
) -> EvidenceExportPolicy:
    """Choose whether expired evidence remains metadata-visible or is rejected.

    The core never allows full disclosure after expiry.  This hook decides the
    remaining UX trade-off: metadata-only keeps citations and discovery alive;
    rejection is stricter for fast market observations where even old labels
    may be misleading.
    """

    fast_market_claims = {
        "funding_context",
        "liquidity_observation",
        "market_structure_observation",
    }
    reject_expired = product in {"seiche", "undertow"} and claim_kind in fast_market_claims
    return EvidenceExportPolicy(
        version="liquilens-evidence-export-example-v1",
        expired_disposition=(
            ExportDisposition.REJECT
            if reject_expired
            else ExportDisposition.METADATA_ONLY
        ),
    )


__all__ = [
    "EvidenceExportPolicy",
    "ExportDisposition",
    "policy_for_claim",
]
