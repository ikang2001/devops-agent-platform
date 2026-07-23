from enum import StrEnum


class AlertSeverity(StrEnum):
    """外部告警系统允许传入的告警级别。"""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class IncidentStatus(StrEnum):
    """生产事故生命周期状态。"""

    OPEN = "OPEN"
    ANALYZING = "ANALYZING"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class WorkflowRunStatus(StrEnum):
    """单次 RCA 工作流执行状态。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class ToolInvocationStatus(StrEnum):
    """单次工具调用执行状态。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class ToolRiskLevel(StrEnum):
    """工具权限校验使用的风险等级。"""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class EvidenceType(StrEnum):
    """RCA 过程中采集的证据类型。"""

    METRIC = "METRIC"
    LOG = "LOG"
    TRACE = "TRACE"
    DEPLOYMENT = "DEPLOYMENT"
    RUNBOOK = "RUNBOOK"
    INCIDENT_HISTORY = "INCIDENT_HISTORY"
