from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from .live_runner import LiveEvidence, LiveScenarioInput
from .schemas import EvidenceType, ToolCall, ToolStatus


class GeneralizationVariant(StrEnum):
    CLEAN = "clean"
    SERVICE_RENAME = "service_rename"
    ERROR_PARAPHRASE = "error_paraphrase"
    NOISE_10 = "noise_10"
    NOISE_30 = "noise_30"
    NOISE_50 = "noise_50"
    MISSING_LOGS = "missing_logs"
    MISSING_TRACES = "missing_traces"
    MISSING_KNOWLEDGE = "missing_knowledge"
    TOPOLOGY_SHIFT = "topology_shift"
    COMPOSITE_FAULT = "composite_fault"


@dataclass(frozen=True)
class VariantResult:
    variant: GeneralizationVariant
    case: LiveScenarioInput


_ERROR_REPLACEMENTS = {
    "DB_TIMEOUT": "storage operation exceeded deadline",
    "db_timeout": "storage operation exceeded deadline",
    "DATABASE TIMEOUT": "storage operation exceeded deadline",
    "database timeout": "storage operation exceeded deadline",
    "DEPENDENCY_TIMEOUT": "upstream request expired",
    "dependency_timeout": "upstream request expired",
    "RESOURCE_EXHAUSTION": "unable to acquire a bounded resource",
    "resource_exhaustion": "unable to acquire a bounded resource",
}


def mutate_case(
    case: LiveScenarioInput,
    variant: GeneralizationVariant,
    *,
    seed: int = 0,
) -> LiveScenarioInput:
    """对 Runtime 快照施加可重复的泛化扰动。Ground Truth 不在此函数中出现。"""

    if variant is GeneralizationVariant.CLEAN:
        return case
    if variant is GeneralizationVariant.SERVICE_RENAME:
        return _rename_services(case, seed=seed)
    if variant is GeneralizationVariant.ERROR_PARAPHRASE:
        return _paraphrase_errors(case)
    if variant in {
        GeneralizationVariant.NOISE_10,
        GeneralizationVariant.NOISE_30,
        GeneralizationVariant.NOISE_50,
    }:
        ratio = {
            GeneralizationVariant.NOISE_10: 0.10,
            GeneralizationVariant.NOISE_30: 0.30,
            GeneralizationVariant.NOISE_50: 0.50,
        }[variant]
        return _inject_noise(case, ratio=ratio, seed=seed)
    if variant is GeneralizationVariant.MISSING_LOGS:
        return _remove_evidence_type(case, EvidenceType.LOG)
    if variant is GeneralizationVariant.MISSING_TRACES:
        return _remove_evidence_type(case, EvidenceType.TRACE)
    if variant is GeneralizationVariant.MISSING_KNOWLEDGE:
        return _remove_evidence_type(case, EvidenceType.KNOWLEDGE)
    if variant is GeneralizationVariant.TOPOLOGY_SHIFT:
        return _add_topology_shift(case, seed=seed)
    if variant is GeneralizationVariant.COMPOSITE_FAULT:
        return _add_composite_signal(case, seed=seed)
    raise ValueError(f"unsupported generalization variant: {variant}")


def _rename_services(case: LiveScenarioInput, *, seed: int) -> LiveScenarioInput:
    suffix = "-blue" if seed % 2 == 0 else "-green"
    replacements = {
        case.service_name: f"{case.service_name}{suffix}",
    }
    if case.entry_service:
        replacements[case.entry_service] = f"{case.entry_service}{suffix}"

    def replace(text: str) -> str:
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text

    evidence = tuple(
        item.model_copy(update={"summary": replace(item.summary)})
        for item in case.evidence
    )
    return case.model_copy(
        update={
            "service_name": replace(case.service_name),
            "entry_service": (
                replace(case.entry_service) if case.entry_service else None
            ),
            "summary": replace(case.summary),
            "evidence": evidence,
        }
    )


