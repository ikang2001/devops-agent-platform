from dataclasses import dataclass

from devops_agent_platform.application.commands.alerts import ReceiveAlertCommand
from devops_agent_platform.ports.repositories import AlertRepositoryPort


@dataclass(frozen=True)
class AlertIdempotencyResult:
    """告警幂等查询结果，不向上层暴露数据库记录。"""

    is_duplicate: bool
    existing_alert_id: str | None


class AlertIdempotencyService:
    """根据上游事件身份判断告警是否已经接收。

    查询只能优化重复请求的正常返回路径，不能替代数据库唯一约束。两个并发请求
    可能同时查询到“不存在”，最终仍要依靠数据库保证只有一个请求写入成功。
    """

    def __init__(self, alert_repository: AlertRepositoryPort) -> None:
        self._alert_repository = alert_repository

    async def check(
        self,
        command: ReceiveAlertCommand,
    ) -> AlertIdempotencyResult:
        """查询同一租户、来源和上游事件 ID 是否已经入库。"""
        existing = await self._alert_repository.get_by_external_event_id(
            tenant_id=command.tenant_id,
            source=command.source,
            external_event_id=command.external_event_id,
        )
        if existing is None:
            return AlertIdempotencyResult(
                is_duplicate=False,
                existing_alert_id=None,
            )
        return AlertIdempotencyResult(
            is_duplicate=True,
            existing_alert_id=existing.alert_id,
        )
