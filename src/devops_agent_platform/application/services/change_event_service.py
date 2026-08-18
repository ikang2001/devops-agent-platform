import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from devops_agent_platform.application.commands.change_events import (
    ReceiveChangeEventCommand,
)
from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.domain.exceptions import AppException, ConflictError
from devops_agent_platform.domain.models.change_event import (
    ChangeEvent,
    build_change_metadata,
)
from devops_agent_platform.ports.identifiers import IdentifierGeneratorPort
from devops_agent_platform.ports.unit_of_work import UnitOfWorkPort
from devops_agent_platform.tools.sanitization import (
    redact_sensitive_text,
    sanitize_evidence_payload,
)

UnitOfWorkFactory = Callable[[], UnitOfWorkPort]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class ReceiveChangeEventResult:
    """变更事件接入返回给接口层的稳定结果。"""

    change_event_id: str
    status: str
    trace_id: str
    is_duplicate: bool

    def to_dict(self) -> dict[str, str | bool]:
        """转换为不依赖 Web 框架的响应数据。"""
        return asdict(self)


@dataclass(frozen=True)
class _PreparedChangeEvent:
    """完成脱敏和规范化、尚未分配业务 ID 的变更事实。"""

    summary: str
    metadata_json: str
    request_hash: str


class ChangeEventApplicationService:
    """编排变更接入、幂等判断、脱敏和 Transactional Outbox。"""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        identifier_generator: IdentifierGeneratorPort,
        clock: Clock | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._identifier_generator = identifier_generator
        self._clock = clock or (lambda: datetime.now(UTC))

    async def receive_change_event(
        self,
        command: ReceiveChangeEventCommand,
    ) -> ReceiveChangeEventResult:
        """接收变更；并发唯一冲突后只执行一次只读恢复。"""
        prepared = self._prepare(command)
        try:
            return await self._receive_once(command, prepared)
        except ConflictError:
            duplicate = await self._find_duplicate_result(command, prepared)
            if duplicate is not None:
                return duplicate
            raise

    async def _receive_once(
        self,
        command: ReceiveChangeEventCommand,
        prepared: _PreparedChangeEvent,
    ) -> ReceiveChangeEventResult:
        """在同一个 UoW 中完成预查、Change 与 Outbox 写入。"""
        async with self._unit_of_work_factory() as unit_of_work:
            existing = await self._find_existing(unit_of_work, command)
            if existing is not None:
                return self._duplicate_or_raise(
                    existing,
                    prepared.request_hash,
                    command.trace_id,
                )

            change_event = self._build_change_event(command, prepared)
            await unit_of_work.change_events.save(change_event)
            await unit_of_work.outbox.add(
                self._build_outbox_event(change_event, command.trace_id)
            )
            await unit_of_work.commit()
            return ReceiveChangeEventResult(
                change_event_id=change_event.change_event_id,
                status="ACCEPTED",
                trace_id=command.trace_id,
                is_duplicate=False,
            )

    def _prepare(
        self,
        command: ReceiveChangeEventCommand,
    ) -> _PreparedChangeEvent:
        """在数据库事务外完成纯计算的脱敏、编码和幂等哈希。"""
        summary = self._safe_summary(command.summary)
        sanitized = sanitize_evidence_payload(command.metadata)
        metadata_json = build_change_metadata(sanitized.payload)
        request_hash = self._build_request_hash(
            command,
            summary=summary,
            metadata_json=metadata_json,
        )
        return _PreparedChangeEvent(
            summary=summary,
            metadata_json=metadata_json,
            request_hash=request_hash,
        )

    def _build_change_event(
        self,
        command: ReceiveChangeEventCommand,
        prepared: _PreparedChangeEvent,
    ) -> ChangeEvent:
        """为规范化变更分配 ID 和接入时间。"""
        return ChangeEvent(
            change_event_id=self._identifier_generator.new_change_event_id(),
            tenant_id=command.tenant_id,
            source=command.source,
            external_event_id=command.external_event_id,
            service_name=command.service_name,
            resource_type=command.resource_type,
            resource_id=command.resource_id,
            change_type=command.change_type,
            status=command.status,
            version_before=command.version_before,
            version_after=command.version_after,
            operator_id=command.operator_id,
            summary=prepared.summary,
            metadata_json=prepared.metadata_json,
            started_at=command.started_at,
            completed_at=command.completed_at,
            created_at=self._now(),
            request_hash=prepared.request_hash,
        )

    def _build_outbox_event(
        self,
        change_event: ChangeEvent,
        trace_id: str,
    ) -> OutboxEvent:
        """构造不携带自由 metadata 的版本化变更审计事件。"""
        return OutboxEvent(
            event_id=self._identifier_generator.new_event_id(),
            tenant_id=change_event.tenant_id,
            aggregate_type="ChangeEvent",
            aggregate_id=change_event.change_event_id,
            event_type="change_event.received",
            schema_version=1,
            payload={
                "change_event_id": change_event.change_event_id,
                "tenant_id": change_event.tenant_id,
                "source": change_event.source,
                "external_event_id": change_event.external_event_id,
                "service_name": change_event.service_name,
                "resource_type": change_event.resource_type,
                "resource_id": change_event.resource_id,
                "change_type": change_event.change_type.value,
                "status": change_event.status.value,
                "version_before": change_event.version_before,
                "version_after": change_event.version_after,
                "started_at": change_event.started_at.isoformat(),
                "completed_at": (
                    change_event.completed_at.isoformat()
                    if change_event.completed_at is not None
                    else None
                ),
            },
            occurred_at=change_event.created_at,
            trace_id=trace_id,
        )

    async def _find_duplicate_result(
        self,
        command: ReceiveChangeEventCommand,
        prepared: _PreparedChangeEvent,
    ) -> ReceiveChangeEventResult | None:
        """写冲突回滚后用新事务确认是否为并发幂等请求。"""
        async with self._unit_of_work_factory() as unit_of_work:
            existing = await self._find_existing(unit_of_work, command)
            if existing is None:
                return None
            return self._duplicate_or_raise(
                existing,
                prepared.request_hash,
                command.trace_id,
            )

    @staticmethod
    async def _find_existing(
        unit_of_work: UnitOfWorkPort,
        command: ReceiveChangeEventCommand,
    ) -> ChangeEvent | None:
        return await unit_of_work.change_events.get_by_external_event_id(
            tenant_id=command.tenant_id,
            source=command.source,
            external_event_id=command.external_event_id,
        )

    @staticmethod
    def _duplicate_or_raise(
        existing: ChangeEvent,
        request_hash: str,
        trace_id: str,
    ) -> ReceiveChangeEventResult:
        """只把相同业务请求视为重试，拒绝幂等键误复用。"""
        if existing.request_hash != request_hash:
            raise ConflictError(
                "Change event idempotency key reuse with different payload"
            )
        return ReceiveChangeEventResult(
            change_event_id=existing.change_event_id,
            status="DUPLICATE",
            trace_id=trace_id,
            is_duplicate=True,
        )

    @staticmethod
    def _safe_summary(summary: str) -> str:
        normalized = " ".join(summary.splitlines()).strip()
        safe_summary = redact_sensitive_text(normalized)[0][:4096].strip()
        if not safe_summary:
            raise AppException("Change event summary is empty after sanitization")
        return safe_summary

    @staticmethod
    def _build_request_hash(
        command: ReceiveChangeEventCommand,
        *,
        summary: str,
        metadata_json: str,
    ) -> str:
        """对脱敏后的规范业务字段计算稳定请求摘要。"""
        payload = {
            "tenant_id": command.tenant_id,
            "source": command.source,
            "external_event_id": command.external_event_id,
            "service_name": command.service_name,
            "resource_type": command.resource_type,
            "resource_id": command.resource_id,
            "change_type": command.change_type.value,
            "status": command.status.value,
            "version_before": command.version_before,
            "version_after": command.version_after,
            "operator_id": command.operator_id,
            "summary": summary,
            "metadata_json": metadata_json,
            "started_at": command.started_at.isoformat(),
            "completed_at": (
                command.completed_at.isoformat()
                if command.completed_at is not None
                else None
            ),
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _now(self) -> datetime:
        now = self._clock()
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise AppException("Change event clock must return a timezone-aware time")
        return now
