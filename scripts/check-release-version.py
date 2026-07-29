from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "devops-agent-platform"
ARTIFACT_PREFIX = "devops_agent_platform"


def validate_release(
    tag: str,
    *,
    project_root: Path = PROJECT_ROOT,
    dist_dir: Path | None = None,
) -> dict[str, Any]:
    """校验发布 Tag 与 Python 包版本，并可校验构建产物名称。"""
    version = _project_version(project_root / "pyproject.toml")
    expected_tag = f"v{version}"
    if tag != expected_tag:
        raise ValueError(f"release tag must be {expected_tag}")

    result: dict[str, Any] = {
        "package": PACKAGE_NAME,
        "version": version,
        "tag": tag,
    }
    if dist_dir is not None:
        artifacts = _validate_artifacts(dist_dir, version)
        result["artifacts"] = artifacts
    return result


def _project_version(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            document = tomllib.load(stream)
        version = document["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"could not read project version from {path}") from exc
    if not isinstance(version, str) or not version or version != version.strip():
        raise ValueError("project version is invalid")
    return version


def _validate_artifacts(dist_dir: Path, version: str) -> list[str]:
    expected = {
        f"{ARTIFACT_PREFIX}-{version}-py3-none-any.whl",
        f"{ARTIFACT_PREFIX}-{version}.tar.gz",
    }
    try:
        actual = {
            path.name
            for path in dist_dir.iterdir()
            if path.is_file() and not path.name.startswith(".")
        }
    except OSError as exc:
        raise ValueError(f"could not read release artifacts from {dist_dir}") from exc
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise ValueError(
            "release artifacts do not match project version; "
            f"missing={missing}, unexpected={unexpected}"
        )
    return sorted(actual)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a release tag and optional Python artifacts.",
    )
    parser.add_argument("--tag", required=True)
    parser.add_argument("--dist-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        result = validate_release(args.tag, dist_dir=args.dist_dir)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
