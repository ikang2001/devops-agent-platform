from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.enums import WorkflowRunStatus
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.infrastructure.database.models.evidence import (
    EvidenceRecord,
)
from devops_agent_platform.infrastructure.database.models.tool_invocation import (
    ToolInvocationRecord,
)
from devops_agent_platform.infrastructure.database.models.workflow_run import (
    WorkflowRunRecord,
)

_TERMINAL_STATUSES = (
    WorkflowRunStatus.SUCCEEDED.value,
    WorkflowRunStatus.FAILED.value,
    WorkflowRunStatus.CANCELED.value,
)


class SQLAlchemyAuditRetentionStore:
    """使用短事务分批清理终态工作流的审计子记录。"""

    MAX_BATCH_SIZE = 1000

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def purge_batch(
        self,
        *,
        cutoff: datetime,
        purged_at: datetime,
        limit: int,
    ) -> int:
        """锁定有限父记录，删除两个子表并原子推进清理水位。"""
        self._validate_timestamp("cutoff", cutoff)
        self._validate_timestamp("purged_at", purged_at)
        if purged_at < cutoff:
            raise AppValidationError("purged_at must not be earlier than cutoff")
        self._validate_limit(limit)

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    workflow_ids = list(
                        await session.scalars(
                            self.build_candidate_statement(
                                cutoff=cutoff,
                                limit=limit,
                            )
                        )
                    )
                    if not workflow_ids:
                        return 0

                    await session.execute(
                        delete(EvidenceRecord).where(
                            EvidenceRecord.workflow_run_id.in_(workflow_ids)
                        )
                    )
                    await session.execute(
                        delete(ToolInvocationRecord).where(
                            ToolInvocationRecord.workflow_run_id.in_(
                                workflow_ids
                            )
                        )
                    )
                    await session.execute(
                        update(WorkflowRunRecord)
                        .where(
                            WorkflowRunRecord.workflow_run_id.in_(workflow_ids)
                        )
                        .values(
                            audit_purged_at=purged_at,
                            version=WorkflowRunRecord.version + 1,
                        )
                    )
                    return len(workflow_ids)
        except SQLAlchemyError as exc:
            raise PersistenceError(
                "Could not purge RCA audit records"
            ) from exc

    @staticmethod
    def build_candidate_statement(
        *,
        cutoff: datetime,
        limit: int,
    ):
        """构造支持多清理实例并行分片的候选父记录查询。"""
        return (
            select(WorkflowRunRecord.workflow_run_id)
            .where(
                WorkflowRunRecord.status.in_(_TERMINAL_STATUSES),
                WorkflowRunRecord.ended_at < cutoff,
                WorkflowRunRecord.audit_purged_at.is_(None),
            )
            .order_by(
                WorkflowRunRecord.ended_at.asc(),
                WorkflowRunRecord.workflow_run_id.asc(),
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )

    @classmethod
    def _validate_limit(cls, limit: int) -> None:
        """限制单批父记录数量，避免形成长事务和大范围行锁。"""
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= cls.MAX_BATCH_SIZE
        ):
            raise AppValidationError(
                f"limit must be between 1 and {cls.MAX_BATCH_SIZE}"
            )

    @staticmethod
    def _validate_timestamp(field_name: str, value: datetime) -> None:
        """要求时间携带时区，防止清理边界随数据库时区漂移。"""
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AppValidationError(
                f"{field_name} must include timezone information"
            )
