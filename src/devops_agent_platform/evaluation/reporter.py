from __future__ import annotations

from devops_agent_platform.evaluation.schemas import BenchmarkMetadata
from devops_agent_platform.evaluation.scorer import RunScore, Summary


def render_evaluation_report(
    metadata: BenchmarkMetadata,
    scores: tuple[RunScore, ...],
    summary: Summary,
) -> str:
    """把真实评分结果渲染为稳定、可审阅的中文 Markdown。"""
    failed = [item for item in scores if not item.passed]
    lines = [
        "# AIOps Benchmark 评测报告",
        "",
        (
            "> 本报告由确定性 Scorer 根据结构化 Prediction 与隔离的 "
            "Ground Truth 自动生成。"
        ),
        "> 当前结果不等同于真实生产环境或真实 LLM 验收。",
        "",
        "## 实验环境",
        "",
        f"- Benchmark 版本：`{metadata.benchmark_version}`",
        f"- Git Commit：`{metadata.git_commit}`",
        f"- 模型：`{metadata.model_provider}/{metadata.model_name}`",
        f"- Temperature：`{metadata.temperature}`",
        f"- Prompt 版本：`{metadata.prompt_version}`",
        f"- 调查策略：`{metadata.investigation_policy}`",
        f"- Scenario 版本：`{metadata.scenario_version}`",
        f"- 运行时间：`{metadata.timestamp.isoformat()}`",
        "",
        "## 汇总指标",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| Runs | {summary.total_runs} |",
        f"| 通过 Runs | {summary.passed_runs} |",
        f"| RCA Top-1 Accuracy | {_percent(summary.rca_top1_accuracy)} |",
        f"| Root Service Accuracy | {_percent(summary.root_service_accuracy)} |",
        f"| Root Type Accuracy | {_percent(summary.root_type_accuracy)} |",
        f"| Evidence Precision | {_percent(summary.evidence_precision)} |",
        f"| Evidence Recall | {_percent(summary.evidence_recall)} |",
        f"| Evidence F1 | {_percent(summary.evidence_f1)} |",
        f"| Unsupported Claim Rate | {_percent(summary.unsupported_claim_rate)} |",
        f"| Forbidden Claim Rate | {_percent(summary.forbidden_claim_rate)} |",
        f"| False Positive Rate | {_percent(summary.false_positive_rate)} |",
        f"| Causal Chain F1 | {_percent(summary.causal_chain_f1)} |",
        f"| Blast Radius F1 | {_percent(summary.blast_radius_f1)} |",
        f"| Tool Selection Accuracy | {_percent(summary.tool_selection_accuracy)} |",
        f"| 平均 Tool Calls | {summary.avg_tool_calls:.2f} |",
        f"| 平均 Investigation Steps | {summary.avg_investigation_steps:.2f} |",
        f"| Redundant Tool Call Rate | {_percent(summary.redundant_tool_call_rate)} |",
        f"| Invalid Tool Proposal Rate | "
        f"{_percent(summary.invalid_tool_proposal_rate)} |",
        f"| Policy Block Rate | {_percent(summary.policy_block_rate)} |",
        f"| 平均 LLM Calls | {summary.avg_llm_calls:.2f} |",
        f"| P50 Latency | {summary.p50_latency_ms} ms |",
        f"| P95 Latency | {summary.p95_latency_ms} ms |",
        f"| 平均 Tokens | {summary.avg_tokens:.2f} |",
        f"| 平均成本 | {summary.avg_cost:.6f} |",
        "",
        "## 每次运行",
        "",
        "| Scenario | Run | 通过 | RCA | Evidence F1 | Causal F1 | Blast F1 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    lines.extend(
        (
            "| {scenario} | {run} | {passed} | {rca} | {evidence} | "
            "{causal} | {blast} |"
        ).format(
            scenario=item.scenario_id,
            run=item.run_id,
            passed="是" if item.passed else "否",
            rca=_percent(item.rca_exact_match),
            evidence=_percent(item.evidence_f1),
            causal=_percent(item.causal_chain_f1),
            blast=_percent(item.blast_radius_f1),
        )
        for item in scores
    )
    lines.extend(["", "## 失败案例", ""])
    if not failed:
        lines.append("本次没有失败案例。")
    else:
        lines.extend(_failure_line(item) for item in failed)
    lines.append("")
    return "\n".join(lines)


def _failure_line(score: RunScore) -> str:
    reasons: list[str] = []
    if not score.rca_exact_match:
        reasons.append("根因不匹配")
    if score.evidence_recall < 1:
        reasons.append("必需 Evidence 不完整")
    if score.unsupported_claim_rate:
        reasons.append("存在无证据 Claim")
    if score.forbidden_claims_found:
        reasons.append("命中 Forbidden Claim")
    if score.causal_chain_f1 < 1:
        reasons.append("因果链不匹配")
    if score.blast_radius_f1 < 1:
        reasons.append("影响面不匹配")
    if score.forbidden_tool_proposed:
        reasons.append("提出禁止工具")
    detail = "、".join(reasons) or "未通过严格门禁"
    return f"- `{score.scenario_id}` / `{score.run_id}`：{detail}。"


def _percent(value: float | bool) -> str:
    return f"{float(value) * 100:.2f}%"
