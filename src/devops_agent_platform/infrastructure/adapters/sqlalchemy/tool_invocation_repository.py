from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.domain.models.tool_invocation import ToolInvocation
from devops_agent_platform.infrastructure.database.mappers.tool_invocation import (
    ToolInvocationMapper,
)
from devops_agent_platform.infrastructure.database.models.tool_invocation import (
    ToolInvocationRecord,
)


class SQLAlchemyToolInvocationRepository:
    """基于 AsyncSession 的工具调用审计仓储。"""

    MAX_LIST_LIMIT = 500

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, invocation: ToolInvocation) -> None:
        """写入审计记录但不提交，由 UnitOfWork 统一控制事务。"""
        record = ToolInvocationMapper.to_record(invocation)
        try:
            self._session.add(record)
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "Tool invocation persistence conflict: "
                f"{invocation.invocation_id}"
            ) from exc
        except SQLAlchemyError as exc:
            raise PersistenceError(
                "Could not persist tool invocation"
            ) from exc

    async def list_by_workflow_run(
        self,
        tenant_id: str,
        workflow_run_id: str,
        *,
        limit: int = 100,
    ) -> list[ToolInvocation]:
        """按租户和工作流查询有限调用记录，禁止无界读取。"""
        self._validate_text("tenant_id", tenant_id, 128)
        self._validate_text("workflow_run_id", workflow_run_id, 64)
        self._validate_limit(limit)
        statement = (
            select(ToolInvocationRecord)
            .where(
                ToolInvocationRecord.tenant_id == tenant_id,
                ToolInvocationRecord.workflow_run_id == workflow_run_id,
            )
            .order_by(
                ToolInvocationRecord.started_at.asc(),
                ToolInvocationRecord.invocation_id.asc(),
            )
            .limit(limit)
        )
        try:
            result = await self._session.scalars(statement)
            return [
                ToolInvocationMapper.to_domain(record)
                for record in result.all()
            ]
        except SQLAlchemyError as exc:
            raise PersistenceError(
                "Could not list tool invocations"
            ) from exc
        except AppValidationError as exc:
            raise PersistenceError(
                "Stored tool invocation violates the domain contract"
            ) from exc

    @staticmethod
    def _validate_text(field_name: str, value: str, maximum: int) -> None:
        """校验租户查询键，避免空值和首尾空白造成误查询。"""
        if not isinstance(value, str) or not 1 <= len(value) <= maximum:
            raise AppValidationError(
                f"{field_name} length must be between 1 and {maximum}"
            )
        if value != value.strip():
            raise AppValidationError(
                f"{field_name} must not contain surrounding whitespace"
            )

    @classmethod
    def _validate_limit(cls, value: int) -> None:
        """限制单次查询数量，防止审计接口造成大结果集读取。"""
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= cls.MAX_LIST_LIMIT
        ):
            raise AppValidationError(
                f"limit must be between 1 and {cls.MAX_LIST_LIMIT}"
            )
