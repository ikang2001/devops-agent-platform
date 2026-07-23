import json

from devops_agent_platform.domain.enums import RunbookStatus
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.runbook import Runbook
from devops_agent_platform.infrastructure.database.models.runbook import (
    RunbookRecord,
)


class RunbookMapper:
    """在 Runbook 领域快照与 ORM 记录之间显式转换。"""

    @staticmethod
    def to_record(runbook: Runbook) -> RunbookRecord:
        """使用规范 JSON 保存步骤和标签。"""
        return RunbookRecord(
            runbook_id=runbook.runbook_id,
            runbook_key=runbook.runbook_key,
            tenant_id=runbook.tenant_id,
            service_name=runbook.service_name,
            title=runbook.title,
            summary=runbook.summary,
            version=runbook.version,
            status=runbook.status.value,
            revision=runbook.revision,
            priority=runbook.priority,
            steps_json=_encode_strings(runbook.steps),
            tags_json=_encode_strings(runbook.tags),
            published_at=runbook.published_at,
            updated_at=runbook.updated_at,
        )

    @staticmethod
    def to_domain(record: RunbookRecord) -> Runbook:
        """从数据库恢复对象，并重新执行全部领域校验。"""
        try:
            status = RunbookStatus(record.status)
        except ValueError as exc:
            raise AppValidationError(
                "runbook status is invalid"
            ) from exc
        return Runbook(
            runbook_id=record.runbook_id,
            runbook_key=record.runbook_key,
            tenant_id=record.tenant_id,
            service_name=record.service_name,
            title=record.title,
            summary=record.summary,
            version=record.version,
            status=status,
            revision=record.revision,
            priority=record.priority,
            steps=_decode_strings(record.steps_json, "steps_json"),
            tags=_decode_strings(record.tags_json, "tags_json"),
            published_at=record.published_at,
            updated_at=record.updated_at,
        )


def _encode_strings(values: tuple[str, ...]) -> str:
    """使用稳定紧凑格式序列化有限字符串元组。"""
    return json.dumps(
        values,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _decode_strings(value: str, field_name: str) -> tuple[str, ...]:
    """解码字符串数组，拒绝数据库中的结构漂移。"""
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
