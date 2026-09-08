#!/usr/bin/env bash
set -euo pipefail
source_sha=${RAILWAY_GIT_COMMIT_SHA:-}
[[ "$source_sha" =~ ^[0-9a-f]{40}$ ]]
if [[ ${1:-} == --verify-build ]]; then
  test "$(cat /controller/passed-source)" = "$source_sha"
  printf 'RAILWAY_NIX_LINUX_PASS source=%s deployment=%s\n' "$source_sha" "${RAILWAY_DEPLOYMENT_ID:-unavailable}"
  exit 0
fi
test -z "${GITHUB_TOKEN:-}${GH_TOKEN:-}${RAILWAY_TOKEN:-}${RAILWAY_API_TOKEN:-}${CLOUDFLARE_API_TOKEN:-}"
test "$(uname -m)" = x86_64
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null
repository=https://github.com/beepboop2025/liquilens-evidence-carrier.git
git clone --quiet --no-checkout "$repository" /source
cd /source
git fetch --quiet origin "$source_sha"
git checkout --quiet --detach "$source_sha"
test "$(git rev-parse HEAD)" = "$source_sha"
base_sha=$(git ls-remote --exit-code origin refs/heads/main | cut -f1)
[[ "$base_sha" =~ ^[0-9a-f]{40}$ ]]
git fetch --quiet origin "$base_sha"
if ! git merge-base --is-ancestor "$base_sha" "$source_sha"; then
  printf 'RAILWAY_NIX_SOURCE_REJECTED source=%s base=%s rebase_required=1\n' "$source_sha" "$base_sha" >&2
  exit 1
fi
printf 'RAILWAY_NIX_SOURCE_ADMITTED source=%s base=%s system=%s isolation=Railway-container nix_sandbox=false\n' "$source_sha" "$base_sha" "$(uname -m)"
test "$(nix eval --raw --impure --expr builtins.currentSystem)" = x86_64-linux
nix flake check --print-build-logs
if ! git diff --exit-code -- flake.lock || test -n "$(git status --porcelain -- flake.lock)"; then
  printf '%s\n' 'flake.lock is missing or stale' >&2
  exit 1
fi
nix run . -- --help
nix run .#mcp -- --help
if [[ "$source_sha" == "$base_sha" ]]; then
  nix run "github:beepboop2025/liquilens-evidence-carrier/$source_sha" -- --help
  nix run "github:beepboop2025/liquilens-evidence-carrier/$source_sha#mcp" -- --help
  printf 'RAILWAY_NIX_PUBLIC_REFERENCE_PASS source=%s\n' "$source_sha"
fi
# A base movement invalidates strict PR admission even if the build itself passed.
test "$(git ls-remote --exit-code origin refs/heads/main | cut -f1)" = "$base_sha"
printf '%s\n' "$source_sha" > /controller/passed-source
printf 'RAILWAY_NIX_BUILD_PASS source=%s base=%s\n' "$source_sha" "$base_sha"
