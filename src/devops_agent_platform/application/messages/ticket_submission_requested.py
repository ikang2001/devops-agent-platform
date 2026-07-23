from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from devops_agent_platform.application.exceptions import MessageContractError


@dataclass(frozen=True)
class TicketSubmissionRequestedEventV1:
    """触发外部工单提交 Worker 的第一版应用消息契约。"""

    event_id: str
    tenant_id: str
    ticket_submission_id: str
    ticket_draft_id: str
    workflow_run_id: str
    target_system: str
    requested_by: str
    draft_version: int
    occurred_at: datetime
    requested_at: datetime
    trace_id: str

    @classmethod
    def from_envelope(
        cls,
        envelope: Mapping[str, object],
    ) -> "TicketSubmissionRequestedEventV1":
        """从 Kafka JSON Envelope 构造经过交叉校验的消息对象。"""
        if not isinstance(envelope, Mapping):
            raise MessageContractError("Message envelope must be an object")

        event_type = cls._text(envelope, "event_type", 128)
        if event_type != "ticket_submission.requested":
            raise MessageContractError("Unsupported event_type")

        schema_version = envelope.get("schema_version")
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != 1
        ):
            raise MessageContractError(
                "Unsupported ticket_submission.requested schema_version"
            )

        aggregate_type = cls._text(envelope, "aggregate_type", 64)
        if aggregate_type != "TicketSubmission":
            raise MessageContractError(
                "ticket_submission.requested aggregate_type must be TicketSubmission"
            )

        payload = envelope.get("payload")
        if not isinstance(payload, Mapping):
            raise MessageContractError("Message payload must be an object")

        tenant_id = cls._text(envelope, "tenant_id", 128)
        aggregate_id = cls._text(envelope, "aggregate_id", 64)
        ticket_submission_id = cls._text(payload, "ticket_submission_id", 64)
        if ticket_submission_id != aggregate_id:
            raise MessageContractError(
                "aggregate_id and payload ticket_submission_id do not match"
            )

        return cls(
            event_id=cls._text(envelope, "event_id", 64),
            tenant_id=tenant_id,
            ticket_submission_id=ticket_submission_id,
            ticket_draft_id=cls._text(payload, "ticket_draft_id", 64),
            workflow_run_id=cls._text(payload, "workflow_run_id", 64),
            target_system=cls._text(payload, "target_system", 64),
            requested_by=cls._text(payload, "requested_by", 128),
            draft_version=cls._positive_int(payload, "draft_version"),
            occurred_at=cls._timestamp(envelope, "occurred_at"),
            requested_at=cls._timestamp(payload, "requested_at"),
            trace_id=cls._text(envelope, "trace_id", 128),
        )

    @staticmethod
    def _text(
        source: Mapping[str, object],
        field_name: str,
        maximum: int,
    ) -> str:
        """读取必填文本字段，并拒绝空值、超长值和污染字符。"""
        value = source.get(field_name)
        if not isinstance(value, str) or not 1 <= len(value) <= maximum:
            raise MessageContractError(
                f"{field_name} length must be between 1 and {maximum}"
            )
        if value != value.strip():
            raise MessageContractError(
                f"{field_name} must not contain surrounding whitespace"
            )
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise MessageContractError(
                f"{field_name} must not contain control characters"
            )
        return value

    @staticmethod
    def _positive_int(
        source: Mapping[str, object],
        field_name: str,
    ) -> int:
        """读取正整数版本字段，拒绝 bool 混入。"""
        value = source.get(field_name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise MessageContractError(f"{field_name} must be a positive integer")
        return value

    @staticmethod
    def _timestamp(
        source: Mapping[str, object],
        field_name: str,
    ) -> datetime:
        """解析 ISO 8601 时间，并要求消息明确携带时区。"""
        raw_value = source.get(field_name)
        if not isinstance(raw_value, str):
            raise MessageContractError(f"{field_name} must be an ISO timestamp")
        normalized = f"{raw_value[:-1]}+00:00" if raw_value.endswith("Z") else raw_value
        try:
            value = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise MessageContractError(
                f"{field_name} must be an ISO timestamp"
            ) from exc
        if value.tzinfo is None or value.utcoffset() is None:
            raise MessageContractError(
                f"{field_name} must include timezone information"
            )
        return value
