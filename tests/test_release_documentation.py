"""Keep live release receipts separate from failed and channel-specific state."""

import hashlib
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_VERSION = "0.19.0"
SOURCE_MCPB_SHA256 = (
    "692f19b3b202fe9a6a8601532e0728f36e406665dfddd09643a1d737d2b5ef74"
)
SOURCE_README_SHA256 = (
    "2d1b4dce5431451510d786f70a5a8e401180f4dd8e4820025e101444e5a97aa6"
)
GATEWAY_VERSION = "0.1.2"
CANONICAL_SITE_REVISION = "3ec660175c81c5b282715ee400eea2f771dc2610"
CANONICAL_SITE_WORKFLOW = "33592149926"
RELEASE_VERSION = "0.18.0"
RELEASE_CANDIDATE = "906ca033a96ea862ab813c64db2a6b01c5ce8c4f"
RELEASE_TREE = "0065206e14a21bb01ce25caed60bf14c9570d12f"
RELEASE_TAG_OBJECT = "42dd412ef27b470841b71b8bc73c0ed63a5e4a6b"
RELEASE_PREFLIGHT = "33593756967"
RELEASE_WORKFLOW = "33593840364"
RELEASE_CONTAINER_WORKFLOW = "33593840346"
RELEASE_SHA256SUMS = (
    "71c2c884d16fd3315a21c263ec8254b0f9578c8150f4a424c296228668d89953"
)
RELEASE_WHEEL_SHA256 = (
    "9fbc7ee50f658e2a8d1d880f8f76d73dca8b07ef6f0747df33a7b9fc346495ef"
)
RELEASE_MCPB_SHA256 = (
    "f57ce3fb488b693e633d8bc66f980b616af09a8080722a11c50507496f39a2bb"
)
RELEASE_GATEWAY_WHEEL_SHA256 = (
    "103cde79c006074eaabe5083fec212ba237fcf3a42f01b0600e0faf0328a05a8"
)
RELEASE_OCI_DIGEST = (
    "293a9ec61ad43f9bac22775936271b19651b486115ab53acbe7928cb177f8c4e"
)
RELEASE_README_SHA256 = (
    "3cc9705a2c1aa0471342199f54509b2aa66a02a2c84d89287732a89cd026018a"
)
RELEASE_ATTESTATION = "44605007"
RELEASE_CONTAINER_ATTESTATION = "44605376"
PREVIOUS_VERSION = "0.17.1"
PREVIOUS_CANDIDATE = "a74274236e177404c2d254541e6a4110a4ce8a0d"
PREVIOUS_TAG_OBJECT = "8844ee4556d59472a587cb9ceb412112c23543db"
PREVIOUS_RELEASE_RECORD_SHA256 = (
    "c089ee719ac4e6eba99936f4daf50681b045694b63aba7effea307aa48e93dd8"
)
PREVIOUS_README_SHA256 = (
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


def test_main_facing_docs_record_published_v0180_and_preserve_history():
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
    previous_release = (
        ROOT / f"docs/RELEASE-{PREVIOUS_VERSION}.md"
    ).read_text(encoding="utf-8")
    candidate_release = (
        ROOT / f"docs/RELEASE-{SOURCE_VERSION}.md"
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
        assert RELEASE_TREE in text
        assert RELEASE_TAG_OBJECT in text
        assert RELEASE_PREFLIGHT in text
        assert RELEASE_WHEEL_SHA256 in text

    for text in (release_receipt, distribution, changelog):
        assert RELEASE_CONTAINER_WORKFLOW in text
        assert RELEASE_OCI_DIGEST in text
        assert RELEASE_GATEWAY_WHEEL_SHA256 in text
        assert RELEASE_ATTESTATION in text
        assert RELEASE_CONTAINER_ATTESTATION in text

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
    assert f"attestations/{RELEASE_ATTESTATION}" in release_receipt
    assert "reports `active` and `isLatest: true`" in normalized_release
    assert "not a hosted Trade Safety gateway" in normalized_release
    assert "immutable: false" in release_receipt
    assert "21288366" in release_receipt
    assert "canonical URL not hosted yet" not in normalized_readme
    assert "returned HTTP 404" not in normalized_readme
    for text in (readme, distribution, changelog, release_receipt):
        assert CANONICAL_SITE_REVISION in text
        assert CANONICAL_SITE_WORKFLOW in text
    for text in (readme, distribution, changelog, candidate_release):
        assert SOURCE_MCPB_SHA256 in text

    assert f"source checkpoint prepares `v{SOURCE_VERSION}`" in readme
    assert "not yet tagged, published, or registered" in normalized_readme
    assert "No v0.18.0 tag object" not in release_receipt
    assert f"## [{SOURCE_VERSION}] - 2026-09-02" in changelog
    assert "No `v0.19.0` tag, GitHub release" in changelog
    assert "prepared source; not tagged, published, registered, or" in (
        candidate_release
    )
    assert "There is no v0.19.0 tag object" in candidate_release

    for text in (readme, failed_release):
        assert FAILED_TAG_OBJECT in text
        assert FAILED_TAG_TARGET in text
        assert FAILED_WORKFLOW in text

    assert "no v0.17.0 GitHub release" in readme
    assert "GitHub has no v0.17.0 release record" in failed_release
    assert "must not be deleted, force-moved, or recreated" in failed_release
    assert "not publication proof" in release_bundle_readme
    assert "local and remote absence of `v0.18.0` before tag creation" in (
        release_receipt
    )
    assert "two byte-identical deterministic MCPB builds" in release_receipt
    normalized_failed = " ".join(failed_release.split())
    assert "stable unversioned protocol filenames remain intentionally reusable" in (
        normalized_failed
    )
    assert "A later recovery release may publish" in failed_release
    assert "The release can publish" not in failed_release
    assert "Published release `v0.18.0` provides" in readme

    published_record = release_receipt
    for stale_claim in (
        "not tagged, published, or registered",
        "No v0.18.0 tag object",
        "candidate metadata, not a downloadable release receipt",
    ):
        assert stale_claim not in published_record

    previous_path = ROOT / f"docs/RELEASE-{PREVIOUS_VERSION}.md"
    assert hashlib.sha256(previous_path.read_bytes()).hexdigest() == (
        PREVIOUS_RELEASE_RECORD_SHA256
    )
    assert PREVIOUS_CANDIDATE in previous_release
    assert PREVIOUS_TAG_OBJECT in previous_release


def test_published_registry_metadata_tracks_source_version():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    package = server["packages"][0]

    assert version == SOURCE_VERSION
    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == version
    assert server["version"] == version
    assert package["identifier"].endswith(
        f"/v{version}/liquilens-evidence-carrier-mcp-{version}.mcpb"
    )
    assert package["fileSha256"] == SOURCE_MCPB_SHA256


def test_published_gateway_identity_and_security_floors_are_consistent():
    gateway_root = ROOT / "integrations/trade-safety-gateway"
    project = tomllib.loads((gateway_root / "pyproject.toml").read_text())
    lock = tomllib.loads((gateway_root / "uv.lock").read_text())
    dependencies = project["project"]["dependencies"]

    assert project["project"]["version"] == GATEWAY_VERSION
    assert f"liquilens-evidence=={SOURCE_VERSION}" in dependencies
    assert "fastapi>=0.141.1,<0.142" in dependencies
    assert "starlette>=1.3.1,<2" in dependencies
    assert "pytest>=9.0.3,<10" in project["project"]["optional-dependencies"][
        "test"
    ]

    versions = {
        package["name"]: package["version"]
        for package in lock["package"]
        if package["name"]
        in {
            "fastapi",
            "liquilens-evidence",
            "liquilens-trade-safety-gateway",
            "pytest",
            "starlette",
        }
    }
    assert versions == {
        "fastapi": "0.141.1",
        "liquilens-evidence": SOURCE_VERSION,
        "liquilens-trade-safety-gateway": GATEWAY_VERSION,
        "pytest": "9.1.1",
        "starlette": "1.6.0",
    }


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


def test_published_v0180_embedded_readme_stays_reproducible():
    frozen = ROOT / "mcpb/release-readmes" / f"{RELEASE_VERSION}.md"
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == RELEASE_README_SHA256
    frozen_text = frozen.read_text(encoding="utf-8")
    normalized_frozen = " ".join(frozen_text.split())
    assert "bytes prepared for the v0.18.0 MCPB candidate" in normalized_frozen
    assert "not publication proof" in frozen_text
    assert "No such publication receipt is asserted" in normalized_frozen
    assert frozen.read_bytes() != (ROOT / "README.md").read_bytes()


def test_candidate_v0190_embedded_readme_matches_registry_digest_input():
    candidate = ROOT / "mcpb/release-readmes" / f"{SOURCE_VERSION}.md"
    assert hashlib.sha256(candidate.read_bytes()).hexdigest() == (
        SOURCE_README_SHA256
    )
    normalized = " ".join(candidate.read_text(encoding="utf-8").split())
    assert "bytes prepared for the v0.19.0 MCPB candidate" in normalized
    assert "No such publication receipt is asserted" in normalized
    assert candidate.read_bytes() != (ROOT / "README.md").read_bytes()


def test_published_v0171_embedded_readme_stays_reproducible():
    frozen = ROOT / "mcpb/release-readmes" / f"{PREVIOUS_VERSION}.md"
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == PREVIOUS_README_SHA256
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
