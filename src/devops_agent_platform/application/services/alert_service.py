from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace

from devops_agent_platform.application.commands.alerts import ReceiveAlertCommand
from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.application.services.alert_correlation_service import (
    AlertCorrelationService,
    CorrelationDecision,
)
from devops_agent_platform.domain.enums import (
    IncidentCreationAction,
    IncidentDecisionReason,
    IncidentStatus,
)
from devops_agent_platform.domain.exceptions import AppException, ConflictError
from devops_agent_platform.domain.models.alert import Alert
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.domain.models.topology import TopologyGraph
from devops_agent_platform.domain.policies.incident_creation import (
    IncidentCreationDecision,
    IncidentCreationPolicy,
)
from devops_agent_platform.ports.identifiers import IdentifierGeneratorPort
from devops_agent_platform.ports.unit_of_work import UnitOfWorkPort
from devops_agent_platform.tools.sanitization import redact_sensitive_text

UnitOfWorkFactory = Callable[[], UnitOfWorkPort]
TopologyProvider = Callable[[str, str], Awaitable[TopologyGraph | None]]


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
        correlation_service: AlertCorrelationService | None = None,
        topology_provider: TopologyProvider | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._incident_policy = incident_policy
        self._identifier_generator = identifier_generator
        self._correlation_service = correlation_service or AlertCorrelationService()
        self._topology_provider = topology_provider

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
            topology: TopologyGraph | None = None
            if self._incident_policy.requires_candidate_lookup(alert):
                topology = await self._load_topology(alert)
                service_names = self._candidate_service_names(alert, topology)
                for service_name in service_names:
                    await unit_of_work.incident_correlation_lock.acquire(
                        tenant_id=alert.tenant_id,
                        service_name=service_name,
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
                for service_name in service_names:
                    candidates.extend(
                        await unit_of_work.incidents.find_candidates(
                            tenant_id=alert.tenant_id,
                            service_name=service_name,
                            statuses=self._incident_policy.active_statuses,
                            created_before=created_before,
                            updated_after=updated_after,
                            limit=50,
                        )
                    )
                candidates = self._deduplicate_incidents(candidates)

            decision, correlation = self._decide_incident(
                alert,
                candidates,
                topology,
            )
            alert = await self._apply_incident_decision(
                alert=alert,
                decision=decision,
                candidates=candidates,
                unit_of_work=unit_of_work,
                trace_id=command.trace_id,
                correlation=correlation,
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
            environment=command.environment,
            alert_type=command.alert_type,
            labels=command.labels,
        )

    async def _load_topology(self, alert: Alert) -> TopologyGraph | None:
        if self._topology_provider is None:
            return None
        try:
            return await self._topology_provider(
                alert.tenant_id,
                alert.environment,
            )
        except Exception:
            # 拓扑源不可用时仍按同服务候选运行，不能阻塞告警接入。
            return None

    @staticmethod
    def _candidate_service_names(
        alert: Alert,
        topology: TopologyGraph | None,
    ) -> tuple[str, ...]:
        names = [alert.service_name]
        if topology is None:
            return tuple(names)
        root_id = f"service:{alert.service_name}"
        connected_nodes = {root_id}
        changed = True
        while changed:
            changed = False
            for edge in topology.edges:
                if (
                    edge.source_node_id in connected_nodes
                    and edge.target_node_id not in connected_nodes
                ):
                    connected_nodes.add(edge.target_node_id)
                    changed = True
                elif (
                    edge.target_node_id in connected_nodes
                    and edge.source_node_id not in connected_nodes
                ):
                    connected_nodes.add(edge.source_node_id)
                    changed = True
        for node in topology.nodes:
            if node.node_id in connected_nodes and hasattr(node, "service_name"):
                names.append(node.service_name)
        return tuple(dict.fromkeys(names))

    @staticmethod
    def _deduplicate_incidents(candidates: list[Incident]) -> list[Incident]:
        return list({item.incident_id: item for item in candidates}.values())

    def _decide_incident(
        self,
        alert: Alert,
        candidates: list[Incident],
        topology: TopologyGraph | None,
    ) -> tuple[IncidentCreationDecision, CorrelationDecision | None]:
        if not self._incident_policy.requires_candidate_lookup(alert):
            return self._incident_policy.decide(alert, ()), None
        correlation = self._correlation_service.correlate(
            alert,
            candidates,
            topology=topology,
        )
        if correlation.incident_id is None:
            return (
                IncidentCreationDecision(
                    action=IncidentCreationAction.CREATE,
                    reason=IncidentDecisionReason.NO_MATCHING_ACTIVE_INCIDENT,
                ),
                correlation,
            )
        return (
            IncidentCreationDecision(
                action=IncidentCreationAction.ATTACH,
                reason=IncidentDecisionReason.ACTIVE_INCIDENT_MATCHED,
                matched_incident_id=correlation.incident_id,
            ),
            correlation,
        )

    async def _apply_incident_decision(
        self,
        alert: Alert,
        decision: IncidentCreationDecision,
        candidates: list[Incident],
        unit_of_work: UnitOfWorkPort,
        trace_id: str,
        correlation: CorrelationDecision | None,
    ) -> Alert:
        """把纯领域决策转换为同一事务中的事故写入和告警关联。"""
        if decision.action is IncidentCreationAction.IGNORE:
            return alert

        if decision.action is IncidentCreationAction.CREATE:
            incident = self._build_incident(alert)
        else:
            incident = self._find_matched_incident(decision, candidates)
            incident.attach_alert(
                alert,
                topology_related=bool(
                    correlation and "TOPOLOGY_DEPENDENCY" in correlation.reason
                ),
                affected_services=(
                    correlation.affected_services if correlation else ()
                ),
            )

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
            primary_alert_id=alert.alert_id,
            environment=alert.environment,
            affected_services=(alert.service_name,),
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
                "environment": incident.environment,
                "affected_services": list(incident.affected_services),
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
