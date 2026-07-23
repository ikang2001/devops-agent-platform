from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.enums import ToolPermissionChangeAction
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
    ResourceNotFound,
)
from devops_agent_platform.infrastructure.database.mappers.outbox import (
    OutboxEventMapper,
)
from devops_agent_platform.infrastructure.database.models.tool_permission import (
    ToolPermissionGrantRecord,
    ToolPermissionOperationRecord,
    ToolPermissionTagRecord,
)
from devops_agent_platform.ports.tool_permissions import (
    ToolPermissionMutation,
    ToolPermissionMutationResult,
    ToolPermissionSnapshot,
)

_MAX_PERMISSION_TAGS = 256


class SQLAlchemyToolPermissionAdminStore:
    """原子持久化权限快照、幂等结果和审计Outbox。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """保存Session工厂，禁止跨管理请求共享Session。"""
        if not callable(session_factory):
            raise AppValidationError("session_factory must be callable")
        self._session_factory = session_factory

    async def get_current(
        self,
        tenant_id: str,
        operator_id: str,
    ) -> ToolPermissionSnapshot:
        """读取当前权限快照，没有活动授权时返回最新版本和空权限。"""
        self._validate_text("tenant_id", tenant_id, 128)
        self._validate_text("operator_id", operator_id, 128)
        try:
            async with self._session_factory() as session:
                latest_version = (
                    await session.scalar(
                        select(func.max(ToolPermissionGrantRecord.version)).where(
                            ToolPermissionGrantRecord.tenant_id == tenant_id,
                            ToolPermissionGrantRecord.operator_id == operator_id,
                        )
                    )
                    or 0
                )
                active = await session.scalar(
                    select(ToolPermissionGrantRecord)
                    .where(
                        ToolPermissionGrantRecord.tenant_id == tenant_id,
                        ToolPermissionGrantRecord.operator_id == operator_id,
                        ToolPermissionGrantRecord.revoked_at.is_(None),
                    )
                    .limit(1)
                )
                if active is None:
                    return ToolPermissionSnapshot(
                        tenant_id=tenant_id,
                        operator_id=operator_id,
                        grant_id=None,
                        version=latest_version,
                        permission_tags=(),
                        expires_at=None,
                        active=False,
                    )
                if active.version != latest_version:
                    raise PersistenceError(
                        "Active permission grant is not the latest version"
                    )
                tags = (
                    await session.scalars(
                        select(ToolPermissionTagRecord.permission_tag)
                        .where(ToolPermissionTagRecord.grant_id == active.grant_id)
                        .order_by(ToolPermissionTagRecord.permission_tag.asc())
                        .limit(_MAX_PERMISSION_TAGS + 1)
                    )
                ).all()
                if len(tags) > _MAX_PERMISSION_TAGS:
                    raise PersistenceError("Active permission grant has too many tags")
                return ToolPermissionSnapshot(
                    tenant_id=active.tenant_id,
                    operator_id=active.operator_id,
                    grant_id=active.grant_id,
                    version=active.version,
                    permission_tags=tuple(tags),
                    expires_at=active.expires_at,
                    active=True,
                )
        except PersistenceError:
            raise
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not load tool permissions") from exc

    async def apply(
        self,
        mutation: ToolPermissionMutation,
        audit_event: OutboxEvent,
    ) -> ToolPermissionMutationResult:
        """应用一次权限变更，并恢复并发幂等提交结果。"""
        self._validate_contract(mutation, audit_event)
        try:
            return await self._apply_once(mutation, audit_event)
        except IntegrityError as exc:
            recovered = await self._recover_after_integrity_error(
                mutation,
            )
            if recovered is not None:
                return recovered
            raise ConflictError("Tool permission mutation conflict") from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not mutate tool permissions") from exc

    async def _apply_once(
        self,
        mutation: ToolPermissionMutation,
        audit_event: OutboxEvent,
    ) -> ToolPermissionMutationResult:
        """在单个数据库事务内完成查重、加锁、变更和审计写入。"""
        async with self._session_factory() as session:
            async with session.begin():
                existing = await self._find_operation(
                    session,
                    mutation.tenant_id,
                    mutation.idempotency_key_hash,
                )
                if existing is not None:
                    return self._existing_result(existing, mutation)

                active = await session.scalar(
                    select(ToolPermissionGrantRecord)
                    .where(
                        ToolPermissionGrantRecord.tenant_id == mutation.tenant_id,
                        ToolPermissionGrantRecord.operator_id == mutation.operator_id,
                        ToolPermissionGrantRecord.revoked_at.is_(None),
                    )
                    .with_for_update()
                    .limit(1)
                )
                latest_version = (
                    await session.scalar(
                        select(func.max(ToolPermissionGrantRecord.version)).where(
                            ToolPermissionGrantRecord.tenant_id == mutation.tenant_id,
                            ToolPermissionGrantRecord.operator_id
                            == mutation.operator_id,
                        )
                    )
                    or 0
                )
                # 等待行锁期间，另一个相同请求可能已经提交；再次查重可把并发
                # 重放恢复为幂等成功，而不是误判成期望版本冲突。
                existing = await self._find_operation(
                    session,
                    mutation.tenant_id,
                    mutation.idempotency_key_hash,
                )
                if existing is not None:
                    return self._existing_result(existing, mutation)
                if active is not None and active.version != latest_version:
                    raise PersistenceError(
                        "Active permission grant is not the latest version"
                    )
                if latest_version != mutation.expected_version:
                    raise ConflictError("Tool permission version conflict")

                result_version = latest_version + 1
                if mutation.action is ToolPermissionChangeAction.SET:
                    grant_id = await self._set_permissions(
                        session,
                        mutation,
                        active,
                        result_version,
                    )
                else:
                    grant_id = self._revoke_permissions(
                        mutation,
                        active,
                        result_version,
                    )

                operation = ToolPermissionOperationRecord(
                    operation_id=mutation.operation_id,
                    tenant_id=mutation.tenant_id,
                    operator_id=mutation.operator_id,
                    idempotency_key_hash=(mutation.idempotency_key_hash),
                    request_hash=mutation.request_hash,
                    action=mutation.action.value,
                    result_grant_id=grant_id,
                    result_version=result_version,
                    requested_by=mutation.requested_by,
                    trace_id=mutation.trace_id,
                    occurred_at=mutation.occurred_at,
                )
                session.add(operation)
                session.add(OutboxEventMapper.to_record(audit_event))
                await session.flush()

                return self._result(
                    operation,
                    is_duplicate=False,
                )

    async def _set_permissions(
        self,
        session: AsyncSession,
        mutation: ToolPermissionMutation,
        active: ToolPermissionGrantRecord | None,
        result_version: int,
    ) -> str:
        """撤销旧快照并创建带完整标签的新快照。"""
        if mutation.new_grant_id is None:
            raise AppValidationError("SET mutation requires new_grant_id")
        if active is not None:
            active.revoked_at = mutation.occurred_at
            active.updated_at = mutation.occurred_at
            # 先释放部分唯一索引，再插入新快照，避免Flush内部顺序产生假冲突。
            await session.flush()

        record = ToolPermissionGrantRecord(
            grant_id=mutation.new_grant_id,
            tenant_id=mutation.tenant_id,
            operator_id=mutation.operator_id,
            expires_at=mutation.expires_at,
            revoked_at=None,
            created_at=mutation.occurred_at,
            updated_at=mutation.occurred_at,
            version=result_version,
        )
        session.add(record)
        await session.flush()
        session.add_all(
            ToolPermissionTagRecord(
                grant_id=record.grant_id,
                permission_tag=permission_tag,
            )
            for permission_tag in mutation.permission_tags
        )
        return record.grant_id

    @staticmethod
    def _revoke_permissions(
        mutation: ToolPermissionMutation,
        active: ToolPermissionGrantRecord | None,
        result_version: int,
    ) -> str:
        """撤销当前快照并递增状态版本。"""
        if active is None:
            raise ResourceNotFound("Active tool permission grant not found")
        active.revoked_at = mutation.occurred_at
        active.updated_at = mutation.occurred_at
        active.version = result_version
        return active.grant_id

    async def _recover_after_integrity_error(
        self,
        mutation: ToolPermissionMutation,
    ) -> ToolPermissionMutationResult | None:
        """提交竞态后用新Session确认是否为相同幂等请求。"""
        try:
            async with self._session_factory() as session:
                operation = await self._find_operation(
                    session,
                    mutation.tenant_id,
                    mutation.idempotency_key_hash,
                )
        except SQLAlchemyError as exc:
            raise PersistenceError(
                "Could not recover permission idempotency result"
            ) from exc
        if operation is None:
            return None
        return self._existing_result(operation, mutation)

    @staticmethod
    async def _find_operation(
        session: AsyncSession,
        tenant_id: str,
        idempotency_key_hash: str,
    ) -> ToolPermissionOperationRecord | None:
        """按租户和幂等摘要查询唯一操作结果。"""
        return await session.scalar(
            select(ToolPermissionOperationRecord)
            .where(
                ToolPermissionOperationRecord.tenant_id == tenant_id,
                ToolPermissionOperationRecord.idempotency_key_hash
                == idempotency_key_hash,
            )
            .limit(1)
        )

    def _existing_result(
        self,
        operation: ToolPermissionOperationRecord,
        mutation: ToolPermissionMutation,
    ) -> ToolPermissionMutationResult:
        """区分同请求重放与幂等键复用于不同请求。"""
        if operation.request_hash != mutation.request_hash:
            raise ConflictError(
                "Idempotency key was used for another permission request"
            )
        return self._result(operation, is_duplicate=True)

    @staticmethod
    def _result(
        operation: ToolPermissionOperationRecord,
        *,
        is_duplicate: bool,
    ) -> ToolPermissionMutationResult:
        """把持久化操作记录转换为稳定应用结果。"""
        return ToolPermissionMutationResult(
            operation_id=operation.operation_id,
            grant_id=operation.result_grant_id,
            tenant_id=operation.tenant_id,
            operator_id=operation.operator_id,
            action=ToolPermissionChangeAction(operation.action),
            version=operation.result_version,
            trace_id=operation.trace_id,
            is_duplicate=is_duplicate,
        )

    @staticmethod
    def _validate_contract(
        mutation: ToolPermissionMutation,
        audit_event: OutboxEvent,
    ) -> None:
        """防止调用方把不匹配的状态变更和审计事件放进同一事务。"""
        if not isinstance(mutation, ToolPermissionMutation):
            raise AppValidationError("mutation must be a ToolPermissionMutation")
        if not isinstance(audit_event, OutboxEvent):
            raise AppValidationError("audit_event must be an OutboxEvent")
        for field_name, value, maximum in (
            ("operation_id", mutation.operation_id, 64),
            ("tenant_id", mutation.tenant_id, 128),
            ("operator_id", mutation.operator_id, 128),
            ("requested_by", mutation.requested_by, 128),
            ("trace_id", mutation.trace_id, 128),
        ):
            SQLAlchemyToolPermissionAdminStore._validate_text(
                field_name,
                value,
                maximum,
            )
        if mutation.new_grant_id is not None:
            SQLAlchemyToolPermissionAdminStore._validate_text(
                "new_grant_id",
                mutation.new_grant_id,
                64,
            )
        SQLAlchemyToolPermissionAdminStore._validate_hash(
            mutation.idempotency_key_hash,
        )
        SQLAlchemyToolPermissionAdminStore._validate_hash(
            mutation.request_hash,
        )
        for permission_tag in mutation.permission_tags:
            if (
                not isinstance(permission_tag, str)
                or not 1 <= len(permission_tag) <= 128
                or permission_tag != permission_tag.strip()
                or any(character.isspace() for character in permission_tag)
            ):
                raise AppValidationError("permission tag is invalid")
        if (
            audit_event.tenant_id != mutation.tenant_id
            or audit_event.aggregate_id != mutation.operation_id
            or audit_event.trace_id != mutation.trace_id
            or audit_event.occurred_at != mutation.occurred_at
        ):
            raise AppValidationError("audit event does not match permission mutation")
        if mutation.action is ToolPermissionChangeAction.SET:
            if mutation.new_grant_id is None or not mutation.permission_tags:
                raise AppValidationError(
                    "SET mutation requires grant id and permission tags"
                )
        elif (
            mutation.new_grant_id is not None
            or mutation.permission_tags
            or mutation.expires_at is not None
        ):
            raise AppValidationError(
                "REVOKE mutation contains unsupported grant fields"
            )

    @staticmethod
    def _validate_text(
        field_name: str,
        value: str,
        maximum: int,
    ) -> None:
        """校验查询索引字段，拒绝空白、超长和首尾空格。"""
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise AppValidationError(f"{field_name} is invalid")

    @staticmethod
    def _validate_hash(value: str) -> None:
        """校验权限幂等与请求指纹摘要。"""
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise AppValidationError("hash value is invalid")
