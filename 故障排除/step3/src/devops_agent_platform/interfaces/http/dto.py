from datetime import datetime

from pydantic import BaseModel, Field

from devops_agent_platform.application.commands.alerts import ReceiveAlertCommand
from devops_agent_platform.application.commands.rca import StartRCACommand
from devops_agent_platform.domain.enums import AlertSeverity


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
            trace_id=trace_id,
        )


class StartRCARequest(BaseModel):
    """请求启动 RCA 工作流的 HTTP 请求体。"""

    tenant_id: str = Field(min_length=1, max_length=128)
    operator_id: str = Field(min_length=1, max_length=128)

    def to_command(self, incident_id: str, trace_id: str) -> StartRCACommand:
        """将 HTTP DTO 转换为应用层 Command。"""
        return StartRCACommand(
            incident_id=incident_id,
            tenant_id=self.tenant_id,
            operator_id=self.operator_id,
            trace_id=trace_id,
        )
