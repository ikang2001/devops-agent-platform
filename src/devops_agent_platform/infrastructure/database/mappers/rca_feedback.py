import json
from typing import Any

from devops_agent_platform.domain.enums import (
    EvidenceType,
    RCAFeedbackVerdict,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.rca_feedback import RCAFeedback
from devops_agent_platform.infrastructure.database.models.rca_feedback import (
    RCAFeedbackRecord,
)


class RCAFeedbackMapper:
    """在领域反馈和数据库记录之间做严格映射。"""

    @staticmethod
    def to_record(feedback: RCAFeedback) -> RCAFeedbackRecord:
        return RCAFeedbackRecord(
            feedback_id=feedback.feedback_id,
            tenant_id=feedback.tenant_id,
            workflow_run_id=feedback.workflow_run_id,
            report_id=feedback.report_id,
            verdict=feedback.verdict.value,
            corrected_root_cause=feedback.corrected_root_cause,
            missing_evidence_types_json=_dump_json(
                [item.value for item in feedback.missing_evidence_types]
            ),
            unsafe_recommendation_indexes_json=_dump_json(
                list(feedback.unsafe_recommendation_indexes)
            ),
            follow_up_label=feedback.follow_up_label,
            notes=feedback.notes,
            created_by=feedback.created_by,
            idempotency_key_hash=feedback.idempotency_key_hash,
            request_hash=feedback.request_hash,
            trace_id=feedback.trace_id,
            created_at=feedback.created_at,
        )

    @staticmethod
    def to_domain(record: RCAFeedbackRecord) -> RCAFeedback:
        missing = _load_list(
            record.missing_evidence_types_json,
            "missing_evidence_types",
        )
        indexes = _load_list(
            record.unsafe_recommendation_indexes_json,
            "unsafe_recommendation_indexes",
        )
        try:
            return RCAFeedback(
                feedback_id=record.feedback_id,
                tenant_id=record.tenant_id,
                workflow_run_id=record.workflow_run_id,
                report_id=record.report_id,
                verdict=RCAFeedbackVerdict(record.verdict),
                corrected_root_cause=record.corrected_root_cause,
                missing_evidence_types=tuple(EvidenceType(item) for item in missing),
                unsafe_recommendation_indexes=tuple(indexes),
                follow_up_label=record.follow_up_label,
                notes=record.notes,
                created_by=record.created_by,
                idempotency_key_hash=record.idempotency_key_hash,
                request_hash=record.request_hash,
                trace_id=record.trace_id,
                created_at=record.created_at,
            )
        except (TypeError, ValueError) as exc:
            raise AppValidationError("stored RCA feedback is invalid") from exc


def _dump_json(value: list[Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _load_list(value: str, field_name: str) -> list[Any]:
    try:
        document = json.loads(value)
    except (json.JSONDecodeError, TypeError) as exc:
        raise AppValidationError(f"stored {field_name} is invalid") from exc
    if not isinstance(document, list):
        raise AppValidationError(f"stored {field_name} is invalid")
    return document
