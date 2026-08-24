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


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceCarrierError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _read_json(path_text: str) -> dict[str, Any]:
    if path_text == "-":
        raw = sys.stdin.buffer.read(EVIDENCE_CARRIER_MAX_BYTES + 1)
    else:
        path = Path(path_text)
        if not path.is_file():
            raise EvidenceCarrierError(f"JSON input is not a file: {path}")
        if path.stat().st_size > EVIDENCE_CARRIER_MAX_BYTES:
            raise EvidenceCarrierError("JSON input exceeds the carrier byte limit")
        raw = path.read_bytes()
    if len(raw) > EVIDENCE_CARRIER_MAX_BYTES:
        raise EvidenceCarrierError("JSON input exceeds the carrier byte limit")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise EvidenceCarrierError("input is not valid unique-key UTF-8 JSON") from error
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


def _verify_paths(
    path_texts: list[str], *, evaluated_at: datetime
) -> dict[str, Any]:
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "verify-files":
            output = _verify_paths(
                args.inputs, evaluated_at=_evaluated_at(args.as_of)
            )
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
