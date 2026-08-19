from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceType(StrEnum):
    """与平台 EvidenceType 对齐的评测证据类型。"""

    METRIC = "METRIC"
    LOG = "LOG"
    TRACE = "TRACE"
    CHANGE = "CHANGE"
    TOPOLOGY = "TOPOLOGY"
    KNOWLEDGE = "KNOWLEDGE"
    RUNBOOK = "RUNBOOK"
    HTTP = "HTTP"


class ConclusionStatus(StrEnum):
    UNDETERMINED = "UNDETERMINED"
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    NO_ACTIONABLE_ROOT_CAUSE = "NO_ACTIONABLE_ROOT_CAUSE"


ExpectedConclusionStatus = Literal[
    ConclusionStatus.UNDETERMINED,
    ConclusionStatus.CANDIDATE,
    ConclusionStatus.NO_ACTIONABLE_ROOT_CAUSE,
]


class ClaimType(StrEnum):
    ROOT_CAUSE = "ROOT_CAUSE"
    CHANGE = "CHANGE"
    DEPENDENCY_FAILURE = "DEPENDENCY_FAILURE"
    AFFECTED_SERVICE = "AFFECTED_SERVICE"
    CAUSAL_EDGE = "CAUSAL_EDGE"


class ToolStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class RootCauseRef(_Model):
    service: str = Field(min_length=1, max_length=128)
    type: str = Field(min_length=1, max_length=128)
    resource: str | None = Field(default=None, max_length=256)


class RootCauseCandidateRef(_Model):
    """进入最终选择器的、按分数降序排列的根因候选快照。"""

    candidate_id: str = Field(min_length=1, max_length=64)
    root_cause: RootCauseRef
    score: float = Field(ge=0, le=1)
    supporting_evidence_ids: tuple[str, ...] = ()
    contradicting_evidence_ids: tuple[str, ...] = ()
    source_evidence_types: tuple[EvidenceType, ...] = ()

    @model_validator(mode="after")
    def validate_candidate(self) -> RootCauseCandidateRef:
        support = self.supporting_evidence_ids
        contradiction = self.contradicting_evidence_ids
        if len(support) != len(set(support)):
            raise ValueError("supporting_evidence_ids must be unique")
        if len(contradiction) != len(set(contradiction)):
            raise ValueError("contradicting_evidence_ids must be unique")
        if set(support) & set(contradiction):
            raise ValueError("support and contradiction evidence must be disjoint")
        if len(self.source_evidence_types) != len(set(self.source_evidence_types)):
            raise ValueError("source_evidence_types must be unique")
        return self


class CausalEdge(_Model):
    from_node: str = Field(min_length=1, max_length=256)
    to_node: str = Field(min_length=1, max_length=256)
    evidence_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_edge(self) -> CausalEdge:
        if self.from_node == self.to_node:
            raise ValueError("causal edge must connect two different nodes")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("causal edge evidence_ids must be unique")
        return self


class Claim(_Model):
    claim_type: ClaimType
    statement: str = Field(min_length=1, max_length=2048)
    evidence_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_claim(self) -> Claim:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("claim evidence_ids must be unique")
        return self


class ToolCall(_Model):
    tool_type: str = Field(min_length=1, max_length=128)
    status: ToolStatus
    evidence_ids: tuple[str, ...] = ()
    proposal_valid: bool = True

    @model_validator(mode="after")
    def validate_tool_call(self) -> ToolCall:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("tool call evidence_ids must be unique")
        return self


