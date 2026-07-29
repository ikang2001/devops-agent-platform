from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.domain.models.rca_feedback import RCAFeedback
from devops_agent_platform.infrastructure.database.mappers.rca_feedback import (
    RCAFeedbackMapper,
)
from devops_agent_platform.infrastructure.database.models.rca_feedback import (
    RCAFeedbackRecord,
)


class SQLAlchemyRCAFeedbackRepository:
    """使用当前 UoW Session 保存和查询不可变反馈。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, feedback: RCAFeedback) -> None:
        try:
            self._session.add(RCAFeedbackMapper.to_record(feedback))
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError("RCA feedback persistence conflict") from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not persist RCA feedback") from exc

    async def get_by_idempotency_key_hash(
        self,
        tenant_id: str,
        idempotency_key_hash: str,
    ) -> RCAFeedback | None:
        _validate_text("tenant_id", tenant_id, 128)
        _validate_hash(idempotency_key_hash)
        statement = (
            select(RCAFeedbackRecord)
            .where(
                RCAFeedbackRecord.tenant_id == tenant_id,
                RCAFeedbackRecord.idempotency_key_hash == idempotency_key_hash,
            )
            .limit(1)
        )
        return await self._get_one(statement)

    async def get_by_id(
        self,
        tenant_id: str,
        feedback_id: str,
    ) -> RCAFeedback | None:
        _validate_text("tenant_id", tenant_id, 128)
        _validate_text("feedback_id", feedback_id, 64)
        statement = (
            select(RCAFeedbackRecord)
            .where(
                RCAFeedbackRecord.tenant_id == tenant_id,
                RCAFeedbackRecord.feedback_id == feedback_id,
            )
            .limit(1)
        )
        return await self._get_one(statement)

    async def list_by_workflow_run(
        self,
        tenant_id: str,
        workflow_run_id: str,
        limit: int,
    ) -> list[RCAFeedback]:
        _validate_text("tenant_id", tenant_id, 128)
        _validate_text("workflow_run_id", workflow_run_id, 64)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise AppValidationError("limit must be between 1 and 100")
        statement = (
            select(RCAFeedbackRecord)
            .where(
                RCAFeedbackRecord.tenant_id == tenant_id,
                RCAFeedbackRecord.workflow_run_id == workflow_run_id,
            )
            .order_by(
                RCAFeedbackRecord.created_at.desc(),
                RCAFeedbackRecord.feedback_id.desc(),
            )
            .limit(limit)
        )
        try:
            records = list((await self._session.scalars(statement)).all())
            return [RCAFeedbackMapper.to_domain(record) for record in records]
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not load RCA feedback") from exc
        except AppValidationError as exc:
            raise PersistenceError(
                "Stored RCA feedback violates the domain contract"
            ) from exc

    async def _get_one(self, statement) -> RCAFeedback | None:
        try:
            record = await self._session.scalar(statement)
            return RCAFeedbackMapper.to_domain(record) if record is not None else None
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not load RCA feedback") from exc
        except AppValidationError as exc:
            raise PersistenceError(
                "Stored RCA feedback violates the domain contract"
            ) from exc


def _validate_text(name: str, value: str, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise AppValidationError(f"{name} is invalid")


def _validate_hash(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise AppValidationError("idempotency_key_hash is invalid")
