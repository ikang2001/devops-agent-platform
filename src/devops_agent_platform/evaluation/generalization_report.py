from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Any

from .generalization import generalization_gap, robustness_drop


@dataclass(frozen=True)
class ResultSummary:
    name: str
    total_runs: int
    rca_top1_accuracy: float
    strict_rca_accuracy: float
    root_service_accuracy: float
    root_type_accuracy: float
    root_resource_accuracy: float
    candidate_recall_at_3: float | None
    candidate_mrr: float | None
    evidence_recall: float
    unsupported_claim_rate: float
    forbidden_claim_rate: float
    false_positive_rate: float
    undetermined_precision: float | None
    undetermined_recall: float | None
    calibration_ece: float | None
    brier_score: float | None
    cross_incident_evidence_leak_rate: float
    benchmark_leakage_violation_count: int
    avg_tool_calls: float
    avg_llm_calls: float
    avg_tokens: float
    avg_cost: float
    p50_latency_ms: int
    p95_latency_ms: int
    change_overattribution_rate: float
    history_overattribution_rate: float
    root_type_breakdown: dict[str, dict[str, float | int]]
    root_resource_breakdown: dict[str, dict[str, float | int]]
    prediction_count: int
    application_error_prediction_count: int
    undetermined_prediction_count: int

    @classmethod
    def from_result(cls, name: str, result: dict[str, Any]) -> ResultSummary:
        summary = result.get("summary")
        if not isinstance(summary, dict):
            raise ValueError(f"{name} result does not contain summary")
        execution = result.get("execution", {})
        if not isinstance(execution, dict):
            execution = {}
        provenance = result.get("provenance", {})
        if not isinstance(provenance, dict):
            provenance = {}
        return cls(
            name=name,
            total_runs=int(summary["total_runs"]),
            rca_top1_accuracy=float(summary["rca_top1_accuracy"]),
            strict_rca_accuracy=float(summary.get("strict_rca_accuracy", 0.0)),
            root_service_accuracy=float(summary["root_service_accuracy"]),
            root_type_accuracy=float(summary.get("root_type_accuracy", 0.0)),
            root_resource_accuracy=float(summary.get("root_resource_accuracy", 0.0)),
            candidate_recall_at_3=(
                float(summary["candidate_recall_at_3"])
                if summary.get("candidate_recall_at_3") is not None
                else None
            ),
            candidate_mrr=(
                float(summary["candidate_mrr"])
                if summary.get("candidate_mrr") is not None
                else None
            ),
            evidence_recall=float(summary.get("evidence_recall", 0.0)),
            unsupported_claim_rate=float(summary["unsupported_claim_rate"]),
            forbidden_claim_rate=float(summary["forbidden_claim_rate"]),
            false_positive_rate=float(summary.get("false_positive_rate", 0.0)),
            undetermined_precision=(
                float(summary["undetermined_precision"])
                if summary.get("undetermined_precision") is not None
                else None
            ),
            undetermined_recall=(
                float(summary["undetermined_recall"])
                if summary.get("undetermined_recall") is not None
                else None
            ),
            calibration_ece=(
                float(summary["calibration_ece"])
                if summary.get("calibration_ece") is not None
                else None
            ),
            brier_score=(
                float(summary["brier_score"])
                if summary.get("brier_score") is not None
                else None
            ),
            cross_incident_evidence_leak_rate=float(
                execution.get("cross_incident_evidence_leak_rate", 0.0)
            ),
            benchmark_leakage_violation_count=int(
                execution.get(
                    "benchmark_leakage_violation_count",
                    provenance.get("leakage_violation_count", 0),
                )
            ),
            avg_tool_calls=float(summary.get("avg_tool_calls", 0.0)),
            avg_llm_calls=float(summary.get("avg_llm_calls", 0.0)),
            avg_tokens=float(summary.get("avg_tokens", 0.0)),
            avg_cost=float(summary.get("avg_cost", 0.0)),
            p50_latency_ms=int(summary.get("p50_latency_ms", 0)),
            p95_latency_ms=int(summary.get("p95_latency_ms", 0)),
            change_overattribution_rate=float(
                summary.get("change_overattribution_rate", 0.0)
            ),
            history_overattribution_rate=float(
                summary.get("history_overattribution_rate", 0.0)
            ),
            root_type_breakdown=_component_breakdown(
                result, "ground_truth_root_type", "root_type_correct"
            ),
            root_resource_breakdown=_component_breakdown(
                result, "ground_truth_root_resource", "root_resource_correct"
            ),
            prediction_count=0,
            application_error_prediction_count=0,
            undetermined_prediction_count=0,
        )


