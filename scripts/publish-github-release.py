from __future__ import annotations

import argparse
import hashlib
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Protocol

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = PROJECT_ROOT / "scripts" / "check-release-version.py"
CHECKSUMS_NAME = "SHA256SUMS"
RELEASE_TITLE = "DevOps Controlled RCA Platform"


class ReleaseClient(Protocol):
    def release_exists(self, tag: str) -> bool: ...

    def create_release(self, tag: str) -> None: ...

    def asset_names(self, tag: str) -> set[str]: ...

    def download_asset(self, tag: str, name: str, destination: Path) -> None: ...

    def upload_asset(self, tag: str, path: Path) -> None: ...


class GitHubCliReleaseClient:
    def __init__(self, repository: str) -> None:
        self._repository = repository

    def release_exists(self, tag: str) -> bool:
        result = self._run(
            "release",
            "view",
            tag,
            "--json",
            "tagName",
            check=False,
        )
        if result.returncode == 0:
            return True
        if "release not found" in result.stderr.lower():
            return False
        result.check_returncode()
        raise AssertionError("unreachable")

    def create_release(self, tag: str) -> None:
        self._run(
            "release",
            "create",
            tag,
            "--verify-tag",
            "--generate-notes",
            "--title",
            f"{RELEASE_TITLE} {tag}",
        )

    def asset_names(self, tag: str) -> set[str]:
        result = self._run(
            "release",
            "view",
            tag,
            "--json",
            "assets",
            "--jq",
            ".assets[].name",
        )
        return {line for line in result.stdout.splitlines() if line}

    def download_asset(self, tag: str, name: str, destination: Path) -> None:
        self._run(
            "release",
            "download",
            tag,
            "--pattern",
            name,
            "--dir",
            str(destination),
        )

    def upload_asset(self, tag: str, path: Path) -> None:
        self._run("release", "upload", tag, str(path))

    def _run(
        self,
        *args: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["gh", *args, "--repo", self._repository],
            check=check,
            capture_output=True,
            text=True,
        )


def prepare_release_assets(tag: str, dist_dir: Path) -> list[Path]:
    checksums_path = dist_dir / CHECKSUMS_NAME
    checksums_path.unlink(missing_ok=True)
    validator = _load_validator()
    result = validator.validate_release(tag, dist_dir=dist_dir)
    artifacts = [dist_dir / name for name in result["artifacts"]]
    checksums = [f"{_sha256(path)}  {path.name}" for path in artifacts]
    checksums_path.write_text("\n".join(checksums) + "\n", encoding="utf-8")
    return [*artifacts, checksums_path]


def publish_release_assets(
    client: ReleaseClient,
    tag: str,
    assets: list[Path],
) -> list[str]:
    if not client.release_exists(tag):
        client.create_release(tag)

    remote_names = client.asset_names(tag)
    existing_assets = [asset for asset in assets if asset.name in remote_names]
    missing_assets = [asset for asset in assets if asset.name not in remote_names]
    with tempfile.TemporaryDirectory(prefix="release-assets-") as temp_dir:
        download_dir = Path(temp_dir)
        for asset in existing_assets:
            client.download_asset(tag, asset.name, download_dir)
            _require_matching_asset(asset, download_dir / asset.name)

    for asset in missing_assets:
        client.upload_asset(tag, asset)
    return [asset.name for asset in missing_assets]


def _load_validator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "release_version_validator",
        VALIDATOR_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load release validator from {VALIDATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _require_matching_asset(local: Path, remote: Path) -> None:
    if _sha256(local) != _sha256(remote):
        raise ValueError(
            f"release asset {local.name} already exists with different content"
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish validated Python artifacts to a GitHub Release.",
    )
    parser.add_argument("--tag", required=True)
    parser.add_argument("--dist-dir", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    args = parser.parse_args(argv)

    try:
        assets = prepare_release_assets(args.tag, args.dist_dir)
        client = GitHubCliReleaseClient(args.repository)
        uploaded = publish_release_assets(client, args.tag, assets)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        print(
            f"GitHub Release command failed ({exc.returncode}): {detail}",
            file=sys.stderr,
        )
        return 2
    except (OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if uploaded:
        print(f"uploaded release assets: {', '.join(uploaded)}")
    else:
        print("release assets already exist with matching content")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
