from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.enums import TicketDraftStatus
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.domain.models.ticket_draft import TicketDraft
from devops_agent_platform.infrastructure.database.mappers.ticket_draft import (
    TicketDraftMapper,
)
from devops_agent_platform.infrastructure.database.models.ticket_draft import (
    TicketDraftRecord,
)


class SQLAlchemyTicketDraftRepository:
    """使用当前 UoW Session 持久化不可变工单草稿。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, draft: TicketDraft) -> None:
        """写入并 flush，提交权仍属于 UnitOfWork。"""
        try:
            self._session.add(TicketDraftMapper.to_record(draft))
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError("Ticket draft persistence conflict") from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not persist ticket draft") from exc

    async def get_by_workflow_run(
        self,
        tenant_id: str,
        workflow_run_id: str,
    ) -> TicketDraft | None:
        """按租户和工作流加载唯一草稿。"""
        self._validate_text("tenant_id", tenant_id, 128)
        self._validate_text(
            "workflow_run_id",
            workflow_run_id,
            64,
        )
        return await self._get_one(
            select(TicketDraftRecord)
            .where(
                TicketDraftRecord.tenant_id == tenant_id,
                TicketDraftRecord.workflow_run_id == workflow_run_id,
            )
            .limit(1)
        )

    async def get_by_idempotency_key_hash(
        self,
        tenant_id: str,
        idempotency_key_hash: str,
    ) -> TicketDraft | None:
        """按租户和幂等摘要恢复已创建草稿。"""
        self._validate_text("tenant_id", tenant_id, 128)
        if (
            not isinstance(idempotency_key_hash, str)
            or len(idempotency_key_hash) != 64
            or any(
                character not in "0123456789abcdef"
                for character in idempotency_key_hash
            )
        ):
            raise AppValidationError("idempotency_key_hash is invalid")
        return await self._get_one(
            select(TicketDraftRecord)
            .where(
                TicketDraftRecord.tenant_id == tenant_id,
                TicketDraftRecord.idempotency_key_hash == idempotency_key_hash,
            )
            .limit(1)
        )

    async def get_by_decision_idempotency_key_hash(
        self,
        tenant_id: str,
        idempotency_key_hash: str,
    ) -> TicketDraft | None:
        """按人工确认幂等摘要恢复终态草稿。"""
        self._validate_text("tenant_id", tenant_id, 128)
        self._validate_hash(idempotency_key_hash)
        return await self._get_one(
            select(TicketDraftRecord)
            .where(
                TicketDraftRecord.tenant_id == tenant_id,
                TicketDraftRecord.decision_idempotency_key_hash == idempotency_key_hash,
            )
            .limit(1)
        )

    async def apply_decision(
        self,
        draft: TicketDraft,
        expected_version: int,
    ) -> None:
        """使用状态与版本谓词保证并发审批只有一个成功。"""
        if draft.status not in {
            TicketDraftStatus.APPROVED,
            TicketDraftStatus.REJECTED,
        }:
            raise AppValidationError("decision draft must be terminal")
        if draft.version != expected_version + 1:
            raise AppValidationError(
                "decision version does not follow expected_version"
            )
        statement = (
            update(TicketDraftRecord)
            .where(
                TicketDraftRecord.ticket_draft_id == draft.ticket_draft_id,
                TicketDraftRecord.tenant_id == draft.tenant_id,
                TicketDraftRecord.status == TicketDraftStatus.DRAFT.value,
                TicketDraftRecord.version == expected_version,
            )
            .values(
                status=draft.status.value,
                version=draft.version,
                decided_by=draft.decided_by,
                decision_reason=draft.decision_reason,
                decided_at=draft.decided_at,
                decision_idempotency_key_hash=(draft.decision_idempotency_key_hash),
                decision_request_hash=draft.decision_request_hash,
                decision_trace_id=draft.decision_trace_id,
            )
            .execution_options(synchronize_session=False)
        )
        try:
            result = await self._session.execute(statement)
            if result.rowcount != 1:
                raise ConflictError("Ticket draft decision version conflict")
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError("Ticket draft decision conflict") from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not persist ticket draft decision") from exc

    async def _get_one(self, statement) -> TicketDraft | None:
        """执行单行查询并统一映射数据库异常。"""
        try:
            record = await self._session.scalar(statement)
            return TicketDraftMapper.to_domain(record) if record is not None else None
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not load ticket draft") from exc
        except AppValidationError as exc:
            raise PersistenceError(
                "Stored ticket draft violates the domain contract"
            ) from exc

    @staticmethod
    def _validate_text(
        field_name: str,
        value: str,
        maximum: int,
    ) -> None:
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise AppValidationError(f"{field_name} is invalid")

    @staticmethod
    def _validate_hash(value: str) -> None:
        """校验查询使用的 SHA-256 小写十六进制摘要。"""
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise AppValidationError("idempotency_key_hash is invalid")
