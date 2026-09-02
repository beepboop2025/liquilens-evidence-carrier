ARG VERSION="0.17.0"

FROM python:3.13.7-alpine3.22@sha256:9ba6d8cbebf0fb6546ae71f2a1c14f6ffd2fdab83af7fa5669734ef30ad48844 AS builder

ARG VERSION
ARG LIQUILENS_WHEEL_URL=""
ARG LIQUILENS_WHEEL_SHA256=""

WORKDIR /tmp/source
COPY LICENSE NOTICE README.md CHANGELOG.md pyproject.toml ./
COPY docs ./docs
COPY integrations ./integrations
COPY protocol ./protocol
COPY src ./src

RUN set -eu; \
    wheel_path="/tmp/liquilens_evidence-${VERSION}-py3-none-any.whl"; \
    if test -n "${LIQUILENS_WHEEL_URL}"; then \
      test -n "${LIQUILENS_WHEEL_SHA256}"; \
      wget --quiet --output-document="${wheel_path}" \
        "${LIQUILENS_WHEEL_URL}"; \
      printf '%s  %s\n' \
        "${LIQUILENS_WHEEL_SHA256}" \
        "${wheel_path}" \
        | sha256sum -c; \
    else \
      python -m pip wheel \
        --disable-pip-version-check \
        --no-cache-dir \
        --no-deps \
        --wheel-dir /tmp/wheels \
        /tmp/source; \
      cp "/tmp/wheels/liquilens_evidence-${VERSION}-py3-none-any.whl" \
        "${wheel_path}"; \
    fi; \
    python -m venv /opt/liquilens; \
    /opt/liquilens/bin/python -m pip install \
      --disable-pip-version-check \
      --no-cache-dir \
      --no-compile \
      --no-deps \
      "${wheel_path}"; \
    /opt/liquilens/bin/python -m pip uninstall --yes pip

FROM python:3.13.7-alpine3.22@sha256:9ba6d8cbebf0fb6546ae71f2a1c14f6ffd2fdab83af7fa5669734ef30ad48844

ARG VERSION
ARG REVISION="source-checkout"
ARG CREATED="1970-01-01T00:00:00Z"
ARG README_URL="https://github.com/beepboop2025/liquilens-evidence-carrier#readme"
ARG MAINTAINERS='[{"name":"LiquiLens maintainers","email":"beepboop2025@users.noreply.github.com"}]'

LABEL org.opencontainers.image.title="LiquiLens Evidence Carrier" \
      org.opencontainers.image.description="Offline verification of evidence carriers, fleet briefs, and trade-safety receipts." \
      org.opencontainers.image.created="${CREATED}" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/beepboop2025/liquilens-evidence-carrier" \
      org.opencontainers.image.documentation="https://liquilens.in/protocol/" \
      org.opencontainers.image.url="https://liquilens.in/protocol/" \
      org.opencontainers.image.vendor="LiquiLens" \
      org.opencontainers.image.base.name="docker.io/library/python:3.13.7-alpine3.22" \
      org.opencontainers.image.base.digest="sha256:9ba6d8cbebf0fb6546ae71f2a1c14f6ffd2fdab83af7fa5669734ef30ad48844" \
      io.artifacthub.package.readme-url="${README_URL}" \
      io.artifacthub.package.keywords="finance,evidence,provenance,mcp,fdc3,openlineage" \
      io.artifacthub.package.license="Apache-2.0" \
      io.artifacthub.package.maintainers="${MAINTAINERS}" \
      io.artifacthub.package.category="integration-delivery"

COPY --from=builder /opt/liquilens /opt/liquilens

RUN addgroup -S -g 65532 liquilens \
    && adduser -S -D -u 65532 -h /home/liquilens -G liquilens liquilens \
    && mkdir -p /evidence \
    && chown 65532:65532 /evidence

ENV PATH="/opt/liquilens/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE="1" \
    PYTHONUNBUFFERED="1"

WORKDIR /evidence
USER 65532:65532

ENTRYPOINT ["liquilens-evidence"]
CMD ["--help"]
