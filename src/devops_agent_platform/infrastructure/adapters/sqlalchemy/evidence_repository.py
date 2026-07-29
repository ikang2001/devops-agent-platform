from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.domain.models.evidence import Evidence
from devops_agent_platform.infrastructure.database.mappers.evidence import (
    EvidenceMapper,
)
from devops_agent_platform.infrastructure.database.models.evidence import (
    EvidenceRecord,
)


class SQLAlchemyEvidenceRepository:
    """基于 SQLAlchemy AsyncSession 的 Evidence 仓储适配器。"""

    MAX_LIST_LIMIT = 500

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, evidence: Evidence) -> None:
        """写入单条证据，并把数据库异常转换为稳定应用异常。

        该方法只执行 flush，不提交事务。调用方必须通过 UnitOfWork 统一提交，
        保证证据写入和工作流状态变更可以整体成功或整体回滚。
        """
        record = EvidenceMapper.to_record(evidence)
        try:
            self._session.add(record)
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                f"Evidence persistence conflict: {evidence.evidence_id}"
            ) from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not persist evidence") from exc

    async def get_by_id(
        self,
        tenant_id: str,
        evidence_id: str,
    ) -> Evidence | None:
        """按租户和证据 ID 查询单条记录。"""
        self._validate_text("tenant_id", tenant_id, 128)
        self._validate_text("evidence_id", evidence_id, 64)

        statement = (
            select(EvidenceRecord)
            .where(
                EvidenceRecord.tenant_id == tenant_id,
                EvidenceRecord.evidence_id == evidence_id,
            )
            .limit(1)
        )
        return await self._load_one(statement, "Could not load evidence")

    async def list_by_workflow_run(
        self,
        tenant_id: str,
        workflow_run_id: str,
        *,
        limit: int = 100,
    ) -> list[Evidence]:
        """按工作流运行加载有限证据列表。

        查询条件必须包含租户和工作流，且强制 limit 上限，避免一次 RCA 报告接口
        在证据量异常时造成全表扫描或把大结果集拉进内存。
        """
        self._validate_text("tenant_id", tenant_id, 128)
        self._validate_text("workflow_run_id", workflow_run_id, 64)
        self._validate_limit(limit)

        statement = (
            select(EvidenceRecord)
            .where(
                EvidenceRecord.tenant_id == tenant_id,
                EvidenceRecord.workflow_run_id == workflow_run_id,
            )
            .order_by(
                EvidenceRecord.collected_at.asc(),
                EvidenceRecord.evidence_id.asc(),
            )
            .limit(limit)
        )
        try:
            result = await self._session.scalars(statement)
            return [
                EvidenceMapper.to_domain(record)
                for record in result.all()
            ]
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not list evidence") from exc
        except AppValidationError as exc:
            raise PersistenceError(
                "Stored evidence violates the domain contract"
            ) from exc

    async def list_by_ids(
        self,
        tenant_id: str,
        evidence_ids: tuple[str, ...],
    ) -> list[Evidence]:
        """在租户边界内一次加载报告引用的有限证据集合。"""
        self._validate_text("tenant_id", tenant_id, 128)
        if (
            not isinstance(evidence_ids, tuple)
            or not 1 <= len(evidence_ids) <= 100
            or len(set(evidence_ids)) != len(evidence_ids)
        ):
            raise AppValidationError(
                "evidence_ids must contain between 1 and 100 unique items"
            )
        for evidence_id in evidence_ids:
            self._validate_text("evidence_id", evidence_id, 64)

        statement = (
            select(EvidenceRecord)
            .where(
                EvidenceRecord.tenant_id == tenant_id,
                EvidenceRecord.evidence_id.in_(evidence_ids),
            )
            .order_by(EvidenceRecord.evidence_id.asc())
        )
        try:
            result = await self._session.scalars(statement)
            return [EvidenceMapper.to_domain(record) for record in result.all()]
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not list evidence by IDs") from exc
        except AppValidationError as exc:
            raise PersistenceError(
                "Stored evidence violates the domain contract"
            ) from exc

    async def _load_one(self, statement, error_message: str) -> Evidence | None:
        """执行单条查询并统一转换数据库异常。"""
        try:
            record = await self._session.scalar(statement)
            return (
                EvidenceMapper.to_domain(record)
                if record is not None
                else None
            )
        except SQLAlchemyError as exc:
            raise PersistenceError(error_message) from exc
        except AppValidationError as exc:
            raise PersistenceError(
                "Stored evidence violates the domain contract"
            ) from exc

    @staticmethod
    def _validate_text(field_name: str, value: str, maximum: int) -> None:
        """校验查询键，阻止空查询和带歧义的首尾空白。"""
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
        """限制分页大小，防止调用方绕过服务层造成大结果集读取。"""
        if isinstance(value, bool) or not isinstance(value, int):
            raise AppValidationError("limit must be an integer")
        if not 1 <= value <= cls.MAX_LIST_LIMIT:
            raise AppValidationError(
                f"limit must be between 1 and {cls.MAX_LIST_LIMIT}"
            )
