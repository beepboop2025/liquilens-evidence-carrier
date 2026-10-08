"""Authenticated REST host for one operator-owned paper agent account.

This is a private server-to-server API, not a public MCP authorization server.
Its CLI binds loopback only. Remote use requires an operator-managed TLS proxy.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
import stat
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from liquilens_alpaca_paper import (
    AlpacaPaperAgentTools,
    SQLiteAlpacaPaperSubmissionJournal,
)
from starlette.middleware.trustedhost import TrustedHostMiddleware
from trade_safety_gateway.app import HttpxUpstreamTransport

from .agent_service import AgentServiceError, PaperAgentService, agent_binding
from .config import SCOPED_PROFILE, CopilotConfig, PaperCredentials, strict_json
from .evidence import LiquiLensStrategyContext, OperatorEvidenceService
from .market import PaperAccountReader
from .runner import utc_now
from .state import CycleStore, operator_lock

_SCOPES = frozenset({"read", "assess", "submit", "reconcile"})
_ROUTES = {
    "/v1/assessments": ("assess", "assess"),
    "/v1/orders/submit": ("submit", "submit"),
    "/v1/orders/status": ("status", "read"),
    "/v1/orders/reconcile": ("reconcile", "reconcile"),
}


def private_json(path: Path) -> dict[str, Any]:
    """Read a bounded owner-only file without following a leaf symlink."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_size > 65536
        ):
            raise ValueError("private_agent_file_invalid")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = strict_json(handle.read(65537))
        if not isinstance(raw, dict):
            raise ValueError("private_agent_file_invalid")
        return raw
    finally:
        os.close(fd)


class BearerAuthority:
    """Locally provisioned tokens bound to the host's fixed agent identity."""

    def __init__(self, value: dict[str, Any], *, agent_id: str) -> None:
        if (
            set(value) != {"schema", "agent_id", "tokens"}
            or value["schema"] != "liquilens.agent-host-auth.v1"
            or value["agent_id"] != agent_id
            or not isinstance(value["tokens"], list)
            or not 1 <= len(value["tokens"]) <= 16
        ):
            raise ValueError("agent_auth_configuration_invalid")
        entries = []
        seen = set()
        for token in value["tokens"]:
            if (
                not isinstance(token, dict)
                or set(token) != {"sha256", "scopes"}
                or not isinstance(token["sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", token["sha256"]) is None
                or token["sha256"] in seen
                or not isinstance(token["scopes"], list)
                or not token["scopes"]
                or any(scope not in _SCOPES for scope in token["scopes"])
            ):
                raise ValueError("agent_auth_configuration_invalid")
            entries.append((token["sha256"], frozenset(token["scopes"])))
            seen.add(token["sha256"])
        self._entries = tuple(entries)
        self.agent_id = agent_id

    def authenticate(self, headers: list[str]) -> frozenset[str] | None:
        if len(headers) != 1 or len(headers[0]) > 512:
            return None
        prefix, separator, token = headers[0].partition(" ")
        if (
            prefix.lower() != "bearer"
            or not separator
            or re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token) is None
        ):
            return None
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        matched = None
        for expected, scopes in self._entries:
            if secrets.compare_digest(digest, expected):
                matched = scopes
        return matched


def _response(value: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(
        value,
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            **({"WWW-Authenticate": "Bearer"} if status == 401 else {}),
        },
    )


def _error(code: str, status: int) -> JSONResponse:
    return _response(
        {
            "schema": "liquilens.agent-host-error.v1",
            "mode": "paper",
            "error": code,
            "resubmit_allowed": False,
        },
        status,
    )


