from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from math import ceil
from statistics import mean

from devops_agent_platform.causal_context import (
    normalize_affected_service,
    normalize_causal_node,
)

from .schemas import (
    CausalEdge,
    ConclusionStatus,
    RCAPrediction,
    ScenarioGroundTruth,
)


@dataclass(frozen=True)
class RunScore:
    scenario_id: str
    run_id: str
    passed: bool
    root_service_correct: bool
    root_type_correct: bool
    root_resource_correct: bool
    rca_exact_match: bool
    strict_rca_match: bool
    ground_truth_candidate_rank: int | None
    candidate_recall_at_3: bool | None
    candidate_reciprocal_rank: float | None
    candidate_ranking_correct: bool | None
    root_cause_failure_type: str | None
    suspected_failure_layer: str | None
    conclusion_status_correct: bool
    expected_conclusion_status: str
    predicted_conclusion_status: str
    change_overattribution: bool
    history_overattribution: bool
    evidence_precision: float
    evidence_recall: float
    required_evidence_id_recall: float | None
    evidence_f1: float
    unsupported_claim_count: int
    unsupported_claim_rate: float
    forbidden_claims_found: tuple[str, ...]
    false_positive_root_cause: bool
    causal_chain_precision: float
    causal_chain_recall: float
    causal_chain_f1: float
    blast_radius_precision: float
    blast_radius_recall: float
    blast_radius_f1: float
    tool_selection_accuracy: float
    redundant_tool_call_rate: float
    invalid_tool_proposal_rate: float
    policy_block_rate: float
    forbidden_tool_proposed: bool
    investigation_steps: int
    tool_calls: int
    llm_calls: int
    latency_ms: int
    total_tokens: int
    estimated_cost: float

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        for field_name in (
            "forbidden_claims_found",
        ):
            result[field_name] = list(getattr(self, field_name))
        return result


