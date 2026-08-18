from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.exceptions import ConflictError
from devops_agent_platform.domain.models.topology import (
    DependencyEdge,
    ResourceNode,
    ServiceNode,
    TopologyGraph,
    TopologyNodeKind,
    TopologySource,
)
from devops_agent_platform.infrastructure.database.models.topology import (
    TopologyEdgeRecord,
    TopologyNodeRecord,
)


class SQLAlchemyTopologyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_service(self, node: ServiceNode) -> None:
        await self._save_node(node)

    async def save_resource(self, node: ResourceNode) -> None:
        await self._save_node(node)

    async def save_edge(self, edge: DependencyEdge) -> None:
        try:
            self._session.add(
                TopologyEdgeRecord(
                    edge_id=edge.edge_id,
                    tenant_id=edge.tenant_id,
                    source_node_id=edge.source_node_id,
                    target_node_id=edge.target_node_id,
                    relation=edge.relation,
                    source=edge.source.value,
                    confidence=edge.confidence,
                    observed_at=edge.observed_at,
                    ttl_seconds=edge.ttl_seconds,
                    metadata_json=json.dumps(edge.metadata, ensure_ascii=False),
                )
            )
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError("topology edge persistence conflict") from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("could not persist topology edge") from exc

    async def _save_node(self, node: ServiceNode | ResourceNode) -> None:
        record = TopologyNodeRecord(
            node_id=node.node_id,
            tenant_id=node.tenant_id,
            node_kind=node.kind.value,
            service_name=getattr(node, "service_name", None),
            resource_type=getattr(node, "resource_type", None),
            resource_name=getattr(node, "resource_name", None),
            environment=node.environment,
            source=node.source.value,
            confidence=node.confidence,
            observed_at=node.observed_at,
            ttl_seconds=node.ttl_seconds,
            metadata_json=json.dumps(node.metadata, ensure_ascii=False),
        )
        try:
            self._session.add(record)
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError("topology node persistence conflict") from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("could not persist topology node") from exc

    async def get_graph(
        self, tenant_id: str, *, environment: str | None = None, max_depth: int = 8
    ) -> TopologyGraph:
        try:
            node_statement = select(TopologyNodeRecord).where(
                TopologyNodeRecord.tenant_id == tenant_id
            )
            if environment is not None:
                node_statement = node_statement.where(
                    TopologyNodeRecord.environment == environment
                )
            nodes = (await self._session.scalars(node_statement)).all()
            edges = (
                await self._session.scalars(
                    select(TopologyEdgeRecord).where(
                        TopologyEdgeRecord.tenant_id == tenant_id
                    )
                )
            ).all()
        except SQLAlchemyError as exc:
            raise PersistenceError("could not load topology") from exc
        now = datetime.now().astimezone()
        domain_nodes = tuple(
            node
            for node in (_node_to_domain(record) for record in nodes)
            if not node.is_expired(now)
        )
        active_node_ids = {node.node_id for node in domain_nodes}
        domain_edges = tuple(
            edge
            for edge in (_edge_to_domain(record) for record in edges)
            if not edge.is_expired(now)
            and edge.source_node_id in active_node_ids
            and edge.target_node_id in active_node_ids
        )
        return TopologyGraph(
            tenant_id=tenant_id,
            nodes=domain_nodes,
            edges=domain_edges,
            max_depth=max_depth,
        )


class SQLAlchemyTopologyRepositoryStore:
    """以 Session 工厂为边界的拓扑仓储，供请求级只读工具使用。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save_service(self, node: ServiceNode) -> None:
        async with self._session_factory() as session:
            await SQLAlchemyTopologyRepository(session).save_service(node)
            await session.commit()

    async def save_resource(self, node: ResourceNode) -> None:
        async with self._session_factory() as session:
            await SQLAlchemyTopologyRepository(session).save_resource(node)
            await session.commit()

    async def save_edge(self, edge: DependencyEdge) -> None:
        async with self._session_factory() as session:
            await SQLAlchemyTopologyRepository(session).save_edge(edge)
            await session.commit()

    async def get_graph(
        self,
        tenant_id: str,
        *,
        environment: str | None = None,
        max_depth: int = 8,
    ) -> TopologyGraph:
        async with self._session_factory() as session:
            return await SQLAlchemyTopologyRepository(session).get_graph(
                tenant_id, environment=environment, max_depth=max_depth
            )


def _metadata(value: str) -> tuple[tuple[str, str], ...]:
    return tuple(tuple(item) for item in json.loads(value or "[]"))


def _node_to_domain(record: TopologyNodeRecord) -> ServiceNode | ResourceNode:
    common = dict(
        node_id=record.node_id,
        tenant_id=record.tenant_id,
        environment=record.environment,
        source=TopologySource(record.source),
        confidence=record.confidence,
        observed_at=record.observed_at,
        ttl_seconds=record.ttl_seconds,
        metadata=_metadata(record.metadata_json),
    )
    if record.node_kind == TopologyNodeKind.SERVICE.value:
        return ServiceNode(service_name=record.service_name or record.node_id, **common)
    return ResourceNode(
        resource_type=record.resource_type or "resource",
        resource_name=record.resource_name or record.node_id,
        **common,
    )


def _edge_to_domain(record: TopologyEdgeRecord) -> DependencyEdge:
    return DependencyEdge(
        edge_id=record.edge_id,
        tenant_id=record.tenant_id,
        source_node_id=record.source_node_id,
        target_node_id=record.target_node_id,
        relation=record.relation,
        source=TopologySource(record.source),
        confidence=record.confidence,
        observed_at=record.observed_at,
        ttl_seconds=record.ttl_seconds,
        metadata=_metadata(record.metadata_json),
    )
