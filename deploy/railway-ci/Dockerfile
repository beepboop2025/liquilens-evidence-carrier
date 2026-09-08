FROM node:24-bookworm-slim AS node
FROM python:3.13-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && apt-get update && apt-get install -y --no-install-recommends \
       bash ca-certificates curl git openssh-client tini unzip \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.12.5 PyYAML==6.0.3
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_PYTHON_PREFERENCE=only-managed \
    PYTHONUNBUFFERED=1 OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 \
    DO_NOT_TRACK=1 UV_LINK_MODE=copy
RUN uv python install 3.11 3.12 3.13 3.14
COPY . /source
WORKDIR /source
ARG RAILWAY_GIT_COMMIT_SHA
ARG RAILWAY_DEPLOYMENT_ID
RUN timeout --signal=TERM --kill-after=30 2700 python deploy/railway-ci/run.py
ENTRYPOINT ["/usr/bin/tini", "-s", "--"]
CMD ["python", "deploy/railway-ci/run.py", "--verify-build"]
