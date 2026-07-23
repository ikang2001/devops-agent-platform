from typing import Protocol


class IncidentCorrelationLockPort(Protocol):
    """串行化同一租户、同一服务事故关联决策的事务锁端口。"""

    async def acquire(self, tenant_id: str, service_name: str) -> None:
        """获取锁；锁必须随当前事务提交或回滚自动释放。"""
        ...
