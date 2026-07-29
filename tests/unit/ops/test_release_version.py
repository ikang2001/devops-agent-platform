import importlib.util
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]["version"]


def load_validator():
    path = ROOT / "scripts" / "check-release-version.py"
    spec = importlib.util.spec_from_file_location("release_version_validator", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_release_tag_matches_project_version() -> None:
    validator = load_validator()
    version = project_version()

    result = validator.validate_release(f"v{version}")

    assert result == {
        "package": "devops-agent-platform",
        "version": version,
        "tag": f"v{version}",
    }


def test_release_tag_mismatch_fails_closed() -> None:
    validator = load_validator()
    version = project_version()

    with pytest.raises(ValueError, match=f"release tag must be v{version}"):
        validator.validate_release("v0.0.0")


def test_release_artifacts_must_match_project_version(tmp_path: Path) -> None:
    validator = load_validator()
    version = project_version()
    expected = {
        f"devops_agent_platform-{version}-py3-none-any.whl",
        f"devops_agent_platform-{version}.tar.gz",
    }
    for name in expected:
        (tmp_path / name).touch()
    (tmp_path / ".gitignore").touch()

    result = validator.validate_release(f"v{version}", dist_dir=tmp_path)

    assert set(result["artifacts"]) == expected

    (tmp_path / "devops_agent_platform-0.3.0.tar.gz").touch()
    with pytest.raises(ValueError, match="do not match project version"):
        validator.validate_release(f"v{version}", dist_dir=tmp_path)


def test_tag_ci_runs_release_version_and_artifact_gates() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert 'tags:\n      - "v*"' in workflow
    assert "scripts/check-release-version.py --tag" in workflow
    assert "uv build --out-dir dist" in workflow
    assert '--tag "${GITHUB_REF_NAME}" --dist-dir dist' in workflow
