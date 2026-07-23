from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from devops_agent_platform.application.commands.alerts import ReceiveAlertCommand
from devops_agent_platform.application.commands.incidents import (
    CloseIncidentCommand,
    ResolveIncidentCommand,
)
from devops_agent_platform.application.commands.rca import (
    CancelRCAWorkflowCommand,
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
from devops_agent_platform.domain.enums import (
    AlertSeverity,
    TicketDecision,
)


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
