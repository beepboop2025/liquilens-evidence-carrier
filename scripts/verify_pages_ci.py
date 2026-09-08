"""Bind Pages deployment to the existing Railway CI result for current main."""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

REPOSITORY = "beepboop2025/liquilens-evidence-carrier"
CONTEXT = "railway-automation - carrier-pr-ci"
BOT = {"id": 68434857, "login": "railway-app[bot]", "type": "Bot"}
PROJECT = "9c094747-8662-4ba7-8d6b-5a4fa7ca27eb"
SERVICE = "f0bb030f-4ffb-406e-8fa6-a9907248f4c5"
ENVIRONMENT = "61489b68-9216-48b2-a755-d43e89368acd"


def deployment_id(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "railway.com"
        or parsed.path != f"/project/{PROJECT}/service/{SERVICE}"
        or parsed.fragment
        or set(query) != {"id", "environmentId"}
        or query["environmentId"] != [ENVIRONMENT]
        or len(query["id"]) != 1
        or re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", query["id"][0])
        is None
    ):
        raise ValueError("Result is not from the configured Railway main CI service")
    return query["id"][0]


def verify_result(statuses: list[dict], *, source: str, current_main: str) -> dict:
    if re.fullmatch(r"[0-9a-f]{40}", source) is None or source != current_main:
        raise ValueError("Pages source must be the exact current main commit")
    # GitHub returns newest first. A later pending/failing main run revokes an old pass.
    for status in statuses:
        if status.get("context") != CONTEXT:
            continue
        creator = status.get("creator") or {}
        if any(creator.get(key) != value for key, value in BOT.items()):
            continue
        deployment = deployment_id(status.get("target_url") or "")
        if status.get("state") != "success":
            raise ValueError("Latest Railway main CI result has not passed")
        return {
            "source": source,
            "status_id": status["id"],
            "context": CONTEXT,
            "provider": BOT,
            "railway_deployment": deployment,
        }
    raise ValueError("No authenticated Railway main CI result exists for this source")


def api(path: str):
    request = urllib.request.Request(
        "https://api.github.com/repos/" + REPOSITORY + path,
        headers={
            "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        body = response.read(4 * 1024 * 1024 + 1)
    if len(body) > 4 * 1024 * 1024:
        raise ValueError("GitHub status response exceeded its bound")
    return json.loads(body)


def main() -> None:
    if (
        os.environ["GITHUB_REPOSITORY"] != REPOSITORY
        or os.environ["GITHUB_REF"] != "refs/heads/main"
    ):
        raise ValueError(
            "Pages publication only accepts this repository's main workflow"
        )
    source = os.environ["GITHUB_SHA"]
    if re.fullmatch(r"[0-9a-f]{40}", source) is None:
        raise ValueError("Expected an exact source commit")
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    if os.environ["GITHUB_EVENT_NAME"] == "status":
        if (
            event.get("sha") != source
            or event.get("context") != CONTEXT
            or event.get("state") != "success"
        ):
            raise ValueError(
                "Status event does not authorize the checked out main source"
            )
        deployment_id(event.get("target_url") or "")
    elif os.environ["GITHUB_EVENT_NAME"] != "workflow_dispatch":
        raise ValueError(
            "Pages must follow native CI completion or an explicit dispatch"
        )
    statuses = api(f"/commits/{source}/statuses?per_page=100")
    proof = verify_result(
        statuses,
        source=source,
        current_main=api("/git/ref/heads/main")["object"]["sha"],
    )
    print("RAILWAY_PAGES_ADMISSION_PASS " + json.dumps(proof, sort_keys=True))


if __name__ == "__main__":
    main()