@dataclass(frozen=True)
class Summary:
    total_runs: int
    passed_runs: int
    rca_top1_accuracy: float
    strict_rca_accuracy: float
    root_service_accuracy: float
    root_type_accuracy: float
    root_resource_accuracy: float
    candidate_evaluated_runs: int
    candidate_recall_at_3: float | None
    candidate_mrr: float | None
    candidate_ranking_accuracy: float | None
    change_overattribution_rate: float
    history_overattribution_rate: float
    conclusion_status_accuracy: float
    undetermined_precision: float | None
    undetermined_recall: float | None
    no_actionable_root_cause_accuracy: float | None
    evidence_precision: float
    evidence_recall: float
    required_evidence_id_recall: float | None
    evidence_f1: float
    unsupported_claim_rate: float
    forbidden_claim_rate: float
    false_positive_rate: float
    causal_chain_f1: float
    blast_radius_f1: float
    tool_selection_accuracy: float
    avg_tool_calls: float
    avg_investigation_steps: float
    redundant_tool_call_rate: float
    invalid_tool_proposal_rate: float
    policy_block_rate: float
    avg_llm_calls: float
    avg_tokens: float
    avg_cost: float
    p50_latency_ms: int
    p95_latency_ms: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class BenchmarkScorer:
    """对单次结构化 RCA 结果执行确定性、无 LLM 依赖的评分。"""

    def score(
        self,
        ground_truth: ScenarioGroundTruth,
        prediction: RCAPrediction,
    ) -> RunScore:
        if ground_truth.scenario_id != prediction.scenario_id:
            raise ValueError("prediction scenario_id does not match ground truth")
        root_service_correct, root_type_correct, root_resource_correct = (
            self._score_root_cause(ground_truth, prediction)
        )
        conclusion_status_correct = (
            prediction.conclusion_status.value
            == ground_truth.expected_conclusion_status.value
        )
        rca_exact = root_service_correct and root_type_correct
        strict_rca = rca_exact and root_resource_correct
        candidate_rank = self._candidate_rank(ground_truth, prediction)
        candidate_recall, reciprocal_rank, ranking_correct = (
            self._candidate_metrics(ground_truth, prediction, candidate_rank)
        )
        failure_type, failure_layer = self._classify_root_cause_failure(
            ground_truth,
            prediction,
            root_service_correct=root_service_correct,
            root_type_correct=root_type_correct,
            root_resource_correct=root_resource_correct,
            candidate_rank=candidate_rank,
        )
        change_overattribution, history_overattribution = (
            self._score_evidence_overattribution(
                ground_truth,
                prediction,
                rca_exact=rca_exact,
            )
        )
        evidence_precision, evidence_recall, evidence_f1 = self._score_evidence(
            ground_truth,
            prediction,
        )
        required_evidence_id_recall = self._score_required_evidence_id_recall(
            ground_truth,
            prediction,
        )
        unsupported_count, unsupported_rate = self._score_claim_support(prediction)
        forbidden = self._find_forbidden_claims(ground_truth, prediction)
        chain = _set_metrics(
            (_edge_key(item) for item in ground_truth.causal_chain),
            (_edge_key(item) for item in prediction.causal_chain),
        )
        blast = _set_metrics(
            (
                normalize_affected_service(item)
                for item in ground_truth.affected_services
            ),
            (
                normalize_affected_service(item)
                for item in prediction.affected_services
            ),
        )
        tool_selection = self._score_tool_selection(ground_truth, prediction)
        redundant = self._redundant_tool_call_rate(prediction)
        tool_count = len(prediction.tool_calls)
        invalid = sum(not item.proposal_valid for item in prediction.tool_calls)
        blocked = sum(item.status.value == "BLOCKED" for item in prediction.tool_calls)
        invalid_rate = invalid / tool_count if tool_count else 0.0
        block_rate = blocked / tool_count if tool_count else 0.0
        forbidden_tool = any(
            item.tool_type in ground_truth.forbidden_tool_types
            for item in prediction.tool_calls
        )
        false_positive = (
            ground_truth.root_cause is None
            and prediction.root_cause is not None
        )
        passed = all(
            (
                rca_exact,
                strict_rca,
                evidence_precision == 1.0,
                evidence_recall == 1.0,
                conclusion_status_correct,
                unsupported_count == 0,
                not forbidden,
                not false_positive,
                chain[2] == 1.0,
                blast[2] == 1.0,
                tool_selection == 1.0,
                not forbidden_tool,
                prediction.conclusion_status is not ConclusionStatus.CONFIRMED,
            )
        )
        return RunScore(
            scenario_id=prediction.scenario_id,
            run_id=prediction.run_id,
            passed=passed,
            root_service_correct=root_service_correct,
            root_type_correct=root_type_correct,
            root_resource_correct=root_resource_correct,
            rca_exact_match=rca_exact,
            strict_rca_match=strict_rca,
            ground_truth_candidate_rank=candidate_rank,
            candidate_recall_at_3=candidate_recall,
            candidate_reciprocal_rank=reciprocal_rank,
            candidate_ranking_correct=ranking_correct,
            root_cause_failure_type=failure_type,
            suspected_failure_layer=failure_layer,
            conclusion_status_correct=conclusion_status_correct,
            expected_conclusion_status=ground_truth.expected_conclusion_status.value,
            predicted_conclusion_status=prediction.conclusion_status.value,
            change_overattribution=change_overattribution,
            history_overattribution=history_overattribution,
            evidence_precision=evidence_precision,
            evidence_recall=evidence_recall,
            required_evidence_id_recall=required_evidence_id_recall,
            evidence_f1=evidence_f1,
            unsupported_claim_count=unsupported_count,
            unsupported_claim_rate=unsupported_rate,
            forbidden_claims_found=forbidden,
            false_positive_root_cause=false_positive,
            causal_chain_precision=chain[0],
            causal_chain_recall=chain[1],
            causal_chain_f1=chain[2],
            blast_radius_precision=blast[0],
            blast_radius_recall=blast[1],
            blast_radius_f1=blast[2],
            tool_selection_accuracy=tool_selection,
            redundant_tool_call_rate=redundant,
            invalid_tool_proposal_rate=invalid_rate,
            policy_block_rate=block_rate,
            forbidden_tool_proposed=forbidden_tool,
            investigation_steps=prediction.investigation_steps,
            tool_calls=tool_count,
            llm_calls=prediction.llm_calls,
            latency_ms=prediction.latency_ms,
            total_tokens=prediction.total_tokens,
            estimated_cost=prediction.estimated_cost,
        )

    @staticmethod
    def _score_root_cause(
        ground_truth: ScenarioGroundTruth,
        prediction: RCAPrediction,
    ) -> tuple[bool, bool, bool]:
        if ground_truth.root_cause is None:
            empty = prediction.root_cause is None
            return empty, empty, empty
        if prediction.root_cause is None:
            return False, False, False
        expected = ground_truth.root_cause
        actual = prediction.root_cause
        return (
            expected.service == actual.service,
            expected.type == actual.type,
            expected.resource == actual.resource,
        )

    @staticmethod
    def _candidate_rank(
        ground_truth: ScenarioGroundTruth,
        prediction: RCAPrediction,
    ) -> int | None:
        expected = ground_truth.root_cause
        if expected is None:
            return None
        for rank, candidate in enumerate(
            prediction.root_cause_candidates,
            start=1,
        ):
            root = candidate.root_cause
            if root.service == expected.service and root.type == expected.type:
                return rank
        return None

    @staticmethod
    def _candidate_metrics(
        ground_truth: ScenarioGroundTruth,
        prediction: RCAPrediction,
        rank: int | None,
    ) -> tuple[bool | None, float | None, bool | None]:
        if ground_truth.root_cause is None or not prediction.root_cause_candidates:
            return None, None, None
        return rank is not None and rank <= 3, 1 / rank if rank else 0.0, rank == 1

    @staticmethod
    def _classify_root_cause_failure(
        ground_truth: ScenarioGroundTruth,
        prediction: RCAPrediction,
        *,
        root_service_correct: bool,
        root_type_correct: bool,
        root_resource_correct: bool,
        candidate_rank: int | None,
    ) -> tuple[str | None, str | None]:
        if root_service_correct and root_type_correct and root_resource_correct:
            return None, None
        if ground_truth.root_cause is None and prediction.root_cause is None:
            return "CONCLUSION_STATUS_MISMATCH", "FINAL_SELECTOR"
        if ground_truth.root_cause is None:
            return "FINAL_SELECTION_ERROR", "FINAL_SELECTOR"
        if prediction.root_cause_candidates:
            if candidate_rank is None or candidate_rank > 3:
                return "INSUFFICIENT_DISCRIMINATION", "CANDIDATE_GENERATOR"
            return "FINAL_SELECTION_ERROR", "FINAL_SELECTOR"
        if prediction.root_cause is None:
            return "INSUFFICIENT_DISCRIMINATION", "FINAL_SELECTOR"
        if not root_service_correct:
            return "WRONG_ROOT_SERVICE", "FINAL_SELECTOR"
        if not root_type_correct:
            return "RIGHT_SERVICE_WRONG_TYPE", "FINAL_SELECTOR"
        return "RIGHT_SERVICE_RIGHT_TYPE_WRONG_RESOURCE", "RESOURCE_RESOLUTION"

    @staticmethod
    def _score_evidence_overattribution(
        ground_truth: ScenarioGroundTruth,
        prediction: RCAPrediction,
        *,
        rca_exact: bool,
    ) -> tuple[bool, bool]:
        if rca_exact or prediction.root_cause is None:
            return False, False
        selected = next(
            (
                item
                for item in prediction.root_cause_candidates
                if item.root_cause == prediction.root_cause
            ),
            None,
        )
        if selected is None:
            return False, False
        sources = {item.value for item in selected.source_evidence_types}
        direct = bool(sources & {"METRIC", "LOG", "TRACE", "HTTP"})
        return "CHANGE" in sources and not direct, "KNOWLEDGE" in sources and not direct

    @staticmethod
    def _score_evidence(
        ground_truth: ScenarioGroundTruth,
        prediction: RCAPrediction,
    ) -> tuple[float, float, float]:
        expected = set(
            ground_truth.required_evidence_types
            + ground_truth.optional_evidence_types
        )
        actual = set(prediction.evidence_types)
        precision, _, _ = _set_metrics(expected, actual)
        required_recall = len(
            set(ground_truth.required_evidence_types) & actual
        ) / len(ground_truth.required_evidence_types)
        f1 = (
            2 * precision * required_recall / (precision + required_recall)
            if precision + required_recall
            else 0.0
        )
        return precision, required_recall, f1

    @staticmethod
    def _score_required_evidence_id_recall(
        ground_truth: ScenarioGroundTruth,
        prediction: RCAPrediction,
    ) -> float | None:
        """计算 Ground Truth 必需 Evidence ID 的召回率。

        Evidence Type Recall 只回答“每种证据类型是否出现”，无法发现模型
        引用了同类型但错误的事实。Manifest 提供稳定 required_evidence IDs
        时，单独按 ID 计算；旧版没有 ID 的内存 Ground Truth 返回 N/A。
        """
        expected = set(ground_truth.required_evidence_ids)
        if not expected:
            return None
        actual = set(prediction.evidence_ids)
        return len(expected & actual) / len(expected)

    @staticmethod
    def _score_claim_support(prediction: RCAPrediction) -> tuple[int, float]:
        evidence_ids = set(prediction.evidence_ids)
        unsupported = sum(
            not _has_supported_evidence(item.evidence_ids, evidence_ids)
            for item in prediction.claims
        ) + sum(
            not _has_supported_evidence(item.evidence_ids, evidence_ids)
            for item in prediction.causal_chain
        )
        total = len(prediction.claims) + len(prediction.causal_chain)
        return unsupported, unsupported / total if total else 0.0

    @staticmethod
    def _find_forbidden_claims(
        ground_truth: ScenarioGroundTruth,
        prediction: RCAPrediction,
    ) -> tuple[str, ...]:
        statements = " ".join(item.statement for item in prediction.claims).casefold()
        return tuple(
            claim
            for claim in ground_truth.forbidden_claims
            if claim.casefold() in statements
        )

    @staticmethod
    def _score_tool_selection(
        ground_truth: ScenarioGroundTruth,
        prediction: RCAPrediction,
    ) -> float:
        expected = set(ground_truth.expected_tool_types)
        if not expected:
            return 1.0 if not prediction.tool_calls else 0.0
        called = {item.tool_type for item in prediction.tool_calls}
        return len(expected & called) / len(expected)

    @staticmethod
    def _redundant_tool_call_rate(prediction: RCAPrediction) -> float:
        if not prediction.tool_calls:
            return 0.0
        seen_tools: set[str] = set()
        seen_evidence: set[str] = set()
        redundant = 0
        for call in prediction.tool_calls:
            new_evidence = set(call.evidence_ids) - seen_evidence
            if call.tool_type in seen_tools and not new_evidence:
                redundant += 1
            seen_tools.add(call.tool_type)
            seen_evidence.update(call.evidence_ids)
        return redundant / len(prediction.tool_calls)


