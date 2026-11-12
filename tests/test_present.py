"""Property tests for the shared evidence presenter.

Every name, figure, message and URL in these tests is synthetic.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.resources
import inspect
import itertools
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

from liquilens_evidence import present as p
from liquilens_evidence.evidence_carrier import (
    EvidenceExportPolicy,
    ExportDisposition,
    issue_evidence_carrier,
    to_flat_row,
    verify_evidence_carrier,
)

ROOT = Path(__file__).resolve().parents[1]
PRESENT_SOURCE = ROOT / "src" / "liquilens_evidence" / "present.py"
DOCS_PAGE = ROOT / "docs" / "EVIDENCE-PRESENTER.md"
DESIGN = importlib.resources.files("liquilens_evidence").joinpath("design")
EN_DASH = "\N{EN DASH}"
EM_DASH = "\N{EM DASH}"
CHECKED = "2026-11-12T08:30:00Z"
EVALUATED_AT = datetime(2026, 8, 24, 12, tzinfo=UTC)
MODES = tuple(itertools.product(p.MARK_MODES, p.COLOUR_MODES))
SGR = re.compile(r"\x1b\[([0-9;]*)m")
GOOD_HEX = "#92d3bd"
GAPS = tuple(state for state in p.STATE_ORDER if state in p.GAP_STATES)
VALUES = tuple(state for state in p.STATE_ORDER if state in p.VALUE_STATES)
REJECT_ONLY = frozenset(
    {
        "evidence_not_yet_known",
        "evidence_as_of_in_future",
        "rights_restricted",
        "rights_unknown",
        "rights_blocked",
    }
)
CODE_RULES = {
    "evidence_not_yet_known": 2,
    "evidence_as_of_in_future": 2,
    "claim_unavailable": 3,
    "rights_restricted": 4,
    "rights_unknown": 4,
    "rights_blocked": 4,
    "claim_restricted": 4,
    "rights_metadata_only": 4,
    "redistribution_not_permitted": 4,
    "rights_license_missing": 4,
    "rights_attribution_missing": 4,
    "evidence_expired": 6,
}
FLAG_RULES = {
    "not_covered": 1,
    "access_refused": 4,
    "insufficient_history": 5,
    "stale": 6,
    "provisional": 7,
}
RULE_STATES = {
    1: p.NOT_COVERED,
    2: p.UNAVAILABLE,
    3: p.UNAVAILABLE,
    4: p.RESTRICTED,
    5: p.PARTIAL,
    6: p.STALE,
    7: p.PROVISIONAL,
    8: p.OBSERVED,
}
SOURCE = p.SourceRef(
    publisher="Synthetic Publisher",
    title="Synthetic source",
    url="https://example.org/synthetic-source",
    retrieved_at="2026-10-24T06:00:00Z",
    content_sha256="0" * 64,
)


def _plain(text: str) -> str:
    return SGR.sub("", text)


def _background_params(params: list[int]) -> list[int]:
    found: list[int] = []
    index = 0
    while index < len(params):
        code = params[index]
        if code in (38, 48):
            if code == 48:
                found.append(code)
            mode = params[index + 1] if index + 1 < len(params) else None
            index += {5: 3, 2: 5}.get(mode or 0, 1)
            continue
        if 40 <= code <= 49 or 100 <= code <= 107:
            found.append(code)
        index += 1
    return found


def _value_reading(state: str, **changes: Any) -> p.Reading:
    fields: dict[str, Any] = {
        "state": state,
        "label": "Synthetic ratio",
        "subject": "Synthetic Bank",
        "value": "12.34",
        "unit": "%",
        "product": "liquilens",
        "period": "2026-09-30T00:00:00Z",
        "known_at": "2026-10-24T00:00:00Z",
        "as_of": "2026-11-01T00:00:00Z",
        "checked_at": CHECKED,
        "sources": (SOURCE,),
        "limits": "Synthetic limitation text.",
    }
    fields.update(changes)
    return p.Reading(**fields)


def _gap_reading(state: str, **changes: Any) -> p.Reading:
    fields: dict[str, Any] = {
        "state": state,
        "label": "Synthetic series",
        "subject": "Synthetic State",
        "product": "seiche",
        "reason": "Synthetic reason.",
        "checked_at": CHECKED,
    }
    fields.update(changes)
    return p.Reading(**fields)


def _corrupted(gap_state: str) -> p.Reading:
    """A value reading forced into a gap state past the constructor's guard."""

    reading = _value_reading(p.OBSERVED)
    object.__setattr__(reading, "state", gap_state)
    return reading


def _every_reading() -> list[p.Reading]:
    readings = [_value_reading(state) for state in VALUES]
    readings += [_gap_reading(state) for state in GAPS]
    readings.append(_value_reading(p.OBSERVED, state_label="STRAINED", severity="warn"))
    readings.append(
        _gap_reading(
            p.RESTRICTED,
            reason=None,
            reason_codes=("rights_metadata_only", "evidence_expired"),
            period="2026-09-30",
            known_at="2026-10-24",
            known_basis="proxy",
            sources=(SOURCE,),
            attribution="Synthetic attribution",
            limits="Synthetic limitation text.",
        )
    )
    return readings


def _descriptor(
    *,
    rights_status: str = "allowed",
    permissions: list[str] | None = None,
    license_name: str | None = "Synthetic licence",
    attribution: str | None = "Synthetic attribution",
    claim_status: str = "observed",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "producer": {
            "name": "seiche",
            "version": "0.0.0",
            "endpoint": "https://example.org/synthetic-producer",
        },
        "subject": {
            "kind": "synthetic_series",
            "name": "Synthetic Series",
            "identifiers": {"synthetic_id": "S-1"},
        },
        "claim": {
            "kind": "synthetic_reading",
            "summary": "Synthetic funding ratio",
            "status": claim_status,
        },
        "clocks": {
            "event_time": "2026-08-24T00:00:00Z",
            "knowledge_time": "2026-08-24T09:02:00Z",
            "as_of": "2026-08-24T09:05:00Z",
            "expires_at": "2026-08-24T13:00:00Z",
        },
        "sources": [
            {
                "source_id": "synthetic:1",
                "publisher": "Synthetic Publisher",
                "title": "Synthetic source",
                "url": "https://example.org/synthetic-source",
                "retrieved_at": "2026-08-24T09:00:00Z",
                "content_sha256": "0" * 64,
            }
        ],
        "rights": {
            "status": rights_status,
            "permissions": permissions or ["ingest", "derive", "display", "redistribute"],
            "license": license_name,
            "license_url": None,
            "attribution": attribution,
            "jurisdictions": ["global"],
        },
        "payload": (
            payload
            if payload is not None
            else {"ratio": {"value": "12.34", "unit": "%", "number": 12.34}}
        ),
    }


def _carrier(**changes: Any) -> dict[str, Any]:
    return issue_evidence_carrier(**_descriptor(**changes))


CODE_CASES: dict[str, tuple[dict[str, Any], datetime]] = {
    "evidence_not_yet_known": ({}, datetime(2026, 8, 24, 9, 1, tzinfo=UTC)),
    "evidence_as_of_in_future": ({}, datetime(2026, 8, 24, 9, 3, tzinfo=UTC)),
    "rights_restricted": ({"rights_status": "restricted"}, EVALUATED_AT),
    "rights_unknown": ({"rights_status": "unknown"}, EVALUATED_AT),
    "rights_blocked": ({"rights_status": "blocked"}, EVALUATED_AT),
    "rights_metadata_only": ({"rights_status": "metadata_only"}, EVALUATED_AT),
    "redistribution_not_permitted": (
        {"permissions": ["ingest", "derive", "display"]},
        EVALUATED_AT,
    ),
    "rights_license_missing": ({"license_name": None}, EVALUATED_AT),
    "rights_attribution_missing": ({"attribution": None}, EVALUATED_AT),
    "claim_restricted": ({"claim_status": "restricted"}, EVALUATED_AT),
    "claim_unavailable": ({"claim_status": "unavailable"}, EVALUATED_AT),
    "evidence_expired": ({}, datetime(2026, 8, 24, 13, tzinfo=UTC)),
}


# ---------------------------------------------------------------------------
# Tokens and the state table.


def test_vendored_tokens_match_source_json() -> None:
    source = json.loads(DESIGN.joinpath("SOURCE.json").read_text(encoding="utf-8"))
    assert source["repository"] == p.TOKENS_REPOSITORY
    assert source["commit"] == p.TOKENS_COMMIT
    assert source["commit"].startswith("59302ce8")
    assert {entry["file"] for entry in source["files"]} == {
        "ansi.json",
        "family-tokens.json",
    }
    for entry in source["files"]:
        data = DESIGN.joinpath(entry["file"]).read_bytes()
        assert entry["path"] == f"design/{entry['file']}"
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
        blob = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        assert blob == entry["git_blob"]


def test_embedded_ansi_export_equals_the_vendored_copy() -> None:
    vendored = json.loads(DESIGN.joinpath("ansi.json").read_text(encoding="utf-8"))
    assert vendored == p._ANSI_EXPORT


def test_embedded_family_tokens_equal_the_vendored_copy() -> None:
    tokens = json.loads(DESIGN.joinpath("family-tokens.json").read_text(encoding="utf-8"))
    for path, expected in p._FAMILY_TOKENS.items():
        node: Any = tokens
        for part in path.split("."):
            node = node[part]
        value = node["$value"]
        if isinstance(expected, tuple) and isinstance(expected[-1], float):
            assert (value["hex"], value["alpha"]) == expected, path
        elif isinstance(expected, tuple):
            assert tuple(value) == expected, path
        elif isinstance(expected, str):
            assert value["hex"] == expected, path
            assert "alpha" not in value, path
        elif isinstance(value, dict):
            assert value == {"value": expected, "unit": "px"}, path
        else:
            assert value == expected, path


