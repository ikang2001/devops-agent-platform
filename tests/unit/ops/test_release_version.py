import hashlib
import importlib.util
import json
import re
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
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
        in workflow
    )
    assert (
        "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"
        in workflow
    )
    assert "needs:\n      - test\n      - image" in workflow
    assert workflow.count("contents: write") == 1
    assert workflow.count("persist-credentials: false") == workflow.count(
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
    )
    assert "overwrite: true" in workflow
    assert "scripts/publish-github-release.py" in workflow
    assert workflow.count(
        "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"
    ) == 2
    assert "name: image-sbom-${{ github.sha }}" in workflow
    assert "--sbom-path \"release-metadata/sbom.spdx.json\"" in workflow


def test_ci_runs_compatibility_and_supply_chain_gates() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "python-311-compatibility:" in workflow
    assert 'python-version: "3.11"' in workflow
    assert 'uv run pytest -q -m "not live"' in workflow
    assert "alembic-upgrade-py311.sql" in workflow
    assert "security:" in workflow
    assert workflow.count("pip-audit==2.10.1") == 2
    assert "scanners: secret" in workflow
    assert (
        "needs:\n      - test\n      - python-311-compatibility\n"
        "      - security"
    ) in workflow
    assert "name: image-sbom-${{ github.sha }}" in workflow
    assert "retention-days: 90" in workflow


def test_ci_actions_are_pinned_to_reviewed_immutable_commits() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    references = re.findall(r"(?m)^\s*-?\s*uses:\s+([^\s#]+)", workflow)

    expected = {
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
        "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        "anchore/sbom-action@e22c389904149dbc22b58101806040fa8d37a610",
        "aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25",
        "astral-sh/setup-uv@37802adc94f370d6bfd71619e3f0bf239e1f3b78",
    }

    assert references
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", ref) for ref in references)
    assert set(references) == expected


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


def test_release_publisher_includes_validated_image_sbom(tmp_path: Path) -> None:
    publisher = load_publisher()
    version = project_version()
    artifacts = [
        tmp_path / f"devops_agent_platform-{version}-py3-none-any.whl",
        tmp_path / f"devops_agent_platform-{version}.tar.gz",
    ]
    for index, path in enumerate(artifacts):
        path.write_bytes(f"artifact-{index}".encode())
    metadata_dir = tmp_path / "release-metadata"
    metadata_dir.mkdir()
    sbom_path = metadata_dir / "sbom.spdx.json"
    sbom_path.write_text(
        json.dumps(
            {
                "spdxVersion": "SPDX-2.3",
                "SPDXID": "SPDXRef-DOCUMENT",
                "packages": [{"name": "devops-agent-platform"}],
            }
        ),
        encoding="utf-8",
    )

    prepared = publisher.prepare_release_assets(
        f"v{version}",
        tmp_path,
        sbom_path=sbom_path,
    )

    assert {path.name for path in prepared} == {
        artifacts[0].name,
        artifacts[1].name,
        "sbom.spdx.json",
        "SHA256SUMS",
    }
    digest = hashlib.sha256(sbom_path.read_bytes()).hexdigest()
    checksums = (tmp_path / "SHA256SUMS").read_text(encoding="utf-8")
    assert f"{digest}  sbom.spdx.json\n" in checksums


@pytest.mark.parametrize(
    "document",
    [
        [],
        {"spdxVersion": "SPDX-3.0", "SPDXID": "SPDXRef-DOCUMENT"},
        {
            "spdxVersion": "SPDX-2.3",
            "SPDXID": "SPDXRef-DOCUMENT",
            "packages": [],
        },
    ],
)
def test_release_publisher_rejects_invalid_image_sbom(
    tmp_path: Path,
    document: object,
) -> None:
    publisher = load_publisher()
    version = project_version()
    for name in (
        f"devops_agent_platform-{version}-py3-none-any.whl",
        f"devops_agent_platform-{version}.tar.gz",
    ):
        (tmp_path / name).touch()
    metadata_dir = tmp_path / "release-metadata"
    metadata_dir.mkdir()
    sbom_path = metadata_dir / "sbom.spdx.json"
    sbom_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="image SBOM"):
        publisher.prepare_release_assets(
            f"v{version}",
            tmp_path,
            sbom_path=sbom_path,
        )


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
