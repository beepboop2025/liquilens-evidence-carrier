# Build from the evidence-carrier repository root so the gateway and exact
# protocol implementation are installed together from the committed lock.
FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7 AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_PROJECT_ENVIRONMENT=/opt/trade-safety

WORKDIR /build/evidence-carrier
COPY . .

RUN python -m pip install --no-cache-dir uv==0.12.5 \
    && uv sync \
      --project integrations/trade-safety-gateway \
      --locked \
      --no-dev \
      --no-editable \
    && uv pip check --python /opt/trade-safety/bin/python

FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

ARG SOURCE_REVISION="source-checkout"
ARG CREATED="1970-01-01T00:00:00Z"
ARG CORE_VERSION="0.19.0"
ARG ISSUER_ENDPOINT="https://liquilens.in/trade-safety-gateway"

LABEL org.opencontainers.image.title="LiquiLens Trade Safety Gateway" \
      org.opencontainers.image.description="Read-only sandbox for order-bound Trade Safety Receipts." \
      org.opencontainers.image.created="${CREATED}" \
      org.opencontainers.image.version="0.2.1" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/beepboop2025/liquilens-evidence-carrier" \
      org.opencontainers.image.documentation="https://liquilens.in/protocol/" \
      org.opencontainers.image.base.name="docker.io/library/python:3.12.11-slim-bookworm" \
      org.opencontainers.image.base.digest="sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7" \
      io.liquilens.evidence.core.version="${CORE_VERSION}" \
      io.liquilens.trade-safety.authority="read-only-hash-only-sandbox"

ENV PATH="/opt/trade-safety/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRADE_SAFETY_SOURCE_REVISION="${SOURCE_REVISION}" \
    TRADE_SAFETY_BUILD_CREATED="${CREATED}" \
    TRADE_SAFETY_ISSUER_ENDPOINT="${ISSUER_ENDPOINT}"

COPY --from=builder /opt/trade-safety /opt/trade-safety

WORKDIR /app
USER 10001:10001
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).read()"]

CMD ["uvicorn", "trade_safety_gateway.asgi:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log", "--workers", "1"]
