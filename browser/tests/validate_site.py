"""Static security, accessibility, and release checks for the Pages bundle."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "browser"


class SiteParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []
        self.text: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.tags.append((tag, {key: value or "" for key, value in attrs}))

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def main() -> int:
    html = (SITE / "index.html").read_text(encoding="utf-8")
    parser = SiteParser()
    parser.feed(html)
    tags = parser.tags

    html_attrs = next(attrs for tag, attrs in tags if tag == "html")
    assert html_attrs.get("lang") == "en"
    assert any(tag == "main" for tag, _attrs in tags)
    assert sum(tag == "h1" for tag, _attrs in tags) == 1

    ids = [attrs["id"] for _tag, attrs in tags if "id" in attrs]
    assert len(ids) == len(set(ids)), "HTML ids must be unique"
    id_set = set(ids)
    for _tag, attrs in tags:
        if "for" in attrs:
            assert attrs["for"] in id_set, f"missing labelled control {attrs['for']}"
        assert not any(name.lower().startswith("on") for name in attrs)
        assert "style" not in attrs

    text = " ".join(parser.text)
    for expected in (
        "LiquiLens Evidence",
        "Protocol 1.0",
        "Release 0.15.0",
        "Apache-2.0",
        "Uploads",
        "Analytics",
        "Runtime dependencies",
    ):
        assert expected in text

    metas = [attrs for tag, attrs in tags if tag == "meta"]
    assert any(attrs.get("name") == "viewport" for attrs in metas)
    assert any(attrs.get("name") == "referrer" and attrs.get("content") == "no-referrer" for attrs in metas)
    csp = next(
        attrs["content"]
        for attrs in metas
        if attrs.get("http-equiv", "").lower() == "content-security-policy"
    )
    for directive in (
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "connect-src 'none'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
    ):
        assert directive in csp

    assert any(attrs.get("role") == "alert" for _tag, attrs in tags)
    assert any(
        attrs.get("role") == "status" and attrs.get("aria-live") == "polite"
        for _tag, attrs in tags
    )
    textarea = next(attrs for tag, attrs in tags if tag == "textarea")
    assert textarea.get("aria-describedby") == "editor-help"
    assert any(
        tag == "a" and attrs.get("class") == "skip-link" and attrs.get("href") == "#carrier-input"
        for tag, attrs in tags
    )

    for tag, attrs in tags:
        if tag == "script" and "src" in attrs:
            assert not urlparse(attrs["src"]).scheme
            assert (SITE / attrs["src"].removeprefix("./")).is_file()
        if tag == "link" and "stylesheet" in attrs.get("rel", "").split():
            assert not urlparse(attrs["href"]).scheme
            assert (SITE / attrs["href"].removeprefix("./")).is_file()
        if tag == "a" and urlparse(attrs.get("href", "")).scheme:
            rel = set(attrs.get("rel", "").split())
            assert {"noreferrer", "noopener"} <= rel

    javascript = "\n".join(
        path.read_text(encoding="utf-8") for path in SITE.glob("*.mjs")
    )
    forbidden_runtime_apis = (
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "EventSource",
        "sendBeacon",
        "document.cookie",
        "localStorage",
        "sessionStorage",
    )
    for token in forbidden_runtime_apis:
        assert token not in javascript
    assert "innerHTML" not in javascript
    assert "eval(" not in javascript

    stylesheet = (SITE / "styles.css").read_text(encoding="utf-8")
    assert "url(" not in stylesheet.lower()
    assert "prefers-reduced-motion" in stylesheet
    assert ":focus-visible" in stylesheet

    source_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    catalog = json.loads((ROOT / "protocol/catalog.json").read_text(encoding="utf-8"))
    published_version = "0.15.0"
    assert catalog["release"] == source_version
    assert f'RELEASE_VERSION = "{published_version}"' in (
        SITE / "verifier.mjs"
    ).read_text()
    assert re.search(rf"Release\s+{re.escape(published_version)}", text)
    assert (SITE / ".nojekyll").is_file()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
