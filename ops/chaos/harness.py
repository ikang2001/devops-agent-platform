from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CASES = (
    "worker-crash",
    "kafka-unavailable",
    "postgres-unavailable",
    "observability-timeout",
)

_EXPECTATIONS = {
    "worker-crash": (
        "等待 lease 过期后由新 owner reclaim；旧 owner 的完成写入必须被 fence 拒绝。"
    ),
    "kafka-unavailable": "Outbox 保留事件，Kafka 恢复后继续发布且不重复创建工作流。",
    "postgres-unavailable": "请求失败关闭、Worker 不误报成功，数据库恢复后可以继续。",
    "observability-timeout": (
        "continue_on_step_failure 开启时只生成 partial/capped 报告且禁止 CONFIRMED。"
    ),
}


@dataclass(frozen=True)
class ChaosResult:
    case: str
    simulated: bool
    passed: bool | None
    recovery_seconds: float | None
    lost_workflow_count: int | None
    duplicate_completion_count: int | None
    fence_rejection_count: int | None
    outbox_recovery_seconds: float | None
    lost_event_count: int | None
    duplicate_workflow_count: int | None
    state_corruption_count: int | None
    erroneous_success_count: int | None
    resumed: bool | None
    partial_report: bool | None
    confidence_capped: bool | None
    confirmed_count: int | None
    notes: str


def run_case(case: str) -> ChaosResult:
    """返回不带伪造耗时的合同检查项。"""
    if case not in CASES:
        raise ValueError(f"unsupported chaos case: {case}")
    return ChaosResult(
        case=case,
        simulated=True,
        passed=None,
        recovery_seconds=None,
        lost_workflow_count=None,
        duplicate_completion_count=None,
        fence_rejection_count=None,
        outbox_recovery_seconds=None,
        lost_event_count=None,
        duplicate_workflow_count=None,
        state_corruption_count=None,
        erroneous_success_count=None,
        resumed=None,
        partial_report=None,
        confidence_capped=None,
        confirmed_count=None,
        notes=_EXPECTATIONS[case],
    )


def run_all() -> tuple[ChaosResult, ...]:
    return tuple(run_case(case) for case in CASES)


def load_live_results(path: Path) -> tuple[ChaosResult, ...]:
    """读取外部故障注入采集结果，并按每类安全不变量判定。"""
    return _parse_live_results(_load_live_document(path))


def _load_live_document(path: Path) -> dict[str, Any]:
    """读取并校验 live 观测信封，保留来源元数据供报告传播。"""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read live chaos observations {path}") from exc
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != "1.0"
        or document.get("simulated") is not False
        or not isinstance(document.get("results"), list)
    ):
        raise ValueError("live chaos observations have an invalid envelope")
    return document


def _parse_live_results(document: dict[str, Any]) -> tuple[ChaosResult, ...]:
    results = tuple(_parse_live_case(item) for item in document["results"])
    if {item.case for item in results} != set(CASES) or len(results) != len(CASES):
        raise ValueError("live chaos observations must contain every case exactly once")
    return results


def _parse_live_case(value: object) -> ChaosResult:
    if not isinstance(value, dict) or value.get("case") not in CASES:
        raise ValueError("live chaos case is invalid")
    case = value["case"]
    numeric_fields = (
        "recovery_seconds",
        "lost_workflow_count",
        "duplicate_completion_count",
        "fence_rejection_count",
        "outbox_recovery_seconds",
        "lost_event_count",
        "duplicate_workflow_count",
        "state_corruption_count",
        "erroneous_success_count",
        "confirmed_count",
    )
    normalized: dict[str, int | float | None] = {}
    for field_name in numeric_fields:
        item = value.get(field_name)
        if item is not None and (
            isinstance(item, bool) or not isinstance(item, int | float) or item < 0
        ):
            raise ValueError(f"live chaos metric {field_name} is invalid")
        normalized[field_name] = item
    boolean_fields = ("resumed", "partial_report", "confidence_capped")
    booleans: dict[str, bool | None] = {}
    for field_name in boolean_fields:
        item = value.get(field_name)
        if item is not None and not isinstance(item, bool):
            raise ValueError(f"live chaos metric {field_name} is invalid")
        booleans[field_name] = item
    result = ChaosResult(
        case=case,
        simulated=False,
        passed=None,
        notes=str(value.get("notes", "Measured live chaos observation."))[:4096],
        **normalized,
        **booleans,
    )
    return ChaosResult(**{**asdict(result), "passed": _case_passed(result)})


def _case_passed(result: ChaosResult) -> bool:
    if result.case == "worker-crash":
        return (
            result.recovery_seconds is not None
            and result.lost_workflow_count == 0
            and result.duplicate_completion_count == 0
            and result.fence_rejection_count is not None
            and result.fence_rejection_count >= 1
        )
    if result.case == "kafka-unavailable":
        return (
            result.outbox_recovery_seconds is not None
            and result.lost_event_count == 0
            and result.duplicate_workflow_count == 0
        )
    if result.case == "postgres-unavailable":
        return (
            result.recovery_seconds is not None
            and result.state_corruption_count == 0
            and result.erroneous_success_count == 0
            and result.resumed is True
        )
    return (
        result.partial_report is True
        and result.confidence_capped is True
        and result.confirmed_count == 0
    )


def write_report(
    output: Path,
    *,
    mode: str = "contract",
    live_input: Path | None = None,
) -> dict[str, Any]:
    if output.exists():
        raise ValueError("chaos report already exists; choose a new output path")
    if mode == "contract":
        results = run_all()
        live_document = None
    elif mode == "live" and live_input is not None:
        live_document = _load_live_document(live_input)
        results = _parse_live_results(live_document)
    else:
        raise ValueError("live mode requires a live observation input")
    simulated = mode == "contract"
    synthetic = simulated or bool(live_document.get("synthetic", True))
    simulation = (
        bool(live_document.get("simulation", False)) if live_document else False
    )
    production_acceptance = (
        not synthetic
        and not simulation
        and live_document is not None
        and live_document.get("production_acceptance") is True
    )
    report = {
        "schema_version": "1.0",
        "mode": mode,
        "simulated": simulated,
        "synthetic": synthetic,
        "simulation": simulation,
        "production_acceptance": production_acceptance,
        "target_label": (
            "contract-only"
            if live_document is None
            else str(live_document.get("target_label", "unspecified-live-target"))
        ),
        "generated_at": datetime.now(UTC).isoformat(),
        "passed": None if simulated else all(item.passed for item in results),
        "results": [asdict(item) for item in results],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DevOps Agent Chaos evidence harness")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("contract", "live"), default="contract")
    parser.add_argument("--live-input", type=Path)
    arguments = parser.parse_args(argv)
    report = write_report(
        arguments.output,
        mode=arguments.mode,
        live_input=arguments.live_input,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
