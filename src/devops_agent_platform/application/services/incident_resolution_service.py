import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime

from devops_agent_platform.application.commands.incidents import (
    CloseIncidentCommand,
    ResolveIncidentCommand,
)
from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
    ResourceNotFound,
)
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.ports.identifiers import IdentifierGeneratorPort
from devops_agent_platform.ports.unit_of_work import UnitOfWorkPort
from devops_agent_platform.tools.sanitization import redact_sensitive_text

UnitOfWorkFactory = Callable[[], UnitOfWorkPort]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class IncidentResolutionResult:
    """事故解决事实提交后的稳定管理端结果。"""

    incident_id: str
    tenant_id: str
    status: str
    version: int
    resolved_by: str
    resolution_reason: str
    resolved_at: datetime
    trace_id: str
    is_duplicate: bool

    def to_dict(self) -> dict:
        """转换为统一响应 envelope 中的 data。"""
        result = asdict(self)
        result["resolved_at"] = self.resolved_at.isoformat()
        return result


@dataclass(frozen=True)
class IncidentClosureResult:
    """事故关闭事实提交后的稳定管理端结果。"""

    incident_id: str
    tenant_id: str
    status: str
    version: int
    closed_by: str
    closure_reason: str
    closed_at: datetime
    trace_id: str
    is_duplicate: bool

    def to_dict(self) -> dict:
        """转换为统一响应 envelope 中的 data。"""
        result = asdict(self)
        result["closed_at"] = self.closed_at.isoformat()
        return result


