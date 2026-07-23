from enum import StrEnum
from typing import Protocol

from devops_agent_platform.application.commands.workflow_execution import (
    ExecuteRCAWorkflowCommand,
)
from devops_agent_platform.domain.models.evidence import Evidence
from devops_agent_platform.domain.models.rca_report import RCAReport


class RCAReportGeneratorPort(Protocol):
    """受控工作流生成结构化 RCA 报告的端口。"""

    async def generate(
        self,
        command: ExecuteRCAWorkflowCommand,
        evidence: tuple[Evidence, ...],
    ) -> RCAReport:
        """根据当前执行代次的 Evidence 异步生成不可变报告。"""
        ...


class LLMReportGenerationOutcome(StrEnum):
    """LLM 报告生成的固定低基数运维结果。"""

    SUCCESS = "SUCCESS"
    FALLBACK_TIMEOUT = "FALLBACK_TIMEOUT"
    FALLBACK_INVALID_RESPONSE = "FALLBACK_INVALID_RESPONSE"
    FALLBACK_PROVIDER_ERROR = "FALLBACK_PROVIDER_ERROR"
    FALLBACK_CIRCUIT_OPEN = "FALLBACK_CIRCUIT_OPEN"
    CANCELLED = "CANCELLED"


class LLMReportGenerationObserverPort(Protocol):
    """接收 LLM 报告结果与耗时的非关键路径观察端口。"""

    def observe_llm_report(
        self,
        outcome: LLMReportGenerationOutcome,
        duration_seconds: float,
    ) -> None:
        """记录一次固定分类结果；实现不得依赖业务动态标签。"""
        ...


class RCAReportRepositoryPort(Protocol):
    """RCA 报告快照持久化端口。"""

    async def save(self, report: RCAReport) -> None:
        """在当前事务中保存报告。"""
        ...

    async def get_by_workflow_run(
        self,
        tenant_id: str,
        workflow_run_id: str,
    ) -> RCAReport | None:
        """按租户和工作流读取报告快照。"""
        ...
