from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.remediation import RemediationPlan


@dataclass(frozen=True)
class RemediationActionDefinition:
    """由运维配置维护、不能由 LLM 或 HTTP 正文创建的动作定义。"""

    action_key: str
    rollback_action_key: str
    risk_level: ToolRiskLevel
    expected_effect: str
    allowed_targets: tuple[str, ...]

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("action_key", self.action_key, 128),
            ("rollback_action_key", self.rollback_action_key, 128),
            ("expected_effect", self.expected_effect, 1024),
        ):
            _validate_text(name, value, maximum)
        if self.action_key == self.rollback_action_key:
            raise AppValidationError(
                "rollback_action_key must differ from action_key"
            )
        if not isinstance(self.risk_level, ToolRiskLevel):
            raise AppValidationError("risk_level is invalid")
        if (
            not isinstance(self.allowed_targets, tuple)
            or not self.allowed_targets
            or len(set(self.allowed_targets)) != len(self.allowed_targets)
        ):
            raise AppValidationError("allowed_targets is invalid")
        for target in self.allowed_targets:
            _validate_text("allowed_target", target, 256)

    def require_target(self, target: str) -> None:
        if target not in self.allowed_targets:
            raise AppValidationError(
                "target is not allowed for remediation action"
            )


class RemediationActionCatalogPort(Protocol):
    """按稳定动作键读取受信动作定义。"""

    def get(self, action_key: str) -> RemediationActionDefinition:
        """返回动作定义或抛出未找到异常。"""
        ...


@dataclass(frozen=True)
class RemediationExecutionRequest:
    """发送给隔离执行器的结构化请求，不包含任意命令文本或 URL。"""

    remediation_plan_id: str
    tenant_id: str
    incident_id: str
    workflow_run_id: str
    action_key: str
    rollback_action_key: str
    target: str
    idempotency_key: str
    trace_id: str

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("remediation_plan_id", self.remediation_plan_id, 64),
            ("tenant_id", self.tenant_id, 128),
            ("incident_id", self.incident_id, 64),
            ("workflow_run_id", self.workflow_run_id, 64),
            ("action_key", self.action_key, 128),
            ("rollback_action_key", self.rollback_action_key, 128),
            ("target", self.target, 256),
            ("idempotency_key", self.idempotency_key, 128),
            ("trace_id", self.trace_id, 128),
        ):
            _validate_text(name, value, maximum)


@dataclass(frozen=True)
class RemediationExecutionOutcome:
    """隔离执行器的规范化、可审计结果。"""

    succeeded: bool
    summary: str

    def __post_init__(self) -> None:
        if not isinstance(self.succeeded, bool):
            raise AppValidationError("succeeded must be a boolean")
        _validate_multiline("summary", self.summary, 4096)


class RemediationExecutorPort(Protocol):
    """执行 dry-run、动作和回滚的隔离边界。"""

    async def dry_run(
        self,
        request: RemediationExecutionRequest,
    ) -> RemediationExecutionOutcome:
        """验证动作与回滚动作，但不得改变目标状态。"""
        ...

    async def execute(
        self,
        request: RemediationExecutionRequest,
    ) -> RemediationExecutionOutcome:
        """运行预注册动作，或抛出稳定依赖异常。"""
        ...

    async def rollback(
        self,
        request: RemediationExecutionRequest,
    ) -> RemediationExecutionOutcome:
        """运行预注册回滚动作，或抛出稳定依赖异常。"""
        ...


class RemediationPlanRepositoryPort(Protocol):
    """修复计划的事务内持久化端口。"""

    async def save(self, plan: RemediationPlan) -> None:
        """保存新计划。"""
        ...

    async def get_by_id(
        self,
        tenant_id: str,
        remediation_plan_id: str,
    ) -> RemediationPlan | None:
        """按租户与计划 ID 读取。"""
        ...

    async def get_by_create_idempotency_key_hash(
        self,
        tenant_id: str,
        idempotency_key_hash: str,
    ) -> RemediationPlan | None:
        """按创建幂等摘要读取。"""
        ...

    async def replace(
        self,
        plan: RemediationPlan,
        expected_version: int,
    ) -> None:
        """使用乐观版本替换状态。"""
        ...

    async def list_stale_execution(
        self,
        *,
        now: datetime,
        limit: int = 50,
    ) -> list[RemediationPlan]:
        """列出执行租约已过期的计划。"""
        ...

    async def list_stale_rollback(
        self,
        *,
        now: datetime,
        limit: int = 50,
    ) -> list[RemediationPlan]:
        """列出回滚租约已过期的计划。"""
        ...


def _validate_text(name: str, value: str, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise AppValidationError(f"{name} is invalid")


def _validate_multiline(name: str, value: str, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(
            (ord(character) < 32 and character != "\n")
            or ord(character) == 127
            for character in value
        )
    ):
        raise AppValidationError(f"{name} is invalid")
