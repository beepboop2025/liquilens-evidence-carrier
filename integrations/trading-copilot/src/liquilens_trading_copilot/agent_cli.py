"""Initialize or serve the private, disabled-by-default paper agent host."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
from dataclasses import asdict, replace
from pathlib import Path

from .agent_host import (
    BearerAuthority,
    configured_service,
    create_agent_app,
    private_json,
)
from .cli import _exclusive_file
from .config import (
    SCOPED_PROFILE,
    CopilotConfig,
    PaperCredentials,
    load_config,
    load_secret_file,
    scoped_policy,
)
from .state import prepare_state


def initialize(state_dir: Path) -> dict:
    prepare_state(state_dir)
    names = (
        "config.json",
        "paper.env",
        "agent-auth.json",
        "agent-read.token",
        "agent-execution.token",
    )
    if any(os.path.lexists(state_dir / name) for name in names):
        raise ValueError("agent_configuration_already_exists")
    config = replace(
        CopilotConfig(state_dir=str(state_dir)),
        agent_id="liquilens-external-agent",
        strategy_id="external-paper-proposal-v1",
        evidence_profile=SCOPED_PROFILE,
        policy=scoped_policy(),
    )
    tokens = [secrets.token_urlsafe(32), secrets.token_urlsafe(32)]
    authority = {
        "schema": "liquilens.agent-host-auth.v1",
        "agent_id": config.agent_id,
        "tokens": [
            {"sha256": hashlib.sha256(token.encode()).hexdigest(), "scopes": scopes}
            for token, scopes in zip(
                tokens,
                (["read", "assess"], ["read", "assess", "submit", "reconcile"]),
                strict=True,
            )
        ],
    }
    _exclusive_file(state_dir / names[0], json.dumps(asdict(config), indent=2) + "\n")
    _exclusive_file(
        state_dir / names[1],
        "ALPACA_PAPER_API_KEY=\nALPACA_PAPER_SECRET_KEY=\nCOPILOT_PAPER_HMAC_KEY="
        + secrets.token_hex(32)
        + "\n",
    )
    _exclusive_file(state_dir / names[2], json.dumps(authority, indent=2) + "\n")
    for name, token in zip(names[3:], tokens, strict=True):
        _exclusive_file(state_dir / name, token + "\n")
    return {
        "mode": "paper",
        "status": "initialized_disabled",
        "state_dir": str(state_dir),
        "files": list(names),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "doctor", "serve"))
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--allowed-host", action="append", default=[])
    parser.add_argument("--undertow-token-file", type=Path)
    parser.add_argument(
        "--require-disabled",
        action="store_true",
        help="serve: refuse startup unless the loaded paper configuration is disabled",
    )
    args = parser.parse_args()
    if args.undertow_token_file is not None and args.command != "serve":
        parser.error("--undertow-token-file applies only to serve")
    if args.require_disabled and args.command != "serve":
        parser.error("--require-disabled applies only to serve")
    state_dir = args.state_dir.expanduser().resolve()
    try:
        if args.command == "init":
            print(json.dumps(initialize(state_dir)))
            return 0
        if args.command == "doctor":
            from .agent_diagnostics import diagnose_agent_host

            result = diagnose_agent_host(state_dir)
            print(json.dumps(result))
            return 0 if result["local_configuration_ready"] else 2
        if not 1024 <= args.port <= 65535:
            raise ValueError("invalid_private_host_port")
        private_json(state_dir / "config.json")
        config = load_config(state_dir / "config.json")
        if Path(config.state_dir) != state_dir:
            raise ValueError("configured_state_directory_mismatch")
        if args.require_disabled and config.enabled is not False:
            raise ValueError("disabled_host_configuration_required")
        credentials = PaperCredentials.from_environment(
            load_secret_file(state_dir / "paper.env")
        )
        authority = BearerAuthority(
            private_json(state_dir / "agent-auth.json"), agent_id=config.agent_id
        )
        app = create_agent_app(
            lambda: configured_service(
                config, credentials, undertow_token_file=args.undertow_token_file
            ),
            authority=authority,
            allowed_hosts=tuple(["127.0.0.1", "localhost", *args.allowed_host]),
        )
        import uvicorn

        uvicorn.run(
            app,
            host="127.0.0.1",
            port=args.port,
            workers=1,
            access_log=False,
            proxy_headers=False,
        )
    except Exception:
        # Secret/config/provider exceptions may include credential-bearing bodies.
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": "private_agent_configuration_or_service_unavailable",
                }
            )
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
