import json

from devops_agent_platform.domain.enums import (
    TicketDraftStatus,
    TicketPriority,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.ticket_draft import TicketDraft
from devops_agent_platform.infrastructure.database.models.ticket_draft import (
    TicketDraftRecord,
)


class TicketDraftMapper:
    """在 TicketDraft 领域快照与 ORM 记录之间转换。"""

    @staticmethod
    def to_record(draft: TicketDraft) -> TicketDraftRecord:
        """把不可变元组保存为规范紧凑 JSON。"""
        return TicketDraftRecord(
            ticket_draft_id=draft.ticket_draft_id,
            tenant_id=draft.tenant_id,
            incident_id=draft.incident_id,
            workflow_run_id=draft.workflow_run_id,
            report_id=draft.report_id,
            status=draft.status.value,
            priority=draft.priority.value,
            title=draft.title,
            description=draft.description,
            evidence_ids_json=_encode_strings(draft.evidence_ids),
            recommendations_json=_encode_strings(
                draft.recommendations
            ),
            created_by=draft.created_by,
            idempotency_key_hash=draft.idempotency_key_hash,
            request_hash=draft.request_hash,
            trace_id=draft.trace_id,
            created_at=draft.created_at,
            version=draft.version,
            decided_by=draft.decided_by,
            decision_reason=draft.decision_reason,
            decided_at=draft.decided_at,
            decision_idempotency_key_hash=(
                draft.decision_idempotency_key_hash
            ),
            decision_request_hash=draft.decision_request_hash,
            decision_trace_id=draft.decision_trace_id,
        )

    @staticmethod
    def to_domain(record: TicketDraftRecord) -> TicketDraft:
        """恢复领域对象并重新验证数据库内容。"""
        try:
            status = TicketDraftStatus(record.status)
            priority = TicketPriority(record.priority)
        except ValueError as exc:
            raise AppValidationError(
                "ticket draft enum value is invalid"
            ) from exc
        return TicketDraft(
            ticket_draft_id=record.ticket_draft_id,
            tenant_id=record.tenant_id,
            incident_id=record.incident_id,
            workflow_run_id=record.workflow_run_id,
            report_id=record.report_id,
            status=status,
            priority=priority,
            title=record.title,
            description=record.description,
            evidence_ids=_decode_strings(
                record.evidence_ids_json,
                "evidence_ids_json",
            ),
            recommendations=_decode_strings(
                record.recommendations_json,
                "recommendations_json",
            ),
            created_by=record.created_by,
            idempotency_key_hash=record.idempotency_key_hash,
            request_hash=record.request_hash,
            trace_id=record.trace_id,
            created_at=record.created_at,
            version=record.version,
            decided_by=record.decided_by,
            decision_reason=record.decision_reason,
            decided_at=record.decided_at,
            decision_idempotency_key_hash=(
                record.decision_idempotency_key_hash
            ),
            decision_request_hash=record.decision_request_hash,
            decision_trace_id=record.decision_trace_id,
        )


def _encode_strings(values: tuple[str, ...]) -> str:
    """使用稳定 JSON 格式保存字符串元组。"""
    return json.dumps(
        values,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _decode_strings(value: str, field_name: str) -> tuple[str, ...]:
    """解码字符串数组并拒绝结构漂移。"""
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise AppValidationError(f"{field_name} is invalid JSON") from exc
    if (
        not isinstance(decoded, list)
        or not all(isinstance(item, str) for item in decoded)
    ):
        raise AppValidationError(
            f"{field_name} must contain a string array"
        )
    return tuple(decoded)