def load_result(path: Path, *, name: str | None = None) -> ResultSummary:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read evaluation result {path}") from exc
    if not isinstance(result, dict):
        raise ValueError(f"evaluation result {path} must be an object")
    summary = ResultSummary.from_result(name or path.parent.name, result)
    predictions_path = path.with_name("predictions.json")
    if not predictions_path.is_file():
        return summary
    try:
        predictions = json.loads(predictions_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read predictions {predictions_path}") from exc
    runs = predictions.get("runs") if isinstance(predictions, dict) else None
    if not isinstance(runs, list):
        raise ValueError(f"predictions {predictions_path} must contain runs")
    return replace(
        summary,
        prediction_count=len(runs),
        application_error_prediction_count=sum(
            1
            for item in runs
            if isinstance(item, dict)
            and isinstance(item.get("root_cause"), dict)
            and item["root_cause"].get("type") == "application_error"
        ),
        undetermined_prediction_count=sum(
            1
            for item in runs
            if isinstance(item, dict)
            and item.get("conclusion_status") == "UNDETERMINED"
        ),
    )


def _component_breakdown(
    result: Mapping[str, Any],
    group_key: str,
    correct_key: str,
) -> dict[str, dict[str, float | int]]:
    runs = result.get("runs")
    if not isinstance(runs, list):
        return {}
    counts: dict[str, list[int]] = {}
    for item in runs:
        if not isinstance(item, dict) or item.get(group_key) is None:
            continue
        name = str(item[group_key])
        values = counts.setdefault(name, [0, 0])
        values[0] += 1
        values[1] += int(bool(item.get(correct_key)))
    return {
        name: {
            "total_runs": values[0],
            "correct_runs": values[1],
            "accuracy": values[1] / values[0],
        }
        for name, values in sorted(counts.items())
    }


def build_report_provenance(
    result_paths: Mapping[str, Path],
    *,
    extra_artifacts: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    """聚合正式结果的 Provenance 与内容哈希，生成报告级追溯链。"""

    sources: dict[str, dict[str, Any]] = {}
    leakage_violation_count = 0
    for name, result_path in sorted(result_paths.items()):
        provenance_path = result_path.with_name("benchmark-provenance.json")
        try:
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"could not read benchmark provenance {provenance_path}"
            ) from exc
        if not isinstance(provenance, dict):
            raise ValueError(
                f"benchmark provenance {provenance_path} must be an object"
            )
        leakage_violation_count += int(
            provenance.get("leakage_violation_count", 0)
        )
        sources[name] = {
            "results_sha256": sha256(result_path.read_bytes()).hexdigest(),
            "provenance_sha256": sha256(provenance_path.read_bytes()).hexdigest(),
            "provenance": provenance,
        }
    extras = {
        name: {
            "sha256": sha256(path.read_bytes()).hexdigest(),
            "filename": path.name,
        }
        for name, path in sorted((extra_artifacts or {}).items())
    }
    return {
        "schema_version": "1.0",
        "artifact": "generalization-report",
        "source_count": len(sources),
        "leakage_violation_count": leakage_violation_count,
        "sources": sources,
        "extra_artifacts": extras,
    }


def load_bad_cases(path: Path) -> tuple[dict[str, Any], ...]:
    """读取 Runner 生成的 JSONL Bad Cases，供报告保留可追溯失败样本。"""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"could not read bad cases {path}") from exc
    cases: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid bad case JSON at line {line_number}") from exc
        if isinstance(value, dict):
            cases.append(value)
    return tuple(cases)


def load_multi_incident_integrity(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read multi-incident integrity {path}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("summary"), dict):
        raise ValueError("multi-incident integrity must contain an object summary")
    return value


