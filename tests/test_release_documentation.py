"""Keep live release receipts separate from failed and channel-specific state."""

import hashlib
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_VERSION = "0.19.0"
SOURCE_MCPB_SHA256 = (
    "11db11aefafcc6c4ba558877d1f9892fc708150b3afbaa28a741e74435b9a91a"
)
SOURCE_README_SHA256 = (
    "2d1b4dce5431451510d786f70a5a8e401180f4dd8e4820025e101444e5a97aa6"
)
GATEWAY_VERSION = "0.2.1"
CANONICAL_SITE_REVISION = "3ec660175c81c5b282715ee400eea2f771dc2610"
CANONICAL_SITE_WORKFLOW = "33592149926"
RELEASE_VERSION = "0.19.0"
RELEASE_CANDIDATE = "8f5738c9e77cc95b9a68543d478b9521f5595d61"
RELEASE_TREE = "acca6fa7aab75ebc91bf044e153c6468cd6f9c0c"
RELEASE_TAG_OBJECT = "c3239bfc7c4d3c4b7fc5ce26e0f602962e7d4337"
RELEASE_PREFLIGHT = "33630656569"
RELEASE_WORKFLOW = "33630790150"
RELEASE_CONTAINER_WORKFLOW = "33630789998"
RELEASE_GATEWAY_CONTAINER_WORKFLOW = "33630790011"
RELEASE_SHA256SUMS = (
    "c6d52cbf8794db6e478e3b2ea9e1ed8eee7757137650892a6a96fcbb839bb6bc"
)
RELEASE_WHEEL_SHA256 = (
    "1adccb72376f50456fd16a979e372f802ae73ba35b766633bc3d8bd4ab5abcc8"
)
RELEASE_MCPB_SHA256 = (
    "11db11aefafcc6c4ba558877d1f9892fc708150b3afbaa28a741e74435b9a91a"
)
RELEASE_GATEWAY_WHEEL_SHA256 = (
    "e3c685a300aadaafa406ccf38b2d8c56107e7145f6a075d0909a9c74a715f285"
)
RELEASE_OCI_DIGEST = (
    "bdbfed2afa87f25e8ef88dffeb4ba7ab198854705528c0de5abe31552a170b9a"
)
RELEASE_GATEWAY_OCI_DIGEST = (
    "b5c43013da1fdddd9e6e56cab0e4f0f562e39ab25cc640869c5008e3457218e3"
)
RELEASE_README_SHA256 = (
    "2d1b4dce5431451510d786f70a5a8e401180f4dd8e4820025e101444e5a97aa6"
)
RELEASE_ATTESTATION = "44695012"
RELEASE_CONTAINER_ATTESTATION = "44695462"
RELEASE_GATEWAY_CONTAINER_ATTESTATION = "44695195"
GATEWAY_013_TAG_OBJECT = "757c18928c8036910ab50c80ec073679d7434abf"
GATEWAY_013_COMMIT = "fa8e25ae8e0e992611706b8d66e951342d594243"
GATEWAY_013_TREE = "7680694bf3397a0844f2388fb29067ff402f066d"
GATEWAY_013_WORKFLOW = "33651560380"
GATEWAY_013_ATTESTATION = "44751184"
GATEWAY_013_OCI_DIGEST = (
    "9b8f704547ecf6c43039b34149d6cca842de5d66cba13c040199cf5f3f216d61"
)
HISTORICAL_VERSION = "0.18.0"
HISTORICAL_CANDIDATE = "906ca033a96ea862ab813c64db2a6b01c5ce8c4f"
HISTORICAL_TAG_OBJECT = "42dd412ef27b470841b71b8bc73c0ed63a5e4a6b"
HISTORICAL_RELEASE_RECORD_SHA256 = (
    "7241d38aba79192ca97aa532027c0774fd6d50c7f1f1e26601fc07571620ce7a"
)
HISTORICAL_README_SHA256 = (
    "3cc9705a2c1aa0471342199f54509b2aa66a02a2c84d89287732a89cd026018a"
)
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


