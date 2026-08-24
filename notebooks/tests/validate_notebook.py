"""Validate and reproducibly execute the public Evidence Carrier notebook."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK = ROOT / "notebooks" / "evidence_carrier_research.ipynb"
BINDER_ENVIRONMENT = ROOT / "binder" / "environment.yml"
WHEEL_NAME = "liquilens_evidence-0.14.0-py3-none-any.whl"
WHEEL_URL = (
    "https://github.com/beepboop2025/liquilens-evidence-carrier/releases/"
    f"download/v0.14.0/{WHEEL_NAME}"
)
WHEEL_SHA256 = "f0162affab57307c8e20acf91dcefc33840f91e8cf9969a8d5ec8d8df860cd24"
SDIST_SHA256 = "bd7a0a61bdb99784071021f95c160b9baeb22e00054f80abc03445a6cf576567"
FULL_HASH = "52d21139aff40ef0bd1d1005183c2c92efb23959d0d4708bb678dfed0768cadf"
REFERENCE_HASH = (
    "2d058e66060a961316dd46e9d07c7c8623cb254d5abb798db6bb9eb6ef91c536"
)
COLAB_URL = (
    "https://colab.research.google.com/github/beepboop2025/"
    "liquilens-evidence-carrier/blob/main/notebooks/"
    "evidence_carrier_research.ipynb"
)
BINDER_URL = (
    "https://mybinder.org/v2/gh/beepboop2025/liquilens-evidence-carrier/"
    "main?urlpath=lab/tree/notebooks/evidence_carrier_research.ipynb"
)


def _source(cell: dict[str, Any]) -> str:
    source = cell.get("source", "")
    return "".join(source) if isinstance(source, list) else str(source)


def _load_notebook(path: Path = NOTEBOOK) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("notebook root must be an object")
    return value


def _validate_static(notebook: dict[str, Any]) -> list[str]:
    assert notebook["nbformat"] == 4
    assert notebook["nbformat_minor"] >= 5
    metadata = notebook["metadata"]
    assert metadata["kernelspec"]["name"] == "python3"
    assert metadata["language_info"]["version"] == "3.11"
    assert metadata["liquilens"] == {
        "artifact_sha256": WHEEL_SHA256,
        "data_class": "embedded_synthetic_only",
        "network_policy": "immutable_release_bootstrap_only",
        "release": "0.14.0",
    }

    cells = notebook["cells"]
    assert isinstance(cells, list) and len(cells) == 11
    ids = [cell.get("id") for cell in cells]
    assert len(ids) == len(set(ids)) and all(isinstance(item, str) for item in ids)

    markdown = "\n".join(
        _source(cell) for cell in cells if cell.get("cell_type") == "markdown"
    )
    for required in (
        COLAB_URL,
        BINDER_URL,
        "synthetic",
        "not investment advice",
        "all-false financial-authority boundary",
        "Observed upstream facts",
        "Missing inputs",
    ):
        assert required in markdown, f"missing notebook disclosure: {required}"

    code_cells = [cell for cell in cells if cell.get("cell_type") == "code"]
    assert len(code_cells) == 5
    code_sources: list[str] = []
    for cell in code_cells:
        assert cell.get("execution_count") is None
        assert cell.get("outputs") == []
        source = _source(cell)
        ast.parse(source, filename=f"{NOTEBOOK.name}:{cell['id']}")
        code_sources.append(source)

    combined = "\n".join(code_sources)
    for required in (
        WHEEL_URL,
        WHEEL_SHA256,
        SDIST_SHA256,
        FULL_HASH,
        REFERENCE_HASH,
        "redistribution_not_permitted",
        "record_hash does not match the carrier payload",
    ):
        assert required in combined, f"missing executable receipt: {required}"

    bootstrap = code_sources[0]
    assert bootstrap.count("urlopen(") == 1
    assert "response.read(wheel[\"bytes\"] + 1)" in bootstrap
    assert "--no-index" in bootstrap and "--no-deps" in bootstrap
    assert all("urlopen(" not in source for source in code_sources[1:])
    assert all("subprocess." not in source for source in code_sources[1:])

    forbidden = (
        "google.colab",
        "getpass(",
        "input(",
        "os.environ",
        "os.getenv",
        "requests.",
        "httpx.",
        "socket.",
        "websocket",
        "analytics",
        "telemetry",
    )
    for token in forbidden:
        assert token not in combined, f"forbidden notebook behavior: {token}"

    binder = BINDER_ENVIRONMENT.read_text(encoding="utf-8")
    assert "python=3.11" in binder
    assert f'"{WHEEL_URL}#sha256={WHEEL_SHA256}"' in binder
    assert "jupyter" not in binder.lower(), "repo2docker supplies the notebook UI"
    return code_sources


def _execute_cells(notebook_path: Path) -> None:
    assert importlib.util.find_spec("liquilens_evidence") is None, (
        "clean runner unexpectedly sees a preinstalled or repository package"
    )
    notebook = _load_notebook(notebook_path)
    namespace: dict[str, Any] = {"__name__": "__main__"}
    receipts: list[str] = []
    for index, cell in enumerate(notebook["cells"]):
        if cell.get("cell_type") != "code":
            continue
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exec(  # noqa: S102 - execution is confined to the audited notebook cells
                compile(
                    _source(cell),
                    f"{notebook_path.name}#{cell.get('id', index)}",
                    "exec",
                ),
                namespace,
            )
        assert stderr.getvalue() == "", f"cell {cell.get('id')} wrote to stderr"
        receipt = stdout.getvalue()
        assert receipt.endswith("\n"), f"cell {cell.get('id')} emitted no receipt"
        receipts.append(receipt)
    print(json.dumps(receipts, ensure_ascii=False))


def _clean_run(code_sources: list[str]) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="liquilens-notebook-clean-") as directory:
        clean_root = Path(directory)
        environment = clean_root / "venv"
        subprocess.run(
            [sys.executable, "-m", "venv", str(environment)],
            check=True,
            capture_output=True,
            text=True,
        )
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        clean_env = {
            "HOME": str(clean_root / "home"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": str(python.parent),
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
            "PYTHONNOUSERSITE": "1",
        }
        result = subprocess.run(
            [python, "-I", str(Path(__file__).resolve()), "--execute", str(NOTEBOOK)],
            check=True,
            cwd=clean_root,
            env=clean_env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.stderr == "", result.stderr
        receipts = json.loads(result.stdout)
        assert isinstance(receipts, list) and len(receipts) == len(code_sources)
        assert all(isinstance(item, str) for item in receipts)
        return receipts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", type=Path)
    arguments = parser.parse_args()
    if arguments.execute is not None:
        _execute_cells(arguments.execute.resolve())
        return

    code_sources = _validate_static(_load_notebook())
    first = _clean_run(code_sources)
    second = _clean_run(code_sources)
    assert first == second, "two clean notebook executions produced different receipts"
    joined = "\n".join(first)
    for expected in (
        WHEEL_NAME,
        WHEEL_SHA256,
        FULL_HASH,
        REFERENCE_HASH,
        "record_hash does not match the carrier payload",
        '"fdc3_type": "com.liquilens.evidence"',
        '"openlineage_disposition": "full"',
    ):
        assert expected in joined, f"clean execution omitted receipt: {expected}"
    print(
        "notebook validated: static boundary and two clean deterministic executions "
        f"passed ({len(code_sources)} code cells, release 0.14.0)"
    )


if __name__ == "__main__":
    main()