def test_state_table_matches_the_contract() -> None:
    tokens = json.loads(DESIGN.joinpath("family-tokens.json").read_text(encoding="utf-8"))
    expected = {
        p.OBSERVED: ("Observed", "good", True),
        p.DERIVED: ("Derived", "good", True),
        p.STRUCTURAL: ("Structural", "muted", False),
        p.RESEARCH: ("Research", "muted", False),
        p.PROVISIONAL: ("Provisional", "warn", False),
        p.PARTIAL: ("Partial", "warn", False),
        p.STALE: ("Stale", "warn", False),
        p.RESTRICTED: ("Restricted", "warn", False),
        p.NOT_COVERED: ("Not covered", "warn", False),
        p.UNAVAILABLE: ("Unavailable", "bad", False),
    }
    assert tuple(expected) == p.STATE_ORDER == tuple(p.DISPLAY_STATES)
    assert len({state.mark for state in p.DISPLAY_STATES.values()}) == 10
    assert len({state.mark_ascii for state in p.DISPLAY_STATES.values()}) == 10
    for key, (word, role, calm) in expected.items():
        state = p.DISPLAY_STATES[key]
        assert (state.word, state.role, state.calm) == (word, role, calm)
        assert key == word.lower().replace(" ", "-")
        assert state.mark_ascii.isascii()
        assert len(state.mark_ascii) == 1
        assert tokens["state"][key]["$value"] == "{color." + role + "}"
        assert tokens["state"][key]["$extensions"]["in.liquilens"]["calm"] is calm
    for key in p.GAP_STATES:
        assert p.DISPLAY_STATES[key].gap
        assert not p.DISPLAY_STATES[key].calm
        assert p.DISPLAY_STATES[key].role != "good"
    assert p.GAP_STATES | p.VALUE_STATES == set(p.STATE_ORDER)
    assert not p.GAP_STATES & p.VALUE_STATES


# ---------------------------------------------------------------------------
# (b) Section 3: each reason code alone, and every combination.


@pytest.mark.parametrize("code", p.REASON_CODES)
def test_each_reason_code_alone_maps_to_its_rule(code: str) -> None:
    changes, clock = CODE_CASES[code]
    carrier = _carrier(**changes)
    verified = verify_evidence_carrier(carrier, evaluated_at=clock)
    assert verified.reason_codes == (code,)
    derived = p.derive_display_state(
        verified.disposition,
        verified.reason_codes,
        claim_status=carrier["claim"]["status"],
    )
    assert derived.rule == CODE_RULES[code]
    assert derived.state == RULE_STATES[CODE_RULES[code]]
    assert derived.reason_codes == (code,)
    assert not derived.display.calm
    reading = p.reading_from_carrier(
        carrier,
        verified,
        evaluated_at=clock,
        value_path=("ratio", "value"),
        unit_path=("ratio", "unit"),
    )
    assert reading.state == derived.state
    assert reading.value is None
    for marks, colour in MODES:
        text = _plain(p.render_text(reading, marks=marks, colour=colour))
        assert code in text
        assert "12.34" not in text


def _expected_rule(codes: tuple[str, ...], flags: dict[str, bool]) -> int:
    rules = [CODE_RULES[code] for code in codes]
    rules += [FLAG_RULES[name] for name, on in flags.items() if on]
    return min(rules) if rules else 8


def test_combinations_take_the_earliest_rule_and_keep_every_code() -> None:
    names = tuple(FLAG_RULES)
    checked = 0
    for size in range(len(p.REASON_CODES) + 1):
        for combo in itertools.combinations(p.REASON_CODES, size):
            if set(combo) & REJECT_ONLY:
                disposition = "reject"
            else:
                disposition = "metadata_only" if combo else "full"
            for bits in itertools.product((False, True), repeat=len(names)):
                flags = dict(zip(names, bits, strict=True))
                derived = p.derive_display_state(
                    disposition, combo, claim_status="observed", **flags
                )
                rule = _expected_rule(combo, flags)
                assert derived.rule == rule, (combo, flags)
                assert derived.state == RULE_STATES[rule], (combo, flags)
                assert derived.reason_codes == combo
                assert rule in derived.matched_rules
                assert min(derived.matched_rules) == rule
                if combo or any(flags.values()):
                    assert not derived.display.calm, (combo, flags)
                checked += 1
            backwards = tuple(reversed(combo))
            assert p.derive_display_state(disposition, backwards).reason_codes == backwards
    assert checked == 2 ** len(p.REASON_CODES) * 2 ** len(names)


def test_every_code_prints_in_both_forms_and_every_mode() -> None:
    for size in (1, 2):
        for combo in itertools.combinations(p.REASON_CODES, size):
            derived = p.derive_display_state(
                "reject" if set(combo) & REJECT_ONLY else "metadata_only", combo
            )
            readings = (
                _gap_reading(derived.state, reason=None, reason_codes=combo),
                _gap_reading(derived.state, reason_codes=combo),
            )
            for reading in readings:
                for marks, colour in MODES:
                    text = _plain(p.render_text(reading, marks=marks, colour=colour))
                    assert all(code in text for code in combo), combo
                html_text = p.render_html(reading)
                assert all(code in html_text for code in combo)
    value = _value_reading(p.OBSERVED, reason_codes=("evidence_expired",))
    assert "| codes: evidence_expired" in p.render_reading(value)


def test_carrier_readings_follow_the_order_with_producer_qualifiers() -> None:
    carrier = _carrier(rights_status="metadata_only")
    verified = verify_evidence_carrier(carrier, evaluated_at=datetime(2026, 8, 24, 13, tzinfo=UTC))
    assert verified.reason_codes == ("rights_metadata_only", "evidence_expired")
    reading = p.reading_from_carrier(
        carrier,
        verified,
        evaluated_at=datetime(2026, 8, 24, 13, tzinfo=UTC),
        value_path=("ratio", "value"),
        insufficient_history=True,
        provisional=True,
    )
    assert reading.state == p.RESTRICTED
    assert reading.reason_codes == ("rights_metadata_only", "evidence_expired")
    full = _carrier()
    verified_full = verify_evidence_carrier(full, evaluated_at=EVALUATED_AT)
    for flags, state in (
        ({"insufficient_history": True}, p.PARTIAL),
        ({"stale": True}, p.STALE),
        ({"provisional": True}, p.PROVISIONAL),
        ({"stale": True, "provisional": True}, p.STALE),
    ):
        shown = p.reading_from_carrier(
            full, verified_full, evaluated_at=EVALUATED_AT, value_path=("ratio", "value"), **flags
        )
        assert shown.state == state
        assert (shown.value is None) is (state in p.GAP_STATES)


def test_unknown_reason_codes_and_inconsistent_outcomes_fail_closed() -> None:
    derived = p.derive_display_state("metadata_only", ("future_code",))
    assert derived.state == p.RESTRICTED
    assert derived.reason_codes == ("future_code",)
    for disposition, codes in (
        ("full", ("evidence_expired",)),
        ("metadata_only", ()),
        ("reject", ()),
        ("metadata_only", ("rights_restricted",)),
        (None, ("evidence_expired",)),
    ):
        with pytest.raises(ValueError):
            p.derive_display_state(disposition, codes)
    with pytest.raises(ValueError):
        p.derive_display_state("partial_disclosure", ())
    with pytest.raises(TypeError):
        p.derive_display_state("full", "evidence_expired")
    with pytest.raises(TypeError):
        p.derive_display_state(stale="yes")  # type: ignore[arg-type]
    assert p.derive_display_state().state == p.UNAVAILABLE
    assert p.derive_display_state(claim_status="bogus").state == p.UNAVAILABLE
    assert p.derive_display_state("full", claim_status="research").state == p.RESEARCH
    assert (
        p.derive_display_state("full", claim_status="observed", native_state=p.RESEARCH).state
        == p.RESEARCH
    )
    assert (
        p.derive_display_state("full", claim_status="research", native_state=p.OBSERVED).state
        == p.RESEARCH
    )


def test_a_custom_policy_outcome_is_still_a_gap() -> None:
    policy = EvidenceExportPolicy(
        version="synthetic-strict-reject",
        expired_disposition=ExportDisposition.REJECT,
        missing_redistribution_disposition=ExportDisposition.REJECT,
    )
    clock = datetime(2026, 8, 24, 13, tzinfo=UTC)
    carrier = _carrier(permissions=["ingest", "derive", "display"])
    verified = verify_evidence_carrier(carrier, evaluated_at=clock, policy=policy)
    assert verified.disposition is ExportDisposition.REJECT
    reading = p.reading_from_carrier(
        carrier, verified, evaluated_at=clock, value_path=("ratio", "value")
    )
    assert reading.state == p.RESTRICTED
    assert reading.value is None
    assert reading.reason_codes == ("redistribution_not_permitted", "evidence_expired")


# ---------------------------------------------------------------------------
# (a) Gaps never show a value, a zero or a placeholder, and never look calm.

PLACEHOLDER_TOKEN = re.compile(r"(^|[\s:|(])(0|0\.0|n/a|N/A|-|None|nan|NaN)($|[\s|),])")


