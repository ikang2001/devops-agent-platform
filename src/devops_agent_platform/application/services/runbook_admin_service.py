import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256

from devops_agent_platform.application.commands.runbooks import (
    PublishRunbookCommand,
    SaveRunbookDraftCommand,
)
from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.domain.enums import RunbookChangeAction
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.identifiers import IdentifierGeneratorPort
from devops_agent_platform.ports.runbook_admin import (
    RunbookAdminStorePort,
    RunbookMutation,
    RunbookMutationResult,
)
from devops_agent_platform.tools.sanitization import redact_sensitive_text

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class RunbookChangeResult:
    """Runbook 管理应用服务返回的稳定结果。"""

    operation_id: str
    runbook_id: str
    tenant_id: str
    runbook_key: str
    version: str
    status: str
    revision: int
    trace_id: str
    is_duplicate: bool

    def to_dict(self) -> dict[str, str | int | bool]:
        """转换为不依赖 HTTP 框架的响应数据。"""
        return asdict(self)


class RunbookAdminService:
    """编排 Runbook 草稿、发布、幂等指纹和事务审计。"""

    def __init__(
        self,
        store: RunbookAdminStorePort,
        identifier_generator: IdentifierGeneratorPort,
        clock: Clock | None = None,
    ) -> None:
        self._store = store
        self._identifier_generator = identifier_generator
        self._clock = clock or (lambda: datetime.now(UTC))

    async def save_draft(
        self,
        command: SaveRunbookDraftCommand,
    ) -> RunbookChangeResult:
        """创建新草稿或按 revision 更新现有草稿。"""
        safe_title = _safe_runbook_text(command.title, maximum=256)
        safe_summary = _safe_runbook_text(command.summary, maximum=4096)
        safe_steps = tuple(
            _safe_runbook_text(step, maximum=2000)
            for step in command.steps
        )
        content = {
            "service_name": command.service_name,
            "title": safe_title,
            "summary": safe_summary,
            "priority": command.priority,
            "steps": safe_steps,
            "tags": command.tags,
        }
        return await self._execute(
            action=RunbookChangeAction.SAVE_DRAFT,
            tenant_id=command.tenant_id,
            runbook_key=command.runbook_key,
            version=command.version,
            expected_revision=command.expected_revision,
            idempotency_key=command.idempotency_key,
            requested_by=command.requested_by,
            trace_id=command.trace_id,
            content=content,
        )

    async def publish(
        self,
        command: PublishRunbookCommand,
    ) -> RunbookChangeResult:
        """发布草稿，并由 Store 原子归档旧发布版本。"""
        return await self._execute(
            action=RunbookChangeAction.PUBLISH,
            tenant_id=command.tenant_id,
            runbook_key=command.runbook_key,
            version=command.version,
            expected_revision=command.expected_revision,
            idempotency_key=command.idempotency_key,
            requested_by=command.requested_by,
            trace_id=command.trace_id,
            content=None,
        )

    async def _execute(
        self,
        *,
        action: RunbookChangeAction,
        tenant_id: str,
        runbook_key: str,
        version: str,
        expected_revision: int,
        idempotency_key: str,
        requested_by: str,
        trace_id: str,
        content: dict | None,
    ) -> RunbookChangeResult:
        """构造不可逆请求指纹、变更参数和同事务审计事件。"""
        now = self._now()
        operation_id = (
            self._identifier_generator.new_runbook_operation_id()
        )
        content_hash = (
            self._canonical_hash(content) if content is not None else None
        )
        request_hash = self._canonical_hash(
            {
                "action": action.value,
                "tenant_id": tenant_id,
                "runbook_key": runbook_key,
                "version": version,
                "expected_revision": expected_revision,
                "requested_by": requested_by,
                "content": content,
            }
        )
        mutation = RunbookMutation(
            operation_id=operation_id,
            new_runbook_id=(
                self._identifier_generator.new_runbook_id()
                if action is RunbookChangeAction.SAVE_DRAFT
                else None
            ),
            tenant_id=tenant_id,
            runbook_key=runbook_key,
            version=version,
            service_name=(
                content["service_name"] if content is not None else None
            ),
            title=content["title"] if content is not None else None,
            summary=content["summary"] if content is not None else None,
            priority=content["priority"] if content is not None else None,
            steps=content["steps"] if content is not None else (),
            tags=content["tags"] if content is not None else (),
            expected_revision=expected_revision,
            idempotency_key_hash=self._hash_text(idempotency_key),
            request_hash=request_hash,
            content_hash=content_hash,
            requested_by=requested_by,
            trace_id=trace_id,
            action=action,
            occurred_at=now,
        )
        event = self._build_audit_event(mutation)
        stored = await self._store.apply(mutation, event)
        return self._result(stored, trace_id)

    def _build_audit_event(
        self,
        mutation: RunbookMutation,
    ) -> OutboxEvent:
        """构造不含 Runbook 正文和原始幂等键的审计事件。"""
        return OutboxEvent(
            event_id=self._identifier_generator.new_event_id(),
            tenant_id=mutation.tenant_id,
            aggregate_type="RunbookOperation",
            aggregate_id=mutation.operation_id,
            event_type=(
                "runbook.draft.saved"
                if mutation.action is RunbookChangeAction.SAVE_DRAFT
                else "runbook.published"
            ),
            schema_version=1,
            payload={
                "operation_id": mutation.operation_id,
                "runbook_key": mutation.runbook_key,
                "version": mutation.version,
                "action": mutation.action.value,
                "content_sha256": mutation.content_hash,
                "requested_by": mutation.requested_by,
                "result_revision": mutation.expected_revision + 1,
                "occurred_at": mutation.occurred_at.isoformat(),
            },
            occurred_at=mutation.occurred_at,
            trace_id=mutation.trace_id,
        )

    @staticmethod
    def _canonical_hash(value: object) -> str:
        """生成字段顺序稳定、禁止非标准数字的 SHA-256 指纹。"""
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return sha256(encoded).hexdigest()

    @staticmethod
    def _hash_text(value: str) -> str:
        """只持久化幂等键摘要，降低数据库泄漏影响。"""
        return sha256(value.encode("utf-8")).hexdigest()

    def _now(self) -> datetime:
        """读取并规范化带时区应用时钟。"""
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AppValidationError(
                "clock must return a timezone-aware datetime"
            )
        return value.astimezone(UTC)

    @staticmethod
    def _result(
        stored: RunbookMutationResult,
        trace_id: str,
    ) -> RunbookChangeResult:
        """转换 Store 结果，并返回当前请求的 trace_id。"""
        return RunbookChangeResult(
            operation_id=stored.operation_id,
            runbook_id=stored.runbook_id,
            tenant_id=stored.tenant_id,
            runbook_key=stored.runbook_key,
            version=stored.version,
            status=stored.status.value,
            revision=stored.revision,
            trace_id=trace_id,
            is_duplicate=stored.is_duplicate,
        )


def _safe_runbook_text(value: str, *, maximum: int) -> str:
    """Runbook 正文进入业务表前遮蔽误粘贴的敏感片段。"""
    safe_value = redact_sensitive_text(value)[0][:maximum].strip()
    if not safe_value:
        raise AppValidationError("runbook content is empty after sanitization")
    return safe_value
