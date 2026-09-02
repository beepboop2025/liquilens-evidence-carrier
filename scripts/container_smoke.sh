#!/bin/sh
set -eu

image_ref="${1:?usage: container_smoke.sh IMAGE [VERSION]}"
expected_version="${2:-$(tr -d '\r\n' < VERSION)}"
smoke_dir="$(mktemp -d)"
trap 'rm -rf -- "$smoke_dir"' EXIT HUP INT TERM
chmod 0755 "$smoke_dir"

cp examples/descriptor.json "$smoke_dir/descriptor.json"
cp examples/trade-safety/receipt.paper.pass.json \
  "$smoke_dir/receipt.paper.pass.json"
chmod 0644 "$smoke_dir/descriptor.json" "$smoke_dir/receipt.paper.pass.json"

docker run --rm \
  --network none \
  --read-only \
  --mount "type=bind,src=$smoke_dir,dst=/evidence,readonly" \
  "$image_ref" \
  issue descriptor.json >"$smoke_dir/carrier.json"

docker run --rm \
  --network none \
  --read-only \
  --mount "type=bind,src=$smoke_dir,dst=/evidence,readonly" \
  "$image_ref" \
  verify carrier.json --as-of 2026-08-25T00:00:00Z \
  >"$smoke_dir/verification.json"

docker run --rm \
  --network none \
  --read-only \
  --mount "type=bind,src=$smoke_dir,dst=/evidence,readonly" \
  "$image_ref" \
  issue-brief --liquilens carrier.json --as-of 2026-08-25T00:00:00Z \
  >"$smoke_dir/fleet-brief.json"

docker run --rm \
  --network none \
  --read-only \
  --mount "type=bind,src=$smoke_dir,dst=/evidence,readonly" \
  "$image_ref" \
  verify-brief fleet-brief.json --as-of 2026-08-25T00:00:00Z \
  >"$smoke_dir/brief-verification.json"

docker run --rm \
  --network none \
  --read-only \
  --mount "type=bind,src=$smoke_dir,dst=/evidence,readonly" \
  "$image_ref" \
  verify-trade-safety receipt.paper.pass.json \
  --as-of 2026-09-02T12:00:30Z \
  >"$smoke_dir/trade-safety-verification.json"

python3 - \
  "$smoke_dir/carrier.json" \
  "$smoke_dir/verification.json" \
  "$smoke_dir/brief-verification.json" \
  "$smoke_dir/trade-safety-verification.json" <<'PY'
import json
import sys
from pathlib import Path

carrier = json.loads(Path(sys.argv[1]).read_text())
verification = json.loads(Path(sys.argv[2]).read_text())
brief_verification = json.loads(Path(sys.argv[3]).read_text())
trade_safety_verification = json.loads(Path(sys.argv[4]).read_text())
assert carrier["carrier_id"].startswith("evidence_")
assert verification["ok"] is True
assert verification["export_disposition"] == "full"
assert brief_verification["ok"] is True
assert brief_verification["states"] == {
    "liquilens": "full",
    "seiche": "missing",
    "undertow": "missing",
    "palimpsest": "missing",
}
assert trade_safety_verification["ok"] is True
assert trade_safety_verification["outcome"] == "pass"
assert trade_safety_verification["authenticated"] is False
assert trade_safety_verification["authority"]["can_execute"] is False
PY

cat >"$smoke_dir/requests.ndjson" <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"container-smoke","version":"1.0.0"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"verify_carrier","arguments":{"path":"carrier.json","evaluated_at":"2026-08-25T00:00:00Z"}}}
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"verify_fleet_brief","arguments":{"path":"fleet-brief.json","evaluated_at":"2026-08-25T00:00:00Z"}}}
{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"verify_trade_safety_receipt","arguments":{"path":"receipt.paper.pass.json","evaluated_at":"2026-09-02T12:00:30Z"}}}
EOF

docker run --rm -i \
  --network none \
  --read-only \
  --mount "type=bind,src=$smoke_dir,dst=/evidence,readonly" \
  --entrypoint liquilens-evidence-mcp \
  "$image_ref" \
  --root /evidence \
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
    "verify_trade_safety_receipt",
]
assert responses[2]["result"]["structuredContent"]["ok"] is True
assert responses[2]["result"]["structuredContent"]["authority"] == {
    "financial_authority": "none",
    "can_execute": False,
    "can_recommend": False,
    "is_credit_rating": False,
}
assert responses[3]["result"]["structuredContent"]["states"]["liquilens"] == "full"
assert responses[4]["result"]["structuredContent"]["outcome"] == "pass"
assert responses[4]["result"]["structuredContent"]["authority"]["can_execute"] is False
PY

IMAGE_UNDER_TEST="$image_ref" EXPECTED_VERSION="$expected_version" python3 <<'PY'
import datetime as dt
import json
import os
import re
import subprocess

image = os.environ["IMAGE_UNDER_TEST"]
expected_version = os.environ["EXPECTED_VERSION"]
details = json.loads(
    subprocess.check_output(["docker", "image", "inspect", image], text=True)
)[0]
labels = details["Config"]["Labels"]
required = {
    "io.artifacthub.package.readme-url",
    "org.opencontainers.image.created",
    "org.opencontainers.image.description",
    "org.opencontainers.image.documentation",
    "org.opencontainers.image.licenses",
    "org.opencontainers.image.revision",
    "org.opencontainers.image.source",
    "org.opencontainers.image.version",
    "io.artifacthub.package.keywords",
    "io.artifacthub.package.license",
    "io.artifacthub.package.maintainers",
}
assert not (required - labels.keys()), sorted(required - labels.keys())
assert labels["org.opencontainers.image.version"] == expected_version
assert labels["io.artifacthub.package.license"] == "Apache-2.0"
assert re.fullmatch(r"[0-9a-f]{40}", labels["org.opencontainers.image.revision"])
dt.datetime.fromisoformat(labels["org.opencontainers.image.created"].replace("Z", "+00:00"))
maintainers = json.loads(labels["io.artifacthub.package.maintainers"])
assert maintainers and maintainers[0]["name"]
assert maintainers[0]["email"]
assert details["Config"]["User"] == "65532:65532"
PY

docker run --rm \
  --network none \
  --read-only \
  --entrypoint python \
  "$image_ref" \
  -c "import importlib.metadata, os; assert os.getuid() != 0; assert importlib.metadata.version('liquilens-evidence') == '$expected_version'"
