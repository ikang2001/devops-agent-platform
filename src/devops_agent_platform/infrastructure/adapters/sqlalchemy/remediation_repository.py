from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.domain.models.remediation import (
    RemediationPlan,
    RemediationStatus,
)
from devops_agent_platform.infrastructure.database.mappers.remediation import (
    RemediationPlanMapper,
)
from devops_agent_platform.infrastructure.database.models.remediation import (
    RemediationPlanRecord,
)


class SQLAlchemyRemediationPlanRepository:
    """在 UoW Session 内保存并以行锁保护修复状态迁移。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, plan: RemediationPlan) -> None:
        try:
            self._session.add(RemediationPlanMapper.to_record(plan))
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError("Remediation plan persistence conflict") from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not persist remediation plan") from exc

    async def get_by_id(
        self,
        tenant_id: str,
        remediation_plan_id: str,
    ) -> RemediationPlan | None:
        _validate_text("tenant_id", tenant_id, 128)
        _validate_text("remediation_plan_id", remediation_plan_id, 64)
        return await self._get_one(
            select(RemediationPlanRecord)
            .where(
                RemediationPlanRecord.tenant_id == tenant_id,
                RemediationPlanRecord.remediation_plan_id
                == remediation_plan_id,
            )
            .limit(1)
        )

    async def get_by_create_idempotency_key_hash(
        self,
        tenant_id: str,
        idempotency_key_hash: str,
    ) -> RemediationPlan | None:
        _validate_text("tenant_id", tenant_id, 128)
        _validate_hash(idempotency_key_hash)
        return await self._get_one(
            select(RemediationPlanRecord)
            .where(
                RemediationPlanRecord.tenant_id == tenant_id,
                RemediationPlanRecord.create_idempotency_key_hash
                == idempotency_key_hash,
            )
            .limit(1)
        )

    async def list_stale_execution(
        self,
        *,
        now: datetime,
        limit: int = 50,
    ) -> list[RemediationPlan]:
        _validate_time("now", now)
        _validate_limit(limit)
        statement = (
            select(RemediationPlanRecord)
            .where(
                RemediationPlanRecord.status
                == RemediationStatus.EXECUTING.value,
                RemediationPlanRecord.execution_lease_expires_at.is_not(None),
                RemediationPlanRecord.execution_lease_expires_at <= now,
            )
            .order_by(
                RemediationPlanRecord.execution_lease_expires_at.asc(),
                RemediationPlanRecord.remediation_plan_id.asc(),
            )
            .limit(limit)
        )
        return await self._get_many(statement)

    async def list_stale_rollback(
        self,
        *,
        now: datetime,
        limit: int = 50,
    ) -> list[RemediationPlan]:
        _validate_time("now", now)
        _validate_limit(limit)
        statement = (
            select(RemediationPlanRecord)
            .where(
                RemediationPlanRecord.status
                == RemediationStatus.ROLLING_BACK.value,
                RemediationPlanRecord.rollback_lease_expires_at.is_not(None),
                RemediationPlanRecord.rollback_lease_expires_at <= now,
            )
            .order_by(
                RemediationPlanRecord.rollback_lease_expires_at.asc(),
                RemediationPlanRecord.remediation_plan_id.asc(),
            )
            .limit(limit)
        )
        return await self._get_many(statement)

    async def replace(
        self,
        plan: RemediationPlan,
        expected_version: int,
    ) -> None:
        if (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 1
        ):
            raise AppValidationError("expected_version is invalid")
        statement = (
            select(RemediationPlanRecord)
            .where(
                RemediationPlanRecord.tenant_id == plan.tenant_id,
                RemediationPlanRecord.remediation_plan_id
                == plan.remediation_plan_id,
            )
            .with_for_update()
        )
        try:
            record = await self._session.scalar(statement)
            if record is None:
                raise ConflictError("Remediation plan no longer exists")
            if record.version != expected_version:
                raise ConflictError("Remediation plan version conflict")
            RemediationPlanMapper.apply(plan, record)
            await self._session.flush()
        except ConflictError:
            raise
        except IntegrityError as exc:
            raise ConflictError("Remediation plan persistence conflict") from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not update remediation plan") from exc

    async def _get_one(self, statement) -> RemediationPlan | None:
        try:
            record = await self._session.scalar(statement)
            return (
                RemediationPlanMapper.to_domain(record)
                if record is not None
                else None
            )
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not load remediation plan") from exc
        except AppValidationError as exc:
            raise PersistenceError(
                "Stored remediation plan violates the domain contract"
            ) from exc

    async def _get_many(self, statement) -> list[RemediationPlan]:
        try:
            records = (await self._session.scalars(statement)).all()
            return [RemediationPlanMapper.to_domain(record) for record in records]
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not list remediation plans") from exc
        except AppValidationError as exc:
            raise PersistenceError(
                "Stored remediation plan violates the domain contract"
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


def _validate_time(name: str, value: datetime) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise AppValidationError(f"{name} must include timezone")


def _validate_limit(value: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= 500
    ):
        raise AppValidationError("limit is invalid")
