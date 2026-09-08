FROM nixos/nix:2.35.2@sha256:7a007c766426c1877758ddc5cb87a965ac131fc78c582ce0083d922d51ae945c
RUN printf '%s\n' 'experimental-features = nix-command flakes' 'sandbox = false' \
    'accept-flake-config = false' 'max-jobs = 2' 'cores = 2' >> /etc/nix/nix.conf
COPY deploy/railway-ci/nix-linux/run.sh /controller/run.sh
ARG RAILWAY_GIT_COMMIT_SHA
ARG RAILWAY_DEPLOYMENT_ID
RUN timeout --signal=TERM --kill-after=30 2700 bash /controller/run.sh
CMD ["bash", "/controller/run.sh", "--verify-build"]
