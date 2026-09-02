"""Command-line issuance, verification, and projection for evidence carriers."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .evidence_carrier import (
    EVIDENCE_CARRIER_MAX_BYTES,
    EvidenceCarrierError,
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
from .fleet_brief import (
    FLEET_BRIEF_MAX_BYTES,
    FLEET_BRIEF_PRODUCTS,
    VerifiedFleetBrief,
    issue_fleet_brief,
    verify_fleet_brief,
)
from .trade_safety import (
    TRADE_SAFETY_MAX_BYTES,
    VerifiedTradeSafetyReceipt,
    issue_trade_safety_receipt,
    verify_trade_safety_receipt,
)

_HMAC_KEY_MAX_BYTES = 65_536


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceCarrierError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _read_json(
    path_text: str,
    *,
    max_bytes: int = EVIDENCE_CARRIER_MAX_BYTES,
    artifact_name: str = "carrier",
) -> dict[str, Any]:
    if path_text == "-":
        raw = sys.stdin.buffer.read(max_bytes + 1)
    else:
        path = Path(path_text)
        if not path.is_file():
            raise EvidenceCarrierError(f"JSON input is not a file: {path}")
        if path.stat().st_size > max_bytes:
            raise EvidenceCarrierError(
                f"JSON input exceeds the {artifact_name} byte limit"
            )
        raw = path.read_bytes()
    if len(raw) > max_bytes:
        raise EvidenceCarrierError(f"JSON input exceeds the {artifact_name} byte limit")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise EvidenceCarrierError(
            "input is not valid unique-key UTF-8 JSON"
        ) from error
    if not isinstance(value, dict):
        raise EvidenceCarrierError("JSON input root must be an object")
    return value


def _evaluated_at(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if not value.endswith("Z"):
        raise EvidenceCarrierError("--as-of must be a UTC timestamp ending in Z")
    try:
        instant = datetime.fromisoformat(value)
    except ValueError as error:
        raise EvidenceCarrierError("--as-of is not a valid timestamp") from error
    return instant.astimezone(UTC)


def _issue(descriptor: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "producer",
        "subject",
        "claim",
        "clocks",
        "sources",
        "rights",
        "payload",
    }
    optional = {"extensions"}
    missing = expected - set(descriptor)
    extra = set(descriptor) - expected - optional
    if missing:
        raise EvidenceCarrierError(
            f"issue descriptor is missing fields: {', '.join(sorted(missing))}"
        )
    if extra:
        raise EvidenceCarrierError(
            f"issue descriptor has unsupported fields: {', '.join(sorted(extra))}"
        )
    sources = descriptor["sources"]
    if not isinstance(sources, list):
        raise EvidenceCarrierError("sources must be an array")
    return issue_evidence_carrier(
        producer=descriptor["producer"],
        subject=descriptor["subject"],
        claim=descriptor["claim"],
        clocks=descriptor["clocks"],
        sources=sources,
        rights=descriptor["rights"],
        payload=descriptor["payload"],
        extensions=descriptor.get("extensions"),
    )


def _project(format_name: str, verified: VerifiedEvidenceCarrier) -> Any:
    if format_name == "fdc3":
        return to_fdc3_context(verified)
    if format_name == "cloudevent":
        return to_cloudevent(verified)
    if format_name == "otel":
        return to_otel_log(verified)
    if format_name == "openlineage":
        return to_openlineage_facet(verified)
    if format_name == "jsonld":
        return to_jsonld(verified)
    if format_name == "csl":
        return to_csl_json(verified)
    if format_name == "flat":
        return to_flat_row(verified)
    if format_name == "openfigi":
        return to_openfigi_mapping_jobs(verified)
    if format_name == "arrow":
        return {
            key.decode("utf-8"): value.decode("utf-8")
            for key, value in to_arrow_metadata(verified).items()
        }
    raise AssertionError(f"unhandled projection: {format_name}")


def _verification_result(verified: VerifiedEvidenceCarrier) -> dict[str, Any]:
    return {
        "ok": True,
        "carrier_id": verified.carrier["carrier_id"],
        "record_hash": verified.carrier["record_hash"],
        "export_disposition": verified.disposition.value,
        "reason_codes": list(verified.reason_codes),
        "policy_version": verified.policy_version,
    }


def _brief_verification_result(verified: VerifiedFleetBrief) -> dict[str, Any]:
    brief = verified.brief
    return {
        "ok": True,
        "brief_id": brief["brief_id"],
        "record_hash": brief["record_hash"],
        "evaluated_at": brief["evaluated_at"],
        "states": verified.states,
        "authority": brief["authority"],
    }


def _trade_safety_verification_result(
    verified: VerifiedTradeSafetyReceipt,
) -> dict[str, Any]:
    receipt = verified.receipt
    return {
        "ok": True,
        "receipt_id": receipt["receipt_id"],
        "record_hash": receipt["record_hash"],
        "evaluated_at": receipt["evaluated_at"],
        "expires_at": receipt["expires_at"],
        "outcome": verified.outcome.value,
        "policy_satisfied": verified.policy_satisfied,
        "authenticated": verified.authenticated,
        "reason_codes": list(receipt["decision"]["reason_codes"]),
        "authority": dict(receipt["authority"]),
    }


def _read_local_json(path_text: str, *, artifact_name: str) -> dict[str, Any]:
    if path_text == "-":
        raise EvidenceCarrierError(
            f"{artifact_name} requires an explicit local JSON path, not stdin"
        )
    return _read_json(
        path_text,
        max_bytes=TRADE_SAFETY_MAX_BYTES,
        artifact_name=artifact_name,
    )


def _read_hmac_key(path_text: str | None) -> bytes | None:
    if path_text is None:
        return None
    if path_text == "-":
        raise EvidenceCarrierError(
            "--hmac-key-file requires an explicit local path, not stdin"
        )
    path = Path(path_text)
    try:
        if not path.is_file():
            raise EvidenceCarrierError(f"HMAC key input is not a file: {path}")
        if path.stat().st_size > _HMAC_KEY_MAX_BYTES:
            raise EvidenceCarrierError("HMAC key input exceeds its byte limit")
        key = path.read_bytes()
    except OSError as error:
        raise EvidenceCarrierError(f"HMAC key input cannot be read: {path}") from error
    if not key:
        raise EvidenceCarrierError("HMAC key input must not be empty")
    return key


def _issue_trade_safety_from_paths(
    args: argparse.Namespace, evaluated_at: datetime
) -> dict[str, Any]:
    return issue_trade_safety_receipt(
        request=_read_local_json(args.request, artifact_name="trade-safety request"),
        evidence=_read_local_json(
            args.evidence, artifact_name="trade-safety evidence"
        ),
        policy=_read_local_json(args.policy, artifact_name="trade-safety policy"),
        broker_preview=_read_local_json(
            args.broker_preview, artifact_name="broker preview reference"
        ),
        issuer=_read_local_json(args.issuer, artifact_name="trade-safety issuer"),
        evaluated_at=evaluated_at,
        ttl_seconds=args.ttl_seconds,
        hmac_key=_read_hmac_key(args.hmac_key_file),
        hmac_key_id=args.hmac_key_id,
    )


def _issue_brief_from_paths(
    args: argparse.Namespace, evaluated_at: datetime
) -> dict[str, Any]:
    carriers: dict[str, dict[str, Any] | None] = {}
    for product in FLEET_BRIEF_PRODUCTS:
        path_text = getattr(args, product)
        if path_text is None:
            carriers[product] = None
            continue
        if path_text == "-":
            raise EvidenceCarrierError(
                f"--{product} requires an explicit local carrier path, not stdin"
            )
        try:
            carriers[product] = _read_json(path_text)
        except EvidenceCarrierError as error:
            raise EvidenceCarrierError(f"--{product} {path_text}: {error}") from error
    return issue_fleet_brief(carriers=carriers, evaluated_at=evaluated_at)


def _verify_paths(path_texts: list[str], *, evaluated_at: datetime) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for path_text in path_texts:
        try:
            verified = verify_evidence_carrier(
                _read_json(path_text), evaluated_at=evaluated_at
            )
        except (EvidenceCarrierError, TypeError, ValueError) as error:
            raise EvidenceCarrierError(f"{path_text}: {error}") from error
        result = _verification_result(verified)
        result["input"] = path_text
        results.append(result)
    return {"ok": True, "verified": results}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="liquilens-evidence",
        description=(
            "Issue, verify, and project transport-neutral LiquiLens evidence carriers."
        ),
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    issue = subcommands.add_parser("issue", help="issue a carrier from a descriptor")
    issue.add_argument("input", help="descriptor JSON path, or - for stdin")

    verify = subcommands.add_parser("verify", help="verify carrier identity and policy")
    verify.add_argument("input", help="carrier JSON path, or - for stdin")
    verify.add_argument("--as-of", help="UTC policy evaluation time ending in Z")

    verify_files = subcommands.add_parser(
        "verify-files", help="verify one or more carrier JSON files"
    )
    verify_files.add_argument("inputs", nargs="+", help="carrier JSON paths")
    verify_files.add_argument("--as-of", help="UTC policy evaluation time ending in Z")

    convert = subcommands.add_parser("convert", help="project a verified carrier")
    convert.add_argument("input", help="carrier JSON path, or - for stdin")
    convert.add_argument(
        "--format",
        required=True,
        choices=(
            "fdc3",
            "cloudevent",
            "otel",
            "openlineage",
            "jsonld",
            "csl",
            "flat",
            "openfigi",
            "arrow",
        ),
    )
    convert.add_argument("--as-of", help="UTC policy evaluation time ending in Z")

    issue_brief = subcommands.add_parser(
        "issue-brief",
        help="issue a fleet brief from explicit local native carrier paths",
    )
    for product in FLEET_BRIEF_PRODUCTS:
        issue_brief.add_argument(
            f"--{product}",
            metavar="PATH",
            help=f"local {product} carrier JSON path; omit to record missing",
        )
    issue_brief.add_argument(
        "--as-of",
        required=True,
        help="mandatory UTC policy evaluation time ending in Z",
    )

    verify_brief = subcommands.add_parser(
        "verify-brief", help="verify a content-addressed fleet brief"
    )
    verify_brief.add_argument("input", help="fleet brief JSON path, or - for stdin")
    verify_brief.add_argument(
        "--as-of",
        required=True,
        help="UTC timestamp that must match the brief evaluation clock",
    )

    issue_trade_safety = subcommands.add_parser(
        "issue-trade-safety",
        help="issue an order-bound receipt from explicit local JSON inputs",
    )
    issue_trade_safety.add_argument(
        "--request", required=True, metavar="PATH", help="local request JSON path"
    )
    issue_trade_safety.add_argument(
        "--evidence", required=True, metavar="PATH", help="local evidence JSON path"
    )
    issue_trade_safety.add_argument(
        "--policy", required=True, metavar="PATH", help="local policy JSON path"
    )
    issue_trade_safety.add_argument(
        "--broker-preview",
        required=True,
        metavar="PATH",
        help="local broker preview reference JSON path",
    )
    issue_trade_safety.add_argument(
        "--issuer", required=True, metavar="PATH", help="local issuer JSON path"
    )
    issue_trade_safety.add_argument(
        "--as-of",
        required=True,
        help="mandatory UTC policy evaluation time ending in Z",
    )
    issue_trade_safety.add_argument(
        "--ttl-seconds",
        type=int,
        default=60,
        help="receipt lifetime in seconds, capped at 3600 (default: 60)",
    )
    issue_trade_safety.add_argument(
        "--hmac-key-file",
        metavar="PATH",
        help="optional local file whose raw bytes authenticate the receipt",
    )
    issue_trade_safety.add_argument(
        "--hmac-key-id",
        help="required external key identifier when --hmac-key-file is supplied",
    )

    verify_trade_safety = subcommands.add_parser(
        "verify-trade-safety",
        help="verify one order-bound trade-safety receipt from a local JSON path",
    )
    verify_trade_safety.add_argument("input", help="local receipt JSON path")
    verify_trade_safety.add_argument(
        "--as-of",
        required=True,
        help="mandatory UTC verification time ending in Z",
    )
    verify_trade_safety.add_argument(
        "--hmac-key-file",
        metavar="PATH",
        help="local file whose raw bytes authenticate an HMAC receipt",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "issue-brief":
            output = _issue_brief_from_paths(args, _evaluated_at(args.as_of))
        elif args.command == "verify-brief":
            brief = _read_json(
                args.input,
                max_bytes=FLEET_BRIEF_MAX_BYTES,
                artifact_name="fleet brief",
            )
            output = _brief_verification_result(
                verify_fleet_brief(brief, evaluated_at=_evaluated_at(args.as_of))
            )
        elif args.command == "issue-trade-safety":
            output = _issue_trade_safety_from_paths(
                args, _evaluated_at(args.as_of)
            )
        elif args.command == "verify-trade-safety":
            receipt = _read_local_json(
                args.input, artifact_name="trade-safety receipt"
            )
            output = _trade_safety_verification_result(
                verify_trade_safety_receipt(
                    receipt,
                    evaluated_at=_evaluated_at(args.as_of),
                    hmac_key=_read_hmac_key(args.hmac_key_file),
                )
            )
        elif args.command == "verify-files":
            output = _verify_paths(args.inputs, evaluated_at=_evaluated_at(args.as_of))
        else:
            value = _read_json(args.input)
            if args.command == "issue":
                output = _issue(value)
            else:
                verified = verify_evidence_carrier(
                    value, evaluated_at=_evaluated_at(args.as_of)
                )
                if args.command == "verify":
                    output = _verification_result(verified)
                else:
                    output = _project(args.format, verified)
    except (EvidenceCarrierError, TypeError, ValueError) as error:
        print(f"liquilens-evidence: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            output,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
