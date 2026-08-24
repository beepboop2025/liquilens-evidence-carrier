FROM python:3.13.7-alpine3.22@sha256:9ba6d8cbebf0fb6546ae71f2a1c14f6ffd2fdab83af7fa5669734ef30ad48844 AS builder

ARG LIQUILENS_WHEEL_URL="https://github.com/beepboop2025/liquilens-evidence-carrier/releases/download/v0.14.0/liquilens_evidence-0.14.0-py3-none-any.whl"
ARG LIQUILENS_WHEEL_SHA256="f0162affab57307c8e20acf91dcefc33840f91e8cf9969a8d5ec8d8df860cd24"

RUN set -eu; \
    wget --quiet --output-document=/tmp/liquilens-evidence.whl \
      "${LIQUILENS_WHEEL_URL}"; \
    printf '%s  %s\n' \
      "${LIQUILENS_WHEEL_SHA256}" \
      /tmp/liquilens-evidence.whl \
      | sha256sum -c; \
    python -m venv /opt/liquilens; \
    /opt/liquilens/bin/python -m pip install \
      --disable-pip-version-check \
      --no-cache-dir \
      --no-compile \
      --no-deps \
      /tmp/liquilens-evidence.whl; \
    /opt/liquilens/bin/python -m pip uninstall --yes pip

FROM python:3.13.7-alpine3.22@sha256:9ba6d8cbebf0fb6546ae71f2a1c14f6ffd2fdab83af7fa5669734ef30ad48844

ARG VERSION="0.14.0"
ARG REVISION="8683351bd72c2a4b46d6913cd5e75c5536a410f1"
ARG CREATED="2026-08-24T12:43:34Z"
ARG README_URL="https://raw.githubusercontent.com/beepboop2025/liquilens-evidence-carrier/8683351bd72c2a4b46d6913cd5e75c5536a410f1/README.md"
ARG MAINTAINERS='[{"name":"LiquiLens maintainers","email":"beepboop2025@users.noreply.github.com"}]'

LABEL org.opencontainers.image.title="LiquiLens Evidence Carrier" \
      org.opencontainers.image.description="Offline verification and rights-bounded projection of financial evidence carriers." \
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
