from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from devops_agent_platform.application.exceptions import MetricsSourceError
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ResourceNotFound,
)
from devops_agent_platform.infrastructure.database.models.incident import (
    IncidentRecord,
)
from devops_agent_platform.ports.observability import ObservabilityTarget


class SQLAlchemyMetricsTargetResolver:
    """从事故事实表解析租户隔离的可信可观测目标。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        if not callable(session_factory):
            raise AppValidationError("session_factory must be callable")
        self._session_factory = session_factory

    async def resolve(
        self,
        tenant_id: str,
        incident_id: str,
    ) -> ObservabilityTarget:
        """按租户和事故标识加载服务名。"""
        ObservabilityTarget._validate_text("tenant_id", tenant_id, 128)
        ObservabilityTarget._validate_text(
            "incident_id",
            incident_id,
            64,
        )
        statement = (
            select(IncidentRecord.service_name)
            .where(
                IncidentRecord.tenant_id == tenant_id,
                IncidentRecord.incident_id == incident_id,
            )
            .limit(1)
        )
        try:
            async with self._session_factory() as session:
                service_name = await session.scalar(statement)
        except SQLAlchemyError as exc:
            raise MetricsSourceError(
                "Could not resolve metrics target"
            ) from exc
        if service_name is None:
            raise ResourceNotFound("Incident metrics target not found")
        return ObservabilityTarget(
            tenant_id=tenant_id,
            incident_id=incident_id,
            service_name=service_name,
        )


SQLAlchemyObservabilityTargetResolver = SQLAlchemyMetricsTargetResolver
