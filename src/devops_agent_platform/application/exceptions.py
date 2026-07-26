from devops_agent_platform.domain.exceptions import AppException


class PersistenceError(AppException):
    """持久化基础设施执行失败，但不属于业务数据冲突。"""

    code = "PERSISTENCE_ERROR"
    status_code = 500
    default_message = "Persistence operation failed"


class ResourceBusyError(AppException):
    """共享业务资源正在被其他事务处理，调用方可以稍后重试。"""

    code = "RESOURCE_BUSY"
    status_code = 409
    default_message = "Resource is busy"


class RuntimeUnavailableError(AppException):
    """应用运行时尚未就绪，当前实例不能安全处理业务请求。"""

    code = "RUNTIME_UNAVAILABLE"
    status_code = 503
    default_message = "Application runtime is not ready"


class TicketingGatewayError(AppException):
    """外部工单系统或中间层无法返回可信提交结果。"""

    code = "TICKETING_GATEWAY_UNAVAILABLE"
    status_code = 503
    default_message = "Ticketing gateway is unavailable"


class RemediationGatewayError(AppException):
    """自动化控制器无法返回可信动作结果。"""

    code = "REMEDIATION_GATEWAY_UNAVAILABLE"
    status_code = 503
    default_message = "Remediation gateway is unavailable"


class NotificationGatewayError(AppException):
    """外部通知系统无法返回可信投递结果。"""

    code = "NOTIFICATION_GATEWAY_UNAVAILABLE"
    status_code = 503
    default_message = "Notification gateway is unavailable"


class RemediationExecutorError(AppException):
    """受控修复执行器不可用或返回了不可信结果。"""

    code = "REMEDIATION_EXECUTOR_UNAVAILABLE"
    status_code = 503
    default_message = "Remediation executor is unavailable"


class EventPublishError(AppException):
    """事件消息系统不可用或拒绝发布请求。"""

    code = "EVENT_PUBLISH_FAILED"
    status_code = 503
    default_message = "Event publishing failed"


class WorkflowLeaseLostError(AppException):
    """工作流执行器已不再拥有有效租约，必须立即停止执行。"""

    code = "WORKFLOW_LEASE_LOST"
    status_code = 409
    default_message = "Workflow execution lease was lost"


class MessageContractError(AppException):
    """消费消息不符合已支持契约，重试不会自动修复。"""

    code = "MESSAGE_CONTRACT_INVALID"
    status_code = 400
    default_message = "Message contract is invalid or unsupported"


class MessageConsumeError(AppException):
    """消息拉取、死信或offset操作失败，消费循环应退避重试。"""

    code = "MESSAGE_CONSUME_FAILED"
    status_code = 503
    default_message = "Message consumption failed"


class AgentWorkflowError(AppException):
    """受控Agent计划或工具结果违反执行契约。"""

    code = "AGENT_WORKFLOW_ERROR"
    status_code = 500
    default_message = "Agent workflow execution failed"


class ToolExecutionTimeoutError(AppException):
    """单个工具执行超过其注册定义允许的时长。"""

    code = "TOOL_EXECUTION_TIMEOUT"
    status_code = 504
    default_message = "Tool execution timed out"


class ToolExecutionError(AppException):
    """工具处理器失败或其输入输出违反统一执行契约。"""

    code = "TOOL_EXECUTION_FAILED"
    status_code = 502
    default_message = "Tool execution failed"


class PermissionDataSourceError(AppException):
    """权限数据库、缓存或IAM暂时无法提供可信授权结果。"""

    code = "PERMISSION_SOURCE_UNAVAILABLE"
    status_code = 503
    default_message = "Permission data source is unavailable"


class PreconditionRequiredError(AppException):
    """写请求缺少用于并发保护的条件版本。"""

    code = "PRECONDITION_REQUIRED"
    status_code = 428
    default_message = "A version precondition is required"


class AuthenticationServiceError(AppException):
    """外部身份认证服务暂时无法给出可信结果。"""

    code = "AUTHENTICATION_SERVICE_UNAVAILABLE"
    status_code = 503
    default_message = "Authentication service is unavailable"


class MetricsSourceError(AppException):
    """Prometheus或指标上下文数据源无法返回可信结果。"""

    code = "METRICS_SOURCE_UNAVAILABLE"
    status_code = 503
    default_message = "Metrics source is unavailable"


class LogsSourceError(AppException):
    """Loki或日志上下文数据源无法返回可信结果。"""

    code = "LOGS_SOURCE_UNAVAILABLE"
    status_code = 503
    default_message = "Logs source is unavailable"


class TracesSourceError(AppException):
    """Tempo或链路追踪上下文数据源无法返回可信结果。"""

    code = "TRACES_SOURCE_UNAVAILABLE"
    status_code = 503
    default_message = "Traces source is unavailable"


class RunbookSourceError(AppException):
    """Runbook 目录无法返回可信、完整的租户内检索结果。"""

    code = "RUNBOOK_SOURCE_UNAVAILABLE"
    status_code = 503
    default_message = "Runbook source is unavailable"


class LLMProviderError(AppException):
    """外部模型供应商无法返回可信且符合契约的结构化结果。"""

    code = "LLM_PROVIDER_UNAVAILABLE"
    status_code = 503
    default_message = "LLM provider is unavailable"
