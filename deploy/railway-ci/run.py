"""Run the repository's portable workflow commands in isolated Railway jobs."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

SOURCE = Path(__file__).resolve().parents[2]
WORKFLOWS = {
    "ci.yml": ("test", "static-and-package", "action-smoke"),
    "alpaca-paper.yml": ("test-and-package",),
    "trading-copilot.yml": ("test-and-package",),
    "openbb.yml": ("router",),
    "vscode.yml": ("vscode",),
    "notebook.yml": ("execute-notebook",),
    "agent-skill.yml": ("discover",),
    "pages.yml": ("verify-browser",),
}
SETUP_ACTIONS = {
    "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
    "actions/setup-node@2028fbc5c25fe9cf00d9f06a71cc4710d4507903",
}
PUBLIC_INSTALL_CONDITION = (
    "github.event_name == 'push' && github.ref == 'refs/heads/main'"
)


def check_job(job):
    """Reject unsupported execution or privilege features before running any job."""
    allowed = {"name", "runs-on", "steps", "strategy", "defaults", "timeout-minutes"}
    if set(job) - allowed or job["runs-on"] != "ubuntu-24.04":
        raise ValueError("Unsupported portable job structure")
    defaults = job.get("defaults", {})
    if set(defaults) - {"run"} or set(defaults.get("run", {})) - {"working-directory"}:
        raise ValueError("Unsupported job defaults")
    strategy = job.get("strategy", {})
    if set(strategy) - {"fail-fast", "matrix"}:
        raise ValueError("Unsupported matrix structure")
    matrix = strategy.get("matrix", {})
    if set(matrix) - {"python-version"}:
        raise ValueError("Unsupported matrix axis")
    for version in matrix.get("python-version", []):
        if version not in {"3.11", "3.12", "3.13", "3.14"}:
            raise ValueError("Unsupported Python matrix version")
    for step in job["steps"]:
        if set(step) - {
            "name",
            "uses",
            "with",
            "run",
            "env",
            "working-directory",
            "if",
        }:
            raise ValueError("Unsupported workflow step structure")
        if "uses" in step and step["uses"] not in SETUP_ACTIONS | {"./"}:
            raise ValueError("Unsupported action: " + step["uses"])
        if ("run" in step) == ("uses" in step):
            raise ValueError("A step must select exactly one execution mechanism")
        if (
            step.get("uses", "").startswith("actions/setup-node@")
            and step.get("with", {}).get("node-version") != "24"
        ):
            raise ValueError("The image only provides Node 24")
        if "if" in step and step["if"] != PUBLIC_INSTALL_CONDITION:
            raise ValueError("Unsupported workflow condition")
        if "run" in step and "${{" in step["run"]:
            raise ValueError("Workflow expressions need an explicit native mapping")
        if any("${{" in str(v) for v in step.get("env", {}).values()):
            raise ValueError("Dynamic environment needs an explicit native mapping")


def execute(command, *, cwd, env):
    subprocess.run(
        ["bash", "--noprofile", "--norc", "-euo", "pipefail", "-c", command],
        cwd=cwd,
        env=env,
        check=True,
        timeout=900,
    )


def run_job(filename, job_id, job, python_version, source_sha):
    label = f"{filename}/{job_id}/python-{python_version}"
    print("RAILWAY_CI_JOB_BEGIN " + label, flush=True)
    with tempfile.TemporaryDirectory(prefix="carrier-job-") as temporary:
        root = Path(temporary)
        checkout = root / "source"
        shutil.copytree(
            SOURCE,
            checkout,
            ignore=shutil.ignore_patterns(
                ".git", ".venv", "__pycache__", "node_modules", "dist"
            ),
        )
        runner = root / "runner"
        subprocess.run(
            ["uv", "venv", "--seed", "--python", python_version, str(runner)],
            check=True,
            timeout=180,
        )
        env = {
            **os.environ,
            "PATH": f"{runner}/bin:" + os.environ["PATH"],
            "UV_PYTHON": str(runner / "bin/python"),
            "GITHUB_REPOSITORY": "beepboop2025/liquilens-evidence-carrier",
            "GITHUB_SHA": source_sha,
            "GITHUB_ACTION_PATH": str(checkout),
            "GITHUB_OUTPUT": str(root / "action-output"),
            "GITHUB_STEP_SUMMARY": str(root / "action-summary"),
            "RUNNER_TEMP": str(root / "tmp"),
        }
        Path(env["RUNNER_TEMP"]).mkdir()
        defaults = job.get("defaults", {}).get("run", {})
        for step in job["steps"]:
            print(
                "RAILWAY_CI_STEP " + step.get("name", step.get("uses", "unnamed")),
                flush=True,
            )
            if step.get("uses") in SETUP_ACTIONS:
                # Source, pinned interpreter and Node 24 are supplied by this image.
                continue
            if step.get("uses") == "./":
                composite = yaml.safe_load((checkout / "action.yml").read_text())
                steps = composite["runs"]["steps"]
                if len(steps) != 2 or steps[0].get("uses") not in SETUP_ACTIONS:
                    raise ValueError("Unsupported local composite action structure")
                verify = steps[1]
                if (
                    verify.get("name") != "Verify evidence carrier"
                    or "${{" in verify["run"]
                ):
                    raise ValueError("Unsupported local composite verification")
                inputs = step["with"]
                execute(
                    verify["run"],
                    cwd=checkout,
                    env={
                        **env,
                        "LIQUILENS_EVIDENCE_PATH": inputs["path"],
                        "LIQUILENS_EVIDENCE_AS_OF": inputs.get("as_of", ""),
                    },
                )
                continue
            directory = step.get(
                "working-directory", defaults.get("working-directory", ".")
            )
            cwd = (checkout / directory).resolve()
            if not cwd.is_relative_to(checkout):
                raise ValueError("Working directory escaped the source checkout")
            # The public-install step also runs for candidates, bound to their exact SHA.
            command = step["run"].replace(
                "/tmp/liquilens-openbb-public", str(root / "openbb-public")
            )
            execute(command, cwd=cwd, env={**env, **step.get("env", {})})
    print("RAILWAY_CI_JOB_PASS " + label, flush=True)


def main():
    source_sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("Railway must provide the exact source commit")
    receipt_path = SOURCE / "railway-ci-proof.json"
    if sys.argv[1:] == ["--verify-build"]:
        receipt = json.loads(receipt_path.read_text())
        if receipt.get("source") != source_sha or receipt.get("status") != "PASS":
            raise ValueError("The image has no matching successful build proof")
        print(
            f"RAILWAY_CI_PASS source={source_sha} "
            f"deployment={os.environ.get('RAILWAY_DEPLOYMENT_ID', 'unavailable')} "
            f"jobs={receipt['jobs']} phase=build-verified",
            flush=True,
        )
        return
    if sys.argv[1:]:
        raise ValueError("Unknown runner arguments")
    plan = []
    for filename, jobs in WORKFLOWS.items():
        document = yaml.safe_load((SOURCE / ".github/workflows" / filename).read_text())
        expected = set(jobs) | ({"deploy"} if filename == "pages.yml" else set())
        if set(document["jobs"]) != expected:
            raise ValueError(f"Workflow job coverage changed: {filename}")
        if document.get("permissions") != {"contents": "read"}:
            raise ValueError(f"Workflow privilege requirements changed: {filename}")
        for job_id in jobs:
            job = document["jobs"][job_id]
            check_job(job)
            versions = job.get("strategy", {}).get("matrix", {}).get("python-version")
            if versions is None:
                versions = [
                    next(
                        (
                            step["with"]["python-version"]
                            for step in job["steps"]
                            if step.get("uses", "").startswith("actions/setup-python@")
                        ),
                        "3.13",
                    )
                ]
            if any(
                version not in {"3.11", "3.12", "3.13", "3.14"} for version in versions
            ):
                raise ValueError("The image does not provide this Python version")
            for version in versions:
                plan.append((filename, job_id, job, version))
    for filename, job_id, job, version in plan:
        run_job(filename, job_id, job, version, source_sha)
    receipt_path.write_text(
        json.dumps({"source": source_sha, "status": "PASS", "jobs": len(plan)}) + "\n"
    )
    print(
        f"RAILWAY_CI_PASS source={source_sha} "
        f"deployment={os.environ.get('RAILWAY_DEPLOYMENT_ID', 'unavailable')} "
        f"jobs={len(plan)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
