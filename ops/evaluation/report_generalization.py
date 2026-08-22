"""Aggregate known, holdout and perturbation Benchmark results."""

from __future__ import annotations

import argparse
from pathlib import Path

from devops_agent_platform.evaluation.generalization_report import (
    build_generalization_document,
    build_report_provenance,
    load_bad_cases,
    load_multi_incident_integrity,
    load_result,
    write_generalization_artifacts,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--known", required=True, type=Path)
    parser.add_argument("--holdout", required=True, type=Path)
    parser.add_argument(
        "--variant-baseline",
        type=Path,
        help="与所有扰动共享同一 Runtime Snapshot 的 clean results.json",
    )
    parser.add_argument(
        "--multi-incident",
        type=Path,
        help="真实双 Incident 并发隔离实验 results.json",
    )
    parser.add_argument(
        "--multi-incident-integrity",
        type=Path,
        help="真实 Multi-Incident 的 pair/workflow/trace 隔离核验 JSON",
    )
    parser.add_argument("--variant", action="append", default=[])
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--bad-cases",
        action="append",
        default=[],
        type=Path,
        help="可重复传入 Runner bad_cases.jsonl；原样汇总供复盘",
    )
    args = parser.parse_args(argv)
    variants = []
    for entry in args.variant:
        try:
            name, path_text = entry.split("=", 1)
        except ValueError as exc:
            raise ValueError("--variant must use NAME=RESULT_PATH") from exc
        variants.append(load_result(Path(path_text), name=name))
    known_path = args.known
    holdout_path = args.holdout
    result_paths = {
        "known-clean": known_path,
        "hidden-holdout": holdout_path,
    }
    if args.variant_baseline:
        result_paths["variant-clean"] = args.variant_baseline
    if args.multi_incident:
        result_paths["multi-incident"] = args.multi_incident
    for entry in args.variant:
        name, path_text = entry.split("=", 1)
        result_paths[name] = Path(path_text)
    document = build_generalization_document(
        known=load_result(known_path, name="known-clean"),
        holdout=load_result(holdout_path, name="hidden-holdout"),
        variant_baseline=(
            load_result(args.variant_baseline, name="variant-clean")
            if args.variant_baseline
            else None
        ),
        multi_incident=(
            load_result(args.multi_incident, name="multi-incident")
            if args.multi_incident
            else None
        ),
        multi_incident_integrity=(
            load_multi_incident_integrity(args.multi_incident_integrity)
            if args.multi_incident_integrity
            else None
        ),
        variants=tuple(variants),
        bad_cases=tuple(
            item
            for path in args.bad_cases
            for item in load_bad_cases(path)
        ),
    )
    extra_artifacts = {}
    if args.multi_incident_integrity:
        extra_artifacts["multi-incident-integrity"] = args.multi_incident_integrity
    write_generalization_artifacts(
        args.output,
        document,
        report_provenance=build_report_provenance(
            result_paths,
            extra_artifacts=extra_artifacts,
        ),
    )
    print(args.output / "generalization-report.md")
    print(args.output / "resume-metrics.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
