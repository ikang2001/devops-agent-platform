from datetime import datetime

from sqlalchemy import and_, case, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.enums import WorkflowRunStatus
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.domain.identity import validate_worker_id
from devops_agent_platform.domain.models.workflow_run import WorkflowRun
from devops_agent_platform.infrastructure.database.mappers.workflow_run import (
    WorkflowRunMapper,
)
from devops_agent_platform.infrastructure.database.models.workflow_run import (
    WorkflowRunRecord,
)


class SQLAlchemyWorkflowRunRepository:
    """使用当前事务Session持久化RCA工作流运行记录。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, workflow_run: WorkflowRun) -> None:
        """新增或更新记录，并转换唯一约束与乐观锁异常。"""
        try:
            record = await self._session.get(
                WorkflowRunRecord,
                workflow_run.workflow_run_id,
            )
            if record is None:
                record = WorkflowRunMapper.to_record(workflow_run)
                self._session.add(record)
            else:
                self._validate_update_contract(record, workflow_run)
                self._apply_mutable_fields(record, workflow_run)
            await self._session.flush()
        except StaleDataError as exc:
            raise ConflictError(
                f"Workflow run version conflict: {workflow_run.workflow_run_id}"
            ) from exc
        except IntegrityError as exc:
            raise ConflictError("Workflow run persistence conflict") from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not persist workflow run") from exc

        workflow_run.version = record.version

    async def get_by_idempotency_key_hash(
        self,
        tenant_id: str,
        idempotency_key_hash: str,
    ) -> WorkflowRun | None:
        """按租户和幂等键哈希查询唯一记录。"""
        self._validate_text("tenant_id", tenant_id, 128)
        self._validate_hash(idempotency_key_hash)
        statement = (
            select(WorkflowRunRecord)
            .where(
                WorkflowRunRecord.tenant_id == tenant_id,
                WorkflowRunRecord.idempotency_key_hash == idempotency_key_hash,
            )
            .limit(1)
        )
        return await self._load_one(
            statement,
            "Could not load workflow run by idempotency key",
        )

    async def get_active_by_incident(
        self,
        tenant_id: str,
        incident_id: str,
    ) -> WorkflowRun | None:
        """查询同一事故唯一的活跃工作流。"""
        self._validate_text("tenant_id", tenant_id, 128)
        self._validate_text("incident_id", incident_id, 64)
        statement = (
            select(WorkflowRunRecord)
            .where(
                WorkflowRunRecord.tenant_id == tenant_id,
                WorkflowRunRecord.incident_id == incident_id,
                WorkflowRunRecord.status.in_(
                    (
                        WorkflowRunStatus.PENDING.value,
                        WorkflowRunStatus.RUNNING.value,
                    )
                ),
            )
            .order_by(
                WorkflowRunRecord.created_at.desc(),
                WorkflowRunRecord.workflow_run_id.desc(),
            )
            .limit(1)
        )
        return await self._load_one(
            statement,
            "Could not load active workflow run",
        )

    async def get_by_id(
        self,
        tenant_id: str,
        workflow_run_id: str,
    ) -> WorkflowRun | None:
        """按租户边界加载工作流，避免跨租户探测运行记录。"""
        self._validate_text("tenant_id", tenant_id, 128)
        self._validate_text("workflow_run_id", workflow_run_id, 64)
        statement = (
            select(WorkflowRunRecord)
            .where(
                WorkflowRunRecord.tenant_id == tenant_id,
                WorkflowRunRecord.workflow_run_id == workflow_run_id,
            )
            .limit(1)
        )
        return await self._load_one(
            statement,
            "Could not load workflow run by id",
        )

    async def claim_for_execution(
        self,
        tenant_id: str,
        workflow_run_id: str,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> WorkflowRun | None:
        """使用单条条件更新原子获得执行租约。

        待执行任务可以首次抢占；运行中任务只有租约已到期才能被接管。条件判断
        与状态更新在数据库内完成，避免“先查询、后更新”在多实例下产生竞态。
        """
        self._validate_text("tenant_id", tenant_id, 128)
        self._validate_text("workflow_run_id", workflow_run_id, 64)
        worker_id = validate_worker_id(worker_id)
        self._validate_timestamp("now", now)
        self._validate_timestamp("lease_expires_at", lease_expires_at)
        if lease_expires_at <= now:
            raise AppValidationError("lease_expires_at must be later than now")

        statement = (
            update(WorkflowRunRecord)
            .where(
                WorkflowRunRecord.tenant_id == tenant_id,
                WorkflowRunRecord.workflow_run_id == workflow_run_id,
                or_(
                    WorkflowRunRecord.status == WorkflowRunStatus.PENDING.value,
                    and_(
                        WorkflowRunRecord.status == WorkflowRunStatus.RUNNING.value,
                        WorkflowRunRecord.lease_expires_at.is_not(None),
                        WorkflowRunRecord.lease_expires_at <= now,
                    ),
                ),
            )
            .values(
                status=WorkflowRunStatus.RUNNING.value,
                started_at=case(
                    (
                        WorkflowRunRecord.status == WorkflowRunStatus.PENDING.value,
                        now,
                    ),
                    else_=WorkflowRunRecord.started_at,
                ),
                updated_at=now,
                lease_owner=worker_id,
                lease_expires_at=lease_expires_at,
                heartbeat_at=now,
                execution_attempts=(WorkflowRunRecord.execution_attempts + 1),
                version=WorkflowRunRecord.version + 1,
            )
            .returning(WorkflowRunRecord)
            .execution_options(synchronize_session=False)
        )
        try:
            record = await self._session.scalar(statement)
        except IntegrityError as exc:
            raise ConflictError(
                f"Workflow run claim conflict: {workflow_run_id}"
            ) from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not claim workflow run") from exc

        return self._to_domain(record) if record is not None else None

    async def renew_execution_lease(
        self,
        tenant_id: str,
        workflow_run_id: str,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> WorkflowRun | None:
        """使用所有权、有效期和单调时间条件原子续租。

        任一条件不满足都不更新记录。调用方必须把空结果视为执行权丢失，不能
        继续调用外部工具或写入分析结果。
        """
        self._validate_text("tenant_id", tenant_id, 128)
        self._validate_text("workflow_run_id", workflow_run_id, 64)
        worker_id = validate_worker_id(worker_id)
        self._validate_timestamp("now", now)
        self._validate_timestamp("lease_expires_at", lease_expires_at)
        if lease_expires_at <= now:
            raise AppValidationError("lease_expires_at must be later than now")

        statement = (
            update(WorkflowRunRecord)
            .where(
                WorkflowRunRecord.tenant_id == tenant_id,
                WorkflowRunRecord.workflow_run_id == workflow_run_id,
                WorkflowRunRecord.status == WorkflowRunStatus.RUNNING.value,
                WorkflowRunRecord.lease_owner == worker_id,
                WorkflowRunRecord.lease_expires_at.is_not(None),
                WorkflowRunRecord.lease_expires_at > now,
                WorkflowRunRecord.lease_expires_at < lease_expires_at,
                WorkflowRunRecord.heartbeat_at.is_not(None),
                WorkflowRunRecord.heartbeat_at <= now,
            )
            .values(
                heartbeat_at=now,
                updated_at=now,
                lease_expires_at=lease_expires_at,
                version=WorkflowRunRecord.version + 1,
            )
            .returning(WorkflowRunRecord)
            .execution_options(synchronize_session=False)
        )
        try:
            record = await self._session.scalar(statement)
        except IntegrityError as exc:
            raise ConflictError(
                f"Workflow run heartbeat conflict: {workflow_run_id}"
            ) from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not renew workflow execution lease") from exc

        return self._to_domain(record) if record is not None else None

    async def complete_execution(
        self,
        tenant_id: str,
        workflow_run_id: str,
        worker_id: str,
        execution_attempt: int,
        target_status: WorkflowRunStatus,
        completed_at: datetime,
    ) -> WorkflowRun | None:
        """由有效租约所有者和执行代次原子写入终态。

        ``execution_attempt`` 是接管后递增的fencing代次，可以阻止旧执行器在
        新执行器接管后迟到提交。完成时同时释放租约，避免终态记录残留所有权。
        """
        self._validate_text("tenant_id", tenant_id, 128)
        self._validate_text("workflow_run_id", workflow_run_id, 64)
        worker_id = validate_worker_id(worker_id)
        self._validate_execution_attempt(execution_attempt)
        self._validate_completion_status(target_status)
        self._validate_timestamp("completed_at", completed_at)

        statement = (
            update(WorkflowRunRecord)
            .where(
                WorkflowRunRecord.tenant_id == tenant_id,
                WorkflowRunRecord.workflow_run_id == workflow_run_id,
                WorkflowRunRecord.status == WorkflowRunStatus.RUNNING.value,
                WorkflowRunRecord.lease_owner == worker_id,
                WorkflowRunRecord.execution_attempts == execution_attempt,
                WorkflowRunRecord.lease_expires_at.is_not(None),
                WorkflowRunRecord.lease_expires_at > completed_at,
                WorkflowRunRecord.heartbeat_at.is_not(None),
                WorkflowRunRecord.heartbeat_at <= completed_at,
            )
            .values(
                status=target_status.value,
                updated_at=completed_at,
                ended_at=completed_at,
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                version=WorkflowRunRecord.version + 1,
            )
            .returning(WorkflowRunRecord)
            .execution_options(synchronize_session=False)
        )
        try:
            record = await self._session.scalar(statement)
        except IntegrityError as exc:
            raise ConflictError(
                f"Workflow run completion conflict: {workflow_run_id}"
            ) from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not complete workflow execution") from exc

        return self._to_domain(record) if record is not None else None

    async def _load_one(self, statement, error_message: str) -> WorkflowRun | None:
        """执行单记录查询并统一转换数据库异常。"""
        try:
            record = await self._session.scalar(statement)
        except SQLAlchemyError as exc:
            raise PersistenceError(error_message) from exc
        return self._to_domain(record) if record is not None else None

    @staticmethod
    def _to_domain(record: WorkflowRunRecord) -> WorkflowRun:
        """恢复领域对象，并把历史脏行隔离为持久化错误。"""
        try:
            return WorkflowRunMapper.to_domain(record)
        except (AppValidationError, ValueError) as exc:
            raise PersistenceError(
                "Stored workflow run violates the domain contract"
            ) from exc

    @staticmethod
    def _validate_update_contract(
        record: WorkflowRunRecord,
        workflow_run: WorkflowRun,
    ) -> None:
        """保护版本和创建后不可修改字段。"""
        if workflow_run.version != record.version:
            raise ConflictError(
                f"Workflow run version conflict: {workflow_run.workflow_run_id}"
            )
        immutable_values = (
            ("tenant_id", record.tenant_id, workflow_run.tenant_id),
            ("incident_id", record.incident_id, workflow_run.incident_id),
            ("operator_id", record.operator_id, workflow_run.operator_id),
            (
                "idempotency_key_hash",
                record.idempotency_key_hash,
                workflow_run.idempotency_key_hash,
            ),
            ("request_hash", record.request_hash, workflow_run.request_hash),
            ("trace_id", record.trace_id, workflow_run.trace_id),
            ("created_at", record.created_at, workflow_run.created_at),
        )
        changed = [
            field_name
            for field_name, stored, incoming in immutable_values
            if stored != incoming
        ]
        if changed:
            raise ConflictError(
                f"Workflow run immutable fields changed: {', '.join(changed)}"
            )

    @staticmethod
    def _apply_mutable_fields(
        record: WorkflowRunRecord,
        workflow_run: WorkflowRun,
    ) -> None:
        """只复制状态机允许变化的字段。"""
        record.status = workflow_run.status.value
        record.updated_at = workflow_run.updated_at
        record.started_at = workflow_run.started_at
        record.ended_at = workflow_run.ended_at
        record.canceled_by = workflow_run.canceled_by
        record.cancellation_reason = workflow_run.cancellation_reason
        record.canceled_at = workflow_run.canceled_at
        record.cancellation_idempotency_key_hash = (
            workflow_run.cancellation_idempotency_key_hash
        )
        record.cancellation_request_hash = workflow_run.cancellation_request_hash
        record.cancellation_trace_id = workflow_run.cancellation_trace_id
        record.step_count = workflow_run.step_count
        record.lease_owner = workflow_run.lease_owner
        record.lease_expires_at = workflow_run.lease_expires_at
        record.heartbeat_at = workflow_run.heartbeat_at
        record.execution_attempts = workflow_run.execution_attempts

    @staticmethod
    def _validate_text(field_name: str, value: str, maximum: int) -> None:
        """校验查询键，避免无意义数据库调用。"""
        if not isinstance(value, str) or not 1 <= len(value) <= maximum:
            raise AppValidationError(
                f"{field_name} length must be between 1 and {maximum}"
            )
        if value != value.strip():
            raise AppValidationError(
                f"{field_name} must not contain surrounding whitespace"
            )
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise AppValidationError(
                f"{field_name} must not contain control characters"
            )

    @staticmethod
    def _validate_hash(value: str) -> None:
        """校验幂等键哈希格式。"""
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise AppValidationError(
                "idempotency_key_hash must be a SHA-256 hex digest"
            )

    @staticmethod
    def _validate_timestamp(field_name: str, value: datetime) -> None:
        """要求租约时间携带时区，避免数据库会话时区改变比较结果。"""
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AppValidationError(f"{field_name} must include timezone information")

    @staticmethod
    def _validate_execution_attempt(value: int) -> None:
        """校验用于隔离旧执行器的执行代次。"""
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise AppValidationError("execution_attempt must be a positive integer")

    @staticmethod
    def _validate_completion_status(value: WorkflowRunStatus) -> None:
        """只允许执行器写入成功或失败，取消由独立控制面处理。"""
        if value not in {
            WorkflowRunStatus.SUCCEEDED,
            WorkflowRunStatus.FAILED,
        }:
            raise AppValidationError("target_status must be SUCCEEDED or FAILED")
