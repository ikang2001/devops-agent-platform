"""Split historical combined Scenario Manifests into v0.7 public/private data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from devops_agent_platform.evaluation.scenario_catalog import split_manifest


def split_directory(
    source_directory: Path,
    public_directory: Path,
    private_directory: Path,
) -> tuple[str, ...]:
    paths = sorted(source_directory.glob("*.json"))
    if not paths:
        raise ValueError(f"no combined manifests found in {source_directory}")
    public_directory.mkdir(parents=True, exist_ok=True)
    private_directory.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    for path in paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        public, private = split_manifest(document)
        public_path = public_directory / path.name
        private_path = private_directory / path.name
        if public_path.exists() or private_path.exists():
            raise ValueError(
                "refusing to overwrite separated scenario: "
                f"{public_path if public_path.exists() else private_path}"
            )
        public_path.write_text(
            json.dumps(public, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        private_path.write_text(
            json.dumps(private, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        created.extend((str(public_path), str(private_path)))
    return tuple(created)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--public", required=True, type=Path)
    parser.add_argument("--private", required=True, type=Path)
    args = parser.parse_args(argv)
    for path in split_directory(args.source, args.public, args.private):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