def _paraphrase_errors(case: LiveScenarioInput) -> LiveScenarioInput:
    def replace(text: str) -> str:
        for old, new in _ERROR_REPLACEMENTS.items():
            text = text.replace(old, new)
        return text

    return case.model_copy(
        update={
            "summary": replace(case.summary),
            "evidence": tuple(
                item.model_copy(update={"summary": replace(item.summary)})
                for item in case.evidence
            ),
        }
    )


def _inject_noise(
    case: LiveScenarioInput,
    *,
    ratio: float,
    seed: int,
) -> LiveScenarioInput:
    count = max(1, round(len(case.evidence) * ratio))
    evidence = list(case.evidence)
    for index in range(count):
        evidence.append(
            LiveEvidence(
                evidence_id=f"noise-{seed}-{index}",
                evidence_type=EvidenceType.LOG,
                source="noise-generator",
                summary="benign background request completed within its budget",
            )
        )
    return case.model_copy(update={"evidence": tuple(evidence)})


def _remove_evidence_type(
    case: LiveScenarioInput,
    evidence_type: EvidenceType,
) -> LiveScenarioInput:
    kept = tuple(item for item in case.evidence if item.evidence_type != evidence_type)
    kept_ids = {item.evidence_id for item in kept}
    calls = tuple(
        item.model_copy(
            update={
                "evidence_ids": tuple(
                    evidence_id
                    for evidence_id in item.evidence_ids
                    if evidence_id in kept_ids
                ),
                "status": (
                    item.status
                    if any(
                        evidence_id in kept_ids for evidence_id in item.evidence_ids
                    )
                    else ToolStatus.FAILED
                ),
            }
        )
        for item in case.tool_calls
    )
    if not kept:
        raise ValueError(f"removing {evidence_type} would remove all evidence")
    return case.model_copy(update={"evidence": kept, "tool_calls": calls})


def _add_topology_shift(case: LiveScenarioInput, *, seed: int) -> LiveScenarioInput:
    evidence_id = f"topology-shift-{seed}"
    evidence = LiveEvidence(
        evidence_id=evidence_id,
        evidence_type=EvidenceType.TOPOLOGY,
        source="topology",
        summary=(
            "request path crosses a renamed gateway in a different "
            "availability zone"
        ),
    )
    call = ToolCall(
        tool_type="topology.query@v1",
        status=ToolStatus.SUCCEEDED,
        evidence_ids=(evidence_id,),
    )
    return case.model_copy(
        update={
            "evidence": (*case.evidence, evidence),
            "tool_calls": (*case.tool_calls, call),
            "investigation_steps": max(
                case.investigation_steps, len(case.tool_calls) + 1
            ),
        }
    )


def _add_composite_signal(case: LiveScenarioInput, *, seed: int) -> LiveScenarioInput:
    evidence_id = f"composite-distractor-{seed}"
    evidence = LiveEvidence(
        evidence_id=evidence_id,
        evidence_type=EvidenceType.METRIC,
        source="prometheus",
        summary="a downstream retry budget also exceeded its warning threshold",
    )
    call = ToolCall(
        tool_type="metrics.query@v1",
        status=ToolStatus.SUCCEEDED,
        evidence_ids=(evidence_id,),
    )
    return case.model_copy(
        update={
            "evidence": (*case.evidence, evidence),
            "tool_calls": (*case.tool_calls, call),
            "investigation_steps": max(
                case.investigation_steps, len(case.tool_calls) + 1
            ),
        }
    )


def robustness_drop(clean: float, perturbed: float) -> float:
    return max(0.0, clean - perturbed)


def generalization_gap(known: float, holdout: float) -> float:
    return known - holdout


def summarize_variant_scores(
    scores: Iterable[tuple[str, float]],
) -> dict[str, float]:
    values = list(scores)
    if not values:
        return {}
    return {name: value for name, value in values}
