from __future__ import annotations

import argparse
import json
from pathlib import Path

VARIANTS = ("baseline", "change", "topology", "rag", "dynamic")


def render_ablation(paths: dict[str, Path]) -> str:
    missing = [name for name in VARIANTS if name not in paths]
    if missing:
        raise ValueError(f"missing measured variant results: {', '.join(missing)}")
    rows = []
    for name in VARIANTS:
        result = json.loads(paths[name].read_text(encoding="utf-8"))
        summary = result.get("summary")
        if not isinstance(summary, dict):
            raise ValueError(f"{name} result has no summary")
        rows.append(
            f"| {name} | {summary.get('rca_top1_accuracy', 'N/A')} | "
            f"{summary.get('evidence_f1', 'N/A')} | "
            f"{summary.get('avg_tool_calls', 'N/A')} | "
            f"{summary.get('avg_tokens', 'N/A')} |"
        )
    return "\n".join(
        (
            "# AIOps Benchmark 消融实验报告",
            "",
            "下表只读取各变体的真实 `results.json`，缺少任何变体时命令失败。",
            "",
            "| 变体 | RCA Top-1 | Evidence F1 | 平均 Tool Calls | 平均 Tokens |",
            "|---|---:|---:|---:|---:|",
            *rows,
            "",
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render measured AIOps ablation results"
    )
    for name in VARIANTS:
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    paths = {name: getattr(args, name) for name in VARIANTS}
    report = render_ablation(paths)
    if args.output.exists():
        raise ValueError("output already exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
