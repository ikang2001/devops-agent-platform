import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime

from devops_agent_platform.application.commands.rca import (
    CancelRCAWorkflowCommand,
)
from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
    ResourceNotFound,
)
from devops_agent_platform.domain.models.workflow_run import WorkflowRun
from devops_agent_platform.ports.identifiers import IdentifierGeneratorPort
from devops_agent_platform.ports.unit_of_work import UnitOfWorkPort
from devops_agent_platform.tools.sanitization import redact_sensitive_text

UnitOfWorkFactory = Callable[[], UnitOfWorkPort]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class RCACancellationResult:
    """RCA 工作流取消后的稳定管理端结果。"""

    workflow_run_id: str
    tenant_id: str
    incident_id: str
    status: str
    version: int
    canceled_by: str
    cancellation_reason: str
    canceled_at: datetime
    trace_id: str
    is_duplicate: bool

    def to_dict(self) -> dict:
        """转换为统一响应 envelope 中的 data。"""
        result = asdict(self)
        result["canceled_at"] = self.canceled_at.isoformat()
        return result


class RCACancellationService:
    """编排 RCA 工作流管理端取消、幂等恢复和审计事件。"""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        identifier_generator: IdentifierGeneratorPort,
        clock: Clock | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._identifier_generator = identifier_generator
        self._clock = clock or (lambda: datetime.now(UTC))

    async def cancel(
        self,
        command: CancelRCAWorkflowCommand,
    ) -> RCACancellationResult:
        """取消 RCA 工作流；并发冲突后只读确认同一幂等请求。"""
        command = self._sanitize_command(command)
        idempotency_hash = self._hash_text(command.idempotency_key)
        request_hash = self._request_hash(command)
        try:
            return await self._cancel_once(
                command,
                idempotency_hash,
                request_hash,
            )
        except ConflictError:
            duplicate = await self._find_duplicate_result(
                command,
                idempotency_hash,
                request_hash,
            )
            if duplicate is not None:
                return duplicate
            raise

    async def _cancel_once(
        self,
        command: CancelRCAWorkflowCommand,
        idempotency_hash: str,
        request_hash: str,
    ) -> RCACancellationResult:
        """在同一 Unit of Work 内取消工作流并追加审计事件。"""
        async with self._unit_of_work_factory() as unit_of_work:
            workflow_run = await unit_of_work.workflow_runs.get_by_id(
                command.tenant_id,
                command.workflow_run_id,
            )
            if workflow_run is None:
                raise ResourceNotFound("Workflow run not found")
            duplicate = self._duplicate_result(
                workflow_run,
                command,
                idempotency_hash,
                request_hash,
            )
            if duplicate is not None:
                return duplicate
            if workflow_run.version != command.expected_version:
                raise ConflictError("Workflow run version conflict")

            now = self._now()
            workflow_run.cancel(
                now,
                canceled_by=command.requested_by,
                reason=command.reason,
                idempotency_key_hash=idempotency_hash,
                request_hash=request_hash,
                trace_id=command.trace_id,
            )
            await unit_of_work.workflow_runs.save(workflow_run)
            await unit_of_work.outbox.add(
                self._build_audit_event(workflow_run)
            )
            await unit_of_work.commit()
            return self._result(
                workflow_run,
                trace_id=command.trace_id,
                is_duplicate=False,
            )

    async def _find_duplicate_result(
        self,
        command: CancelRCAWorkflowCommand,
        idempotency_hash: str,
        request_hash: str,
    ) -> RCACancellationResult | None:
        """写冲突回滚后使用新事务恢复同一取消请求。"""
        async with self._unit_of_work_factory() as unit_of_work:
            workflow_run = await unit_of_work.workflow_runs.get_by_id(
                command.tenant_id,
                command.workflow_run_id,
            )
            if workflow_run is None:
                return None
            return self._duplicate_result(
                workflow_run,
                command,
                idempotency_hash,
                request_hash,
            )

    def _build_audit_event(self, workflow_run: WorkflowRun) -> OutboxEvent:
        """构造不复制取消原因正文的审计事件。"""
        assert workflow_run.canceled_at is not None
        assert workflow_run.canceled_by is not None
        assert workflow_run.cancellation_reason is not None
        assert workflow_run.cancellation_trace_id is not None
        return OutboxEvent(
            event_id=self._identifier_generator.new_event_id(),
            tenant_id=workflow_run.tenant_id,
            aggregate_type="WorkflowRun",
            aggregate_id=workflow_run.workflow_run_id,
            event_type="rca.canceled",
            schema_version=1,
            payload={
                "workflow_run_id": workflow_run.workflow_run_id,
                "incident_id": workflow_run.incident_id,
                "status": workflow_run.status.value,
                "version": workflow_run.version,
                "canceled_by": workflow_run.canceled_by,
                "cancellation_reason_sha256": self._hash_text(
                    workflow_run.cancellation_reason
                ),
                "canceled_at": workflow_run.canceled_at.isoformat(),
            },
            occurred_at=workflow_run.canceled_at,
            trace_id=workflow_run.cancellation_trace_id,
        )

    @staticmethod
    def _duplicate_result(
        workflow_run: WorkflowRun,
        command: CancelRCAWorkflowCommand,
        idempotency_hash: str,
        request_hash: str,
    ) -> RCACancellationResult | None:
        """区分同一取消请求重放、幂等键误复用和其它终态。"""
        if workflow_run.cancellation_idempotency_key_hash != idempotency_hash:
            return None
        if workflow_run.cancellation_request_hash != request_hash:
            raise ConflictError(
                "Idempotency key was used for another RCA cancellation"
            )
        return RCACancellationService._result(
            workflow_run,
            trace_id=command.trace_id,
            is_duplicate=True,
        )

    @staticmethod
    def _result(
        workflow_run: WorkflowRun,
        *,
        trace_id: str,
        is_duplicate: bool,
    ) -> RCACancellationResult:
        """从完整取消事实构造不含内部哈希的返回值。"""
        if (
            workflow_run.canceled_by is None
            or workflow_run.cancellation_reason is None
            or workflow_run.canceled_at is None
        ):
            raise ConflictError(
                "Workflow run does not contain a replayable cancellation"
            )
        return RCACancellationResult(
            workflow_run_id=workflow_run.workflow_run_id,
            tenant_id=workflow_run.tenant_id,
            incident_id=workflow_run.incident_id,
            status=workflow_run.status.value,
            version=workflow_run.version,
            canceled_by=workflow_run.canceled_by,
            cancellation_reason=workflow_run.cancellation_reason,
            canceled_at=workflow_run.canceled_at,
            trace_id=trace_id,
            is_duplicate=is_duplicate,
        )

    @staticmethod
    def _sanitize_command(
        command: CancelRCAWorkflowCommand,
    ) -> CancelRCAWorkflowCommand:
        """取消原因进入业务表和请求指纹前压单行并脱敏。"""
        reason = " ".join(command.reason.splitlines()).strip()
        safe_reason = redact_sensitive_text(reason)[0][:2048].strip()
        if safe_reason == command.reason:
            return command
        return replace(command, reason=safe_reason)

    @staticmethod
    def _hash_text(value: str) -> str:
        """生成不可逆 SHA-256 摘要。"""
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @classmethod
    def _request_hash(cls, command: CancelRCAWorkflowCommand) -> str:
        """生成不含 trace_id 的稳定取消请求指纹。"""
        encoded = json.dumps(
            {
                "tenant_id": command.tenant_id,
                "workflow_run_id": command.workflow_run_id,
                "expected_version": command.expected_version,
                "reason": command.reason,
                "requested_by": command.requested_by,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

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
