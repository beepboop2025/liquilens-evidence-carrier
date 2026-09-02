"""Keep live release receipts separate from failed and channel-specific state."""

import hashlib
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE_VERSION = "0.17.1"
RELEASE_CANDIDATE = "a74274236e177404c2d254541e6a4110a4ce8a0d"
RELEASE_TAG_OBJECT = "8844ee4556d59472a587cb9ceb412112c23543db"
RELEASE_PREFLIGHT = "33589423934"
RELEASE_WORKFLOW = "33589489958"
RELEASE_CONTAINER_WORKFLOW = "33589489966"
RELEASE_SHA256SUMS = (
    "666924e261c7760bc598713598390be6b1ca7d0854b5746811fb990cf951cf46"
)
RELEASE_WHEEL_SHA256 = (
    "dec2751fa2f20d09a1a77b5f25ae99f28fa49484ea1bf5ede7ca2bcdd86610ea"
)
RELEASE_MCPB_SHA256 = (
    "4d6c409f2c69588fad6fe13bf2f78ed1b72d3555d81082d5da638d037b0307a1"
)
RELEASE_OCI_DIGEST = (
    "bd9b92f25fa8666ea1f43afc4047261ad82213f3c121da87f4dcb9f2e401776d"
)
RELEASE_README_SHA256 = (
    "8422e21dc715443c22c8d18e1991fa8427136292a06ee45068db4a1a26029c9e"
)
FAILED_VERSION = "0.17.0"
FAILED_TAG_OBJECT = "cb85e527c2b74abf476fd9a01b73b2235ce976b7"
FAILED_TAG_TARGET = "edde9b92ad9851d2974b91326a8c3877f4386d3a"
FAILED_WORKFLOW = "33585764285"
FAILED_README_SHA256 = (
    "ec252e147ed8e835ba4eaf3a2a4132ab70f3739b14eb0a0610766c3574b51767"
)
PRIOR_VERSION = "0.16.0"
PRIOR_COMMIT = "410f7d91114fba715e9a9ae830faa775064a4502"
PRIOR_WORKFLOW = "33261143612"
PRIOR_WHEEL_SHA256 = (
    "317c06b728a2b087eca3d51ba1cdf3f7570e4078334829959008ceb0a29dfd11"
)
PRIOR_MCPB_SHA256 = (
    "c44b13b2efc4622a8ecfc06848f32358982dd2a9458a271e1ed77d646791961a"
)
PRIOR_RELEASE_RECORD_SHA256 = (
    "6898b3f0e5b1856d165ac1b8ea74503e8d6d24401b2a77e77904be9a617f0048"
)
PRIOR_README_SHA256 = (
    "10706d94c666c9376bd212ec31bb9206b7e1b697ed6529ac2b6dc647c9f4b28d"
)


