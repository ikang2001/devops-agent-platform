from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from pathlib import Path

from .generalization import GeneralizationVariant, mutate_case
from .integrity import BenchmarkLeakageGuard
from .live_runner import LiveSuiteInput, load_live_suite


def build_variant_suite(
    runtime_input: Path,
    variant: GeneralizationVariant,
    *,
    seed: int = 0,
) -> tuple[LiveSuiteInput, dict[str, str]]:
    suite = load_live_suite(runtime_input)
    if suite.synthetic or suite.simulation:
        raise ValueError(
            "generalization variants require a real black-box runtime snapshot"
        )
    mutated_cases = tuple(
        mutate_case(
            case,
            variant,
            seed=(
                seed
                if variant is GeneralizationVariant.SERVICE_RENAME
                else seed + index
            ),
        )
        for index, case in enumerate(suite.cases)
    )
    aliases: dict[str, str] = {}
    if variant is GeneralizationVariant.SERVICE_RENAME:
        for original, mutated in zip(suite.cases, mutated_cases, strict=True):
            aliases[original.service_name] = mutated.service_name
            if original.entry_service and mutated.entry_service:
                aliases[original.entry_service] = mutated.entry_service
    result = suite.model_copy(
        update={
            "suite": f"{suite.suite}-{variant.value}",
            "cases": mutated_cases,
        }
    )
    BenchmarkLeakageGuard().assert_clean(
        result.model_dump(mode="json"),
        source=f"generalization:{variant.value}",
    )
    return result, aliases


def write_variant_suite(path: Path, suite: LiveSuiteInput) -> None:
    if path.exists():
        raise ValueError(f"refusing to overwrite variant runtime input {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(suite.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def transformed_private_directory(
    source: Path,
    aliases: Mapping[str, str],
    *,
    temporary_root: Path,
) -> tempfile.TemporaryDirectory[str]:
    temporary_root.mkdir(parents=True, exist_ok=True)
    directory = tempfile.TemporaryDirectory(
        prefix="generalization-private-",
        dir=temporary_root,
    )
    destination = Path(directory.name)
    for path in sorted(source.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        transformed = _replace_aliases(document, aliases)
        (destination / path.name).write_text(
            json.dumps(transformed, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    if not any(destination.glob("*.json")):
        directory.cleanup()
        raise ValueError(f"private ground truth directory is empty: {source}")
    return directory


def _replace_aliases(value: object, aliases: Mapping[str, str]) -> object:
    if isinstance(value, dict):
        return {key: _replace_aliases(item, aliases) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_aliases(item, aliases) for item in value]
    if isinstance(value, str):
        result = value
        for original, renamed in sorted(
            aliases.items(), key=lambda item: len(item[0]), reverse=True
        ):
            result = result.replace(original, renamed)
        return result
    return value