@pytest.mark.parametrize("state", GAPS)
def test_gap_states_never_render_a_value_or_a_placeholder(state: str) -> None:
    display = p.DISPLAY_STATES[state]
    for value in ("12.34", 0, 0.0, Decimal(0), "0"):
        with pytest.raises(ValueError):
            p.Reading(state=state, label="Synthetic series", value=value)
    with pytest.raises(ValueError):
        p.Reading(state=state, label="Synthetic series", severity="good")
    readings = (_gap_reading(state), _gap_reading(state, reason=None), _corrupted(state))
    for reading in readings:
        for marks, colour in MODES:
            text = p.render_reading(reading, marks=marks, colour=colour)
            plain = _plain(text)
            mark = display.mark if marks == "unicode" else display.mark_ascii
            lines = plain.splitlines()
            assert lines[0].endswith(f": {mark} {display.word}"), lines[0]
            assert "12.34" not in plain
            for line in lines:
                assert line.strip()
                assert not line.rstrip().endswith(":")
                for segment in line.split(" | "):
                    assert segment.strip(), line
                    assert not PLACEHOLDER_TOKEN.search(segment), segment
            if colour != "none":
                good = p._ANSI_EXPORT["roles"]["good"][colour]
                assert f"\x1b[{good}m" not in text
        html_text = p.render_html(reading)
        assert "12.34" not in html_text
        assert GOOD_HEX not in html_text
        cell = re.search(r"text-align:right;color:[^>]*>([^<]*)</td>", html_text)
        assert cell is not None
        assert cell.group(1) == display.word
        assert math.isnan(p.reading_number(reading))
        row = p.add_display_columns({"synthetic": "row"}, reading)
        assert math.isnan(row["value"])
        assert row["display_state"] == display.word


def test_every_gap_row_from_a_constructor_drops_its_value() -> None:
    gap_rows = {
        p.PARTIAL: p.state_for("undertow", "ACCRUING"),
        p.STALE: p.state_for("liquilens", "stale"),
        p.RESTRICTED: p.state_for("liquilens", "withheld"),
        p.NOT_COVERED: p.state_for("liquilens", "not_assessed"),
        p.UNAVAILABLE: p.state_for("liquilens", "not_disclosed"),
    }
    for state, product_state in gap_rows.items():
        reading = p.reading_from_native(product_state, label="Synthetic", value="12.34")
        assert reading.state == state
        assert reading.value is None
        assert "12.34" not in p.render_reading(reading)
    for value in (None, "", "  ", "-", "n/a", "NaN", "null", EN_DASH, EM_DASH, math.nan):
        reading = p.reading_from_native(
            p.state_for("liquilens", "observed"), label="Synthetic", value=value
        )
        assert reading.state == p.UNAVAILABLE
        assert reading.state_label is None


def test_a_real_served_zero_is_shown() -> None:
    reading = p.reading_from_native(
        p.state_for("seiche", "observed"), label="Synthetic spread", value=0, unit="bp"
    )
    assert reading.state == p.OBSERVED
    assert p.render_reading(reading).splitlines()[0] == "Synthetic spread: 0 bp"
    assert p.reading_number(reading) == 0.0


# ---------------------------------------------------------------------------
# (c) Every state's word in every mode; (d) colour never on a background.


def test_every_state_word_appears_in_every_mode() -> None:
    for reading in _every_reading():
        display = p.DISPLAY_STATES[reading.state]
        for marks, colour in MODES:
            text = _plain(p.render_text(reading, marks=marks, colour=colour))
            mark = display.mark if marks == "unicode" else display.mark_ascii
            assert f"{mark} {display.word}" in text
        html_text = p.render_html(reading)
        assert display.word in html_text
        assert display.mark in html_text


def test_no_background_codes_and_no_escapes_without_colour() -> None:
    hostile = _value_reading(
        p.OBSERVED,
        label="Synthetic \x1b[41mred\x1b[0m label",
        limits="\x1b[48;5;196mtinted\x1b[0m",
    )
    readings = [*_every_reading(), hostile, _gap_reading(p.STALE, reason="\x1b[101mx")]
    roles = p._ANSI_EXPORT["roles"]
    allowed = {"0"} | {
        roles[role][mode]
        for role in ("accent", "muted", "good", "warn", "bad")
        for mode in ("truecolor", "ansi256", "ansi16")
    }
    for reading in readings:
        for marks, colour in MODES:
            text = p.render_text(reading, marks=marks, colour=colour)
            if colour == "none":
                assert "\x1b" not in text
                continue
            assert "\x1b" not in SGR.sub("", text)
            for match in SGR.finditer(text):
                assert match.group(1) in allowed
                params = [int(part) for part in match.group(1).split(";")]
                assert not _background_params(params)
    tier = p.reading_from_native(
        p.state_for("undertow", "DEFICIENT", scored=True), label="Synthetic segment", value="0.91"
    )
    bad = roles["bad"]["truecolor"]
    assert f"\x1b[{bad}mDEFICIENT\x1b[0m" in p.render_text(tier, colour="truecolor")


# ---------------------------------------------------------------------------
# (e) HTML escapes producer text and loads nothing.