class IncidentResolutionService:
    """编排事故人工解决、关闭、幂等恢复和事务审计。"""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        identifier_generator: IdentifierGeneratorPort,
        clock: Clock | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._identifier_generator = identifier_generator
        self._clock = clock or (lambda: datetime.now(UTC))

    async def resolve(
        self,
        command: ResolveIncidentCommand,
    ) -> IncidentResolutionResult:
        """解决事故；并发提交冲突后只读确认同一幂等请求。"""
        command = self._sanitize_command(command)
        idempotency_hash = self._hash_text(command.idempotency_key)
        request_hash = self._request_hash(command)
        try:
            return await self._resolve_once(
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

    async def close(
        self,
        command: CloseIncidentCommand,
    ) -> IncidentClosureResult:
        """关闭已解决事故；并发提交冲突后只读确认同一幂等请求。"""
        command = self._sanitize_close_command(command)
        idempotency_hash = self._hash_text(command.idempotency_key)
        request_hash = self._closure_request_hash(command)
        try:
            return await self._close_once(
                command,
                idempotency_hash,
                request_hash,
            )
        except ConflictError:
            duplicate = await self._find_duplicate_closure_result(
                command,
                idempotency_hash,
                request_hash,
            )
            if duplicate is not None:
                return duplicate
            raise

    async def _resolve_once(
        self,
        command: ResolveIncidentCommand,
        idempotency_hash: str,
        request_hash: str,
    ) -> IncidentResolutionResult:
        """在同一 Unit of Work 内更新聚合并追加审计事件。"""
        async with self._unit_of_work_factory() as unit_of_work:
            incident = await unit_of_work.incidents.get_by_id(
                command.incident_id,
                command.tenant_id,
            )
            if incident is None:
                raise ResourceNotFound("Incident not found")
            duplicate = self._duplicate_result(
                incident,
                command,
                idempotency_hash,
                request_hash,
            )
            if duplicate is not None:
                return duplicate
            if incident.version != command.expected_version:
                raise ConflictError("Incident version conflict")
            await self._reject_active_workflow(
                unit_of_work,
                command.tenant_id,
                command.incident_id,
            )

            now = self._now()
            incident.mark_resolved(
                resolved_by=command.requested_by,
                reason=command.reason,
                resolved_at=now,
                idempotency_key_hash=idempotency_hash,
                request_hash=request_hash,
                trace_id=command.trace_id,
            )
            await unit_of_work.incidents.save(incident)
            await unit_of_work.outbox.add(
                self._build_audit_event(incident)
            )
            await unit_of_work.commit()
            return self._result(
                incident,
                trace_id=command.trace_id,
                is_duplicate=False,
            )

    async def _close_once(
        self,
        command: CloseIncidentCommand,
        idempotency_hash: str,
        request_hash: str,
    ) -> IncidentClosureResult:
        """在同一 Unit of Work 内关闭聚合并追加审计事件。"""
        async with self._unit_of_work_factory() as unit_of_work:
            incident = await unit_of_work.incidents.get_by_id(
                command.incident_id,
                command.tenant_id,
            )
            if incident is None:
                raise ResourceNotFound("Incident not found")
            duplicate = self._duplicate_closure_result(
                incident,
                command,
                idempotency_hash,
                request_hash,
            )
            if duplicate is not None:
                return duplicate
            if incident.version != command.expected_version:
                raise ConflictError("Incident version conflict")
            await self._reject_active_workflow(
                unit_of_work,
                command.tenant_id,
                command.incident_id,
            )

            now = self._now()
            incident.mark_closed(
                closed_by=command.requested_by,
                reason=command.reason,
                closed_at=now,
                idempotency_key_hash=idempotency_hash,
                request_hash=request_hash,
                trace_id=command.trace_id,
            )
            await unit_of_work.incidents.save(incident)
            await unit_of_work.outbox.add(
                self._build_closure_audit_event(incident)
            )
            await unit_of_work.commit()
            return self._closure_result(
                incident,
                trace_id=command.trace_id,
                is_duplicate=False,
            )

    async def _find_duplicate_result(
        self,
        command: ResolveIncidentCommand,
        idempotency_hash: str,
        request_hash: str,
    ) -> IncidentResolutionResult | None:
        """写冲突回滚后使用新事务恢复同一幂等请求。"""
        async with self._unit_of_work_factory() as unit_of_work:
            incident = await unit_of_work.incidents.get_by_id(
                command.incident_id,
                command.tenant_id,
            )
            if incident is None:
                return None
            return self._duplicate_result(
                incident,
                command,
                idempotency_hash,
                request_hash,
            )

    async def _find_duplicate_closure_result(
        self,
        command: CloseIncidentCommand,
        idempotency_hash: str,
        request_hash: str,
    ) -> IncidentClosureResult | None:
        """写冲突回滚后使用新事务恢复同一关闭请求。"""
        async with self._unit_of_work_factory() as unit_of_work:
            incident = await unit_of_work.incidents.get_by_id(
                command.incident_id,
                command.tenant_id,
            )
            if incident is None:
                return None
            return self._duplicate_closure_result(
                incident,
                command,
                idempotency_hash,
                request_hash,
            )

    @staticmethod
    async def _reject_active_workflow(
        unit_of_work: UnitOfWorkPort,
        tenant_id: str,
        incident_id: str,
    ) -> None:
        """人工终态变更前拒绝仍在排队或运行的 RCA 工作流。"""
        active = await unit_of_work.workflow_runs.get_active_by_incident(
            tenant_id,
            incident_id,
        )
        if active is not None:
            raise ConflictError("Incident has an active RCA workflow")

    def _build_audit_event(self, incident: Incident) -> OutboxEvent:
        """构造只含解决原因摘要、不复制正文的审计事件。"""
        assert incident.resolved_at is not None
        assert incident.resolved_by is not None
        assert incident.resolution_reason is not None
        assert incident.resolution_trace_id is not None
        return OutboxEvent(
            event_id=self._identifier_generator.new_event_id(),
            tenant_id=incident.tenant_id,
            aggregate_type="Incident",
            aggregate_id=incident.incident_id,
            event_type="incident.resolved",
            schema_version=1,
            payload={
                "incident_id": incident.incident_id,
                "status": incident.status.value,
                "version": incident.version,
                "resolved_by": incident.resolved_by,
                "resolution_reason_sha256": self._hash_text(
                    incident.resolution_reason
                ),
                "resolved_at": incident.resolved_at.isoformat(),
            },
            occurred_at=incident.resolved_at,
            trace_id=incident.resolution_trace_id,
        )

    def _build_closure_audit_event(self, incident: Incident) -> OutboxEvent:
        """构造只含关闭原因摘要、不复制正文的审计事件。"""
        assert incident.closed_at is not None
        assert incident.closed_by is not None
        assert incident.closure_reason is not None
        assert incident.closure_trace_id is not None
        return OutboxEvent(
            event_id=self._identifier_generator.new_event_id(),
            tenant_id=incident.tenant_id,
            aggregate_type="Incident",
            aggregate_id=incident.incident_id,
            event_type="incident.closed",
            schema_version=1,
            payload={
                "incident_id": incident.incident_id,
                "status": incident.status.value,
                "version": incident.version,
                "closed_by": incident.closed_by,
                "closure_reason_sha256": self._hash_text(
                    incident.closure_reason
                ),
                "closed_at": incident.closed_at.isoformat(),
            },
            occurred_at=incident.closed_at,
            trace_id=incident.closure_trace_id,
        )

    @staticmethod
    def _duplicate_result(
        incident: Incident,
        command: ResolveIncidentCommand,
        idempotency_hash: str,
        request_hash: str,
    ) -> IncidentResolutionResult | None:
        """区分同一请求重放、幂等键误复用和其它终态。"""
        if incident.resolution_idempotency_key_hash != idempotency_hash:
            return None
        if incident.resolution_request_hash != request_hash:
            raise ConflictError(
                "Idempotency key was used for another resolution request"
            )
        return IncidentResolutionService._result(
            incident,
            trace_id=command.trace_id,
            is_duplicate=True,
        )

    @staticmethod
    def _duplicate_closure_result(
        incident: Incident,
        command: CloseIncidentCommand,
        idempotency_hash: str,
        request_hash: str,
    ) -> IncidentClosureResult | None:
        """区分同一关闭请求重放、幂等键误复用和其它终态。"""
        if incident.closure_idempotency_key_hash != idempotency_hash:
            return None
        if incident.closure_request_hash != request_hash:
            raise ConflictError(
                "Idempotency key was used for another closure request"
            )
        return IncidentResolutionService._closure_result(
            incident,
            trace_id=command.trace_id,
            is_duplicate=True,
        )

    @staticmethod
    def _result(
        incident: Incident,
        *,
        trace_id: str,
        is_duplicate: bool,
    ) -> IncidentResolutionResult:
        """从完整解决事实构造不含内部哈希的返回值。"""
        if (
            incident.resolved_by is None
            or incident.resolution_reason is None
            or incident.resolved_at is None
        ):
            raise ConflictError(
                "Incident does not contain a replayable resolution"
            )
        return IncidentResolutionResult(
            incident_id=incident.incident_id,
            tenant_id=incident.tenant_id,
            status=incident.status.value,
            version=incident.version,
            resolved_by=incident.resolved_by,
            resolution_reason=incident.resolution_reason,
            resolved_at=incident.resolved_at,
            trace_id=trace_id,
            is_duplicate=is_duplicate,
        )

    @staticmethod
    def _closure_result(
        incident: Incident,
        *,
        trace_id: str,
        is_duplicate: bool,
    ) -> IncidentClosureResult:
        """从完整关闭事实构造不含内部哈希的返回值。"""
        if (
            incident.closed_by is None
            or incident.closure_reason is None
            or incident.closed_at is None
        ):
            raise ConflictError(
                "Incident does not contain a replayable closure"
            )
        return IncidentClosureResult(
            incident_id=incident.incident_id,
            tenant_id=incident.tenant_id,
            status=incident.status.value,
            version=incident.version,
            closed_by=incident.closed_by,
            closure_reason=incident.closure_reason,
            closed_at=incident.closed_at,
            trace_id=trace_id,
            is_duplicate=is_duplicate,
        )

    @staticmethod
    def _sanitize_command(
        command: ResolveIncidentCommand,
    ) -> ResolveIncidentCommand:
        """解决原因进入业务表和请求指纹前压单行并脱敏。"""
        reason = " ".join(command.reason.splitlines()).strip()
        safe_reason = redact_sensitive_text(reason)[0][:2048].strip()
        if safe_reason == command.reason:
            return command
        return replace(command, reason=safe_reason)

    @staticmethod
    def _sanitize_close_command(
        command: CloseIncidentCommand,
    ) -> CloseIncidentCommand:
        """关闭原因进入业务表和请求指纹前压单行并脱敏。"""
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
    def _request_hash(cls, command: ResolveIncidentCommand) -> str:
        """生成不含 trace_id 的稳定解决请求指纹。"""
        encoded = json.dumps(
            {
                "tenant_id": command.tenant_id,
                "incident_id": command.incident_id,
                "expected_version": command.expected_version,
                "reason": command.reason,
                "requested_by": command.requested_by,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _closure_request_hash(cls, command: CloseIncidentCommand) -> str:
        """生成不含 trace_id 的稳定关闭请求指纹。"""
        encoded = json.dumps(
            {
                "tenant_id": command.tenant_id,
                "incident_id": command.incident_id,
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
