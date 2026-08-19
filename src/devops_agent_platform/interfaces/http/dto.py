from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from devops_agent_platform.application.commands.alerts import ReceiveAlertCommand
from devops_agent_platform.application.commands.change_events import (
    ReceiveChangeEventCommand,
)
from devops_agent_platform.application.commands.dataset_releases import (
    CreateDatasetReleaseCommand,
    ReviewDatasetReleaseCommand,
)
from devops_agent_platform.application.commands.incidents import (
    CloseIncidentCommand,
    ResolveIncidentCommand,
)
from devops_agent_platform.application.commands.notifications import (
    SendWorkflowNotificationCommand,
)
from devops_agent_platform.application.commands.rca import (
    CancelRCAWorkflowCommand,
)
from devops_agent_platform.application.commands.rca_feedback import (
    CreateRCAFeedbackCommand,
)
from devops_agent_platform.application.commands.remediation import (
    CreateRemediationPlanCommand,
    DecideRemediationPlanCommand,
    ExecuteRemediationPlanCommand,
    RollbackRemediationPlanCommand,
)
from devops_agent_platform.application.commands.runbooks import (
    SaveRunbookDraftCommand,
)
from devops_agent_platform.application.commands.ticket_drafts import (
    DecideTicketDraftCommand,
    SubmitTicketDraftCommand,
)
from devops_agent_platform.application.commands.tool_permissions import (
    SetToolPermissionsCommand,
)
from devops_agent_platform.application.commands.workspaces import (
    UpsertWorkspaceCommand,
)
from devops_agent_platform.domain.enums import (
    AlertSeverity,
    ChangeEventStatus,
    ChangeType,
    EvidenceType,
    RCAFeedbackVerdict,
    TicketDecision,
)
from devops_agent_platform.domain.models.dataset_release import DatasetReviewRole


class AlertWebhookRequest(BaseModel):
    """监控系统接入告警时提交的 HTTP 请求体。

    接口层 DTO 只描述网络传输格式。进入用例编排前，必须转换为
    application command，避免 application 层感知 HTTP 框架细节。
    """

    tenant_id: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=128)
    service_name: str = Field(min_length=1, max_length=256)
    severity: AlertSeverity
    summary: str = Field(min_length=1, max_length=2048)
    starts_at: datetime
    fingerprint: str = Field(min_length=1, max_length=256)
    external_event_id: str = Field(min_length=1, max_length=256)
    environment: str = Field(default="default", min_length=1, max_length=64)
    alert_type: str = Field(default="generic", min_length=1, max_length=128)
    labels: dict[str, str] = Field(default_factory=dict, max_length=64)

    def to_command(self, trace_id: str) -> ReceiveAlertCommand:
        """将 HTTP DTO 转换为应用层 Command。"""
        return ReceiveAlertCommand(
            tenant_id=self.tenant_id,
            source=self.source,
            service_name=self.service_name,
            severity=self.severity,
            summary=self.summary,
            starts_at=self.starts_at,
            fingerprint=self.fingerprint,
            external_event_id=self.external_event_id,
            trace_id=trace_id,
            environment=self.environment,
            alert_type=self.alert_type,
            labels=tuple(sorted(self.labels.items())),
        )


class ChangeEventWebhookRequest(BaseModel):
    """CD/配置系统接入变更事实时提交的 HTTP 请求体。"""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=128)
    external_event_id: str = Field(min_length=1, max_length=256)
    service_name: str = Field(min_length=1, max_length=256)
    resource_type: str = Field(min_length=1, max_length=128)
    resource_id: str = Field(min_length=1, max_length=256)
    change_type: ChangeType
    status: ChangeEventStatus
    version_before: str | None = Field(default=None, min_length=1, max_length=256)
    version_after: str | None = Field(default=None, min_length=1, max_length=256)
    operator_id: str | None = Field(default=None, min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=4096)
    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime
    completed_at: datetime | None = None

    def to_command(self, trace_id: str) -> ReceiveChangeEventCommand:
        """将变更接入 DTO 转换为不依赖 HTTP 的应用命令。"""
        return ReceiveChangeEventCommand(
            tenant_id=self.tenant_id,
            source=self.source,
            external_event_id=self.external_event_id,
            service_name=self.service_name,
            resource_type=self.resource_type,
            resource_id=self.resource_id,
            change_type=self.change_type,
            status=self.status,
            version_before=self.version_before,
            version_after=self.version_after,
            operator_id=self.operator_id,
            summary=self.summary,
            metadata=self.metadata,
            started_at=self.started_at,
            completed_at=self.completed_at,
            trace_id=trace_id,
        )


