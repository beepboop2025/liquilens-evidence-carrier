import tomllib
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = GATEWAY_ROOT.parents[1]


def test_standalone_legal_files_match_repository_and_are_declared() -> None:
    metadata = tomllib.loads((GATEWAY_ROOT / "pyproject.toml").read_text())

    assert metadata["project"]["license-files"] == ["LICENSE", "NOTICE"]
    for filename in ("LICENSE", "NOTICE"):
        assert (GATEWAY_ROOT / filename).read_bytes() == (
            REPOSITORY_ROOT / filename
        ).read_bytes()
