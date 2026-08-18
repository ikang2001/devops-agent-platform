from __future__ import annotations

from typing import Protocol

from devops_agent_platform.domain.models.knowledge import KnowledgeDocument


class KnowledgeRetrieverPort(Protocol):
    async def save(self, document: KnowledgeDocument) -> None: ...

    async def search(
        self,
        tenant_id: str,
        query: object,
        *,
        top_k: int = 5,
    ) -> tuple[object, ...]: ...


class EmbeddingPort(Protocol):
    async def embed(self, text: str) -> tuple[float, ...]: ...