class StartRCARequest(BaseModel):
    """RCA 启动不接受任何可伪造的身份正文。"""

    model_config = ConfigDict(extra="forbid")


class CancelRCAWorkflowRequest(BaseModel):
    """管理员取消 RCA 工作流时提交的最小正文。"""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2048)

    def to_command(
        self,
        *,
        tenant_id: str,
        workflow_run_id: str,
        expected_version: int,
        idempotency_key: str,
        requested_by: str,
        trace_id: str,
    ) -> CancelRCAWorkflowCommand:
        """把可信路径、认证和条件头合并为取消命令。"""
        return CancelRCAWorkflowCommand(
            tenant_id=tenant_id,
            workflow_run_id=workflow_run_id,
            expected_version=expected_version,
            reason=self.reason,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
            trace_id=trace_id,
        )


class CreateRCAFeedbackRequest(BaseModel):
    """人工复核 RCA 报告时提交的结构化发现。"""

    model_config = ConfigDict(extra="forbid")

    verdict: RCAFeedbackVerdict
    corrected_root_cause: str | None = Field(
        default=None,
        min_length=1,
        max_length=4096,
    )
    missing_evidence_types: list[EvidenceType] = Field(
        default_factory=list,
        max_length=5,
    )
    unsafe_recommendation_indexes: list[int] = Field(
        default_factory=list,
        max_length=100,
    )
    follow_up_label: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    notes: str | None = Field(
        default=None,
        min_length=1,
        max_length=4096,
    )

    @field_validator("missing_evidence_types")
    @classmethod
    def validate_missing_evidence_types(
        cls,
        value: list[EvidenceType],
    ) -> list[EvidenceType]:
        if len(set(value)) != len(value):
            raise ValueError("missing_evidence_types must be unique")
        return value

    @field_validator("unsafe_recommendation_indexes")
    @classmethod
    def validate_unsafe_recommendation_indexes(
        cls,
        value: list[int],
    ) -> list[int]:
        if any(index < 0 or index > 99 for index in value) or value != sorted(
            set(value)
        ):
            raise ValueError("unsafe_recommendation_indexes must be sorted and unique")
        return value

    def to_command(
        self,
        *,
        tenant_id: str,
        workflow_run_id: str,
        idempotency_key: str,
        requested_by: str,
        trace_id: str,
    ) -> CreateRCAFeedbackCommand:
        return CreateRCAFeedbackCommand(
            tenant_id=tenant_id,
            workflow_run_id=workflow_run_id,
            verdict=self.verdict,
            corrected_root_cause=self.corrected_root_cause,
            missing_evidence_types=tuple(self.missing_evidence_types),
            unsafe_recommendation_indexes=tuple(self.unsafe_recommendation_indexes),
            follow_up_label=self.follow_up_label,
            notes=self.notes,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
            trace_id=trace_id,
        )


class CreateRemediationPlanRequest(BaseModel):
    """只选择动作目录中的键、精确目标和可信证据引用。"""

    model_config = ConfigDict(extra="forbid")

    action_key: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=256)
    evidence_ids: list[str] = Field(min_length=4, max_length=100)

    @field_validator("evidence_ids")
    @classmethod
    def validate_evidence_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("evidence_ids must be unique")
        return value

    def to_command(
        self,
        *,
        tenant_id: str,
        workflow_run_id: str,
        idempotency_key: str,
        requested_by: str,
        trace_id: str,
    ) -> CreateRemediationPlanCommand:
        return CreateRemediationPlanCommand(
            tenant_id=tenant_id,
            workflow_run_id=workflow_run_id,
            action_key=self.action_key,
            target=self.target,
            evidence_ids=tuple(self.evidence_ids),
            idempotency_key=idempotency_key,
            requested_by=requested_by,
            trace_id=trace_id,
        )


class DecideRemediationPlanRequest(BaseModel):
    """人工审批或拒绝明确版本的修复计划。"""

    model_config = ConfigDict(extra="forbid")

    approved: bool
    reason: str = Field(min_length=1, max_length=4096)

    def to_command(
        self,
        *,
        tenant_id: str,
        remediation_plan_id: str,
        expected_version: int,
        idempotency_key: str,
        requested_by: str,
        trace_id: str,
    ) -> DecideRemediationPlanCommand:
        return DecideRemediationPlanCommand(
            tenant_id=tenant_id,
            remediation_plan_id=remediation_plan_id,
            expected_version=expected_version,
            approved=self.approved,
            reason=self.reason,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
            trace_id=trace_id,
        )


