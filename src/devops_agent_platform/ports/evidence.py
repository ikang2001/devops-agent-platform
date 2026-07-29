from typing import Protocol

from devops_agent_platform.domain.models.evidence import Evidence


class EvidenceRepositoryPort(Protocol):
    """RCA 证据持久化端口。

    应用层只依赖该协议，不直接感知 SQLAlchemy 或具体数据库。实现方必须保证
    查询始终带租户条件，避免跨租户证据被误读。
    """

    async def save(self, evidence: Evidence) -> None:
        """保存单条结构化证据。

        具体实现只负责写入当前事务，不应自行提交。重复证据必须转换为稳定的
        ConflictError，不能把数据库驱动异常泄露到应用层。
        """
        ...

    async def get_by_id(
        self,
        tenant_id: str,
        evidence_id: str,
    ) -> Evidence | None:
        """按租户和证据 ID 精确加载证据。"""
        ...

    async def list_by_workflow_run(
        self,
        tenant_id: str,
        workflow_run_id: str,
        *,
        limit: int = 100,
    ) -> list[Evidence]:
        """按工作流运行加载有限数量证据，调用方必须显式接受分页上限。"""
        ...

    async def list_by_ids(
        self,
        tenant_id: str,
        evidence_ids: tuple[str, ...],
    ) -> list[Evidence]:
        """按租户精确加载有限且唯一的证据 ID 集合。"""
        ...
