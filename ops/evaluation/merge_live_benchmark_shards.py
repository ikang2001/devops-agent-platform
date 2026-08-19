from __future__ import annotations

import argparse
import json
from collections import Counter
from hashlib import sha256
from pathlib import Path

from devops_agent_platform.evaluation.schemas import (
    BenchmarkInput,
    BenchmarkMetadata,
    RCAPrediction,
    load_benchmark_input,
)


def merge_benchmark_shards(
    input_paths: tuple[Path, ...],
    *,
    output_path: Path,
    provenance_path: Path,
    required_runs: int,
    runs_per_scenario: int,
) -> BenchmarkInput:
    if len(input_paths) < 2:
        raise ValueError("at least two benchmark shards are required")
    if output_path.exists() or provenance_path.exists():
        raise ValueError("merged benchmark output already exists")

    documents = tuple(load_benchmark_input(path) for path in input_paths)
    _validate_metadata(tuple(item.benchmark for item in documents))

    runs: dict[str, RCAPrediction] = {}
    source_by_run: dict[str, Path] = {}
    replaced_run_ids: list[str] = []
    for path, document in zip(input_paths, documents, strict=True):
        for run in document.runs:
            previous = runs.get(run.run_id)
            if previous is not None:
                if previous.scenario_id != run.scenario_id:
                    raise ValueError("duplicate run_id belongs to different scenarios")
                replaced_run_ids.append(run.run_id)
            runs[run.run_id] = run
            source_by_run[run.run_id] = path

    merged_runs = tuple(
        sorted(runs.values(), key=lambda item: (item.scenario_id, item.run_id))
    )
    _validate_shape(
        merged_runs,
        required_runs=required_runs,
        runs_per_scenario=runs_per_scenario,
    )
    metadata = documents[0].benchmark.model_copy(
        update={
            "timestamp": max(item.benchmark.timestamp for item in documents),
        }
    )
    merged = BenchmarkInput(benchmark=metadata, runs=merged_runs)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            merged.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    provenance_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "composite_benchmark": True,
                "simulation": True,
                "synthetic": False,
                "production_acceptance": False,
                "required_runs": required_runs,
                "runs_per_scenario": runs_per_scenario,
                "replaced_run_ids": sorted(set(replaced_run_ids)),
                "inputs": [
                    {
                        "path": str(path),
                        "sha256": sha256(path.read_bytes()).hexdigest(),
                        "selected_runs": sum(
                            source == path for source in source_by_run.values()
                        ),
                    }
                    for path in input_paths
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return merged


def _validate_metadata(metadata: tuple[BenchmarkMetadata, ...]) -> None:
    expected = metadata[0].model_dump(mode="json", exclude={"timestamp"})
    for item in metadata[1:]:
        if item.model_dump(mode="json", exclude={"timestamp"}) != expected:
            raise ValueError("benchmark shard metadata does not match")


def _validate_shape(
    runs: tuple[RCAPrediction, ...],
    *,
    required_runs: int,
    runs_per_scenario: int,
) -> None:
    if len(runs) != required_runs:
        raise ValueError(
            f"merged benchmark requires {required_runs} runs, got {len(runs)}"
        )
    counts = Counter(item.scenario_id for item in runs)
    invalid = {
        scenario_id: count
        for scenario_id, count in counts.items()
        if count != runs_per_scenario
    }
    if invalid:
        raise ValueError(f"scenario run counts are invalid: {invalid}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge compatible real-LLM benchmark shards with provenance"
    )
    parser.add_argument("--input", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--provenance", required=True, type=Path)
    parser.add_argument("--required-runs", type=int, default=60)
    parser.add_argument("--runs-per-scenario", type=int, default=5)
    args = parser.parse_args(argv)
    try:
        merged = merge_benchmark_shards(
            tuple(args.input),
            output_path=args.output,
            provenance_path=args.provenance,
            required_runs=args.required_runs,
            runs_per_scenario=args.runs_per_scenario,
        )
    except (OSError, ValueError) as exc:
        print(str(exc))
        return 2
    print(
        json.dumps(
            {
                "runs": len(merged.runs),
                "output": str(args.output),
                "provenance": str(args.provenance),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
