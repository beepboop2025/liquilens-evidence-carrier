"""Prevent core release receipts from drifting back to preparation state."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE_VERSION = "0.16.0"
RELEASE_COMMIT = "410f7d91114fba715e9a9ae830faa775064a4502"
RELEASE_WORKFLOW = "33261143612"
WHEEL_SHA256 = "317c06b728a2b087eca3d51ba1cdf3f7570e4078334829959008ceb0a29dfd11"
MCPB_SHA256 = "c44b13b2efc4622a8ecfc06848f32358982dd2a9458a271e1ed77d646791961a"
RELEASE_README_SHA256 = (
    "10706d94c666c9376bd212ec31bb9206b7e1b697ed6529ac2b6dc647c9f4b28d"
)


def test_main_facing_docs_record_the_published_core_release():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    release = (ROOT / "docs/RELEASE-0.16.0.md").read_text(encoding="utf-8")
    distribution = (ROOT / "DISTRIBUTION.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    for text in (readme, release):
        assert RELEASE_COMMIT in text
        assert RELEASE_WORKFLOW in text
        assert WHEEL_SHA256 in text
        assert MCPB_SHA256 in text

    assert f"v{RELEASE_VERSION}` core release was published" in readme
    assert "signed, published, attested, and active" in release
    assert f"core implementation release is `v{RELEASE_VERSION}`" in distribution
    assert f"## [{RELEASE_VERSION}] - 2026-08-29" in changelog

    current_docs = f"{readme}\n{release}\n{distribution}"
    for stale_claim in (
        "source candidate",
        "not tagged, published, or registered",
        "future `v0.16.0` asset URL",
        (
            "latest signed,\ndownloadable, and Registry-listed release was immutable "
            "`v0.15.0`"
        ),
        "The immutable public implementation release is `v0.15.0`",
        "The repository source is preparing `v0.16.0`",
    ):
        assert stale_claim not in current_docs


def test_published_mcpb_identity_and_embedded_readme_stay_reproducible():
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    assert server["version"] == RELEASE_VERSION
    assert server["packages"][0]["fileSha256"] == MCPB_SHA256

    frozen = ROOT / "mcpb/release-readmes" / f"{RELEASE_VERSION}.md"
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == RELEASE_README_SHA256
    assert frozen.read_bytes() != (ROOT / "README.md").read_bytes()

    builder = (ROOT / "scripts/build_mcpb.py").read_text(encoding="utf-8")
    assert 'ROOT / "mcpb/release-readmes" / f"{version}.md"' in builder
    assert '(release_readme, "README.md")' in builder
