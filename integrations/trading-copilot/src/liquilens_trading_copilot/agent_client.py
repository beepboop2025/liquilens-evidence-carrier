"""Explicit, non-retrying client for the private paper agent REST interface."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

import httpx

from .config import strict_json

_OPERATIONS = {
    "capabilities": ("GET", "/v1/capabilities"),
    "assess": ("POST", "/v1/assessments"),
    "submit": ("POST", "/v1/orders/submit"),
    "status": ("POST", "/v1/orders/status"),
    "reconcile": ("POST", "/v1/orders/reconcile"),
}


def read_agent_token(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_size > 512
        ):
            raise ValueError("private_token_file_required")
        token = os.read(fd, 513).decode("ascii").strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token) is None:
            raise ValueError("invalid_agent_token")
        return token
    finally:
        os.close(fd)


class AgentHostClient:
    """No automatic submit, retry, redirect, proxy or replacement intent.

    Persist the business intent ID before assessment and the assessment ID before
    submission. A transport failure can mean an order reached the broker; use
    status/reconcile on that same assessment, never a fresh intent as a retry.
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        url = httpx.URL(base_url)
        if (
            not url.host
            or url.scheme not in {"http", "https"}
            or (url.scheme == "http" and url.host not in {"127.0.0.1", "localhost"})
            or url.userinfo
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
            or re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token) is None
        ):
            raise ValueError("private_agent_endpoint_invalid")
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": "Bearer " + token},
            timeout=15,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def call(
        self, operation: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if operation not in _OPERATIONS:
            raise ValueError("unknown_agent_operation")
        method, route = _OPERATIONS[operation]
        try:
            with self._client.stream(method, route, json=payload) as response:
                if 300 <= response.status_code < 400:
                    raise ValueError("agent_redirect_rejected")
                if (
                    response.headers.get("content-type", "").split(";", 1)[0]
                    != "application/json"
                ):
                    raise ValueError("agent_json_response_required")
                raw = bytearray()
                for chunk in response.iter_bytes(chunk_size=16384):
                    raw.extend(chunk)
                    if len(raw) > 524288:
                        raise ValueError("agent_response_too_large")
                result = strict_json(bytes(raw))
                if not isinstance(result, dict) or result.get("mode") != "paper":
                    raise ValueError("invalid_paper_agent_response")
                return {"http_status": response.status_code, "result": result}
        except (httpx.HTTPError, ValueError, RecursionError):
            # Never expose a provider exception, credential or untrusted body.
            # The caller retains the same intent/assessment identity for lookup.
            return {
                "http_status": None,
                "result": {
                    "mode": "paper",
                    "error": "agent_response_unavailable",
                    "submission_outcome": "unknown",
                    "resubmit_allowed": False,
                },
            }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=tuple(_OPERATIONS))
    parser.add_argument("--url", default="http://127.0.0.1:8766")
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--intent-id")
    parser.add_argument("--side", choices=("buy", "sell"))
    parser.add_argument("--notional-usd", type=float)
    parser.add_argument("--assessment-id")
    args = parser.parse_args()
    proposal_args = (args.intent_id, args.side, args.notional_usd)
    if args.operation == "assess":
        if any(value is None for value in proposal_args) or args.assessment_id:
            parser.error("assess requires only intent-id, side and notional-usd")
        payload = dict(
            zip(("intent_id", "side", "notional_usd"), proposal_args, strict=True)
        )
    elif args.operation == "capabilities":
        if any(value is not None for value in proposal_args) or args.assessment_id:
            parser.error("capabilities takes no proposal or assessment")
        payload = None
    else:
        if not args.assessment_id or any(value is not None for value in proposal_args):
            parser.error("order operations require only assessment-id")
        payload = {"assessment_id": args.assessment_id}
    try:
        client = AgentHostClient(args.url, token=read_agent_token(args.token_file))
        try:
            result = client.call(args.operation, payload)
        finally:
            client.close()
    except Exception:
        result = {
            "http_status": None,
            "result": {"error": "private_agent_client_unavailable"},
        }
    print(json.dumps(result, allow_nan=False))
    return (
        0
        if (
            result["http_status"] == 200
            and not result["result"].get("error")
            and not result["result"].get("tool_error")
        )
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
