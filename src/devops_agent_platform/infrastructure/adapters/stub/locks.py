from devops_agent_platform.domain.exceptions import NotImplementedInSkeleton


class StubIncidentCorrelationLock:
    """不伪造跨实例并发保护的事故关联锁占位实现。"""

    async def acquire(self, tenant_id: str, service_name: str) -> None:
        raise NotImplementedInSkeleton(
            "StubIncidentCorrelationLock is unavailable in skeleton mode"
        )
