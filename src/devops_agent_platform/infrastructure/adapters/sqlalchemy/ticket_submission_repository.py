from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.enums import TicketSubmissionStatus
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.domain.models.ticket_submission import TicketSubmission
from devops_agent_platform.infrastructure.database.mappers.ticket_submission import (
    TicketSubmissionMapper,
)
from devops_agent_platform.infrastructure.database.models.ticket_submission import (
    TicketSubmissionRecord,
)


class SQLAlchemyTicketSubmissionRepository:
    """使用当前 UoW Session 持久化外部工单提交请求。"""

    MAX_LIST_LIMIT = 500

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, submission: TicketSubmission) -> None:
        """写入并 flush，确保唯一键冲突在当前用例内暴露。"""
        try:
            self._session.add(TicketSubmissionMapper.to_record(submission))
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError("Ticket submission persistence conflict") from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not persist ticket submission") from exc

    async def get_by_idempotency_key_hash(
        self,
        tenant_id: str,
        idempotency_key_hash: str,
    ) -> TicketSubmission | None:
        """按租户和幂等摘要恢复已提交请求。"""
        self._validate_text("tenant_id", tenant_id, 128)
        self._validate_hash(idempotency_key_hash)
        return await self._get_one(
            select(TicketSubmissionRecord)
            .where(
                TicketSubmissionRecord.tenant_id == tenant_id,
                TicketSubmissionRecord.idempotency_key_hash == idempotency_key_hash,
            )
            .limit(1)
        )

    async def get_by_draft_and_target(
        self,
        tenant_id: str,
        ticket_draft_id: str,
        target_system: str,
    ) -> TicketSubmission | None:
        """按草稿和目标系统读取唯一提交请求。"""
        self._validate_text("tenant_id", tenant_id, 128)
        self._validate_text("ticket_draft_id", ticket_draft_id, 64)
        self._validate_text("target_system", target_system, 64)
        return await self._get_one(
            select(TicketSubmissionRecord)
            .where(
                TicketSubmissionRecord.tenant_id == tenant_id,
                TicketSubmissionRecord.ticket_draft_id == ticket_draft_id,
                TicketSubmissionRecord.target_system == target_system,
            )
            .limit(1)
        )

    async def get_by_id(
        self,
        tenant_id: str,
        ticket_submission_id: str,
    ) -> TicketSubmission | None:
        """按租户和提交请求标识加载记录。"""
        self._validate_text("tenant_id", tenant_id, 128)
        self._validate_text(
            "ticket_submission_id",
            ticket_submission_id,
            64,
        )
        return await self._get_one(
            select(TicketSubmissionRecord)
            .where(
                TicketSubmissionRecord.tenant_id == tenant_id,
                TicketSubmissionRecord.ticket_submission_id == ticket_submission_id,
            )
            .limit(1)
        )

    async def list_by_workflow(
        self,
        tenant_id: str,
        workflow_run_id: str,
        *,
        limit: int = 50,
    ) -> list[TicketSubmission]:
        """按租户和工作流读取有限提交状态快照。"""
        self._validate_text("tenant_id", tenant_id, 128)
        self._validate_text("workflow_run_id", workflow_run_id, 64)
        self._validate_limit(limit)
        statement = (
            select(TicketSubmissionRecord)
            .where(
                TicketSubmissionRecord.tenant_id == tenant_id,
                TicketSubmissionRecord.workflow_run_id == workflow_run_id,
            )
            .order_by(
                TicketSubmissionRecord.requested_at.desc(),
                TicketSubmissionRecord.ticket_submission_id.desc(),
            )
            .limit(limit)
        )
        try:
            result = await self._session.scalars(statement)
            return [TicketSubmissionMapper.to_domain(record) for record in result.all()]
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not list ticket submissions") from exc
        except AppValidationError as exc:
            raise PersistenceError(
                "Stored ticket submission violates the domain contract"
            ) from exc

    async def get_by_result_idempotency_key_hash(
        self,
        tenant_id: str,
        idempotency_key_hash: str,
    ) -> TicketSubmission | None:
        """按结果回填幂等摘要恢复已提交终态。"""
        self._validate_text("tenant_id", tenant_id, 128)
        self._validate_hash(idempotency_key_hash)
        return await self._get_one(
            select(TicketSubmissionRecord)
            .where(
                TicketSubmissionRecord.tenant_id == tenant_id,
                TicketSubmissionRecord.result_idempotency_key_hash
                == idempotency_key_hash,
            )
            .limit(1)
        )

    async def apply_result(
        self,
        submission: TicketSubmission,
        expected_version: int,
    ) -> None:
        """使用状态与版本谓词保证外部结果只记录一次。"""
        if submission.status not in {
            TicketSubmissionStatus.SUBMITTED,
            TicketSubmissionStatus.FAILED,
        }:
            raise AppValidationError("ticket submission result must be terminal")
        if submission.version != expected_version + 1:
            raise AppValidationError("result version does not follow expected_version")
        statement = (
            update(TicketSubmissionRecord)
            .where(
                TicketSubmissionRecord.ticket_submission_id
                == submission.ticket_submission_id,
                TicketSubmissionRecord.tenant_id == submission.tenant_id,
                TicketSubmissionRecord.status == TicketSubmissionStatus.REQUESTED.value,
                TicketSubmissionRecord.version == expected_version,
            )
            .values(
                status=submission.status.value,
                version=submission.version,
                external_ticket_id=submission.external_ticket_id,
                external_ticket_url=submission.external_ticket_url,
                failure_reason=submission.failure_reason,
                completed_by=submission.completed_by,
                completed_at=submission.completed_at,
                result_idempotency_key_hash=(submission.result_idempotency_key_hash),
                result_request_hash=submission.result_request_hash,
                result_trace_id=submission.result_trace_id,
            )
            .execution_options(synchronize_session=False)
        )
        try:
            result = await self._session.execute(statement)
            if result.rowcount != 1:
                raise ConflictError("Ticket submission result version conflict")
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError("Ticket submission result conflict") from exc
        except SQLAlchemyError as exc:
            raise PersistenceError(
                "Could not persist ticket submission result"
            ) from exc

    async def _get_one(self, statement) -> TicketSubmission | None:
        """执行单行查询并统一映射数据库异常。"""
        try:
            record = await self._session.scalar(statement)
            return (
                TicketSubmissionMapper.to_domain(record) if record is not None else None
            )
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not load ticket submission") from exc
        except AppValidationError as exc:
            raise PersistenceError(
                "Stored ticket submission violates the domain contract"
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

    @classmethod
    def _validate_limit(cls, value: int) -> None:
        """限制列表读取规模，避免状态页把异常数据量拉进内存。"""
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= cls.MAX_LIST_LIMIT
        ):
            raise AppValidationError(
                f"limit must be between 1 and {cls.MAX_LIST_LIMIT}"
            )
