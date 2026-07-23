from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.enums import (
    RunbookChangeAction,
    RunbookStatus,
)
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
    ResourceNotFound,
)
from devops_agent_platform.domain.models.runbook import Runbook
from devops_agent_platform.infrastructure.database.mappers.outbox import (
    OutboxEventMapper,
)
from devops_agent_platform.infrastructure.database.mappers.runbook import (
    RunbookMapper,
)
from devops_agent_platform.infrastructure.database.models.runbook import (
    RunbookHeadRecord,
    RunbookOperationRecord,
    RunbookRecord,
)
from devops_agent_platform.ports.runbook_admin import (
    RunbookMutation,
    RunbookMutationResult,
)


class SQLAlchemyRunbookAdminStore:
    """原子持久化 Runbook 状态、幂等结果和审计 Outbox。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """保存 Session 工厂，禁止管理请求之间共享 Session。"""
        if not callable(session_factory):
            raise AppValidationError("session_factory must be callable")
        self._session_factory = session_factory

    async def apply(
        self,
        mutation: RunbookMutation,
        audit_event: OutboxEvent,
    ) -> RunbookMutationResult:
        """应用一次草稿或发布变更，并恢复并发幂等结果。"""
        self._validate_contract(mutation, audit_event)
        try:
            return await self._apply_once(mutation, audit_event)
        except IntegrityError as exc:
            recovered = await self._recover_after_integrity_error(
                mutation
            )
            if recovered is not None:
                return recovered
            raise ConflictError("Runbook mutation conflict") from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not mutate runbook") from exc

    async def _apply_once(
        self,
        mutation: RunbookMutation,
        audit_event: OutboxEvent,
    ) -> RunbookMutationResult:
        """在单个短事务内完成查重、锁定、状态切换和审计写入。"""
        async with self._session_factory() as session:
            async with session.begin():
                existing = await self._find_operation(
                    session,
                    mutation.tenant_id,
                    mutation.idempotency_key_hash,
                )
                if existing is not None:
                    return self._existing_result(existing, mutation)

                head = await self._get_or_create_head(
                    session,
                    mutation,
                )
                # 等待聚合锁时相同请求可能已经提交，再查一次可恢复为幂等成功。
                existing = await self._find_operation(
                    session,
                    mutation.tenant_id,
                    mutation.idempotency_key_hash,
                )
                if existing is not None:
                    return self._existing_result(existing, mutation)
                if head.revision != mutation.expected_revision:
                    raise ConflictError("Runbook revision conflict")
                if mutation.occurred_at < head.updated_at:
                    raise ConflictError("Runbook mutation time is stale")

                target = await self._find_version(
                    session,
                    mutation.tenant_id,
                    mutation.runbook_key,
                    mutation.version,
                )

                if mutation.action is RunbookChangeAction.SAVE_DRAFT:
                    target = await self._save_draft(
                        session,
                        mutation,
                        head,
                        target,
                    )
                else:
                    target = await self._publish(
                        session,
                        mutation,
                        head,
                        target,
                    )

                operation = RunbookOperationRecord(
                    operation_id=mutation.operation_id,
                    tenant_id=mutation.tenant_id,
                    runbook_key=mutation.runbook_key,
                    version=mutation.version,
                    idempotency_key_hash=mutation.idempotency_key_hash,
                    request_hash=mutation.request_hash,
                    action=mutation.action.value,
                    result_runbook_id=target.runbook_id,
                    result_status=target.status,
                    result_revision=target.revision,
                    requested_by=mutation.requested_by,
                    trace_id=mutation.trace_id,
                    occurred_at=mutation.occurred_at,
                )
                session.add(operation)
                session.add(OutboxEventMapper.to_record(audit_event))
                await session.flush()
                return self._result(operation, is_duplicate=False)

    async def _get_or_create_head(
        self,
        session: AsyncSession,
        mutation: RunbookMutation,
    ) -> RunbookHeadRecord:
        """锁定聚合头；旧数据首次写入时根据有限版本集合建立指针。"""
        head = await session.scalar(
            select(RunbookHeadRecord)
            .where(
                RunbookHeadRecord.tenant_id == mutation.tenant_id,
                RunbookHeadRecord.runbook_key == mutation.runbook_key,
            )
            .with_for_update()
            .limit(1)
        )
        if head is not None:
            return head

        records = (
            await session.scalars(
                select(RunbookRecord)
                .where(
                    RunbookRecord.tenant_id == mutation.tenant_id,
                    RunbookRecord.runbook_key == mutation.runbook_key,
                )
                .with_for_update()
                .limit(102)
            )
        ).all()
        if len(records) > 100:
            raise PersistenceError(
                "Runbook aggregate exceeds the version limit"
            )
        drafts = [
            item
            for item in records
            if item.status == RunbookStatus.DRAFT.value
        ]
        published = [
            item
            for item in records
            if item.status == RunbookStatus.PUBLISHED.value
        ]
        if len(drafts) > 1 or len(published) > 1:
            raise PersistenceError(
                "Stored runbook aggregate has ambiguous active versions"
            )
        latest_updated_at = max(
            (item.updated_at for item in records),
            default=mutation.occurred_at,
        )
        head = RunbookHeadRecord(
            tenant_id=mutation.tenant_id,
            runbook_key=mutation.runbook_key,
            revision=max(
                (item.revision for item in records),
                default=0,
            ),
            draft_runbook_id=(
                drafts[0].runbook_id if drafts else None
            ),
            published_runbook_id=(
                published[0].runbook_id if published else None
            ),
            updated_at=latest_updated_at,
        )
        session.add(head)
        await session.flush()
        return head

    @staticmethod
    async def _find_version(
        session: AsyncSession,
        tenant_id: str,
        runbook_key: str,
        version: str,
    ) -> RunbookRecord | None:
        """锁定明确业务版本，禁止模糊选择最新版本。"""
        return await session.scalar(
            select(RunbookRecord)
            .where(
                RunbookRecord.tenant_id == tenant_id,
                RunbookRecord.runbook_key == runbook_key,
                RunbookRecord.version == version,
            )
            .with_for_update()
            .limit(1)
        )

    async def _save_draft(
        self,
        session: AsyncSession,
        mutation: RunbookMutation,
        head: RunbookHeadRecord,
        target: RunbookRecord | None,
    ) -> RunbookRecord:
        """创建新草稿或更新尚未发布的现有草稿。"""
        if target is None:
            if head.draft_runbook_id is not None:
                raise ConflictError(
                    "Another runbook draft is already active"
                )
            if mutation.new_runbook_id is None:
                raise AppValidationError(
                    "SAVE_DRAFT requires new_runbook_id"
                )
            draft = self._build_draft(
                mutation,
                mutation.new_runbook_id,
                revision=head.revision + 1,
            )
            target = RunbookMapper.to_record(draft)
            session.add(target)
            await session.flush()
        else:
            trusted = self._to_trusted_domain(target)
            if trusted.status is not RunbookStatus.DRAFT:
                raise ConflictError(
                    "Published runbook version is immutable"
                )
            if head.draft_runbook_id not in {
                None,
                trusted.runbook_id,
            }:
                raise ConflictError(
                    "Another runbook draft is already active"
                )
            if mutation.occurred_at < trusted.updated_at:
                raise ConflictError("Runbook mutation time is stale")
            draft = self._build_draft(
                mutation,
                trusted.runbook_id,
                revision=head.revision + 1,
            )
            self._copy_domain(target, draft)
        head.revision += 1
        head.draft_runbook_id = target.runbook_id
        head.updated_at = mutation.occurred_at
        await session.flush()
        return target

    async def _publish(
        self,
        session: AsyncSession,
        mutation: RunbookMutation,
        head: RunbookHeadRecord,
        target: RunbookRecord | None,
    ) -> RunbookRecord:
        """归档旧生效版本，再发布指定草稿。"""
        if target is None:
            raise ResourceNotFound("Runbook draft not found")
        trusted = self._to_trusted_domain(target)
        if trusted.status is not RunbookStatus.DRAFT:
            raise ConflictError("Only draft runbooks can be published")
        if head.draft_runbook_id != trusted.runbook_id:
            raise ConflictError("Runbook is not the active draft")
        if mutation.occurred_at < trusted.updated_at:
            raise ConflictError("Runbook mutation time is stale")

        active = None
        if head.published_runbook_id is not None:
            active = await session.scalar(
                select(RunbookRecord)
                .where(
                    RunbookRecord.runbook_id
                    == head.published_runbook_id,
                    RunbookRecord.tenant_id == mutation.tenant_id,
                    RunbookRecord.runbook_key == mutation.runbook_key,
                )
                .with_for_update()
                .limit(1)
            )
            if active is None:
                raise PersistenceError(
                    "Runbook head references a missing published version"
                )
        if active is not None:
            active_domain = self._to_trusted_domain(active)
            if mutation.occurred_at < active_domain.updated_at:
                raise ConflictError("Runbook mutation time is stale")
            active.status = RunbookStatus.ARCHIVED.value
            active.revision = head.revision + 1
            active.updated_at = mutation.occurred_at
            # 先释放部分唯一索引，再把目标切为 PUBLISHED。
            await session.flush()

        target.status = RunbookStatus.PUBLISHED.value
        target.revision = head.revision + 1
        target.published_at = mutation.occurred_at
        target.updated_at = mutation.occurred_at
        head.revision += 1
        head.draft_runbook_id = None
        head.published_runbook_id = target.runbook_id
        head.updated_at = mutation.occurred_at
        await session.flush()
        return target

    @staticmethod
    def _build_draft(
        mutation: RunbookMutation,
        runbook_id: str,
        *,
        revision: int,
    ) -> Runbook:
        """从已校验 Mutation 构造领域草稿并再次执行领域约束。"""
        if (
            mutation.service_name is None
            or mutation.title is None
            or mutation.summary is None
            or mutation.priority is None
        ):
            raise AppValidationError(
                "SAVE_DRAFT mutation is missing content"
            )
        return Runbook(
            runbook_id=runbook_id,
            runbook_key=mutation.runbook_key,
            tenant_id=mutation.tenant_id,
            service_name=mutation.service_name,
            title=mutation.title,
            summary=mutation.summary,
            version=mutation.version,
            status=RunbookStatus.DRAFT,
            revision=revision,
            priority=mutation.priority,
            steps=mutation.steps,
            tags=mutation.tags,
            published_at=None,
            updated_at=mutation.occurred_at,
        )

    @staticmethod
    def _copy_domain(
        target: RunbookRecord,
        source: Runbook,
    ) -> None:
        """把更新后的草稿字段复制到已锁定 ORM 行。"""
        mapped = RunbookMapper.to_record(source)
        for field_name in (
            "service_name",
            "title",
            "summary",
            "priority",
            "steps_json",
            "tags_json",
            "revision",
            "updated_at",
        ):
            setattr(target, field_name, getattr(mapped, field_name))

    @staticmethod
    def _to_trusted_domain(record: RunbookRecord) -> Runbook:
        """把数据库脏状态转换为稳定持久化异常。"""
        try:
            return RunbookMapper.to_domain(record)
        except (AppValidationError, ValueError) as exc:
            raise PersistenceError(
                "Stored runbook violates the domain contract"
            ) from exc

    async def _recover_after_integrity_error(
        self,
        mutation: RunbookMutation,
    ) -> RunbookMutationResult | None:
        """提交竞态后使用新 Session 查询幂等操作结果。"""
        try:
            async with self._session_factory() as session:
                operation = await self._find_operation(
                    session,
                    mutation.tenant_id,
                    mutation.idempotency_key_hash,
                )
        except SQLAlchemyError as exc:
            raise PersistenceError(
                "Could not recover runbook idempotency result"
            ) from exc
        if operation is None:
            return None
        return self._existing_result(operation, mutation)

    @staticmethod
    async def _find_operation(
        session: AsyncSession,
        tenant_id: str,
        idempotency_key_hash: str,
    ) -> RunbookOperationRecord | None:
        """按租户和幂等摘要读取唯一管理结果。"""
        return await session.scalar(
            select(RunbookOperationRecord)
            .where(
                RunbookOperationRecord.tenant_id == tenant_id,
                RunbookOperationRecord.idempotency_key_hash
                == idempotency_key_hash,
            )
            .limit(1)
        )

    def _existing_result(
        self,
        operation: RunbookOperationRecord,
        mutation: RunbookMutation,
    ) -> RunbookMutationResult:
        """区分同请求重放与幂等键误复用。"""
        if operation.request_hash != mutation.request_hash:
            raise ConflictError(
                "Idempotency key was used for another runbook request"
            )
        return self._result(operation, is_duplicate=True)

    @staticmethod
    def _result(
        operation: RunbookOperationRecord,
        *,
        is_duplicate: bool,
    ) -> RunbookMutationResult:
        """把持久化操作转换为稳定应用结果。"""
        return RunbookMutationResult(
            operation_id=operation.operation_id,
            runbook_id=operation.result_runbook_id,
            tenant_id=operation.tenant_id,
            runbook_key=operation.runbook_key,
            version=operation.version,
            status=RunbookStatus(operation.result_status),
            revision=operation.result_revision,
            trace_id=operation.trace_id,
            is_duplicate=is_duplicate,
        )

    @staticmethod
    def _validate_contract(
        mutation: RunbookMutation,
        audit_event: OutboxEvent,
    ) -> None:
        """防止不匹配的状态变更和审计事件进入同一事务。"""
        if not isinstance(mutation, RunbookMutation):
            raise AppValidationError(
                "mutation must be a RunbookMutation"
            )
        if not isinstance(mutation.action, RunbookChangeAction):
            raise AppValidationError(
                "mutation action must be a RunbookChangeAction"
            )
        if not isinstance(audit_event, OutboxEvent):
            raise AppValidationError(
                "audit_event must be an OutboxEvent"
            )
        if (
            audit_event.tenant_id != mutation.tenant_id
            or audit_event.aggregate_id != mutation.operation_id
            or audit_event.trace_id != mutation.trace_id
            or audit_event.occurred_at != mutation.occurred_at
        ):
            raise AppValidationError(
                "audit event does not match runbook mutation"
            )
        if mutation.action is RunbookChangeAction.SAVE_DRAFT:
            if (
                mutation.new_runbook_id is None
                or mutation.content_hash is None
                or mutation.service_name is None
                or mutation.title is None
                or mutation.summary is None
                or mutation.priority is None
                or not mutation.steps
            ):
                raise AppValidationError(
                    "SAVE_DRAFT mutation requires complete content"
                )
        elif (
            mutation.new_runbook_id is not None
            or mutation.content_hash is not None
            or mutation.service_name is not None
            or mutation.title is not None
            or mutation.summary is not None
            or mutation.priority is not None
            or mutation.steps
            or mutation.tags
        ):
            raise AppValidationError(
                "PUBLISH mutation contains draft content"
            )
