from dataclasses import dataclass
from typing import Protocol

from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.runbook import Runbook


@dataclass(frozen=True)
class RunbookSearchResult:
    """一次有界 Runbook 检索结果。"""

    items: tuple[Runbook, ...]
    possibly_truncated: bool

    def __post_init__(self) -> None:
        """拒绝错误适配器返回可变或不受控对象。"""
        if (
            not isinstance(self.items, tuple)
            or not all(isinstance(item, Runbook) for item in self.items)
        ):
            raise AppValidationError("items must contain only Runbook")
        if not isinstance(self.possibly_truncated, bool):
            raise AppValidationError(
                "possibly_truncated must be a boolean"
            )


class RunbookSearchPort(Protocol):
    """按租户和可信服务目标检索已发布 Runbook 的端口。"""

    async def search(
        self,
        tenant_id: str,
        service_name: str,
        limit: int,
    ) -> RunbookSearchResult:
        """返回服务专属及租户通用手册，不允许跨租户回退。"""
        ...
