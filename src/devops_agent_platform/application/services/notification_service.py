import hashlib
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from devops_agent_platform.application.commands.notifications import (
    SendWorkflowNotificationCommand,
)
from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.application.exceptions import (
    NotificationGatewayError,
)
from devops_agent_platform.domain.enums import WorkflowRunStatus
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
    ResourceNotFound,
)
from devops_agent_platform.ports.identifiers import IdentifierGeneratorPort
from devops_agent_platform.ports.notifications import (
    NotificationDeliveryOutcome,
    NotificationGatewayPort,
    NotificationMessage,
)
from devops_agent_platform.ports.unit_of_work import UnitOfWorkPort

UnitOfWorkFactory = Callable[[], UnitOfWorkPort]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class NotificationDeliveryView:
    tenant_id: str
    workflow_run_id: str
    target_system: str
    external_reference: str
    trace_id: str
    delivered_at: datetime

    def to_dict(self) -> dict:
        result = asdict(self)
        result["delivered_at"] = self.delivered_at.isoformat()
        return result


class NotificationApplicationService:
    """从可信 RCA 事实组装并投递有限通知。"""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        gateway: NotificationGatewayPort,
        identifier_generator: IdentifierGeneratorPort,
        clock: Clock | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._gateway = gateway
        self._identifier_generator = identifier_generator
        self._clock = clock or (lambda: datetime.now(UTC))

    async def send(
        self,
        command: SendWorkflowNotificationCommand,
    ) -> NotificationDeliveryView:
        message = await self._build_message(command)
        delivered_at = self._now()
        try:
            outcome = await self._gateway.send(
                command.target_system,
                message,
            )
        except NotificationGatewayError:
            await self._record_audit(
                command,
                delivered_at,
                succeeded=False,
                external_reference=None,
            )
            raise
        await self._record_audit(
            command,
            delivered_at,
            succeeded=True,
            external_reference=outcome.external_reference,
        )
        return self._view(command, outcome, delivered_at)

    async def _build_message(
        self,
        command: SendWorkflowNotificationCommand,
    ) -> NotificationMessage:
        async with self._unit_of_work_factory() as unit_of_work:
            workflow = await unit_of_work.workflow_runs.get_by_id(
                command.tenant_id,
                command.workflow_run_id,
            )
            if workflow is None:
                raise ResourceNotFound("Workflow run not found")
            if workflow.status is not WorkflowRunStatus.SUCCEEDED:
                raise ConflictError("Notification requires a succeeded workflow")
            if workflow.audit_purged_at is not None:
                raise ConflictError("Notification requires available audit evidence")
            report = await unit_of_work.rca_reports.get_by_workflow_run(
                command.tenant_id,
                command.workflow_run_id,
            )
            if report is None:
                raise ConflictError("Notification requires an RCA report")
            incident = await unit_of_work.incidents.get_by_id(
                workflow.incident_id,
                command.tenant_id,
            )
            if incident is None:
                raise ResourceNotFound("Incident not found")
        return NotificationMessage(
            tenant_id=command.tenant_id,
            workflow_run_id=command.workflow_run_id,
            incident_id=incident.incident_id,
            service_name=incident.service_name,
            severity=incident.severity.value,
            title=report.title,
            summary=report.summary,
            recommendations=report.recommendations,
            idempotency_key=command.idempotency_key,
            trace_id=command.trace_id,
        )

    async def _record_audit(
        self,
        command: SendWorkflowNotificationCommand,
        occurred_at: datetime,
        *,
        succeeded: bool,
        external_reference: str | None,
    ) -> None:
        reference_hash = (
            hashlib.sha256(external_reference.encode("utf-8")).hexdigest()
            if external_reference is not None
            else None
        )
        event = OutboxEvent(
            event_id=self._identifier_generator.new_event_id(),
            tenant_id=command.tenant_id,
            aggregate_type="WorkflowRun",
            aggregate_id=command.workflow_run_id,
            event_type=(
                "rca.notification.delivered" if succeeded else "rca.notification.failed"
            ),
            schema_version=1,
            payload={
                "workflow_run_id": command.workflow_run_id,
                "target_system": command.target_system,
                "succeeded": succeeded,
                "external_reference_sha256": reference_hash,
                "requested_by": command.requested_by,
                "occurred_at": occurred_at.isoformat(),
            },
            occurred_at=occurred_at,
            trace_id=command.trace_id,
        )
        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.outbox.add(event)
            await unit_of_work.commit()

    @staticmethod
    def _view(
        command: SendWorkflowNotificationCommand,
        outcome: NotificationDeliveryOutcome,
        delivered_at: datetime,
    ) -> NotificationDeliveryView:
        return NotificationDeliveryView(
            tenant_id=command.tenant_id,
            workflow_run_id=command.workflow_run_id,
            target_system=outcome.provider,
            external_reference=outcome.external_reference,
            trace_id=command.trace_id,
            delivered_at=delivered_at,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AppValidationError("clock must return a timezone-aware datetime")
        return value.astimezone(UTC)
