#!/bin/sh
set -eu

image_ref="${1:?usage: mcp_container_smoke.sh MCP_IMAGE [VERSION]}"
expected_version="${2:-0.15.0}"
cli_image="${LIQUILENS_CLI_IMAGE:-ghcr.io/beepboop2025/liquilens-evidence-carrier@sha256:9ec0646269357e971a67e88c8076c3c52c1561b094c1f2093ee19882a33294d1}"
expected_base_digest="${LIQUILENS_EXPECTED_BASE_DIGEST:-sha256:9ec0646269357e971a67e88c8076c3c52c1561b094c1f2093ee19882a33294d1}"
expected_release_revision="${LIQUILENS_EXPECTED_RELEASE_REVISION:-8683351bd72c2a4b46d6913cd5e75c5536a410f1}"
smoke_dir="$(mktemp -d)"
trap 'rm -rf -- "$smoke_dir"' EXIT HUP INT TERM
chmod 0755 "$smoke_dir"

cp examples/descriptor.json "$smoke_dir/descriptor.json"
cp examples/fleet-brief/mixed-states.fleet-brief.json "$smoke_dir/fleet-brief.json"
chmod 0644 "$smoke_dir/descriptor.json"
chmod 0644 "$smoke_dir/fleet-brief.json"

if test -z "${LIQUILENS_CLI_IMAGE:-}"; then
  docker pull --platform linux/amd64 "$cli_image"
fi
docker run --rm \
  --network none \
  --read-only \
  --mount "type=bind,src=$smoke_dir,dst=/evidence,readonly" \
  "$cli_image" \
  issue descriptor.json >"$smoke_dir/carrier.json"
chmod 0644 "$smoke_dir/carrier.json"

cat >"$smoke_dir/requests.ndjson" <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"mcp-container-smoke","version":"1.0.0"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"verify_carrier","arguments":{"path":"carrier.json","evaluated_at":"2026-08-25T00:00:00Z"}}}
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"verify_carrier","arguments":{"path":"../etc/passwd","evaluated_at":"2026-08-25T00:00:00Z"}}}
{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"verify_fleet_brief","arguments":{"path":"fleet-brief.json","evaluated_at":"2026-08-25T00:00:00Z"}}}
EOF

docker run --rm -i \
  --network none \
  --read-only \
  --mount "type=bind,src=$smoke_dir,dst=/evidence,readonly" \
  "$image_ref" \
  <"$smoke_dir/requests.ndjson" >"$smoke_dir/responses.ndjson"

python3 - "$smoke_dir/responses.ndjson" <<'PY'
import json
import sys
from pathlib import Path

responses = [json.loads(line) for line in Path(sys.argv[1]).read_text().splitlines()]
assert [response["id"] for response in responses] == [1, 2, 3, 4, 5]
assert responses[0]["result"]["serverInfo"]["name"] == (
    "io.github.beepboop2025/liquilens-evidence-carrier"
)
assert [tool["name"] for tool in responses[1]["result"]["tools"]] == [
    "verify_carrier",
    "project_carrier",
    "verify_fleet_brief",
]
verified = responses[2]["result"]["structuredContent"]
assert verified["ok"] is True
assert verified["source_path"] == "/evidence/carrier.json"
assert verified["authority"] == {
    "financial_authority": "none",
    "can_execute": False,
    "can_recommend": False,
    "is_credit_rating": False,
}
rejected = responses[3]["result"]["structuredContent"]
assert rejected["ok"] is False
assert rejected["error"]["code"] == "carrier_input_rejected"
assert "escapes the configured root" in rejected["error"]["message"]
brief = responses[4]["result"]["structuredContent"]
assert brief["ok"] is True
assert brief["states"] == {
    "liquilens": "full",
    "seiche": "metadata_only",
    "undertow": "unavailable",
    "palimpsest": "rejected",
}
PY

IMAGE_UNDER_TEST="$image_ref" \
EXPECTED_VERSION="$expected_version" \
EXPECTED_BASE_DIGEST="$expected_base_digest" \
EXPECTED_RELEASE_REVISION="$expected_release_revision" \
python3 <<'PY'
import json
import os
import subprocess

image = os.environ["IMAGE_UNDER_TEST"]
details = json.loads(
    subprocess.check_output(["docker", "image", "inspect", image], text=True)
)[0]
config = details["Config"]
labels = config["Labels"]
assert config["User"] == "65532:65532"
assert config["Entrypoint"] == ["liquilens-evidence-mcp"]
assert config["Cmd"] == ["--root", "/evidence"]
assert labels["org.opencontainers.image.version"] == os.environ["EXPECTED_VERSION"]
assert labels["org.opencontainers.image.licenses"] == "Apache-2.0"
assert labels["org.opencontainers.image.base.digest"] == os.environ["EXPECTED_BASE_DIGEST"]
assert labels["io.liquilens.evidence.release.revision"] == os.environ[
    "EXPECTED_RELEASE_REVISION"
]
PY