def _has_supported_evidence(
    evidence_ids: tuple[str, ...],
    available_ids: set[str],
) -> bool:
    return bool(evidence_ids) and set(evidence_ids).issubset(available_ids)


def _edge_key(edge: CausalEdge) -> tuple[str, str]:
    return (
        normalize_causal_node(edge.from_node),
        normalize_causal_node(edge.to_node),
    )


def _set_metrics(
    expected: Iterable[object],
    actual: Iterable[object],
) -> tuple[float, float, float]:
    expected_set = set(expected)
    actual_set = set(actual)
    if not expected_set and not actual_set:
        return 1.0, 1.0, 1.0
    true_positive = len(expected_set & actual_set)
    precision = true_positive / len(actual_set) if actual_set else 0.0
    recall = true_positive / len(expected_set) if expected_set else 1.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return precision, recall, f1


def summarize_scores(scores: tuple[RunScore, ...]) -> Summary:
    if not scores:
        raise ValueError("at least one score is required")

    def average(field_name: str) -> float:
        return mean(float(getattr(item, field_name)) for item in scores)

    def optional_average(field_name: str) -> float | None:
        values = [
            float(value)
            for item in scores
            if (value := getattr(item, field_name)) is not None
        ]
        return mean(values) if values else None

    def status_metrics(status: str) -> tuple[float | None, float | None]:
        expected = sum(item.expected_conclusion_status == status for item in scores)
        predicted = sum(item.predicted_conclusion_status == status for item in scores)
        true_positive = sum(
            item.expected_conclusion_status == status
            and item.predicted_conclusion_status == status
            for item in scores
        )
        precision = true_positive / predicted if predicted else None
        recall = true_positive / expected if expected else None
        return precision, recall

    latencies = sorted(item.latency_ms for item in scores)
    p50_index = max(ceil(len(latencies) * 0.5) - 1, 0)
    p95_index = ceil(len(latencies) * 0.95) - 1
    return Summary(
        total_runs=len(scores),
        passed_runs=sum(item.passed for item in scores),
        rca_top1_accuracy=average("rca_exact_match"),
        strict_rca_accuracy=average("strict_rca_match"),
        root_service_accuracy=average("root_service_correct"),
        root_type_accuracy=average("root_type_correct"),
        root_resource_accuracy=average("root_resource_correct"),
        candidate_evaluated_runs=sum(
            item.candidate_recall_at_3 is not None for item in scores
        ),
        candidate_recall_at_3=optional_average("candidate_recall_at_3"),
        candidate_mrr=optional_average("candidate_reciprocal_rank"),
        candidate_ranking_accuracy=optional_average("candidate_ranking_correct"),
        change_overattribution_rate=average("change_overattribution"),
        history_overattribution_rate=average("history_overattribution"),
        conclusion_status_accuracy=average("conclusion_status_correct"),
        undetermined_precision=status_metrics("UNDETERMINED")[0],
        undetermined_recall=status_metrics("UNDETERMINED")[1],
        no_actionable_root_cause_accuracy=status_metrics(
            "NO_ACTIONABLE_ROOT_CAUSE"
        )[1],
        evidence_precision=average("evidence_precision"),
        evidence_recall=average("evidence_recall"),
        required_evidence_id_recall=optional_average(
            "required_evidence_id_recall"
        ),
        evidence_f1=average("evidence_f1"),
        unsupported_claim_rate=average("unsupported_claim_rate"),
        forbidden_claim_rate=mean(bool(item.forbidden_claims_found) for item in scores),
        false_positive_rate=average("false_positive_root_cause"),
        causal_chain_f1=average("causal_chain_f1"),
        blast_radius_f1=average("blast_radius_f1"),
        tool_selection_accuracy=average("tool_selection_accuracy"),
        avg_tool_calls=average("tool_calls"),
        avg_investigation_steps=average("investigation_steps"),
        redundant_tool_call_rate=average("redundant_tool_call_rate"),
        invalid_tool_proposal_rate=average("invalid_tool_proposal_rate"),
        policy_block_rate=average("policy_block_rate"),
        avg_llm_calls=average("llm_calls"),
        avg_tokens=average("total_tokens"),
        avg_cost=average("estimated_cost"),
        p50_latency_ms=latencies[p50_index],
        p95_latency_ms=latencies[p95_index],
    )
