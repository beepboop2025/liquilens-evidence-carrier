"""Keep candidate metadata separate from immutable published receipts."""

import hashlib
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_VERSION = "0.17.0"
PUBLISHED_VERSION = "0.16.0"
PUBLISHED_COMMIT = "410f7d91114fba715e9a9ae830faa775064a4502"
PUBLISHED_WORKFLOW = "33261143612"
PUBLISHED_WHEEL_SHA256 = (
    "317c06b728a2b087eca3d51ba1cdf3f7570e4078334829959008ceb0a29dfd11"
)
PUBLISHED_MCPB_SHA256 = (
    "c44b13b2efc4622a8ecfc06848f32358982dd2a9458a271e1ed77d646791961a"
)
PUBLISHED_RELEASE_RECORD_SHA256 = (
    "6898b3f0e5b1856d165ac1b8ea74503e8d6d24401b2a77e77904be9a617f0048"
)
PUBLISHED_README_SHA256 = (
    "10706d94c666c9376bd212ec31bb9206b7e1b697ed6529ac2b6dc647c9f4b28d"
)


def test_main_facing_docs_distinguish_candidate_from_published_release():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    published_release = (
        ROOT / f"docs/RELEASE-{PUBLISHED_VERSION}.md"
    ).read_text(encoding="utf-8")
    candidate_release = (
        ROOT / f"docs/RELEASE-{CANDIDATE_VERSION}.md"
    ).read_text(encoding="utf-8")
    distribution = (ROOT / "DISTRIBUTION.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    for text in (readme, published_release):
        assert PUBLISHED_COMMIT in text
        assert PUBLISHED_WORKFLOW in text
        assert PUBLISHED_WHEEL_SHA256 in text
        assert PUBLISHED_MCPB_SHA256 in text

    assert f"versioned for `v{CANDIDATE_VERSION}`" in readme
    assert f"not published as `v{CANDIDATE_VERSION}`" in readme
    assert f"latest signed core\nrelease remains `v{PUBLISHED_VERSION}`" in readme
    assert "signed, published, attested, and active" in published_release
    assert "not tagged, published, registered, or" in candidate_release
    assert f"current core implementation release is `v{PUBLISHED_VERSION}`" in (
        distribution
    )
    assert f"preparing `v{CANDIDATE_VERSION}`" in distribution
    assert f"## [{CANDIDATE_VERSION}] - 2026-09-02" in changelog
    assert f"## [{PUBLISHED_VERSION}] - 2026-08-29" in changelog

    published_record = f"{published_release}\n{distribution}"
    for stale_claim in (
        "not tagged, published, or registered",
        f"future `v{PUBLISHED_VERSION}` asset URL",
        (
            "latest signed,\ndownloadable, and Registry-listed release was immutable "
            "`v0.15.0`"
        ),
        "The immutable public implementation release is `v0.15.0`",
        f"The repository source is preparing `v{PUBLISHED_VERSION}`",
    ):
        assert stale_claim not in published_record


def test_candidate_registry_metadata_tracks_source_version():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    package = server["packages"][0]

    assert version == CANDIDATE_VERSION
    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == version
    assert server["version"] == version
    assert package["identifier"].endswith(
        f"/v{version}/liquilens-evidence-carrier-mcp-{version}.mcpb"
    )


def test_published_v016_records_and_embedded_readme_stay_reproducible():
    published_release = ROOT / f"docs/RELEASE-{PUBLISHED_VERSION}.md"
    assert (
        hashlib.sha256(published_release.read_bytes()).hexdigest()
        == PUBLISHED_RELEASE_RECORD_SHA256
    )

    frozen = ROOT / "mcpb/release-readmes" / f"{PUBLISHED_VERSION}.md"
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == PUBLISHED_README_SHA256
    assert frozen.read_bytes() != (ROOT / "README.md").read_bytes()

    builder = (ROOT / "scripts/build_mcpb.py").read_text(encoding="utf-8")
    assert 'ROOT / "mcpb/release-readmes" / f"{version}.md"' in builder
    assert '(release_readme, "README.md")' in builder