def test_main_facing_docs_record_published_v0190_and_preserve_history():
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
    recovery_release = (
        ROOT / f"docs/RELEASE-{PREVIOUS_VERSION}.md"
    ).read_text(encoding="utf-8")
    historical_release_path = ROOT / f"docs/RELEASE-{HISTORICAL_VERSION}.md"
    historical_release = historical_release_path.read_text(encoding="utf-8")
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
        assert RELEASE_GATEWAY_CONTAINER_WORKFLOW in text
        assert RELEASE_OCI_DIGEST in text
        assert RELEASE_GATEWAY_OCI_DIGEST in text
        assert RELEASE_GATEWAY_WHEEL_SHA256 in text
        assert RELEASE_ATTESTATION in text
        assert RELEASE_CONTAINER_ATTESTATION in text
        assert RELEASE_GATEWAY_CONTAINER_ATTESTATION in text

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
    for text in (readme, distribution, changelog, release_receipt):
        assert SOURCE_MCPB_SHA256 in text

    for stale_claim in (
        "source checkpoint prepares `v0.19.0`",
        "not yet tagged, published, or registered",
        "No `v0.19.0` tag, GitHub release",
        "prepared source; not tagged, published, registered, or",
        "There is no v0.19.0 tag object",
    ):
        assert stale_claim not in readme
        assert stale_claim not in release_receipt
        assert stale_claim not in distribution
        assert stale_claim not in changelog

    for text in (readme, failed_release):
        assert FAILED_TAG_OBJECT in text
        assert FAILED_TAG_TARGET in text
        assert FAILED_WORKFLOW in text

    assert "no v0.17.0 GitHub release" in readme
    assert "GitHub has no v0.17.0 release record" in failed_release
    assert "must not be deleted, force-moved, or recreated" in failed_release
    assert "not publication proof" in release_bundle_readme
    normalized_failed = " ".join(failed_release.split())
    assert "stable unversioned protocol filenames remain intentionally reusable" in (
        normalized_failed
    )
    assert "A later recovery release may publish" in failed_release
    assert "The release can publish" not in failed_release
    assert f"Published release `v{RELEASE_VERSION}` provides" in readme

    for stale_claim in (
        "not tagged, published, or registered",
        "No v0.19.0 tag object",
        "candidate metadata, not a downloadable release receipt",
    ):
        assert stale_claim not in release_receipt

    assert hashlib.sha256(historical_release_path.read_bytes()).hexdigest() == (
        HISTORICAL_RELEASE_RECORD_SHA256
    )
    assert HISTORICAL_CANDIDATE in historical_release
    assert HISTORICAL_TAG_OBJECT in historical_release

    previous_path = ROOT / f"docs/RELEASE-{PREVIOUS_VERSION}.md"
    assert hashlib.sha256(previous_path.read_bytes()).hexdigest() == (
        PREVIOUS_RELEASE_RECORD_SHA256
    )
    assert PREVIOUS_CANDIDATE in recovery_release
    assert PREVIOUS_TAG_OBJECT in recovery_release


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


def test_independent_gateway_013_receipt_preserves_exact_historical_boundary():
    receipt = (
        ROOT / "docs/RELEASE-TRADE-SAFETY-GATEWAY-0.1.3.md"
    ).read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    for text in (receipt, readme, changelog):
        for identity in (
            GATEWAY_013_TAG_OBJECT,
            GATEWAY_013_COMMIT,
            GATEWAY_013_TREE,
            GATEWAY_013_WORKFLOW,
            GATEWAY_013_ATTESTATION,
            GATEWAY_013_OCI_DIGEST,
        ):
            assert identity in text

    normalized = " ".join(receipt.split())
    assert "not a GitHub Release" in normalized
    assert "no GitHub Release object" in normalized
    assert "Nothing in this receipt proves a public endpoint" in normalized
    assert "paid-route activation" in normalized
    assert "order-path enforcement" in normalized
    assert "trade-safety-gateway-v0.2.0" not in receipt


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
    frozen = ROOT / "mcpb/release-readmes" / f"{HISTORICAL_VERSION}.md"
    assert (
        hashlib.sha256(frozen.read_bytes()).hexdigest()
        == HISTORICAL_README_SHA256
    )
    frozen_text = frozen.read_text(encoding="utf-8")
    normalized_frozen = " ".join(frozen_text.split())
    assert "bytes prepared for the v0.18.0 MCPB candidate" in normalized_frozen
    assert "not publication proof" in frozen_text
    assert "No such publication receipt is asserted" in normalized_frozen
    assert frozen.read_bytes() != (ROOT / "README.md").read_bytes()


def test_published_v0190_embedded_readme_matches_registry_digest_input():
    frozen = ROOT / "mcpb/release-readmes" / f"{SOURCE_VERSION}.md"
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == (
        SOURCE_README_SHA256
    )
    normalized = " ".join(frozen.read_text(encoding="utf-8").split())
    assert "bytes prepared for the v0.19.0 MCPB candidate" in normalized
    assert "No such publication receipt is asserted" in normalized
    assert frozen.read_bytes() != (ROOT / "README.md").read_bytes()


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