class RCAPrediction(_Model):
    """Benchmark Runner 接收的结构化 Agent 输出快照。

    该模型只描述评分契约，不改变生产 RCAReport 的持久化结构；Ground Truth
    不会被序列化到 Agent 输入中。
    """

    scenario_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=128)
    root_cause: RootCauseRef | None
    conclusion_status: ConclusionStatus
    confidence: float = Field(ge=0, le=1)
    evidence_ids: tuple[str, ...] = ()
    evidence_types: tuple[EvidenceType, ...] = ()
    claims: tuple[Claim, ...] = ()
    causal_chain: tuple[CausalEdge, ...] = ()
    affected_services: tuple[str, ...] = ()
    root_cause_candidates: tuple[RootCauseCandidateRef, ...] = Field(
        default=(),
        max_length=5,
    )
    tool_calls: tuple[ToolCall, ...] = ()
    investigation_steps: int = Field(ge=0)
    llm_calls: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_prediction(self) -> RCAPrediction:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence_ids must be unique")
        if len(self.evidence_types) != len(set(self.evidence_types)):
            raise ValueError("evidence_types must be unique")
        if len(self.affected_services) != len(set(self.affected_services)):
            raise ValueError("affected_services must be unique")
        candidate_ids = [item.candidate_id for item in self.root_cause_candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate_id values must be unique")
        candidate_roots = [
            (
                item.root_cause.service,
                item.root_cause.type,
                item.root_cause.resource,
            )
            for item in self.root_cause_candidates
        ]
        if len(candidate_roots) != len(set(candidate_roots)):
            raise ValueError("root cause candidates must be unique")
        scores = [item.score for item in self.root_cause_candidates]
        if scores != sorted(scores, reverse=True):
            raise ValueError("root cause candidates must be sorted by score")
        if self.root_cause is None and self.conclusion_status in {
            ConclusionStatus.CANDIDATE,
            ConclusionStatus.CONFIRMED,
        }:
            raise ValueError("a root cause is required for a candidate conclusion")
        if self.root_cause is not None and self.conclusion_status in {
            ConclusionStatus.UNDETERMINED,
            ConclusionStatus.NO_ACTIONABLE_ROOT_CAUSE,
        }:
            raise ValueError("a root cause cannot be reported as undetermined")
        if self.root_cause is not None and not any(
            claim.claim_type is ClaimType.ROOT_CAUSE for claim in self.claims
        ):
            raise ValueError("ROOT_CAUSE claim is required when root_cause is present")
        return self


class ScenarioGroundTruth(_Model):
    """从 MiniShop Manifest 提取的、仅供 Scorer 使用的 Ground Truth。"""

    scenario_id: str = Field(min_length=1, max_length=64)
    scenario_version: str = Field(min_length=1, max_length=32)
    root_cause: RootCauseRef | None
    expected_conclusion_status: ExpectedConclusionStatus = ConclusionStatus.CANDIDATE
    # Ground Truth 的 Evidence ID 是稳定的事实引用；不要把它和 EvidenceType
    # 混为一谈。旧版的内存构造数据可能没有 ID，因此保留空元组表示 N/A。
    required_evidence_ids: tuple[str, ...] = ()
    required_evidence_types: tuple[EvidenceType, ...] = Field(min_length=1)
    optional_evidence_types: tuple[EvidenceType, ...] = ()
    causal_chain: tuple[CausalEdge, ...] = ()
    affected_services: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = Field(min_length=1)
    expected_tool_types: tuple[str, ...] = Field(min_length=1)
    forbidden_tool_types: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def default_expected_conclusion_status(cls, value: object) -> object:
        if (
            not isinstance(value, Mapping)
            or (
                "expected_conclusion_status" in value
                and value["expected_conclusion_status"] is not None
            )
        ):
            return value
        payload = dict(value)
        payload["expected_conclusion_status"] = (
            ConclusionStatus.CANDIDATE
            if payload.get("root_cause") is not None
            else ConclusionStatus.NO_ACTIONABLE_ROOT_CAUSE
        )
        return payload

    @model_validator(mode="after")
    def validate_ground_truth(self) -> ScenarioGroundTruth:
        if set(self.required_evidence_types) & set(self.optional_evidence_types):
            raise ValueError("required and optional evidence types must be disjoint")
        for field_name in (
            "required_evidence_ids",
            "required_evidence_types",
            "optional_evidence_types",
            "affected_services",
            "forbidden_claims",
            "expected_tool_types",
            "forbidden_tool_types",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
        if set(self.expected_tool_types) & set(self.forbidden_tool_types):
            raise ValueError("expected and forbidden tools must be disjoint")
        edges = [(item.from_node, item.to_node) for item in self.causal_chain]
        if len(edges) != len(set(edges)):
            raise ValueError("causal_chain edges must be unique")
        if self.root_cause is None:
            if self.expected_conclusion_status is ConclusionStatus.CANDIDATE:
                raise ValueError("a candidate conclusion requires a root_cause")
            if self.causal_chain or self.affected_services:
                raise ValueError("a no-root-cause scenario cannot define causal impact")
        elif self.expected_conclusion_status is not ConclusionStatus.CANDIDATE:
            raise ValueError(
                "a root_cause requires expected_conclusion_status CANDIDATE"
            )
        return self

    @classmethod
    def from_manifest(cls, document: Mapping[str, Any]) -> ScenarioGroundTruth:
        if not isinstance(document, Mapping):
            raise ValueError("scenario manifest must be an object")
        scenario_id = document.get("scenario_id")
        schema_version = document.get("schema_version")
        ground_truth = document.get("ground_truth")
        if not isinstance(ground_truth, Mapping):
            raise ValueError("ground_truth must be an object")
        root = ground_truth.get("root_cause")
        root_payload: dict[str, Any] | None
        if root is None:
            root_payload = None
        elif isinstance(root, Mapping):
            root_payload = {
                "service": root.get("service_name"),
                "type": root.get("root_cause_type"),
                "resource": root.get("root_cause_resource"),
            }
        else:
            raise ValueError("ground_truth.root_cause must be an object or null")
        required = ground_truth.get("required_evidence_types")
        required_ids = ground_truth.get("required_evidence", [])
        optional = ground_truth.get("optional_evidence_types", [])
        if not isinstance(required_ids, list):
            raise ValueError("ground_truth.required_evidence must be a list")
        chain = ground_truth.get("causal_chain")
        if not isinstance(required, list):
            raise ValueError("ground_truth.required_evidence_types is required")
        if not isinstance(optional, list):
            raise ValueError("ground_truth.optional_evidence_types must be a list")
        if not isinstance(chain, list):
            raise ValueError("ground_truth.causal_chain is required")
        return cls.model_validate(
            {
                "scenario_id": scenario_id,
                "scenario_version": schema_version,
                "root_cause": root_payload,
                **(
                    {
                        "expected_conclusion_status": ground_truth[
                            "expected_conclusion_status"
                        ]
                    }
                    if "expected_conclusion_status" in ground_truth
                    else {}
                ),
                "required_evidence_ids": required_ids,
                "required_evidence_types": required,
                "optional_evidence_types": optional,
                "causal_chain": chain,
                "affected_services": ground_truth.get("affected_services", []),
                "forbidden_claims": ground_truth.get("forbidden_claims"),
                "expected_tool_types": ground_truth.get("expected_tool_types"),
                "forbidden_tool_types": ground_truth.get("forbidden_tool_types"),
            }
        )


class BenchmarkMetadata(_Model):
    benchmark_version: str = Field(min_length=1, max_length=32)
    git_commit: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    model_provider: str = Field(min_length=1, max_length=64)
    model_name: str = Field(min_length=1, max_length=128)
    temperature: float = Field(ge=0, le=2)
    prompt_version: str = Field(min_length=1, max_length=64)
    investigation_policy: str = Field(min_length=1, max_length=64)
    scenario_version: str = Field(min_length=1, max_length=32)
    timestamp: datetime
    top_p: float = Field(default=1.0, ge=0, le=1)
    max_tokens: int = Field(default=0, ge=0, le=1_000_000)
    scenario_hash: str = ""
    config_hash: str = ""
    prompt_hash: str = ""
    knowledge_dataset_hash: str = ""
    docker_image_digest: str = ""

    @model_validator(mode="after")
    def validate_metadata(self) -> BenchmarkMetadata:
        if not math.isfinite(self.temperature):
            raise ValueError("temperature must be finite")
        if not math.isfinite(self.top_p):
            raise ValueError("top_p must be finite")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must include timezone information")
        for field_name in (
            "scenario_hash",
            "config_hash",
            "prompt_hash",
            "knowledge_dataset_hash",
            "docker_image_digest",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or len(value) > 256:
                raise ValueError(f"{field_name} is invalid")
        return self


class BenchmarkInput(_Model):
    benchmark: BenchmarkMetadata
    runs: tuple[RCAPrediction, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_runs(self) -> BenchmarkInput:
        run_ids = [item.run_id for item in self.runs]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("run_id values must be unique")
        return self


def load_benchmark_input(path: Path) -> BenchmarkInput:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read benchmark input {path}") from exc
    return BenchmarkInput.model_validate(document)


def load_ground_truth_catalog(
    directory: Path,
    *,
    include_extended: bool = False,
) -> tuple[ScenarioGroundTruth, ...]:
    paths = sorted(directory.glob("*.json"))
    if not include_extended and directory.name == "scenarios":
        baseline_ids = {
            "checkout-latency",
            "deployment-regression",
            "inventory-db-timeout",
            "payment-error",
        }
        paths = [path for path in paths if path.stem in baseline_ids]
    if not paths:
        raise ValueError(f"no scenario manifests found in {directory}")
    catalog: list[ScenarioGroundTruth] = []
    for path in paths:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"could not read scenario manifest {path}") from exc
        catalog.append(ScenarioGroundTruth.from_manifest(document))
    ids = [item.scenario_id for item in catalog]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate scenario_id in ground truth catalog")
    return tuple(catalog)


def load_extended_ground_truth_catalog(
    directory: Path,
) -> tuple[ScenarioGroundTruth, ...]:
    return load_ground_truth_catalog(directory, include_extended=True)