def test_main_facing_docs_record_published_v0171_and_separate_deployment():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    release_receipt = (
        ROOT / f"docs/RELEASE-{RELEASE_VERSION}.md"
    ).read_text(encoding="utf-8")
    release_bundle_readme = (
        ROOT / "mcpb/release-readmes" / f"{RELEASE_VERSION}.md"
    ).read_text(encoding="utf-8")
    failed_release = (
        ROOT / f"docs/RELEASE-{FAILED_VERSION}.md"
    ).read_text(encoding="utf-8")
    distribution = (ROOT / "DISTRIBUTION.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    normalized_release = " ".join(release_receipt.split())
    normalized_readme = " ".join(readme.split())

    for text in (readme, release_receipt, distribution, changelog):
        assert RELEASE_CANDIDATE in text
        assert RELEASE_WORKFLOW in text
        assert RELEASE_MCPB_SHA256 in text

    for text in (readme, release_receipt, distribution):
        assert RELEASE_TAG_OBJECT in text
        assert RELEASE_PREFLIGHT in text
        assert RELEASE_WHEEL_SHA256 in text

    for text in (release_receipt, distribution, changelog):
        assert RELEASE_CONTAINER_WORKFLOW in text
        assert RELEASE_OCI_DIGEST in text

    assert f"current signed and published core release is `v{RELEASE_VERSION}`" in (
        readme
    )
    assert "signed, published, attested, and active/latest" in normalized_release
    assert f"current core implementation release is `v{RELEASE_VERSION}`" in (
        distribution
    )
    assert f"## [{RELEASE_VERSION}] - 2026-09-02" in changelog
    assert f"## [{PRIOR_VERSION}] - 2026-08-29" in changelog
    assert RELEASE_SHA256SUMS in release_receipt
    assert "attestations/44596593" in release_receipt
    assert "reports `active` and `isLatest: true`" in normalized_release
    assert "not a hosted Trade Safety gateway" in normalized_release
    assert "canonical URL not hosted yet" in normalized_readme
    assert "returned HTTP 404" in normalized_readme

    for text in (readme, failed_release):
        assert FAILED_TAG_OBJECT in text
        assert FAILED_TAG_TARGET in text
        assert FAILED_WORKFLOW in text

    assert "no v0.17.0 GitHub release" in readme
    assert "GitHub has no v0.17.0 release record" in failed_release
    assert "must not be deleted, force-moved, or recreated" in failed_release
    assert "not publication proof" in release_bundle_readme
    assert "local and remote absence of `v0.17.1` before tag creation" in (
        release_receipt
    )
    assert "two byte-identical deterministic MCPB builds" in release_receipt
    assert "The GitHub release makes all Trade Safety contracts" in release_receipt
    normalized_failed = " ".join(failed_release.split())
    assert "stable unversioned protocol filenames remain intentionally reusable" in (
        normalized_failed
    )
    assert "A later recovery release may publish" in failed_release
    assert "The release can publish" not in failed_release
    assert "Published release `v0.17.1` provides" in readme

    published_record = f"{release_receipt}\n{distribution}"
    for stale_claim in (
        "not tagged, published, or registered",
        f"future `v{RELEASE_VERSION}` asset URL",
        (
            "latest signed,\ndownloadable, and Registry-listed release was immutable "
            "`v0.15.0`"
        ),
        "The immutable public implementation release is `v0.15.0`",
        f"The repository source is preparing `v{RELEASE_VERSION}`",
    ):
        assert stale_claim not in published_record


def test_published_registry_metadata_tracks_release_version():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    package = server["packages"][0]

    assert version == RELEASE_VERSION
    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == version
    assert server["version"] == version
    assert package["identifier"].endswith(
        f"/v{version}/liquilens-evidence-carrier-mcp-{version}.mcpb"
    )


def test_published_v016_records_and_embedded_readme_stay_reproducible():
    published_release = ROOT / f"docs/RELEASE-{PRIOR_VERSION}.md"
    published_text = published_release.read_text(encoding="utf-8")
    assert (
        hashlib.sha256(published_release.read_bytes()).hexdigest()
        == PRIOR_RELEASE_RECORD_SHA256
    )
    for receipt in (
        PRIOR_COMMIT,
        PRIOR_WORKFLOW,
        PRIOR_WHEEL_SHA256,
        PRIOR_MCPB_SHA256,
    ):
        assert receipt in published_text

    frozen = ROOT / "mcpb/release-readmes" / f"{PRIOR_VERSION}.md"
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == PRIOR_README_SHA256
    assert frozen.read_bytes() != (ROOT / "README.md").read_bytes()

    builder = (ROOT / "scripts/build_mcpb.py").read_text(encoding="utf-8")
    assert 'ROOT / "mcpb/release-readmes" / f"{version}.md"' in builder
    assert '(release_readme, "README.md")' in builder


def test_published_v0171_embedded_readme_stays_reproducible():
    frozen = ROOT / "mcpb/release-readmes" / f"{RELEASE_VERSION}.md"
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == RELEASE_README_SHA256
    frozen_text = frozen.read_text(encoding="utf-8")
    normalized_frozen = " ".join(frozen_text.split())
    assert "bytes prepared for the v0.17.1 MCPB" in normalized_frozen
    assert "not publication proof" in frozen_text
    assert frozen.read_bytes() != (ROOT / "README.md").read_bytes()


def test_failed_v0170_embedded_readme_stays_reproducible():
    frozen = ROOT / "mcpb/release-readmes" / f"{FAILED_VERSION}.md"
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == FAILED_README_SHA256
    frozen_text = frozen.read_text(encoding="utf-8")
    assert "bytes prepared for the v0.17.0 MCPB" in frozen_text
    assert "not publication proof" in frozen_text
