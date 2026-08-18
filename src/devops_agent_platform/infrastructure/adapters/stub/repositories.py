from datetime import datetime

from devops_agent_platform.domain.enums import IncidentStatus, WorkflowRunStatus
from devops_agent_platform.domain.exceptions import NotImplementedInSkeleton
from devops_agent_platform.domain.models.alert import Alert
from devops_agent_platform.domain.models.change_event import ChangeEvent
from devops_agent_platform.domain.models.incident import Incident
from devops_agent_platform.domain.models.ticket_submission import TicketSubmission
from devops_agent_platform.domain.models.workflow_run import WorkflowRun


class StubAlertRepository:
    """不做持久化的告警仓储占位实现。

    该类不能模拟生产持久化，也不能返回伪造业务数据。
    """

    async def save(self, alert: Alert) -> None:
        raise NotImplementedInSkeleton(
            "StubAlertRepository is unavailable in skeleton mode"
        )

    async def get_by_external_event_id(
        self,
        tenant_id: str,
        source: str,
        external_event_id: str,
    ) -> Alert | None:
        raise NotImplementedInSkeleton(
            "StubAlertRepository is unavailable in skeleton mode"
        )


class StubChangeEventRepository:
    """不模拟变更事件持久化或查询的占位仓储。"""

    async def save(self, change_event: ChangeEvent) -> None:
        raise NotImplementedInSkeleton(
            "StubChangeEventRepository is unavailable in skeleton mode"
        )

    async def get_by_id(
        self,
        change_event_id: str,
        tenant_id: str,
    ) -> ChangeEvent | None:
        raise NotImplementedInSkeleton(
            "StubChangeEventRepository is unavailable in skeleton mode"
        )

    async def get_by_external_event_id(
        self,
        tenant_id: str,
        source: str,
        external_event_id: str,
    ) -> ChangeEvent | None:
        raise NotImplementedInSkeleton(
            "StubChangeEventRepository is unavailable in skeleton mode"
        )

    async def list_for_service(
        self,
        tenant_id: str,
        service_name: str,
        *,
        limit: int = 50,
    ) -> list[ChangeEvent]:
        raise NotImplementedInSkeleton(
            "StubChangeEventRepository is unavailable in skeleton mode"
        )

    async def list_in_time_window(
        self,
        tenant_id: str,
        service_name: str,
        started_at_from: datetime,
        started_at_to: datetime,
        *,
        limit: int = 50,
    ) -> list[ChangeEvent]:
        raise NotImplementedInSkeleton(
            "StubChangeEventRepository is unavailable in skeleton mode"
        )


class StubIncidentRepository:
    """不做持久化的事故仓储占位实现。"""

    async def save(self, incident: Incident) -> None:
        raise NotImplementedInSkeleton(
            "StubIncidentRepository is unavailable in skeleton mode"
        )

    async def get_by_id(self, incident_id: str, tenant_id: str) -> Incident | None:
        raise NotImplementedInSkeleton(
            "StubIncidentRepository is unavailable in skeleton mode"
        )

    async def list_page(
        self,
        tenant_id: str,
        statuses: frozenset[IncidentStatus],
        *,
        before_updated_at: datetime | None,
        before_incident_id: str | None,
        limit: int,
    ) -> list[Incident]:
        raise NotImplementedInSkeleton(
            "StubIncidentRepository is unavailable in skeleton mode"
        )

    async def find_candidates(
        self,
        tenant_id: str,
        service_name: str,
        statuses: frozenset[IncidentStatus],
        created_before: datetime,
        updated_after: datetime,
        limit: int = 50,
    ) -> list[Incident]:
        raise NotImplementedInSkeleton(
            "StubIncidentRepository is unavailable in skeleton mode"
        )


class StubWorkflowRunRepository:
    """不模拟持久化或幂等行为的WorkflowRun占位仓储。"""

    async def save(self, workflow_run: WorkflowRun) -> None:
        raise NotImplementedInSkeleton(
            "StubWorkflowRunRepository is a skeleton placeholder"
        )

    async def get_by_idempotency_key_hash(
        self,
        tenant_id: str,
        idempotency_key_hash: str,
    ) -> WorkflowRun | None:
        raise NotImplementedInSkeleton(
            "StubWorkflowRunRepository is a skeleton placeholder"
        )

    async def get_active_by_incident(
        self,
        tenant_id: str,
        incident_id: str,
    ) -> WorkflowRun | None:
        raise NotImplementedInSkeleton(
            "StubWorkflowRunRepository is a skeleton placeholder"
        )

    async def get_by_id(
        self,
        tenant_id: str,
        workflow_run_id: str,
    ) -> WorkflowRun | None:
        raise NotImplementedInSkeleton(
            "StubWorkflowRunRepository is a skeleton placeholder"
        )

    async def claim_for_execution(
        self,
        tenant_id: str,
        workflow_run_id: str,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> WorkflowRun | None:
        raise NotImplementedInSkeleton(
            "StubWorkflowRunRepository is a skeleton placeholder"
        )

    async def renew_execution_lease(
        self,
        tenant_id: str,
        workflow_run_id: str,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> WorkflowRun | None:
        raise NotImplementedInSkeleton(
            "StubWorkflowRunRepository is a skeleton placeholder"
        )

    async def complete_execution(
        self,
        tenant_id: str,
        workflow_run_id: str,
        worker_id: str,
        execution_attempt: int,
        target_status: WorkflowRunStatus,
        completed_at: datetime,
    ) -> WorkflowRun | None:
        raise NotImplementedInSkeleton(
            "StubWorkflowRunRepository is a skeleton placeholder"
        )


class StubTicketSubmissionRepository:
    """不模拟外部工单提交持久化的占位仓储。"""

    async def save(self, submission: TicketSubmission) -> None:
        raise NotImplementedInSkeleton(
            "StubTicketSubmissionRepository is a skeleton placeholder"
        )

    async def get_by_idempotency_key_hash(
        self,
        tenant_id: str,
        idempotency_key_hash: str,
    ) -> TicketSubmission | None:
        raise NotImplementedInSkeleton(
            "StubTicketSubmissionRepository is a skeleton placeholder"
        )

    async def get_by_draft_and_target(
        self,
        tenant_id: str,
        ticket_draft_id: str,
        target_system: str,
    ) -> TicketSubmission | None:
        raise NotImplementedInSkeleton(
            "StubTicketSubmissionRepository is a skeleton placeholder"
        )

    async def get_by_id(
        self,
        tenant_id: str,
        ticket_submission_id: str,
    ) -> TicketSubmission | None:
        raise NotImplementedInSkeleton(
            "StubTicketSubmissionRepository is a skeleton placeholder"
        )

    async def list_by_workflow(
        self,
        tenant_id: str,
        workflow_run_id: str,
        *,
        limit: int = 50,
    ) -> list[TicketSubmission]:
        raise NotImplementedInSkeleton(
            "StubTicketSubmissionRepository is a skeleton placeholder"
        )

    async def get_by_result_idempotency_key_hash(
        self,
        tenant_id: str,
        idempotency_key_hash: str,
    ) -> TicketSubmission | None:
        raise NotImplementedInSkeleton(
            "StubTicketSubmissionRepository is a skeleton placeholder"
        )

    async def apply_result(
        self,
        submission: TicketSubmission,
        expected_version: int,
    ) -> None:
        raise NotImplementedInSkeleton(
            "StubTicketSubmissionRepository is a skeleton placeholder"
        )