def create_agent_app(
    service_factory: Callable[..., Any],
    *,
    authority: BearerAuthority,
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost"),
    requests_per_minute: int = 60,
    monotonic: Callable[[], float] = time.monotonic,
) -> FastAPI:
    if (
        not allowed_hosts
        or any(not item or "*" in item or "/" in item for item in allowed_hosts)
        or type(requests_per_minute) is not int
        or not 1 <= requests_per_minute <= 600
    ):
        raise ValueError("agent_host_admission_configuration_invalid")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with service_factory() as service:
            if service.binding.agent_id != authority.agent_id:
                raise ValueError("authenticated_agent_binding_mismatch")
            app.state.service = service
            yield

    app = FastAPI(
        title="LiquiLens private paper agent host",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    admitted: deque[float] = deque()

    @app.middleware("http")
    async def boundary(request: Request, call_next: Any) -> Any:
        now = monotonic()
        while admitted and admitted[0] <= now - 60:
            admitted.popleft()
        if len(admitted) >= requests_per_minute:
            return _error("rate_limit_exceeded", 429)
        admitted.append(now)
        # No ambient browser credentials or permissive cross-origin interface.
        if request.headers.get("origin") is not None:
            return _error("browser_origin_not_supported", 403)
        scopes = authority.authenticate(request.headers.getlist("authorization"))
        if scopes is None:
            return _error("authentication_required", 401)
        if request.query_params:
            return _error("query_parameters_not_supported", 400)
        request.state.scopes = scopes
        try:
            return await call_next(request)
        except AgentServiceError as error:
            return _error(error.code, error.status)
        except Exception:
            # No raw provider/credential/traceback body reaches an agent.
            return _error("operator_service_unavailable", 503)

    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=list(allowed_hosts), www_redirect=False
    )

    @app.get("/v1/capabilities")
    async def capabilities(request: Request) -> JSONResponse:
        if "read" not in request.state.scopes:
            return _error("scope_required", 403)
        service = request.app.state.service
        return _response(
            {
                "schema": "liquilens.agent-host-capabilities.v1",
                "mode": "paper",
                "agent_id": authority.agent_id,
                "execution_enabled": service.enabled(),
                "instrument": "BTC/USD",
                "order_type": "market",
                "time_in_force": "IOC",
                "supported_notional_usd": [1000]
                if service.config.evidence_profile == SCOPED_PROFILE
                else None,
                "max_notional_usd": min(
                    1000,
                    service.config.policy["max_notional_usd"],
                    service.config.strategy.order_notional_usd,
                ),
                "evidence_profile": service.config.evidence_profile,
                "required_products": service.config.policy["required_products"],
                "live_execution_supported": False,
                "cash_transfers_supported": False,
                "credentials_in_tool_arguments": False,
                "source_policy_pass_is_order_authorization": False,
            }
        )

    async def operation(request: Request) -> JSONResponse:
        action, scope = _ROUTES[request.url.path]
        if scope not in request.state.scopes:
            return _error("scope_required", 403)
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/json"
            or request.headers.get("content-encoding", "identity") != "identity"
            or len(request.headers.getlist("content-length")) > 1
        ):
            return _error("unsupported_request_encoding", 415)
        body = bytearray()
        try:
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 4096:
                        return _error("request_too_large", 413)
            payload = strict_json(bytes(body))
        except (ValueError, RecursionError, TimeoutError):
            return _error("invalid_bounded_json", 400)
        try:
            return _response(await request.app.state.service.run(action, payload))
        except AgentServiceError as error:
            return _error(error.code, error.status)

    for route in _ROUTES:
        app.add_api_route(route, operation, methods=["POST"])
    return app


@asynccontextmanager
async def configured_service(
    config: CopilotConfig, credentials: PaperCredentials
) -> AsyncIterator[PaperAgentService]:
    """Keep the process lock, databases and fixed-origin clients for the lifespan."""
    config.validate()
    binding = agent_binding(config)
    with operator_lock(Path(config.state_dir)):
        store = CycleStore(Path(config.state_dir))
        journal = None
        transport = None
        try:
            journal = SQLiteAlpacaPaperSubmissionJournal(
                Path(config.state_dir) / "alpaca-submissions.sqlite3"
            )
            async with httpx.AsyncClient(
                follow_redirects=False, trust_env=False
            ) as client:
                if config.evidence_profile == SCOPED_PROFILE:
                    from .scoped import (
                        ScopedPaperEvidenceService,
                        ScopedUpstreamTransport,
                    )

                    transport = ScopedUpstreamTransport()
                    evidence = ScopedPaperEvidenceService(
                        transport,
                        binding=binding,
                        policy=config.policy,
                        hmac_key=credentials.hmac_key,
                        clock=utc_now,
                    )
                else:
                    transport = HttpxUpstreamTransport()
                    context = (
                        None
                        if config.liquilens_institution_slug is None
                        else LiquiLensStrategyContext(
                            config.liquilens_institution_slug,
                            required=config.liquilens_required,
                        )
                    )
                    evidence = OperatorEvidenceService(
                        transport,
                        binding=binding,
                        policy=config.policy,
                        hmac_key=credentials.hmac_key,
                        clock=utc_now,
                        liquilens_context=context,
                    )

                def tools_factory(**options: Any) -> AlpacaPaperAgentTools:
                    return AlpacaPaperAgentTools(
                        binding=binding,
                        submission_journal=journal,
                        hmac_key=credentials.hmac_key,
                        api_key=credentials.api_key,
                        secret_key=credentials.secret_key,
                        clock=utc_now,
                        **options,
                    )

                service = PaperAgentService(
                    config,
                    store,
                    evidence=evidence,
                    account_reader=PaperAccountReader(
                        client, credentials, binding.account_id
                    ),
                    journal=journal,
                    tools_factory=tools_factory,
                    hmac_key=credentials.hmac_key,
                    clock=utc_now,
                )
                try:
                    yield service
                finally:
                    await service.close()
        finally:
            try:
                if transport is not None:
                    await transport.aclose()
            finally:
                try:
                    if journal is not None:
                        journal.close()
                finally:
                    store.close()
