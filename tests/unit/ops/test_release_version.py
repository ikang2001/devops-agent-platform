import hashlib
import importlib.util
import subprocess
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


def load_publisher():
    path = ROOT / "scripts" / "publish-github-release.py"
    spec = importlib.util.spec_from_file_location("github_release_publisher", path)
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
    assert (
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
        in workflow
    )
    assert (
        "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093"
        in workflow
    )
    assert "needs:\n      - test\n      - image" in workflow
    assert workflow.count("contents: write") == 1
    assert workflow.count("persist-credentials: false") == 3
    assert "overwrite: true" in workflow
    assert "scripts/publish-github-release.py" in workflow


def test_release_publisher_generates_checksums_for_validated_assets(
    tmp_path: Path,
) -> None:
    publisher = load_publisher()
    version = project_version()
    artifacts = [
        tmp_path / f"devops_agent_platform-{version}-py3-none-any.whl",
        tmp_path / f"devops_agent_platform-{version}.tar.gz",
    ]
    for index, path in enumerate(artifacts):
        path.write_bytes(f"artifact-{index}".encode())

    prepared = publisher.prepare_release_assets(f"v{version}", tmp_path)

    assert {path.name for path in prepared} == {
        artifacts[0].name,
        artifacts[1].name,
        "SHA256SUMS",
    }
    checksums = (tmp_path / "SHA256SUMS").read_text(encoding="utf-8")
    for path in artifacts:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert f"{digest}  {path.name}\n" in checksums

    repeated = publisher.prepare_release_assets(f"v{version}", tmp_path)

    assert [path.name for path in repeated] == [path.name for path in prepared]


class FakeReleaseClient:
    def __init__(self, existing_assets: dict[str, bytes] | None = None) -> None:
        self.exists = existing_assets is not None
        self.assets = dict(existing_assets or {})
        self.created = False
        self.uploaded: list[str] = []

    def release_exists(self, tag: str) -> bool:
        return self.exists

    def create_release(self, tag: str) -> None:
        self.exists = True
        self.created = True

    def asset_names(self, tag: str) -> set[str]:
        return set(self.assets)

    def download_asset(self, tag: str, name: str, destination: Path) -> None:
        (destination / name).write_bytes(self.assets[name])

    def upload_asset(self, tag: str, path: Path) -> None:
        self.assets[path.name] = path.read_bytes()
        self.uploaded.append(path.name)


def test_release_publisher_creates_missing_release_and_uploads_assets(
    tmp_path: Path,
) -> None:
    publisher = load_publisher()
    asset = tmp_path / "package.whl"
    asset.write_bytes(b"content")
    client = FakeReleaseClient()

    uploaded = publisher.publish_release_assets(client, "v1.0.0", [asset])

    assert client.created is True
    assert uploaded == [asset.name]
    assert client.uploaded == [asset.name]


def test_release_publisher_is_idempotent_for_matching_assets(tmp_path: Path) -> None:
    publisher = load_publisher()
    asset = tmp_path / "package.whl"
    asset.write_bytes(b"same-content")
    client = FakeReleaseClient({asset.name: asset.read_bytes()})

    uploaded = publisher.publish_release_assets(client, "v1.0.0", [asset])

    assert uploaded == []
    assert client.uploaded == []


@pytest.mark.parametrize(
    ("returncode", "stderr", "expected"),
    [
        (0, "", True),
        (1, "release not found", False),
    ],
)
def test_github_cli_release_existence_classification(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stderr: str,
    expected: bool,
) -> None:
    publisher = load_publisher()
    client = publisher.GitHubCliReleaseClient("owner/repository")
    result = subprocess.CompletedProcess(
        args=["gh"],
        returncode=returncode,
        stdout="",
        stderr=stderr,
    )
    monkeypatch.setattr(client, "_run", lambda *args, **kwargs: result)

    assert client.release_exists("v1.0.0") is expected


def test_github_cli_release_existence_propagates_other_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = load_publisher()
    client = publisher.GitHubCliReleaseClient("owner/repository")
    result = subprocess.CompletedProcess(
        args=["gh"],
        returncode=1,
        stdout="",
        stderr="authentication failed",
    )
    monkeypatch.setattr(client, "_run", lambda *args, **kwargs: result)

    with pytest.raises(subprocess.CalledProcessError):
        client.release_exists("v1.0.0")


def test_release_publisher_fails_before_upload_on_asset_collision(
    tmp_path: Path,
) -> None:
    publisher = load_publisher()
    missing = tmp_path / "missing.whl"
    missing.write_bytes(b"missing-content")
    collision = tmp_path / "collision.tar.gz"
    collision.write_bytes(b"new-content")
    client = FakeReleaseClient({collision.name: b"old-content"})

    with pytest.raises(ValueError, match="different content"):
        publisher.publish_release_assets(
            client,
            "v1.0.0",
            [missing, collision],
        )

    assert client.uploaded == []