class RemediationActionRequest(BaseModel):
    """执行与回滚不接收命令文本或动态参数。"""

    model_config = ConfigDict(extra="forbid")

    def to_execute_command(
        self,
        *,
        tenant_id: str,
        remediation_plan_id: str,
        expected_version: int,
        idempotency_key: str,
        requested_by: str,
        trace_id: str,
    ) -> ExecuteRemediationPlanCommand:
        return ExecuteRemediationPlanCommand(
            tenant_id=tenant_id,
            remediation_plan_id=remediation_plan_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
            trace_id=trace_id,
        )

    def to_rollback_command(
        self,
        *,
        tenant_id: str,
        remediation_plan_id: str,
        expected_version: int,
        idempotency_key: str,
        requested_by: str,
        trace_id: str,
    ) -> RollbackRemediationPlanCommand:
        return RollbackRemediationPlanCommand(
            tenant_id=tenant_id,
            remediation_plan_id=remediation_plan_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
            trace_id=trace_id,
        )


class SendWorkflowNotificationRequest(BaseModel):
    """请求向已配置供应商发送可信 RCA 摘要。"""

    model_config = ConfigDict(extra="forbid")

    target_system: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^(slack|teams|pagerduty)$",
    )

    def to_command(
        self,
        *,
        tenant_id: str,
        workflow_run_id: str,
        idempotency_key: str,
        requested_by: str,
        trace_id: str,
    ) -> SendWorkflowNotificationCommand:
        return SendWorkflowNotificationCommand(
            tenant_id=tenant_id,
            workflow_run_id=workflow_run_id,
            target_system=self.target_system,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
            trace_id=trace_id,
        )


class ResolveIncidentRequest(BaseModel):
    """管理员解决事故时提交的最小正文。"""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2048)

    def to_command(
        self,
        *,
        tenant_id: str,
        incident_id: str,
        expected_version: int,
        idempotency_key: str,
        requested_by: str,
        trace_id: str,
    ) -> ResolveIncidentCommand:
        """把可信路径、认证和条件头合并为应用命令。"""
        return ResolveIncidentCommand(
            tenant_id=tenant_id,
            incident_id=incident_id,
            expected_version=expected_version,
            reason=self.reason,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
            trace_id=trace_id,
        )


class CloseIncidentRequest(BaseModel):
    """管理员关闭已解决事故时提交的最小正文。"""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2048)

    def to_command(
        self,
        *,
        tenant_id: str,
        incident_id: str,
        expected_version: int,
        idempotency_key: str,
        requested_by: str,
        trace_id: str,
    ) -> CloseIncidentCommand:
        """把可信路径、认证和条件头合并为关闭命令。"""
        return CloseIncidentCommand(
            tenant_id=tenant_id,
            incident_id=incident_id,
            expected_version=expected_version,
            reason=self.reason,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
            trace_id=trace_id,
        )


class SetToolPermissionsRequest(BaseModel):
    """完整替换操作者工具权限时提交的HTTP请求体。"""

    model_config = ConfigDict(extra="forbid")

    permission_tags: list[str] = Field(min_length=1, max_length=256)
    expires_at: datetime | None = None

    def to_command(
        self,
        *,
        tenant_id: str,
        operator_id: str,
        expected_version: int,
        idempotency_key: str,
        requested_by: str,
        trace_id: str,
    ) -> SetToolPermissionsCommand:
        """把HTTP字段和可信管理员身份转换为应用Command。"""
        return SetToolPermissionsCommand(
            tenant_id=tenant_id,
            operator_id=operator_id,
            permission_tags=tuple(self.permission_tags),
            expires_at=self.expires_at,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
            trace_id=trace_id,
        )


class SaveRunbookDraftRequest(BaseModel):
    """创建或更新 Runbook 草稿时提交的有限正文。"""

    model_config = ConfigDict(extra="forbid")

    service_name: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=256)
    summary: str = Field(min_length=1, max_length=4096)
    priority: int = Field(default=0, ge=0, le=1000)
    steps: list[str] = Field(min_length=1, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=20)

    def to_command(
        self,
        *,
        tenant_id: str,
        runbook_key: str,
        version: str,
        expected_revision: int,
        idempotency_key: str,
        requested_by: str,
        trace_id: str,
    ) -> SaveRunbookDraftCommand:
        """把 HTTP 字段和可信管理员身份转换为应用 Command。"""
        return SaveRunbookDraftCommand(
            tenant_id=tenant_id,
            runbook_key=runbook_key,
            version=version,
            service_name=self.service_name,
            title=self.title,
            summary=self.summary,
            priority=self.priority,
            steps=tuple(self.steps),
            tags=tuple(self.tags),
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
            trace_id=trace_id,
        )


