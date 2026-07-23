from collections.abc import Callable
from dataclasses import asdict, dataclass, replace

from devops_agent_platform.application.commands.alerts import ReceiveAlertCommand
from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.domain.enums import (
    IncidentCreationAction,
    IncidentStatus,
)
from devops_agent_platform.domain.exceptions import AppException, ConflictError
from devops_agent_platform.domain.models.alert import Alert
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.domain.policies.incident_creation import (
    IncidentCreationDecision,
    IncidentCreationPolicy,
)
from devops_agent_platform.ports.identifiers import IdentifierGeneratorPort
from devops_agent_platform.ports.unit_of_work import UnitOfWorkPort
from devops_agent_platform.tools.sanitization import redact_sensitive_text

UnitOfWorkFactory = Callable[[], UnitOfWorkPort]


@dataclass(frozen=True)
class ReceiveAlertResult:
    """告警接入用例返回给接口层的稳定结果。"""

    alert_id: str
    incident_id: str | None
    status: str
    incident_action: IncidentCreationAction | None
    trace_id: str
    is_duplicate: bool

    def to_dict(self) -> dict[str, str | bool | None]:
        """转换为不依赖 Web 框架的响应数据。"""
        return asdict(self)


class AlertApplicationService:
    """编排告警幂等、事故策略和原子持久化的应用服务。

    该服务不发布外部消息。数据库事务与消息系统之间需要 Transactional Outbox
    才能保证一致性，直接调用 EventPublisher 会产生不可恢复的双写窗口。
    """

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        incident_policy: IncidentCreationPolicy,
        identifier_generator: IdentifierGeneratorPort,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._incident_policy = incident_policy
        self._identifier_generator = identifier_generator

    async def receive_alert(
        self,
        command: ReceiveAlertCommand,
    ) -> ReceiveAlertResult:
        """接收告警；并发幂等冲突时执行一次只读确认。

        非幂等冲突不会被吞掉，也不会自动重试写操作。调用方可以依据稳定异常码
        决定是否重试整个请求。
        """
        try:
            return await self._receive_once(command)
        except ConflictError:
            duplicate = await self._find_duplicate_result(command)
            if duplicate is not None:
                return duplicate
            raise

    async def _receive_once(
        self,
        command: ReceiveAlertCommand,
    ) -> ReceiveAlertResult:
        """在一个 Unit of Work 内完成预查、决策、写入和提交。"""
        async with self._unit_of_work_factory() as unit_of_work:
            existing = await self._find_existing_alert(unit_of_work, command)
            if existing is not None:
                return self._duplicate_result(existing, command.trace_id)

            alert = self._build_alert(command)
            candidates: list[Incident] = []
            if self._incident_policy.requires_candidate_lookup(alert):
                await unit_of_work.incident_correlation_lock.acquire(
                    tenant_id=alert.tenant_id,
                    service_name=alert.service_name,
                )
                existing = await self._find_existing_alert(
                    unit_of_work,
                    command,
                )
                if existing is not None:
                    return self._duplicate_result(
                        existing,
                        command.trace_id,
                    )

                created_before, updated_after = (
                    self._incident_policy.candidate_time_bounds(alert)
                )
                candidates = await unit_of_work.incidents.find_candidates(
                    tenant_id=alert.tenant_id,
                    service_name=alert.service_name,
                    statuses=self._incident_policy.active_statuses,
                    created_before=created_before,
                    updated_after=updated_after,
                    limit=50,
                )

            decision = self._incident_policy.decide(alert, candidates)
            alert = await self._apply_incident_decision(
                alert=alert,
                decision=decision,
                candidates=candidates,
                unit_of_work=unit_of_work,
                trace_id=command.trace_id,
            )
            await unit_of_work.alerts.save(alert)
            await unit_of_work.commit()

            return ReceiveAlertResult(
                alert_id=alert.alert_id,
                incident_id=alert.incident_id,
                status="ACCEPTED",
                incident_action=decision.action,
                trace_id=command.trace_id,
                is_duplicate=False,
            )

    def _build_alert(self, command: ReceiveAlertCommand) -> Alert:
        """把已校验 Command 转换为新的不可变告警事实。"""
        return Alert(
            alert_id=self._identifier_generator.new_alert_id(),
            tenant_id=command.tenant_id,
            source=command.source,
            service_name=command.service_name,
            severity=command.severity,
            summary=self._safe_alert_summary(command.summary),
            starts_at=command.starts_at,
            fingerprint=command.fingerprint,
            external_event_id=command.external_event_id,
        )

    async def _apply_incident_decision(
        self,
        alert: Alert,
        decision: IncidentCreationDecision,
        candidates: list[Incident],
        unit_of_work: UnitOfWorkPort,
        trace_id: str,
    ) -> Alert:
        """把纯领域决策转换为同一事务中的事故写入和告警关联。"""
        if decision.action is IncidentCreationAction.IGNORE:
            return alert

        if decision.action is IncidentCreationAction.CREATE:
            incident = self._build_incident(alert)
        else:
            incident = self._find_matched_incident(decision, candidates)
            incident.attach_alert(alert)

        await unit_of_work.incidents.save(incident)
        if decision.action is IncidentCreationAction.CREATE:
            await unit_of_work.outbox.add(
                self._build_incident_created_event(
                    alert=alert,
                    incident=incident,
                    trace_id=trace_id,
                )
            )
        return replace(alert, incident_id=incident.incident_id)

    def _build_incident(self, alert: Alert) -> Incident:
        """根据首条告警创建 OPEN 状态事故。"""
        title = alert.summary
        if len(title) > 512:
            title = f"{title[:509]}..."
        return Incident(
            incident_id=self._identifier_generator.new_incident_id(),
            tenant_id=alert.tenant_id,
            service_name=alert.service_name,
            severity=alert.severity,
            status=IncidentStatus.OPEN,
            title=title,
            created_at=alert.starts_at,
            updated_at=alert.starts_at,
        )

    def _build_incident_created_event(
        self,
        alert: Alert,
        incident: Incident,
        trace_id: str,
    ) -> OutboxEvent:
        """构造版本化的 IncidentCreated Outbox 事件。"""
        return OutboxEvent(
            event_id=self._identifier_generator.new_event_id(),
            tenant_id=incident.tenant_id,
            aggregate_type="Incident",
            aggregate_id=incident.incident_id,
            event_type="incident.created",
            schema_version=1,
            payload={
                "incident_id": incident.incident_id,
                "alert_id": alert.alert_id,
                "tenant_id": incident.tenant_id,
                "service_name": incident.service_name,
                "severity": incident.severity.value,
                "created_at": incident.created_at.isoformat(),
            },
            occurred_at=incident.created_at,
            trace_id=trace_id,
        )

    @staticmethod
    def _find_matched_incident(
        decision: IncidentCreationDecision,
        candidates: list[Incident],
    ) -> Incident:
        """从本次策略输入中取得被选中的事故，防止越界加载其他资源。"""
        for incident in candidates:
            if incident.incident_id == decision.matched_incident_id:
                return incident
        raise AppException("Incident policy returned an unknown candidate")

    async def _find_duplicate_result(
        self,
        command: ReceiveAlertCommand,
    ) -> ReceiveAlertResult | None:
        """冲突回滚后使用新事务确认是否由并发重复请求造成。"""
        async with self._unit_of_work_factory() as unit_of_work:
            existing = await self._find_existing_alert(unit_of_work, command)
            if existing is None:
                return None
            return self._duplicate_result(existing, command.trace_id)

    @staticmethod
    async def _find_existing_alert(
        unit_of_work: UnitOfWorkPort,
        command: ReceiveAlertCommand,
    ) -> Alert | None:
        """按完整幂等键查询已接收告警。"""
        return await unit_of_work.alerts.get_by_external_event_id(
            tenant_id=command.tenant_id,
            source=command.source,
            external_event_id=command.external_event_id,
        )

    @staticmethod
    def _duplicate_result(alert: Alert, trace_id: str) -> ReceiveAlertResult:
        """为普通重试和并发重试返回同一稳定结果。"""
        return ReceiveAlertResult(
            alert_id=alert.alert_id,
            incident_id=alert.incident_id,
            status="DUPLICATE",
            incident_action=None,
            trace_id=trace_id,
            is_duplicate=True,
        )

    @staticmethod
    def _safe_alert_summary(summary: str) -> str:
        """告警摘要进入 Alert 和 Incident 前压成单行并遮蔽敏感片段。"""
        normalized = " ".join(summary.splitlines()).strip()
        safe_summary = redact_sensitive_text(normalized)[0][:2048].strip()
        if not safe_summary:
            raise AppException("Alert summary is empty after sanitization")
        return safe_summary