ALLOWED_TAGS = {"div", "table", "thead", "tbody", "tr", "th", "td", "span", "a", "p", "br"}
ALLOWED_ATTRIBUTES = {"style", "href", "title", "scope"}


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.attributes: list[tuple[str, str]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.attributes.extend((name, value or "") for name, value in attrs)

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def _hostile_reading(hostile: str) -> p.Reading:
    return p.Reading(
        state=p.OBSERVED,
        label=hostile,
        subject=hostile,
        value=hostile,
        unit=hostile,
        product=hostile,
        state_label=hostile,
        severity="warn",
        reason=hostile,
        limits=hostile,
        boundary=hostile,
        subject_identifiers={"synthetic": hostile},
        sources=(
            p.SourceRef(publisher=hostile, title=hostile, url="javascript:alert(1)"),
            p.SourceRef(publisher="Synthetic", url='https://example.org/"onmouseover="x'),
            p.SourceRef(publisher="Synthetic link", url="https://example.org/a?b=1&c=2"),
        ),
        checked_at=CHECKED,
    )


def _structure(html_text: str) -> _Collector:
    collector = _Collector()
    collector.feed(html_text)
    collector.close()
    assert set(collector.tags) <= ALLOWED_TAGS
    for name, value in collector.attributes:
        assert name in ALLOWED_ATTRIBUTES
        if name == "href":
            assert value.startswith("https://")
        if name == "style":
            lowered = value.lower()
            for banned in ("url(", "@import", "expression(", "javascript:"):
                assert banned not in lowered
    return collector


def test_html_escapes_hostile_producer_text() -> None:
    hostile = "<script>alert('x')</script> \"quoted\" & 'single' & more"
    html_text = p.render_html(_hostile_reading(hostile))
    lowered = html_text.lower()
    assert "<script" not in lowered
    assert "@import" not in lowered
    assert "url(" not in lowered
    assert "javascript:" not in lowered
    assert "&lt;script&gt;" in html_text
    assert "&quot;quoted&quot;" in html_text
    assert "&amp; more" in html_text
    assert 'href="https://example.org/a?b=1&amp;c=2"' in html_text
    collector = _structure(html_text)
    assert hostile in "".join(collector.text)
    assert p.ReadingTable(_hostile_reading(hostile))._repr_html_() == html_text


def test_html_structure_holds_for_style_shaped_producer_text() -> None:
    hostile = "url(https://example.org/x) @import 'y'; </td></tr><style>* {}</style>"
    collector = _structure(p.render_html(_hostile_reading(hostile)))
    assert "style" not in collector.tags
    for reading in _every_reading():
        html_text = p.render_html(reading)
        assert "url(" not in html_text
        assert "@import" not in html_text
        _structure(html_text)


def test_html_table_uses_the_tokens_and_keeps_alignment() -> None:
    readings = _every_reading()
    html_text = p.render_html(readings)
    assert html_text.startswith('<div style="background-color:#0b0d10;color:#f2f1ec;')
    assert "font-variant-numeric:tabular-nums" in html_text
    assert html_text.count("text-align:right") == len(readings) + 1
    assert html_text.count(p.DEFAULT_BOUNDARY) == 1
    assert "Source: " in html_text
    assert "checked " in html_text
    table = p.ReadingTable(readings)
    assert table._repr_html_() == html_text
    assert repr(table) == p.render_text(readings)
    assert table.to_text(marks="ascii") == p.render_text(readings, marks="ascii")


# ---------------------------------------------------------------------------
# (f) No en or em dash in output, source or docs.


def _my_readme_section() -> str:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    start = readme.index("## Present readings the same way on every surface")
    end = readme.find("\n## ", start + 3)
    return readme[start : end if end != -1 else len(readme)]


def _my_changelog_lines() -> str:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    start = changelog.index("## [Unreleased]")
    end = changelog.index("\n## ", start + 3)
    bullets = changelog[start:end].split("\n- ")
    mine = [bullet for bullet in bullets if "present" in bullet or "design/ansi.json" in bullet]
    assert len(mine) == 2
    return "\n".join(mine)


def test_no_en_or_em_dash_in_output_source_or_docs() -> None:
    for reading in _every_reading():
        for marks, colour in MODES:
            text = p.render_text(reading, marks=marks, colour=colour)
            assert EN_DASH not in text
            assert EM_DASH not in text
        html_text = p.render_html(reading)
        assert EN_DASH not in html_text
        assert EM_DASH not in html_text
    texts = [
        PRESENT_SOURCE.read_text(encoding="utf-8"),
        Path(__file__).read_text(encoding="utf-8"),
        DOCS_PAGE.read_text(encoding="utf-8"),
        DESIGN.joinpath("SOURCE.json").read_text(encoding="utf-8"),
        _my_readme_section(),
        _my_changelog_lines(),
    ]
    for text in texts:
        assert EN_DASH not in text
        assert EM_DASH not in text


# ---------------------------------------------------------------------------
# (h) Section 4 rows, unknown values, checks and resolution statuses.

SECTION_4_ROWS: list[tuple[tuple[str, str, dict[str, Any]], tuple[str, str, str | None]]] = [
    (("liquilens", "observed", {}), (p.OBSERVED, "Accepted filing evidence", None)),
    (("liquilens", "stale", {}), (p.STALE, "Stale evidence", None)),
    (("liquilens", "historical", {}), (p.STALE, "Historical evidence", None)),
    (("liquilens", "CALM", {}), (p.OBSERVED, "CALM", "good")),
    (("liquilens", "WATCH", {}), (p.OBSERVED, "WATCH", "warn")),
    (("liquilens", "ALARM", {}), (p.OBSERVED, "ALARM", "bad")),
    (("liquilens", "unavailable", {}), (p.UNAVAILABLE, "Evidence unavailable", None)),
    (("liquilens", "not_disclosed", {}), (p.UNAVAILABLE, "Not disclosed", None)),
    (("liquilens", "provisional", {}), (p.PROVISIONAL, "Provisional business update", None)),
    (
        ("liquilens", "provisional", {"provisional_business_update": True}),
        (p.PROVISIONAL, "Provisional business update", None),
    ),
    (
        ("liquilens", "stale", {"provisional_business_update": True}),
        (p.STALE, "Stale provisional business update", None),
    ),
    (("liquilens", "withheld", {}), (p.RESTRICTED, "Withheld", None)),
    (("liquilens", "not_covered", {}), (p.NOT_COVERED, "Not covered", None)),
    (("liquilens", "lender_not_listed", {}), (p.NOT_COVERED, "Not covered", None)),
    (("liquilens", "not_in_slim", {}), (p.NOT_COVERED, "Not covered", None)),
    (("liquilens", "not_applicable", {}), (p.NOT_COVERED, "Not applicable", None)),
    (("liquilens", "not_assessed", {}), (p.NOT_COVERED, "Not assessed", None)),
    (("liquilens", "PARTIAL", {}), (p.PARTIAL, "PARTIAL", None)),
    (("liquilens", "identity", {}), (p.STRUCTURAL, "Identity record", None)),
    (
        ("liquilens", "failure_radar", {"tier_publication": "paused"}),
        (p.RESEARCH, "Research only", None),
    ),
    (("liquilens", "failure_radar", {}), (p.RESEARCH, "Research only", None)),
    (
        ("liquilens", "observed", {"age_days": 401, "annual": True}),
        (p.STALE, "Stale evidence", None),
    ),
    (
        ("liquilens", "observed", {"age_days": 400, "annual": True}),
        (p.OBSERVED, "Accepted filing evidence", None),
    ),
    (
        ("liquilens", "observed", {"age_days": 184, "annual": False}),
        (p.STALE, "Stale evidence", None),
    ),
    (
        ("liquilens", "observed", {"age_days": 183}),
        (p.OBSERVED, "Accepted filing evidence", None),
    ),
    (("liquilens", "observed", {"age_days": 184}), (p.STALE, "Stale evidence", None)),
    (("seiche", "observed", {}), (p.OBSERVED, "observed", None)),
    (("seiche", "derived", {}), (p.DERIVED, "derived", None)),
    (("seiche", "structural", {}), (p.STRUCTURAL, "structural", None)),
    (("seiche", "restricted", {}), (p.RESTRICTED, "restricted", None)),
    (("seiche", "unavailable", {}), (p.UNAVAILABLE, "unavailable", None)),
    (("seiche", "stale", {}), (p.STALE, "stale", None)),
    (("seiche", "STALE", {}), (p.STALE, "STALE", None)),
    (("seiche", "RESTRICTED", {}), (p.RESTRICTED, "RESTRICTED", None)),
    (("seiche", "UNAVAILABLE", {}), (p.UNAVAILABLE, "UNAVAILABLE", None)),
    (("undertow", "SURPLUS", {"scored": True}), (p.OBSERVED, "SURPLUS", "good")),
    (("undertow", "NORMAL", {"scored": True}), (p.OBSERVED, "NORMAL", "good")),
    (("undertow", "STRAINED", {"scored": True}), (p.OBSERVED, "STRAINED", "warn")),
    (("undertow", "DEFICIENT", {"scored": True}), (p.OBSERVED, "DEFICIENT", "bad")),
    (("undertow", "NORMAL", {"scored": False}), (p.PARTIAL, "PARTIAL", None)),
    (("undertow", "DEFICIENT", {}), (p.PARTIAL, "PARTIAL", None)),
    (("undertow", "ACCRUING", {}), (p.PARTIAL, "ACCRUING", None)),
    (("undertow", "PARTIAL", {"scored": True}), (p.PARTIAL, "PARTIAL", None)),
    (
        ("undertow", "behind", {"note": "2 rebuilds behind"}),
        (p.STALE, "2 rebuilds behind", None),
    ),
    (("undertow", "stale", {}), (p.STALE, "stale", None)),
    (
        ("undertow", "unreachable", {"status": "unreachable"}),
        (p.UNAVAILABLE, "unreachable", None),
    ),
    (
        ("undertow", "behind", {"ok": False, "status": "HTTP 503"}),
        (p.UNAVAILABLE, "HTTP 503", None),
    ),
    (("undertow", "UNAVAILABLE", {}), (p.UNAVAILABLE, "UNAVAILABLE", None)),
    (("undertow", "UNAVAILABLE", {"scored": False}), (p.UNAVAILABLE, "UNAVAILABLE", None)),
    (("undertow", "exit_check.available", {}), (p.DERIVED, "Available", None)),
    (("undertow", "exit_check.unavailable", {}), (p.UNAVAILABLE, "Unavailable", None)),
    (("undertow", "exit_check.partial", {}), (p.PARTIAL, "Partial", None)),
    (("undertow", "restricted", {}), (p.RESTRICTED, "Restricted", None)),
    (("undertow", "observed", {}), (p.OBSERVED, "Observed", None)),
    (("undertow", "derived", {}), (p.DERIVED, "Derived", None)),
    (("undertow", "structural", {}), (p.STRUCTURAL, "Structural", None)),
    (("undertow", "unavailable", {}), (p.UNAVAILABLE, "Unavailable", None)),
    (("undertow", "issuer_disclosed", {}), (p.OBSERVED, "Issuer disclosed", None)),
    (("undertow", "context", {}), (p.RESEARCH, "Context (display only)", None)),
    (("undertow", "accruing", {}), (p.PARTIAL, "Accruing", None)),
    (("undertow", "partial", {}), (p.PARTIAL, "Partial", None)),
]

UNDERTOW_TABLE: dict[str, tuple[dict[str, Any], str, str]] = {
    "SURPLUS": ({"scored": True}, p.OBSERVED, "SURPLUS"),
    "NORMAL": ({"scored": True}, p.OBSERVED, "NORMAL"),
    "STRAINED": ({"scored": True}, p.OBSERVED, "STRAINED"),
    "DEFICIENT": ({"scored": True}, p.OBSERVED, "DEFICIENT"),
    "ACCRUING": ({}, p.PARTIAL, "ACCRUING"),
    "PARTIAL": ({}, p.PARTIAL, "PARTIAL"),
    "UNAVAILABLE": ({}, p.UNAVAILABLE, "UNAVAILABLE"),
    "behind": ({}, p.STALE, "behind"),
    "stale": ({}, p.STALE, "stale"),
    "unreachable": ({}, p.UNAVAILABLE, "unreachable"),
    "exit_check.available": ({}, p.DERIVED, "Available"),
    "exit_check.unavailable": ({}, p.UNAVAILABLE, "Unavailable"),
    "exit_check.partial": ({}, p.PARTIAL, "Partial"),
    "restricted": ({}, p.RESTRICTED, "Restricted"),
    "observed": ({}, p.OBSERVED, "Observed"),
    "derived": ({}, p.DERIVED, "Derived"),
    "structural": ({}, p.STRUCTURAL, "Structural"),
    "unavailable": ({}, p.UNAVAILABLE, "Unavailable"),
    "issuer_disclosed": ({}, p.OBSERVED, "Issuer disclosed"),
    "context": ({}, p.RESEARCH, "Context (display only)"),
    "accruing": ({}, p.PARTIAL, "Accruing"),
    "partial": ({}, p.PARTIAL, "Partial"),
}


def test_every_undertow_native_resolves_to_exactly_one_row() -> None:
    groups = (
        set(p._UNDERTOW_TIERS),
        set(p._UNDERTOW_GAP_WORDS),
        {p._UNDERTOW_NO_MEASURES},
        set(p._UNDERTOW_BOARD_WORDS),
        set(p._UNDERTOW_EXIT_CHECK),
        {p._UNDERTOW_RIGHTS},
        set(p._UNDERTOW_EVIDENCE),
    )
    natives = [native for group in groups for native in group]
    assert len(natives) == len(set(natives))
    assert set(natives) == set(UNDERTOW_TABLE)
    for native, (qualifiers, state, label) in UNDERTOW_TABLE.items():
        results = {p.state_for("undertow", native, **qualifiers) for _ in range(2)}
        assert len(results) == 1
        (result,) = results
        assert (result.state, result.label) == (state, label)
    folded = [native.casefold() for native in natives]
    # Case alone separates the upper-case tier words from the evidence words.
    assert {native for native in folded if folded.count(native) > 1} == {
        "unavailable",
        "partial",
        "accruing",
    }
    for licence in ("consent_required", "licensed", "metadata_only"):
        with pytest.raises(ValueError):
            p.state_for("undertow", licence)
    context = p.state_for("undertow", "context")
    assert not p.DISPLAY_STATES[context.state].calm
    assert p.DISPLAY_STATES[context.state].role != "good"
    reading = p.reading_from_native(context, label="Synthetic sibling reading", value="7")
    for colour in ("truecolor", "ansi256", "ansi16"):
        good = p._ANSI_EXPORT["roles"]["good"][colour]
        assert f"\x1b[{good}m" not in p.render_reading(reading, colour=colour)
    assert GOOD_HEX not in p.render_html(reading)
    for gap in ("accruing", "partial"):
        assert p.state_for("undertow", gap).state in p.GAP_STATES


SEICHE_TABLE: dict[str, str] = {
    "observed": p.OBSERVED,
    "derived": p.DERIVED,
    "structural": p.STRUCTURAL,
    "restricted": p.RESTRICTED,
    "unavailable": p.UNAVAILABLE,
    "stale": p.STALE,
    "STALE": p.STALE,
    "RESTRICTED": p.RESTRICTED,
    "UNAVAILABLE": p.UNAVAILABLE,
}


def test_seiche_rows_are_exactly_its_words_and_the_added_ones_are_gaps() -> None:
    assert set(p._SEICHE_ROWS) == set(SEICHE_TABLE)
    for native, state in SEICHE_TABLE.items():
        result = p.state_for("seiche", native)
        assert (result.state, result.label, result.severity) == (state, native, None)
    for native in ("stale", "STALE", "RESTRICTED", "UNAVAILABLE"):
        result = p.state_for("seiche", native)
        display = p.DISPLAY_STATES[result.state]
        assert result.state in p.GAP_STATES
        assert not display.calm and display.role != "good"
        # A gap drops a value it is given and prints its word, never a figure.
        reading = p.reading_from_native(
            result, label="Synthetic series", value="1.25", unit="%"
        )
        assert reading.value is None and reading.state == result.state
        text = p.render_reading(reading)
        assert "1.25" not in text
        assert text.startswith(f"Synthetic series: {display.mark} {display.word}\n")


@pytest.mark.parametrize(("call", "expected"), SECTION_4_ROWS)
def test_section_4_rows(
    call: tuple[str, str, dict[str, Any]], expected: tuple[str, str, str | None]
) -> None:
    product, native, qualifiers = call
    result = p.state_for(product, native, **qualifiers)
    assert (result.state, result.label, result.severity) == expected
    assert (result.product, result.native) == (product, native)
    display = p.DISPLAY_STATES[result.state]
    if result.state in p.GAP_STATES:
        assert not display.calm
        assert display.role != "good"
        assert result.severity is None


UNKNOWN_NATIVES = {
    "liquilens": (
        "Observed",
        "OBSERVED",
        " observed",
        "observed ",
        "observed_subset",
        "partial",
        "research",
        "UNAVAILABLE",
        "tier",
        "",
    ),
    "seiche": (
        "Observed",
        "research",
        "provisional",
        "Stale",
        "PARTIAL",
        "",
        "derived ",
        # Freshness words that are not states, in both cases Seiche serves them.
        "fresh",
        "FRESH",
        "aging",
        "AGING",
        "dead",
        "DEAD",
        "unknown",
        "UNKNOWN",
        # Coverage and availability words that are not readings.
        "available",
        "AVAILABLE",
        "DERIVED_CONTEXT",
        "partial",
        "partial_or_unknown",
        "partial_release",
        "scenario",
        "NORMAL",
    ),
    "undertow": (
        "strained",
        "fresh",
        "unknown",
        "scored",
        "Behind",
        "",
        "available",
        "Restricted",
        "exit_check",
        "exit_check.bogus",
        "exit_check.Available",
        "exit_check.available ",
    ),
}


def test_unknown_products_and_native_values_raise() -> None:
    for product, natives in UNKNOWN_NATIVES.items():
        for native in natives:
            with pytest.raises(ValueError):
                p.state_for(product, native)
    known = {native for (product, native, _), _ in SECTION_4_ROWS}
    for (product, native, _), _ in SECTION_4_ROWS:
        for variant in {native.upper(), native.lower(), native.title(), f"{native}_x"}:
            if variant not in known:
                with pytest.raises(ValueError):
                    p.state_for(product, variant)
    for product in ("palimpsest", "riptide", "LiquiLens", "Seiche", ""):
        with pytest.raises(ValueError):
            p.state_for(product, "observed")
    with pytest.raises(TypeError):
        p.state_for(None, "observed")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        p.state_for("seiche", None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="published"):
        p.state_for("liquilens", "failure_radar", tier_publication="published")


def test_new_undertow_rows_are_gaps_except_an_available_exit_check() -> None:
    rows = {
        "UNAVAILABLE": p.UNAVAILABLE,
        "exit_check.available": p.DERIVED,
        "exit_check.unavailable": p.UNAVAILABLE,
        "exit_check.partial": p.PARTIAL,
        "restricted": p.RESTRICTED,
    }
    for native, state in rows.items():
        result = p.state_for("undertow", native)
        assert result.state == state
        assert result.severity is None
        assert (state in p.GAP_STATES) is (native != "exit_check.available")
        if state in p.GAP_STATES:
            assert not p.DISPLAY_STATES[state].calm
            reading = p.reading_from_native(
                result,
                label="Synthetic exit check",
                value="12.34",
                reason="SYNTHETIC_REASON_CODE",
            )
            assert reading.value is None
            assert "reason: SYNTHETIC_REASON_CODE" in p.render_reading(reading)
    available = p.reading_from_native(
        p.state_for("undertow", "exit_check.available"),
        label="Synthetic exit check",
        value="0.42",
        unit="% of book",
    )
    assert p.render_reading(available).splitlines()[1].startswith(
        "\N{BLACK DIAMOND} Derived (Available) | "
    )
    with pytest.raises(ValueError, match="scored"):
        p.state_for("undertow", "UNAVAILABLE", scored=True)
    for native in ("exit_check.available", "restricted"):
        with pytest.raises(TypeError):
            p.state_for("undertow", native, scored=True)
    rights = p.reading_from_native(
        p.state_for("undertow", "restricted"),
        label="Synthetic result",
        value=None,
        reason="Synthetic source_rights summary: redistribution not permitted.",
    )
    lines = p.render_reading(rights).splitlines()
    assert lines[0] == "Synthetic result: \N{CIRCLED DIVISION SLASH} Restricted"
    assert lines[1].startswith(
        "reason: Synthetic source_rights summary: redistribution not permitted."
    )
    identity = p.reading_from_native(
        p.state_for("liquilens", "identity"),
        label="Synthetic legal entity identifier",
        value="SYNTHETIC-ID-1",
    )
    assert p.render_reading(identity).splitlines()[1].startswith(
        "\N{BLACK SQUARE} Structural (Identity record) | "
    )


def test_json_safe_rows_hold_no_nan_or_infinity() -> None:
    row = {"carrier_id": "synthetic", "note": "Synthetic"}
    readings = [
        *_every_reading(),
        *(_corrupted(state) for state in GAPS),
        _value_reading(p.OBSERVED, value="1,23,456.78"),
        _value_reading(p.OBSERVED, value=Decimal("12.340")),
    ]
    for reading in readings:
        safe = p.add_display_columns(row, reading, json_safe=True)
        json.dumps(safe, allow_nan=False)
        default = p.add_display_columns(row, reading)
        assert list(safe) == list(default)
        if safe["value"] is None:
            assert math.isnan(default["value"])
        else:
            assert safe["value"] == default["value"]
            assert math.isfinite(safe["value"])
        if reading.state in p.GAP_STATES:
            assert safe["value"] is None
            assert math.isnan(p.reading_number(reading))
    for bad_row in (
        {"bad": math.nan},
        {"bad": [1.0, math.inf]},
        {"bad": {"deeper": Decimal("NaN")}},
    ):
        with pytest.raises(ValueError, match="not valid JSON"):
            p.add_display_columns(bad_row, _value_reading(p.OBSERVED), json_safe=True)
        p.add_display_columns(bad_row, _value_reading(p.OBSERVED))
    with pytest.raises(TypeError):
        p.add_display_columns(row, _value_reading(p.OBSERVED), json_safe=1)  # type: ignore[arg-type]


def test_qualifiers_that_do_not_apply_raise() -> None:
    for product, native, qualifiers in (
        ("seiche", "observed", {"stale": True}),
        ("undertow", "STRAINED", {"note": "x"}),
        ("undertow", "STRAINED", {"ok": False}),
        ("undertow", "behind", {"scored": True}),
        ("undertow", "STRAINED", {"scored": "true"}),
        ("liquilens", "stale", {"age_days": 500}),
        ("liquilens", "failure_radar", {"provisional_business_update": True}),
        ("liquilens", "observed", {"age_days": True}),
        ("liquilens", "observed", {"annual": True}),
        ("liquilens", "observed", {"provisional_business_update": True, "age_days": 1}),
    ):
        with pytest.raises(TypeError):
            p.state_for(product, native, **qualifiers)
    with pytest.raises(ValueError):
        p.state_for("liquilens", "observed", age_days=-1)
    with pytest.raises(ValueError):
        p.state_for("liquilens", "observed", provisional_business_update=True)


CHECK_WORDS = {
    ("liquilens", "reconciled"): "Reconciled",
    ("liquilens", "within_rounding"): "Within rounding",
    ("liquilens", "inconsistent"): "Inconsistent",
    ("liquilens", "clean"): "Clean",
    ("liquilens", "clean_pass"): "Clean pass",
    ("liquilens", "flagged"): "Flagged",
    ("liquilens", "warned"): "Warned",
    ("liquilens", "false_alarm"): "False alarm",
    ("liquilens", "missed"): "Missed",
    ("liquilens", "missed_fraud_masked"): "Missed fraud masked",
    ("liquilens", "insufficient_data"): "Insufficient data",
    ("undertow", "match"): "Match",
    ("undertow", "mismatch"): "Mismatch",
    ("undertow", "indeterminate"): "Indeterminate",
}


def test_check_words_are_words_not_readings() -> None:
    state_words = {state.word for state in p.DISPLAY_STATES.values()}
    for (product, word), label in CHECK_WORDS.items():
        assert p.check_label(product, word) == label
        assert label not in state_words
        for any_product in ("liquilens", "seiche", "undertow"):
            with pytest.raises(ValueError, match="check_label"):
                p.state_for(any_product, word)
    assert p.check_label("liquilens", "inconsistent") == "Inconsistent"
    assert p.check_label("undertow", "mismatch") == "Mismatch"
    indeterminate = p.check_label("undertow", "indeterminate")
    assert indeterminate not in {"Match", "Mismatch", "Pass", "Passed", "OK"}
    for product, word in (
        ("undertow", "pass"),
        ("undertow", "Match"),
        ("liquilens", "match"),
        ("seiche", "reconciled"),
        ("palimpsest", "match"),
    ):
        with pytest.raises(ValueError):
            p.check_label(product, word)


def test_resolution_statuses_are_not_display_states() -> None:
    assert p.resolution_notice("not_found") == "No match for this name"
    assert p.resolution_notice("ambiguous", candidates=3) == "Ambiguous (3 candidates)"
    assert p.resolution_notice("ambiguous", candidates=1) == "Ambiguous (1 candidate)"
    assert p.resolution_notice("ambiguous") == "Ambiguous (candidates: Not stated)"
    assert p.resolution_notice("resolved") is None
    for status in ("resolved", "ambiguous", "not_found"):
        notice = p.resolution_notice(status, candidates=2)
        assert notice is None or "Not covered" not in notice
        for product in ("liquilens", "seiche", "undertow"):
            with pytest.raises(ValueError, match="resolution"):
                p.state_for(product, status)
            with pytest.raises(ValueError, match="resolution"):
                p.state_for(product, status.upper())
    assert "resolution" not in str(inspect.signature(p.derive_display_state))
    for bad in ("not_covered", "Not covered", ""):
        with pytest.raises(ValueError):
            p.resolution_notice(bad)


@pytest.mark.parametrize("status", (401, 402, None))
def test_access_gate_refusal_is_withheld_with_its_message(status: int | None) -> None:
    message = "Synthetic gate message: an API key is required for this endpoint."
    reading = p.reading_access_refused(
        message=message,
        product="liquilens",
        label="Synthetic credit stress",
        subject="Synthetic Bank",
        checked_at=CHECKED,
        http_status=status,
    )
    assert reading.state == p.RESTRICTED
    assert reading.state_label == "Withheld"
    assert reading.reason == message
    assert reading.value is None
    lines = p.render_reading(reading).splitlines()
    restricted = "\N{CIRCLED DIVISION SLASH} Restricted (Withheld)"
    assert lines[0] == f"Synthetic credit stress, Synthetic Bank: {restricted}"
    assert lines[1] == f"reason: {message} | checked 2026-11-12 08:30 UTC"
    assert math.isnan(p.reading_number(reading))
    html_text = p.render_html(reading)
    assert "Withheld" in html_text
    assert ">Restricted</td>" in html_text


def test_access_gate_refusal_is_only_401_or_402() -> None:
    for status in (403, 404, 500, True):
        with pytest.raises(ValueError):
            p.reading_access_refused(message="Synthetic", http_status=status)


# ---------------------------------------------------------------------------
# Carriers, values, clocks and the contract's own examples.


def test_reading_from_a_full_carrier_shows_the_value_as_served() -> None:
    carrier = _carrier()
    verified = verify_evidence_carrier(carrier, evaluated_at=EVALUATED_AT)
    reading = p.reading_from_carrier(
        carrier,
        verified,
        evaluated_at=EVALUATED_AT,
        value_path=("ratio", "value"),
        unit_path=("ratio", "unit"),
        numeric_path=("ratio", "number"),
        limits="Synthetic limitation text.",
    )
    assert (reading.state, reading.value, reading.unit) == (p.OBSERVED, "12.34", "%")
    assert reading.numeric == 12.34
    assert reading.carrier_id == carrier["carrier_id"]
    assert p.render_text(reading).splitlines() == [
        "Synthetic funding ratio, Synthetic Series: 12.34 %",
        (
            "\N{BLACK CIRCLE} Observed | period 2026-08-24 | known 2026-08-24 09:02 UTC"
            " | source Synthetic Publisher | attribution Synthetic attribution"
        ),
        "Limits: Synthetic limitation text.",
        "",
        p.DEFAULT_BOUNDARY,
    ]
    mcp_result = {
        "carrier_id": carrier["carrier_id"],
        "record_hash": carrier["record_hash"],
        "export_disposition": "full",
        "reason_codes": [],
        "evaluated_at": "2026-08-24T12:00:00Z",
    }
    again = p.reading_from_carrier(
        carrier,
        mcp_result,
        evaluated_at="2026-08-24T12:00:00Z",
        value_path=("ratio", "value"),
    )
    assert again.value == "12.34"


def test_reading_from_a_metadata_only_carrier_withholds_the_value() -> None:
    carrier = _carrier(rights_status="metadata_only")
    verified = verify_evidence_carrier(carrier, evaluated_at=EVALUATED_AT)
    reading = p.reading_from_carrier(
        carrier, verified, evaluated_at=EVALUATED_AT, value_path=("ratio", "value"), unit="%"
    )
    assert reading.state == p.RESTRICTED
    assert reading.value is None
    assert reading.label == "Synthetic funding ratio"
    lines = p.render_reading(reading).splitlines()
    assert lines[0] == (
        "Synthetic funding ratio, Synthetic Series: \N{CIRCLED DIVISION SLASH} Restricted"
    )
    assert lines[1].startswith("reason: rights_metadata_only | period 2026-08-24")
    assert "source Synthetic Publisher" in lines[1]


def test_a_rejected_carrier_discloses_nothing_beyond_identity_and_codes() -> None:
    carrier = _carrier(rights_status="restricted")
    verified = verify_evidence_carrier(carrier, evaluated_at=EVALUATED_AT)
    reading = p.reading_from_carrier(
        carrier, verified, evaluated_at=EVALUATED_AT, value_path=("ratio", "value")
    )
    assert reading.state == p.RESTRICTED
    assert reading.disposition == "reject"
    assert (reading.label, reading.subject, reading.period, reading.sources) == (
        None,
        None,
        None,
        (),
    )
    text = p.render_text(reading) + p.render_html(reading)
    for secret in ("Synthetic funding ratio", "Synthetic Series", "Synthetic Publisher", "12.34"):
        assert secret not in text
    assert p.render_reading(reading).splitlines() == [
        "Seiche: \N{CIRCLED DIVISION SLASH} Restricted",
        "reason: rights_restricted | checked 2026-08-24 12:00 UTC",
    ]
    named = p.reading_from_carrier(
        carrier,
        verified,
        evaluated_at=EVALUATED_AT,
        value_path=("ratio", "value"),
        label="Synthetic series the surface asked for",
    )
    assert named.label == "Synthetic series the surface asked for"


def test_reading_from_carrier_refuses_mismatched_or_unsafe_inputs() -> None:
    carrier = _carrier()
    other = _carrier(claim_status="derived")
    verified_other = verify_evidence_carrier(other, evaluated_at=EVALUATED_AT)
    with pytest.raises(ValueError, match="another carrier"):
        p.reading_from_carrier(
            carrier, verified_other, evaluated_at=EVALUATED_AT, value_path="ratio"
        )
    verified = verify_evidence_carrier(carrier, evaluated_at=EVALUATED_AT)
    mismatched_clock = {
        "export_disposition": "full",
        "reason_codes": [],
        "evaluated_at": "2026-08-24T12:00:01Z",
    }
    with pytest.raises(ValueError, match="different clock"):
        p.reading_from_carrier(
            carrier, mismatched_clock, evaluated_at=EVALUATED_AT, value_path="ratio"
        )
    widened = json.loads(json.dumps(carrier))
    widened["authority"]["can_execute"] = True
    with pytest.raises(ValueError, match="all-false"):
        p.reading_from_carrier(
            widened, verified, evaluated_at=EVALUATED_AT, value_path=("ratio", "value")
        )
    with pytest.raises(TypeError):
        p.reading_from_carrier(carrier, verified, evaluated_at=EVALUATED_AT, value_path="ratio")
    with pytest.raises(TypeError):
        p.reading_from_carrier(
            carrier, verified, evaluated_at="2026-08-24", value_path=("ratio", "value")
        )
    for path in (("ratio", "missing"), ("absent",), ("ratio", "value", "deeper")):
        missing = p.reading_from_carrier(
            carrier, verified, evaluated_at=EVALUATED_AT, value_path=path
        )
        assert missing.state == p.UNAVAILABLE
        assert missing.value is None
    placeholder = _carrier(payload={"ratio": {"value": "n/a"}})
    shown = p.reading_from_carrier(
        placeholder,
        verify_evidence_carrier(placeholder, evaluated_at=EVALUATED_AT),
        evaluated_at=EVALUATED_AT,
        value_path=("ratio", "value"),
    )
    assert shown.state == p.UNAVAILABLE


def test_a_product_row_makes_a_carrier_reading_less_calm_never_calmer() -> None:
    carrier = _carrier()
    verified = verify_evidence_carrier(carrier, evaluated_at=EVALUATED_AT)

    def read(product_state: p.ProductState) -> p.Reading:
        return p.reading_from_carrier(
            carrier,
            verified,
            evaluated_at=EVALUATED_AT,
            value_path=("ratio", "value"),
            product_state=product_state,
        )

    research = read(p.state_for("liquilens", "failure_radar", tier_publication="paused"))
    assert (research.state, research.state_label) == (p.RESEARCH, "Research only")
    assert research.value == "12.34"
    stale = read(p.state_for("liquilens", "stale"))
    assert (stale.state, stale.state_label, stale.value) == (p.STALE, "Stale evidence", None)
    calm = read(p.state_for("seiche", "derived"))
    assert calm.state == p.OBSERVED
    assert calm.state_label is None
    gated = p.reading_from_carrier(
        _carrier(rights_status="metadata_only"),
        verify_evidence_carrier(_carrier(rights_status="metadata_only"), evaluated_at=EVALUATED_AT),
        evaluated_at=EVALUATED_AT,
        value_path=("ratio", "value"),
        product_state=p.state_for("undertow", "STRAINED", scored=True),
    )
    assert (gated.state, gated.state_label, gated.severity) == (p.RESTRICTED, None, None)


def test_values_print_exactly_as_served() -> None:
    cases = (
        ("1,23,456.78", "INR crore", "1,23,456.78 INR crore"),
        (Decimal("12.340"), "%", "12.340 %"),
        (12.34, "%", "12.34 %"),
        (1e-07, "ratio", "1e-07 ratio"),
        (1234567, "count", "1234567 count"),
        ("0.25", "pp", "0.25 pp"),
    )
    for value, unit, expected in cases:
        reading = _value_reading(p.OBSERVED, value=value, unit=unit, subject=None)
        assert p.render_reading(reading).splitlines()[0] == f"Synthetic ratio: {expected}"
        assert _plain(p.render_html(reading)).count(expected.split(" ")[0]) == 1
    lakh = _value_reading(p.OBSERVED, value="1,23,456.78")
    assert math.isnan(p.reading_number(lakh))
    assert p.reading_number(_value_reading(p.OBSERVED, value="1,23,456.78", numeric=123456.78)) == (
        123456.78
    )
    no_unit = _value_reading(p.OBSERVED, unit=None, subject=None)
    assert p.render_reading(no_unit).splitlines()[0] == "Synthetic ratio: 12.34 (unit Not stated)"
    carrier = issue_evidence_carrier(
        **_descriptor(payload={"ratio": {"value": 100.0}})
    )
    exact = json.loads(json.dumps(carrier), parse_float=Decimal)
    reading = p.reading_from_carrier(
        exact,
        verify_evidence_carrier(carrier, evaluated_at=EVALUATED_AT),
        evaluated_at=EVALUATED_AT,
        value_path=("ratio", "value"),
        unit="%",
    )
    assert p.render_reading(reading).splitlines()[0].endswith(": 100.0 %")


def test_clocks_are_utc_and_never_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    if hasattr(time, "tzset"):
        time.tzset()
    try:
        assert p.format_period("2026-09-30T00:00:00Z") == "2026-09-30"
        assert p.format_period("2026-09-30") == "2026-09-30"
        assert p.format_period(date(2026, 9, 30)) == "2026-09-30"
        assert p.format_period("2026-09-30T18:30:00Z") == "2026-09-30 18:30 UTC"
        assert p.format_period("2026-10-01T00:00:00+05:30") == "2026-09-30 18:30 UTC"
        assert p.format_instant("2026-11-12T08:30:59.999999Z") == "2026-11-12 08:30 UTC"
        assert p.format_instant("2026-11-12T14:00:00+0530") == "2026-11-12 08:30 UTC"
        assert p.format_instant("2026-11-12 08:30z") == "2026-11-12 08:30 UTC"
        assert p.format_instant(datetime(2026, 11, 12, 8, 30, tzinfo=UTC)) == (
            "2026-11-12 08:30 UTC"
        )
        ist = timezone(timedelta(hours=5, minutes=30))
        assert p.format_instant(datetime(2026, 11, 12, 14, 0, tzinfo=ist)) == (
            "2026-11-12 08:30 UTC"
        )
        assert p.format_instant("2026-11-12") == "2026-11-12"
        for bad in (
            "2026-11-12T08:30:00",
            "12/11/2026",
            "2026-13-01",
            "2026-11-12T08:30:00+25:00",
        ):
            with pytest.raises(ValueError):
                p.parse_clock(bad)
        with pytest.raises(ValueError):
            p.parse_clock(datetime(2026, 11, 12, 8, 30))  # noqa: DTZ001
        proxy = _value_reading(p.OBSERVED, known_basis="proxy")
        line = p.render_reading(proxy).splitlines()[1]
        assert "known 2026-10-24 (availability proxy)" in line
        assert "published" not in line
    finally:
        monkeypatch.undo()
        if hasattr(time, "tzset"):
            time.tzset()


def test_the_contract_examples_render_exactly() -> None:
    value = p.Reading(
        state=p.OBSERVED,
        label="Example ratio",
        value="12.34",
        unit="%",
        period="2026-09-30",
        known_at="2026-10-24",
        sources=("Example Publisher",),
        limits="<the producer's limitation text, verbatim>",
    )
    assert p.render_reading(value) == (
        "Example ratio: 12.34 %\n"
        "\N{BLACK CIRCLE} Observed | period 2026-09-30 | known 2026-10-24 "
        "| source Example Publisher\n"
        "Limits: <the producer's limitation text, verbatim>"
    )
    gap = p.Reading(
        state=p.UNAVAILABLE,
        label="Example series",
        subject="Example State",
        reason="<the producer's reason, verbatim>",
        checked_at="2026-11-12T08:30:00Z",
    )
    assert p.render_reading(gap) == (
        "Example series, Example State: "
        "\N{SQUARE WITH UPPER RIGHT TO LOWER LEFT FILL} Unavailable\n"
        "reason: <the producer's reason, verbatim> | checked 2026-11-12 08:30 UTC"
    )


def test_the_boundary_prints_once_per_view() -> None:
    readings = _every_reading()
    assert p.render_text(readings).count(p.DEFAULT_BOUNDARY) == 1
    sentence = "Research context from public records. Not a rating, a credit decision or a forecast."
    own = [_value_reading(p.OBSERVED, boundary=sentence), _gap_reading(p.STALE, boundary=sentence)]
    text = p.render_text(own)
    assert text.count(sentence) == 1
    assert p.DEFAULT_BOUNDARY not in text
    assert p.render_text(readings, boundary=sentence).count(sentence) == 1
    assert p.render_text([]) == p.DEFAULT_BOUNDARY


def test_producer_text_cannot_break_lines_or_reorder_them() -> None:
    sneaky = "Synthetic\n\N{BLACK CIRCLE} Observed | period 2026\r\N{RIGHT-TO-LEFT OVERRIDE}x"
    reading = _gap_reading(p.UNAVAILABLE, label=sneaky, reason=sneaky, limits=sneaky)
    lines = p.render_reading(reading).splitlines()
    assert len(lines) == 3
    assert lines[0].endswith("Unavailable")
    assert "\N{RIGHT-TO-LEFT OVERRIDE}" not in "".join(lines)
    assert "\\u202e" in lines[1]


# ---------------------------------------------------------------------------
# Data frames.


def test_display_columns_extend_the_flat_row_without_renaming() -> None:
    carrier = _carrier()
    verified = verify_evidence_carrier(carrier, evaluated_at=EVALUATED_AT)
    flat = to_flat_row(verified)
    reading = p.reading_from_carrier(
        carrier, verified, evaluated_at=EVALUATED_AT, value_path=("ratio", "value"), unit="%"
    )
    row = p.add_display_columns(flat, reading)
    assert list(row)[: len(flat)] == list(flat)
    assert all(row[key] == flat[key] for key in flat)
    assert list(row)[len(flat) :] == list(p.DISPLAY_COLUMNS)
    assert row["display_state"] == "Observed"
    assert row["value"] == 12.34
    assert row["unit"] == "%"
    assert row["source_publisher"] == "Synthetic Publisher"

    expired_clock = datetime(2026, 8, 24, 13, tzinfo=UTC)
    expired = verify_evidence_carrier(carrier, evaluated_at=expired_clock)
    stale = p.reading_from_carrier(
        carrier, expired, evaluated_at=expired_clock, value_path=("ratio", "value")
    )
    stale_row = p.add_display_columns(to_flat_row(expired), stale)
    assert stale_row["display_state"] == "Stale"
    assert math.isnan(stale_row["value"])
    assert stale_row["unit"] == p.NOT_STATED

    with pytest.raises(ValueError, match="display columns"):
        p.add_display_columns({**flat, "value": 1}, reading)
    with pytest.raises(ValueError, match="different carriers"):
        p.add_display_columns({**flat, "carrier_id": "evidence_" + "1" * 24}, reading)


# ---------------------------------------------------------------------------
# Terminals.


class _Stream:
    def __init__(self, *, tty: bool = True, encoding: str = "utf-8", broken: bool = False):
        self.tty = tty
        self.encoding = encoding
        self.broken = broken

    def isatty(self) -> bool:
        if self.broken:
            raise ValueError("synthetic closed stream")
        return self.tty


def test_terminal_style_honours_no_color_ttys_and_colour_depth() -> None:
    tty = _Stream()
    assert p.terminal_style(tty, {"NO_COLOR": "1", "COLORTERM": "truecolor"}) == ("ascii", "none")
    assert p.terminal_style(_Stream(tty=False), {"COLORTERM": "truecolor"}) == ("ascii", "none")
    assert p.terminal_style(_Stream(broken=True), {}) == ("ascii", "none")
    assert p.terminal_style(tty, {"TERM": "dumb"}) == ("ascii", "none")
    assert p.terminal_style(object(), {}) == ("ascii", "none")
    assert p.terminal_style(tty, {"NO_COLOR": "", "COLORTERM": "24bit"}) == ("unicode", "truecolor")
    assert p.terminal_style(tty, {"COLORTERM": "truecolor"}) == ("unicode", "truecolor")
    assert p.terminal_style(tty, {"TERM": "xterm-256color"}) == ("unicode", "ansi256")
    assert p.terminal_style(tty, {"TERM": "xterm"}) == ("unicode", "ansi16")
    assert p.terminal_style(_Stream(encoding="ascii"), {"TERM": "xterm"}) == ("ascii", "ansi16")


# ---------------------------------------------------------------------------
# Vendoring: one stdlib file that runs on Python 3.10.

FORBIDDEN_NAMES = {
    "StrEnum",
    "Self",
    "UTC",
    "fromisoformat",
    "Never",
    "assert_never",
    "LiteralString",
    "NotRequired",
    "Required",
    "tomllib",
    "ExceptionGroup",
}


def test_present_is_one_stdlib_file_that_suits_python_310() -> None:
    source = PRESENT_SOURCE.read_text(encoding="utf-8")
    assert PRESENT_SOURCE.read_bytes().isascii()
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        assert type(node).__name__ != "TryStar"
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert node.module is not None
            imported.add(node.module.split(".")[0])
            assert not {alias.name for alias in node.names} & FORBIDDEN_NAMES
        elif isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.Attribute):
            assert node.attr not in FORBIDDEN_NAMES
        elif isinstance(node, ast.Name):
            assert node.id not in FORBIDDEN_NAMES
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}
    assert "liquilens_evidence" not in imported