class TicketDraftDecisionRequest(BaseModel):
    """人工批准或拒绝 Ticket Draft 的请求体。"""

    model_config = ConfigDict(extra="forbid")

    decision: TicketDecision
    reason: str | None = Field(default=None, max_length=2048)

    def to_command(
        self,
        *,
        tenant_id: str,
        workflow_run_id: str,
        expected_version: int,
        idempotency_key: str,
        requested_by: str,
        trace_id: str,
    ) -> DecideTicketDraftCommand:
        """组合路径、条件头和可信管理员身份。"""
        return DecideTicketDraftCommand(
            tenant_id=tenant_id,
            workflow_run_id=workflow_run_id,
            decision=self.decision,
            reason=self.reason,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
            trace_id=trace_id,
        )


class TicketSubmissionRequest(BaseModel):
    """请求把已批准 Ticket Draft 提交到外部工单系统。"""

    model_config = ConfigDict(extra="forbid")

    target_system: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9._:-]*$",
    )

    def to_command(
        self,
        *,
        tenant_id: str,
        workflow_run_id: str,
        expected_draft_version: int,
        idempotency_key: str,
        requested_by: str,
        trace_id: str,
    ) -> SubmitTicketDraftCommand:
        """组合路径、条件头和可信管理员身份。"""
        return SubmitTicketDraftCommand(
            tenant_id=tenant_id,
            workflow_run_id=workflow_run_id,
            target_system=self.target_system,
            expected_draft_version=expected_draft_version,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
            trace_id=trace_id,
        )


class UpsertWorkspaceRequest(BaseModel):
    """Workspace 管理正文不接受租户、身份、版本或任意凭据字段。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    prometheus_target: str = Field(min_length=1, max_length=512)
    loki_target: str = Field(min_length=1, max_length=512)
    tempo_target: str = Field(min_length=1, max_length=512)
    knowledge_scope: str = Field(default="tenant", min_length=1, max_length=128)
    investigation_policy: str = Field(
        default="fixed_default",
        min_length=1,
        max_length=64,
    )
    allowed_tools: list[str] = Field(default_factory=list, max_length=100)
    llm_provider_policy: str = Field(
        default="deterministic",
        min_length=1,
        max_length=128,
    )
    retention_days: int = Field(default=30, ge=1, le=3650)

    @field_validator("allowed_tools")
    @classmethod
    def validate_allowed_tools(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("allowed_tools must be unique")
        return value

    def to_command(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        expected_revision: int,
        idempotency_key: str,
        requested_by: str,
        trace_id: str,
    ) -> UpsertWorkspaceCommand:
        return UpsertWorkspaceCommand(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            name=self.name,
            prometheus_target=self.prometheus_target,
            loki_target=self.loki_target,
            tempo_target=self.tempo_target,
            knowledge_scope=self.knowledge_scope,
            investigation_policy=self.investigation_policy,
            allowed_tools=tuple(self.allowed_tools),
            llm_provider_policy=self.llm_provider_policy,
            retention_days=self.retention_days,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
            trace_id=trace_id,
        )


class CreateDatasetReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(min_length=1, max_length=128)
    source_version: str = Field(pattern=r"^v[1-9][0-9]*$", max_length=32)
    version: str = Field(pattern=r"^v[1-9][0-9]*$", max_length=32)
    dataset: dict[str, Any]
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    curation_review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    synthetic: bool

    def to_command(
        self,
        *,
        tenant_id: str,
        release_id: str,
        idempotency_key: str,
        requested_by: str,
        trace_id: str,
    ) -> CreateDatasetReleaseCommand:
        return CreateDatasetReleaseCommand(
            tenant_id=tenant_id,
            release_id=release_id,
            dataset_id=self.dataset_id,
            source_version=self.source_version,
            version=self.version,
            dataset=self.dataset,
            candidate_sha256=self.candidate_sha256,
            curation_review_sha256=self.curation_review_sha256,
            synthetic=self.synthetic,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
            trace_id=trace_id,
        )


class ReviewDatasetReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: DatasetReviewRole
    approved: bool
    notes: str = Field(min_length=1, max_length=1024)

    @field_validator("approved")
    @classmethod
    def require_approval(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("dataset review must be explicitly approved")
        return value

    def to_command(
        self,
        *,
        tenant_id: str,
        release_id: str,
        expected_revision: int,
        idempotency_key: str,
        requested_by: str,
        trace_id: str,
    ) -> ReviewDatasetReleaseCommand:
        return ReviewDatasetReleaseCommand(
            tenant_id=tenant_id,
            release_id=release_id,
            role=self.role,
            notes=self.notes,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
            trace_id=trace_id,
        )