def build_generalization_document(
    *,
    known: ResultSummary,
    holdout: ResultSummary,
    variant_baseline: ResultSummary | None = None,
    multi_incident: ResultSummary | None = None,
    multi_incident_integrity: dict[str, Any] | None = None,
    variants: tuple[ResultSummary, ...] = (),
    bad_cases: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    robustness_baseline = variant_baseline or known
    variant_results: dict[str, dict[str, Any]] = {}
    for item in variants:
        variant_results[item.name] = {
            "total_runs": item.total_runs,
            "rca_top1_accuracy": item.rca_top1_accuracy,
            "strict_rca_accuracy": item.strict_rca_accuracy,
            "root_service_accuracy": item.root_service_accuracy,
            "root_type_accuracy": item.root_type_accuracy,
            "root_resource_accuracy": item.root_resource_accuracy,
            "candidate_recall_at_3": item.candidate_recall_at_3,
            "candidate_mrr": item.candidate_mrr,
            "evidence_recall": item.evidence_recall,
            "robustness_drop": robustness_drop(
                robustness_baseline.rca_top1_accuracy, item.rca_top1_accuracy
            ),
            "unsupported_claim_rate": item.unsupported_claim_rate,
            "forbidden_claim_rate": item.forbidden_claim_rate,
            "false_positive_rate": item.false_positive_rate,
            "undetermined_precision": item.undetermined_precision,
            "undetermined_recall": item.undetermined_recall,
            "calibration_ece": item.calibration_ece,
            "brier_score": item.brier_score,
            "change_overattribution_rate": item.change_overattribution_rate,
            "history_overattribution_rate": item.history_overattribution_rate,
            "prediction_count": item.prediction_count,
            "application_error_prediction_count": (
                item.application_error_prediction_count
            ),
            "undetermined_prediction_count": item.undetermined_prediction_count,
            "avg_tool_calls": item.avg_tool_calls,
            "avg_llm_calls": item.avg_llm_calls,
            "avg_tokens": item.avg_tokens,
            "avg_cost": item.avg_cost,
            "p50_latency_ms": item.p50_latency_ms,
            "p95_latency_ms": item.p95_latency_ms,
        }
    leakage_count = sum(
        item.benchmark_leakage_violation_count
        for item in (
            known,
            holdout,
            *([variant_baseline] if variant_baseline is not None else []),
            *variants,
            *([multi_incident] if multi_incident is not None else []),
        )
    )
    cross_incident_leak_rate = (
        multi_incident.cross_incident_evidence_leak_rate
        if multi_incident is not None
        else holdout.cross_incident_evidence_leak_rate
    )
    return {
        "schema_version": "1.0",
        "known": known.__dict__,
        "hidden_holdout": holdout.__dict__,
        "variant_baseline": robustness_baseline.__dict__,
        "multi_incident": (
            multi_incident.__dict__ if multi_incident is not None else None
        ),
        "multi_incident_integrity": multi_incident_integrity,
        "metrics": {
            "generalization_gap": generalization_gap(
                known.rca_top1_accuracy, holdout.rca_top1_accuracy
            ),
            "root_service_gap": generalization_gap(
                known.root_service_accuracy, holdout.root_service_accuracy
            ),
            # Hidden Holdout deliberately uses resource systems unseen by the
            # reasoner.  Its resource component score is therefore the formal
            # Resource OOD metric; keeping the alias explicit avoids presenting
            # a synthetic resource rename as a separate real experiment.
            "root_resource_ood_accuracy": holdout.root_resource_accuracy,
            "unsupported_claim_rate": holdout.unsupported_claim_rate,
            "forbidden_claim_rate": holdout.forbidden_claim_rate,
            "false_positive_rate": holdout.false_positive_rate,
            "undetermined_precision": holdout.undetermined_precision,
            "undetermined_recall": holdout.undetermined_recall,
            "calibration_ece": holdout.calibration_ece,
            "brier_score": holdout.brier_score,
            "cross_incident_evidence_leak_rate": cross_incident_leak_rate,
            "benchmark_leakage_violation_count": leakage_count,
            "worst_root_types": _worst_components(holdout.root_type_breakdown),
            "worst_root_resources": _worst_components(
                holdout.root_resource_breakdown
            ),
        },
        "variants": variant_results,
        "bad_cases": list(bad_cases),
    }


def _worst_components(
    breakdown: Mapping[str, Mapping[str, float | int]],
) -> list[dict[str, float | int | str]]:
    if not breakdown:
        return []
    minimum = min(float(item["accuracy"]) for item in breakdown.values())
    return [
        {"name": name, **dict(values)}
        for name, values in sorted(breakdown.items())
        if float(values["accuracy"]) == minimum
    ]


def render_generalization_report(document: dict[str, Any]) -> str:
    known = document["known"]
    holdout = document["hidden_holdout"]
    variant_baseline = document["variant_baseline"]
    multi_incident = document.get("multi_incident")
    multi_integrity = document.get("multi_incident_integrity")
    metrics = document["metrics"]
    variants = document["variants"]
    lines = [
        "# v0.7 Black-box E2E 泛化评测报告",
        "",
        "> 本报告只读取正式 Runner 产生的 results.json，不接受手工填写指标。",
        "",
        "## 已知场景与 Hidden Holdout",
        "",
        "| 数据集 | Runs | RCA Top-1 | Root Service | Unsupported | Forbidden |",
        "|---|---:|---:|---:|---:|---:|",
        f"| Known Clean | {known['total_runs']} | {known['rca_top1_accuracy']:.4f} | "
        f"{known['root_service_accuracy']:.4f} | "
        f"{known['unsupported_claim_rate']:.4f} | "
        f"{known['forbidden_claim_rate']:.4f} |",
        f"| Hidden Holdout | {holdout['total_runs']} | "
        f"{holdout['rca_top1_accuracy']:.4f} | "
        f"{holdout['root_service_accuracy']:.4f} | "
        f"{holdout['unsupported_claim_rate']:.4f} | "
        f"{holdout['forbidden_claim_rate']:.4f} |",
        "",
        "## 泛化与完整性指标",
        "",
        f"- Generalization Gap：`{metrics['generalization_gap']:.4f}`",
        f"- Root Service Gap：`{metrics['root_service_gap']:.4f}`",
        f"- Root Type Accuracy：`{holdout['root_type_accuracy']:.4f}`",
        f"- Root Resource Accuracy：`{holdout['root_resource_accuracy']:.4f}`",
        "- Root Resource OOD Accuracy："
        f"`{metrics['root_resource_ood_accuracy']:.4f}`",
        f"- Candidate Recall@3：`{holdout['candidate_recall_at_3']}`",
        f"- Candidate MRR：`{holdout['candidate_mrr']}`",
        f"- False Confirmation：`{metrics['false_positive_rate']:.4f}`",
        "- Undetermined Precision / Recall："
        f"`{metrics['undetermined_precision']}` / "
        f"`{metrics['undetermined_recall']}`",
        "- Calibration ECE / Brier："
        f"`{metrics['calibration_ece']}` / `{metrics['brier_score']}`",
        "- Cross-Incident Evidence Leak："
        f"`{metrics['cross_incident_evidence_leak_rate']:.4f}`",
        "- Benchmark Leakage Violations："
        f"`{metrics['benchmark_leakage_violation_count']}`",
        "- 最差 Root Type："
        f"`{_format_worst_components(metrics['worst_root_types'])}`",
        "- 最差 Root Resource："
        f"`{_format_worst_components(metrics['worst_root_resources'])}`",
        "",
        "## 变体结果",
        "",
        "变体 Robustness Drop 使用同源 Runtime Snapshot 的 Clean 基线："
        f"`{variant_baseline['name']}` / "
        f"`{variant_baseline['rca_top1_accuracy']:.4f}`。",
        "",
        "| 变体 | Runs | RCA Top-1 | Strict | Root Service | Evidence Recall | "
        "Drop | Unsupported | Tool Calls | Tokens | P50/P95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, item in sorted(variants.items()):
        lines.append(
            f"| {name} | {item['total_runs']} | {item['rca_top1_accuracy']:.4f} | "
            f"{item['strict_rca_accuracy']:.4f} | "
            f"{item['root_service_accuracy']:.4f} | "
            f"{item['evidence_recall']:.4f} | "
            f"{item['robustness_drop']:.4f} | "
            f"{item['unsupported_claim_rate']:.4f} | "
            f"{item['avg_tool_calls']:.2f} | {item['avg_tokens']:.2f} | "
            f"{item['p50_latency_ms']}/{item['p95_latency_ms']} |"
        )
    if isinstance(multi_incident, dict) or isinstance(multi_integrity, dict):
        multi_values = multi_incident or {}
        leak_rate = multi_values.get(
            "cross_incident_evidence_leak_rate",
            metrics["cross_incident_evidence_leak_rate"],
        )
        lines.extend(
            (
                "",
                "## Multi-Incident 隔离",
                "",
                f"- Runs：`{multi_values.get('total_runs', 'n/a')}`",
                "- Cross-Incident Evidence Leak："
                f"`{leak_rate:.4f}`",
                "- Benchmark Leakage Violations："
                f"`{multi_values.get('benchmark_leakage_violation_count', 0)}`",
            )
        )
        if multi_values:
            lines.extend(
                (
                    "- RCA Top-1："
                    f"`{float(multi_values['rca_top1_accuracy']):.4f}`",
                    "- Strict RCA："
                    f"`{float(multi_values['strict_rca_accuracy']):.4f}`",
                )
            )
        if isinstance(multi_integrity, dict):
            summary = multi_integrity.get("summary", {})
            lines.extend(
                (
                    f"- Pair Count：`{summary.get('pair_count')}`",
                    "- Distinct Incident Pairs："
                    f"`{summary.get('distinct_incident_pair_count')}`",
                    "- Distinct Workflow Pairs："
                    f"`{summary.get('distinct_workflow_pair_count')}`",
                    "- Distinct Trace Pairs："
                    f"`{summary.get('distinct_trace_pair_count')}`",
                    "- Overlapped Workflow Pairs："
                    f"`{summary.get('overlapped_workflow_pair_count')}`",
                    f"- Successful Pairs：`{summary.get('successful_pair_count')}`",
                    f"- Failed Pairs：`{summary.get('failed_pair_count')}`",
                )
            )
    clean = variant_baseline
    service_rename = variants.get("service_rename") or variants.get(
        "service-rename"
    )
    error_paraphrase = variants.get("error_paraphrase") or variants.get(
        "error-paraphrase"
    )
    noise_30 = variants.get("noise_30") or variants.get("noise-30")
    missing_logs = variants.get("missing_logs") or variants.get("missing-logs")
    missing_traces = variants.get("missing_traces") or variants.get(
        "missing-traces"
    )
    missing_knowledge = variants.get("missing_knowledge") or variants.get(
        "missing-knowledge"
    )
    lines.extend(
        (
            "",
            "## 任务书问题回答",
            "",
            _variant_answer("服务改名", clean, service_rename),
            _variant_answer("错误改写", clean, error_paraphrase),
            _variant_answer("30% Noise", clean, noise_30),
            _missing_logs_answer(missing_logs),
            _missing_traces_answer(clean, missing_traces),
            _knowledge_answer(missing_knowledge),
            _change_answer(variants),
        )
    )
    lines.extend(
        (
            "",
            "## Bad Cases",
            "",
            f"- 自动收集失败样本：`{len(document.get('bad_cases', ()))}` 条",
            "- Bad Case 只保留 Runner 已生成的失败结果；"
            "不会为了美化指标删除错误 Prediction。",
            "",
            "## 结论",
            "",
            "所有数字来自正式 Benchmark 产物；未运行的变体不会被补写为通过。",
            "",
        )
    )
    return "\n".join(lines)


def _format_worst_components(items: object) -> str:
    if not isinstance(items, list) or not items:
        return "n/a"
    return ", ".join(
        f"{item['name']} ({float(item['accuracy']):.2%}, {item['correct_runs']}/"
        f"{item['total_runs']})"
        for item in items
        if isinstance(item, dict)
    )


def _variant_answer(
    label: str,
    baseline: Mapping[str, Any],
    variant: Mapping[str, Any] | None,
) -> str:
    if variant is None:
        return f"- {label}：未运行。"
    drop_pp = float(variant["robustness_drop"]) * 100
    return (
        f"- {label}：Top-1 `{float(variant['rca_top1_accuracy']):.2%}`，"
        f"相对 Clean `{float(baseline['rca_top1_accuracy']):.2%}` 下降 "
        f"`{drop_pp:.2f}pp`。"
    )


def _missing_logs_answer(variant: Mapping[str, Any] | None) -> str:
    if variant is None:
        return "- Logs 不可用：未运行。"
    predictions = int(variant.get("prediction_count", 0))
    app_errors = int(variant.get("application_error_prediction_count", 0))
    undetermined = int(variant.get("undetermined_prediction_count", 0))
    conclusion = (
        "未观察到 application_error 硬猜" if app_errors == 0 else "仍存在硬猜"
    )
    return (
        "- Logs 不可用："
        f"`{predictions}` 条 Prediction 中 `application_error` 硬猜 `{app_errors}` 条，"
        f"UNDETERMINED `{undetermined}` 条，False Positive "
        f"`{float(variant['false_positive_rate']):.4f}`；"
        f"结论：{conclusion}。"
    )


def _missing_traces_answer(
    baseline: Mapping[str, Any],
    variant: Mapping[str, Any] | None,
) -> str:
    if variant is None:
        return "- Traces 不可用：未运行。"
    overconfident = float(variant["false_positive_rate"]) > float(
        baseline["false_positive_rate"]
    )
    conclusion = (
        "出现额外过度自信" if overconfident else "未观察到相对 Clean 的额外过度自信"
    )
    return (
        "- Traces 不可用：False Positive "
        f"`{float(variant['false_positive_rate']):.4f}`，Calibration ECE "
        f"`{variant['calibration_ece']}`；结论："
        f"{conclusion}。"
    )


def _knowledge_answer(variant: Mapping[str, Any] | None) -> str:
    if variant is None:
        return "- Knowledge：未运行。"
    rate = float(variant["history_overattribution_rate"])
    return (
        f"- Knowledge：History Over-attribution `{rate:.4f}`；"
        f"结论：{'未观察到知识带偏' if rate == 0 else '存在知识带偏'}。"
    )


def _change_answer(variants: Mapping[str, Mapping[str, Any]]) -> str:
    rate = max(
        (float(item["change_overattribution_rate"]) for item in variants.values()),
        default=0.0,
    )
    return (
        f"- Change：所有变体最大 Change Over-attribution `{rate:.4f}`；"
        f"结论：{'未观察到 Change 带偏' if rate == 0 else '存在 Change 带偏'}。"
    )


def write_generalization_artifacts(
    output_directory: Path,
    document: dict[str, Any],
    *,
    report_provenance: Mapping[str, Any] | None = None,
) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    (output_directory / "generalization-report.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_directory / "generalization-report.md").write_text(
        render_generalization_report(document),
        encoding="utf-8",
    )
    bad_cases = "".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
        for item in document.get("bad_cases", ())
    )
    (output_directory / "generalization-bad-cases.jsonl").write_text(
        bad_cases,
        encoding="utf-8",
    )
    provenance = dict(
        report_provenance
        or {
            "schema_version": "1.0",
            "artifact": "generalization-report",
            "source_count": 0,
            "leakage_violation_count": document["metrics"][
                "benchmark_leakage_violation_count"
            ],
            "sources": {},
            "extra_artifacts": {},
        }
    )
    (output_directory / "benchmark-provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    noise_30 = document["variants"].get("noise-30") or document["variants"].get(
        "noise_30"
    )
    noise_30_drop_pp = (
        float(noise_30["robustness_drop"]) * 100 if noise_30 is not None else None
    )
    generalization_gap_pp = float(document["metrics"]["generalization_gap"]) * 100
    resume_metrics = {
        "schema_version": "1.0",
        "metrics": document["metrics"],
        "known_rca_top1": document["known"]["rca_top1_accuracy"],
        "holdout_rca_top1": document["hidden_holdout"]["rca_top1_accuracy"],
        "hidden_holdout_rca_top1": document["hidden_holdout"]["rca_top1_accuracy"],
        "generalization_gap_pp": generalization_gap_pp,
        "root_resource_ood_accuracy": document["metrics"][
            "root_resource_ood_accuracy"
        ],
        "noise_30_drop_pp": noise_30_drop_pp,
        "unsupported_claim_rate": document["metrics"]["unsupported_claim_rate"],
        "cross_incident_leak_rate": document["metrics"][
            "cross_incident_evidence_leak_rate"
        ],
        "variant_rca_top1": {
            name: item["rca_top1_accuracy"]
            for name, item in document["variants"].items()
        },
        "variant_baseline_rca_top1": document["variant_baseline"][
            "rca_top1_accuracy"
        ],
        "multi_incident": document.get("multi_incident"),
        "multi_incident_integrity": document.get("multi_incident_integrity"),
        "bad_case_count": len(document.get("bad_cases", ())),
    }
    (output_directory / "resume-metrics.json").write_text(
        json.dumps(resume_metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