STANDALONE = """
import sys
sys.path.insert(0, sys.argv[1])
import liquilens_present as p
assert "liquilens_evidence" not in sys.modules
count = 0
for key in p.STATE_ORDER:
    if key in p.VALUE_STATES:
        reading = p.Reading(state=key, label="Synthetic ratio", value="12.34", unit="%",
                            period="2026-09-30T00:00:00Z", known_at="2026-10-24T09:15:00Z")
    else:
        reading = p.Reading(state=key, label="Synthetic series", reason="Synthetic",
                            checked_at="2026-11-12T08:30:00Z")
    for marks in p.MARK_MODES:
        for colour in p.COLOUR_MODES:
            assert p.DISPLAY_STATES[key].word in p.render_text(reading, marks=marks, colour=colour)
            count += 1
    assert "<script" not in p.render_html(reading)
assert p.state_for("undertow", "STRAINED", scored=True).severity == "warn"
assert p.check_label("undertow", "indeterminate") == "Indeterminate"
assert p.format_instant("2026-11-12T14:00:00+05:30") == "2026-11-12 08:30 UTC"
print("ok", count, "liquilens_evidence" in sys.modules)
"""


def _interpreters() -> list[str]:
    found = [sys.executable]
    older = shutil.which("python3.10")
    if older is not None:
        found.append(older)
    return found


