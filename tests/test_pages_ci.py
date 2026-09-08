"""Pages may consume only a current, authenticated native CI result."""

import copy
import runpy
from pathlib import Path

import pytest

GATE = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/verify_pages_ci.py")
)
SOURCE = "a" * 40
URL = (
    f"https://railway.com/project/{GATE['PROJECT']}/service/{GATE['SERVICE']}"
    f"?id=0311d2f9-2adf-4466-b8e7-2e9d037f4f9c&environmentId={GATE['ENVIRONMENT']}"
)


def result():
    return {
        "id": 123,
        "context": GATE["CONTEXT"],
        "state": "success",
        "creator": dict(GATE["BOT"]),
        "target_url": URL,
    }


def verify(statuses, current=SOURCE):
    return GATE["verify_result"](statuses, source=SOURCE, current_main=current)


def test_current_native_main_pass_is_accepted():
    assert verify([result()])["source"] == SOURCE


def test_main_advancing_revokes_admission():
    with pytest.raises(ValueError, match="current main"):
        verify([result()], "b" * 40)


@pytest.mark.parametrize("state", ["pending", "failure", "error"])
def test_newer_nonpassing_result_cannot_reuse_old_success(state):
    with pytest.raises(ValueError, match="has not passed"):
        verify([{**result(), "state": state}, result()])


@pytest.mark.parametrize(
    "field,value", [("id", 1), ("login", "someone"), ("type", "User")]
)
def test_lookalike_status_provider_is_rejected(field, value):
    status = result()
    status["creator"][field] = value
    with pytest.raises(ValueError):
        verify([status])


@pytest.mark.parametrize(
    "state,url",
    [
        ("pending", None),
        (
            "success",
            URL.replace(GATE["ENVIRONMENT"], "5f41a5a7-2960-452e-b8fa-c4def50b1137"),
        ),
    ],
)
def test_newer_authenticated_invalid_result_cannot_reuse_old_pass(state, url):
    with pytest.raises(ValueError):
        verify([{**result(), "state": state, "target_url": url}, result()])


@pytest.mark.parametrize(
    "url",
    [
        URL.replace(GATE["ENVIRONMENT"], "5f41a5a7-2960-452e-b8fa-c4def50b1137"),
        URL.replace(GATE["SERVICE"], "477a4dbe-47c2-4d61-b0ef-cfa398a20b53"),
        URL.replace("railway.com", "railway.com.evil.example"),
        URL.replace("https://", "http://"),
        URL + "&environmentId=other",
        URL + "#fragment",
        URL + "&unexpected=true",
    ],
)
def test_preview_other_service_and_malformed_targets_are_rejected(url):
    status = copy.deepcopy(result())
    status["target_url"] = url
    with pytest.raises(ValueError, match="configured Railway main CI service"):
        verify([status])
