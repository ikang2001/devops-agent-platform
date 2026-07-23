from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.domain.models.rca_report import RCAReport
from devops_agent_platform.infrastructure.database.mappers.rca_report import (
    RCAReportMapper,
)
from devops_agent_platform.infrastructure.database.models.rca_report import (
    RCAReportRecord,
)


class SQLAlchemyRCAReportRepository:
    """基于 AsyncSession 的不可变 RCA 报告仓储。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, report: RCAReport) -> None:
        """写入报告但不提交，由完成用例统一控制事务。"""
        try:
            self._session.add(RCAReportMapper.to_record(report))
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                f"RCA report persistence conflict: {report.report_id}"
            ) from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not persist RCA report") from exc

    async def get_by_workflow_run(
        self,
        tenant_id: str,
        workflow_run_id: str,
    ) -> RCAReport | None:
        """按租户和工作流加载唯一报告快照。"""
        self._validate_text("tenant_id", tenant_id, 128)
        self._validate_text("workflow_run_id", workflow_run_id, 64)
        statement = (
            select(RCAReportRecord)
            .where(
                RCAReportRecord.tenant_id == tenant_id,
                RCAReportRecord.workflow_run_id == workflow_run_id,
            )
            .limit(1)
        )
        try:
            record = await self._session.scalar(statement)
            return (
                RCAReportMapper.to_domain(record)
                if record is not None
                else None
            )
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not load RCA report") from exc
        except (AppValidationError, ValueError) as exc:
            raise PersistenceError(
                "Stored RCA report violates the domain contract"
            ) from exc

    @staticmethod
    def _validate_text(field_name: str, value: str, maximum: int) -> None:
        """校验租户查询键，避免空值、歧义空白和控制字符。"""
        if not isinstance(value, str) or not 1 <= len(value) <= maximum:
            raise AppValidationError(
                f"{field_name} length must be between 1 and {maximum}"
            )
        if value != value.strip():
            raise AppValidationError(
                f"{field_name} must not contain surrounding whitespace"
            )
        if any(
            ord(character) < 32 or ord(character) == 127
            for character in value
        ):
            raise AppValidationError(
                f"{field_name} must not contain control characters"
            )
