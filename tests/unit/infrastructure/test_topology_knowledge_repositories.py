from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from devops_agent_platform.application.services.knowledge_service import KnowledgeQuery
from devops_agent_platform.application.services.topology_service import TopologyService
from devops_agent_platform.domain.models.knowledge import (
    KnowledgeDocument,
    KnowledgeDocumentType,
    KnowledgeReviewStatus,
)
from devops_agent_platform.domain.models.topology import (
    DependencyEdge,
    ServiceNode,
    TopologySource,
)
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyKnowledgeRetriever,
    SQLAlchemyTopologyRepository,
    SQLAlchemyTopologyRepositoryStore,
)
from devops_agent_platform.infrastructure.database.base import Base


@pytest.fixture
async def session_factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'graph.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def service_node(
    service_name: str,
    *,
    tenant_id: str = "tenant-a",
    observed_at: datetime | None = None,
) -> ServiceNode:
    return ServiceNode(
        node_id=f"service:{service_name}",
        tenant_id=tenant_id,
        service_name=service_name,
        environment="prod",
        source=TopologySource.STATIC,
        observed_at=observed_at or datetime.now(UTC),
    )


def knowledge_document(
    document_id: str,
    *,
    tenant_id: str = "tenant-a",
    status: KnowledgeReviewStatus = KnowledgeReviewStatus.PUBLISHED,
) -> KnowledgeDocument:
    created_at = datetime.now(UTC)
    return KnowledgeDocument(
        document_id=document_id,
        tenant_id=tenant_id,
        document_type=KnowledgeDocumentType.KNOWN_ERROR,
        title="Payment timeout known error",
        service_name="payment-service",
        body="payment-service timeout fingerprint fp-payment",
        error_fingerprint="fp-payment",
        review_status=status,
        created_at=created_at,
        published_at=created_at if status is KnowledgeReviewStatus.PUBLISHED else None,
    )


async def test_sqlalchemy_topology_repository_is_tenant_and_ttl_scoped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        repository = SQLAlchemyTopologyRepository(session)
        await repository.save_service(service_node("checkout-service"))
        await repository.save_service(service_node("payment-service"))
        await repository.save_edge(
            DependencyEdge(
                edge_id="edge:checkout-payment",
                tenant_id="tenant-a",
                source_node_id="service:checkout-service",
                target_node_id="service:payment-service",
                observed_at=now,
            )
        )
        await repository.save_service(
            service_node("other-service", tenant_id="tenant-b")
        )
        await repository.save_service(
            service_node(
                "expired-service",
                observed_at=now - timedelta(hours=2),
            )
        )
        await session.commit()

    async with session_factory() as session:
        graph = await SQLAlchemyTopologyRepository(session).get_graph(
            "tenant-a", environment="prod"
        )

    assert {node.node_id for node in graph.nodes} == {
        "service:checkout-service",
        "service:payment-service",
    }
    assert len(graph.edges) == 1


async def test_topology_session_store_and_knowledge_retriever_are_runtime_adapters(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    topology = TopologyService(SQLAlchemyTopologyRepositoryStore(session_factory))
    await topology.register_static_service(service_node("checkout-service"))
    graph = await topology.query("tenant-a", environment="prod")
    assert (
        graph.node_by_id["service:checkout-service"].service_name
        == "checkout-service"
    )

    retriever = SQLAlchemyKnowledgeRetriever(session_factory)
    await retriever.save(knowledge_document("doc-published"))
    await retriever.save(
        knowledge_document("doc-draft", status=KnowledgeReviewStatus.DRAFT)
    )
    results = await retriever.search(
        "tenant-a",
        KnowledgeQuery(service="payment-service", error_fingerprint="fp-payment"),
    )

    assert [item.document.document_id for item in results] == ["doc-published"]
