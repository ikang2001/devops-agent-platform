from sqlalchemy import case, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from devops_agent_platform.application.exceptions import RunbookSourceError
from devops_agent_platform.domain.enums import RunbookStatus
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.infrastructure.database.mappers.runbook import (
    RunbookMapper,
)
from devops_agent_platform.infrastructure.database.models.runbook import (
    RunbookRecord,
)
from devops_agent_platform.ports.runbooks import RunbookSearchResult


class SQLAlchemyRunbookSearch:
    """从关系库执行租户隔离、服务优先的有界 Runbook 检索。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        if not callable(session_factory):
            raise AppValidationError("session_factory must be callable")
        self._session_factory = session_factory

    async def search(
        self,
        tenant_id: str,
        service_name: str,
        limit: int,
    ) -> RunbookSearchResult:
        """先返回服务专属手册，再返回同租户通用手册。"""
        self._validate_text("tenant_id", tenant_id, 128)
        self._validate_text("service_name", service_name, 256)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 50
        ):
            raise AppValidationError("limit must be between 1 and 50")

        exact_service_first = case(
            (RunbookRecord.service_name == service_name, 0),
            else_=1,
        )
        statement = (
            select(RunbookRecord)
            .where(
                RunbookRecord.tenant_id == tenant_id,
                RunbookRecord.status == RunbookStatus.PUBLISHED.value,
                RunbookRecord.service_name.in_((service_name, "*")),
            )
            .order_by(
                exact_service_first,
                RunbookRecord.priority.desc(),
                RunbookRecord.updated_at.desc(),
                RunbookRecord.runbook_id,
            )
            .limit(limit + 1)
        )
        try:
            async with self._session_factory() as session:
                records = (await session.scalars(statement)).all()
            items = tuple(RunbookMapper.to_domain(record) for record in records[:limit])
        except (SQLAlchemyError, AppValidationError) as exc:
            raise RunbookSourceError("Could not load trusted runbooks") from exc
        return RunbookSearchResult(
            items=items,
            possibly_truncated=len(records) > limit,
        )

    @staticmethod
    def _validate_text(
        field_name: str,
        value: str,
        maximum: int,
    ) -> None:
        """在构造 SQL 前拒绝空值、控制字符和歧义空白。"""
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise AppValidationError(f"{field_name} is invalid")
