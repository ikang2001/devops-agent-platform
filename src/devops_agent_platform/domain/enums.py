from enum import StrEnum


class AlertSeverity(StrEnum):
    """外部告警系统允许传入的告警级别。"""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        """返回告警级别的显式业务顺序。"""
        return {
            AlertSeverity.INFO: 10,
            AlertSeverity.WARNING: 20,
            AlertSeverity.CRITICAL: 30,
        }[self]


class IncidentStatus(StrEnum):
    """生产事故生命周期状态。"""

    OPEN = "OPEN"
    ANALYZING = "ANALYZING"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class IncidentCreationAction(StrEnum):
    """告警进入事故聚合策略后的处理动作。"""

    CREATE = "CREATE"
    ATTACH = "ATTACH"
    IGNORE = "IGNORE"


class IncidentDecisionReason(StrEnum):
    """事故创建策略返回的稳定原因码。"""

    BELOW_SEVERITY_THRESHOLD = "BELOW_SEVERITY_THRESHOLD"
    ACTIVE_INCIDENT_MATCHED = "ACTIVE_INCIDENT_MATCHED"
    NO_MATCHING_ACTIVE_INCIDENT = "NO_MATCHING_ACTIVE_INCIDENT"


class OutboxStatus(StrEnum):
    """Outbox 消息的持久化投递状态。"""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class OutboxWorkerState(StrEnum):
    """Outbox后台Worker的运行状态。"""

    STOPPED = "STOPPED"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"


class AuditRetentionWorkerState(StrEnum):
    """审计留存清理 Worker 的运行状态。"""

    STOPPED = "STOPPED"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"


class RCAConsumerWorkerState(StrEnum):
    """RCA消息消费Worker的运行状态。"""

    STOPPED = "STOPPED"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"


class TicketSubmissionConsumerWorkerState(StrEnum):
    """外部工单提交消费Worker的运行状态。"""

    STOPPED = "STOPPED"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"


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


class ToolPermissionChangeAction(StrEnum):
    """权限管理写服务允许执行的变更动作。"""

    SET = "SET"
    REVOKE = "REVOKE"


class EvidenceType(StrEnum):
    """RCA 过程中采集的证据类型。"""

    METRIC = "METRIC"
    LOG = "LOG"
    TRACE = "TRACE"
    DEPLOYMENT = "DEPLOYMENT"
    RUNBOOK = "RUNBOOK"


class RunbookStatus(StrEnum):
    """Runbook 生命周期状态，只有已发布版本允许进入 RCA 证据。"""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class RunbookChangeAction(StrEnum):
    """Runbook 管理写用例支持的审计动作。"""

    SAVE_DRAFT = "SAVE_DRAFT"
    PUBLISH = "PUBLISH"
    INCIDENT_HISTORY = "INCIDENT_HISTORY"


class RCAConclusionStatus(StrEnum):
    """RCA 报告中根因结论的确认状态。"""

    UNDETERMINED = "UNDETERMINED"
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"


class RCAFeedbackVerdict(StrEnum):
    """人工复核对 RCA 结论的稳定判定。"""

    ACCEPTED = "ACCEPTED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"


class TicketDraftStatus(StrEnum):
    """本地工单草稿及人工确认状态。"""

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class TicketDecision(StrEnum):
    """人工确认允许提交的明确决策。"""

    APPROVE = "APPROVE"
    REJECT = "REJECT"


class TicketSubmissionStatus(StrEnum):
    """外部工单提交链路的本地请求状态。"""

    REQUESTED = "REQUESTED"
    SUBMITTED = "SUBMITTED"
    FAILED = "FAILED"


class TicketPriority(StrEnum):
    """从事故严重度确定性映射出的工单优先级。"""

    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