@pytest.mark.parametrize("interpreter", _interpreters())
def test_a_vendored_copy_runs_alone(tmp_path: Path, interpreter: str) -> None:
    vendored = tmp_path / "vendor"
    vendored.mkdir()
    shutil.copyfile(PRESENT_SOURCE, vendored / "liquilens_present.py")
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    completed = subprocess.run(
        [interpreter, "-I", "-S", "-c", STANDALONE, str(vendored)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ok 80 False"


# ---------------------------------------------------------------------------
# (n) Severity words, never-hidden checks, capped views and dashes.

SEVERITY_WORDS = {"CALM": "good", "WATCH": "warn", "ALARM": "bad"}


def test_liquilens_severity_words_ride_beside_an_observed_reading() -> None:
    for word, severity in SEVERITY_WORDS.items():
        row = p.state_for("liquilens", word)
        shown = p.reading_from_native(row, label="Synthetic leg", value="11.0", unit="bp")
        assert (shown.state, shown.state_label, shown.severity) == (
            p.OBSERVED,
            word,
            severity,
        )
        assert f"Observed ({word})" in p.render_reading(shown)
        missing = p.reading_from_native(row, label="Synthetic leg")
        assert (missing.state, missing.severity) == (p.UNAVAILABLE, None)
        assert word not in p.render_reading(missing)
        with pytest.raises(TypeError):
            p.state_for("liquilens", word, scored=True)
        for other in ("seiche", "undertow"):
            with pytest.raises(ValueError):
                p.state_for(other, word)


def test_never_hidden_checks_are_known_check_words() -> None:
    for product, words in p.NEVER_HIDDEN_CHECKS.items():
        for word in words:
            assert p.check_label(product, word)
    assert {"inconsistent", "missed", "missed_fraud_masked"} <= p.NEVER_HIDDEN_CHECKS[
        "liquilens"
    ]
    assert "mismatch" in p.NEVER_HIDDEN_CHECKS["undertow"]


def _served_mix(pattern: str) -> list[p.Reading]:
    """'g' is a gap reading and 'v' a value reading, in served order."""

    readings = []
    for index, kind in enumerate(pattern):
        label = f"Synthetic reading {index}"
        if kind == "g":
            readings.append(_gap_reading(p.UNAVAILABLE, label=label, subject=None))
        else:
            readings.append(_value_reading(p.OBSERVED, label=label, subject=None))
    return readings


def _shown_kinds(text: str, pattern: str) -> list[str]:
    found = re.findall(r"^Synthetic reading (\d+):", text, flags=re.MULTILINE)
    return [pattern[int(index)] for index in found]


@pytest.mark.parametrize(
    "pattern", ["", "v", "g", "vg", "gv", "vvgvgg", "gggvvv", "vg" * 10, "v" * 7 + "g"]
)
def test_a_capped_view_counts_first_then_gaps_and_holds_its_cap(pattern: str) -> None:
    readings = _served_mix(pattern)
    whole = p.render_text(readings, max_bytes=10**9)
    assert p.FULL_LIST_LINE not in whole
    for cap in range(1, len(whole.encode("utf-8")) + 2):
        try:
            text = p.render_text(readings, max_bytes=cap)
        except ValueError:
            continue
        assert len(text.encode("utf-8")) <= cap
        blocks = text.split("\n\n")
        assert blocks[-1] == p.DEFAULT_BOUNDARY
        if len(readings) > 1:
            assert blocks[0].startswith(f"Readings: {len(readings)}. ")
        kinds = _shown_kinds(text, pattern)
        assert kinds == sorted(kinds, key=lambda kind: kind != "g")
        if "v" in kinds:
            assert kinds.count("g") == pattern.count("g")
        cut = len(kinds) < len(pattern)
        assert (p.FULL_LIST_LINE in blocks) is cut
        if cut:
            assert blocks[-2] == p.FULL_LIST_LINE
    smallest = p.render_text(readings, max_bytes=len(whole.encode("utf-8")))
    assert smallest == whole


def test_an_uncapped_view_is_unchanged() -> None:
    readings = _served_mix("vgvvg")
    expected = "\n\n".join(
        [*(p.render_reading(reading) for reading in readings), p.DEFAULT_BOUNDARY]
    )
    assert p.render_text(readings) == expected


@pytest.mark.parametrize("cap", [True, 1.5, "4096"])
def test_a_cap_must_be_a_whole_number(cap: Any) -> None:
    with pytest.raises(TypeError):
        p.render_text(_served_mix("vg"), max_bytes=cap)


@pytest.mark.parametrize("cap", [0, -1, 10])
def test_a_cap_too_small_for_the_fixed_lines_raises(cap: int) -> None:
    with pytest.raises(ValueError):
        p.render_text(_served_mix("vg"), max_bytes=cap)


def test_dashes_in_producer_text_print_as_hyphens_in_text_and_html() -> None:
    dashed = f"Synthetic {EN_DASH} text {EM_DASH} here"
    source = p.SourceRef(
        publisher=dashed, title=dashed, url=f"https://example.org/a{EN_DASH}b"
    )
    reading = p.reading_from_native(
        p.state_for("liquilens", "observed"),
        label=dashed,
        subject=dashed,
        value=f"2024{EN_DASH}25",
        unit=dashed,
        sources=[source],
        attribution=dashed,
        limits=dashed,
        boundary=dashed,
    )
    gap = p.reading_from_native(
        p.state_for("liquilens", "unavailable"), label=dashed, reason=dashed, limits=dashed
    )
    views = [
        p.render_text([reading, gap]),
        p.render_text([reading, gap], max_bytes=4096),
        p.render_html([reading, gap]),
    ]
    for text in views:
        assert EN_DASH not in text and EM_DASH not in text
        assert "Synthetic - text - here" in text
    assert "2024-25" in views[0]
    assert "example.org/a" not in views[2]


def test_the_mcp_example_takes_its_clock_from_the_served_payload() -> None:
    page = DOCS_PAGE.read_text(encoding="utf-8")
    section = page[page.index("### An MCP tool result") : page.index("### A notebook table")]
    assert "now(" not in section
    assert 'checked_at=served["retrieved_at"]' in section
