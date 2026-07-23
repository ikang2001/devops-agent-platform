import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256

from devops_agent_platform.application.commands.tool_permissions import (
    RevokeToolPermissionsCommand,
    SetToolPermissionsCommand,
)
from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.application.queries.tool_permissions import (
    GetToolPermissionsQuery,
)
from devops_agent_platform.domain.enums import ToolPermissionChangeAction
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.identifiers import IdentifierGeneratorPort
from devops_agent_platform.ports.tool_permissions import (
    ToolPermissionAdminStorePort,
    ToolPermissionMutation,
    ToolPermissionMutationResult,
)

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class ToolPermissionChangeResult:
    """权限管理应用服务返回的稳定结果。"""

    operation_id: str
    grant_id: str
    tenant_id: str
    operator_id: str
    action: str
    version: int
    trace_id: str
    is_duplicate: bool

    def to_dict(self) -> dict[str, str | int | bool]:
        """转换为不依赖HTTP框架的响应数据。"""
        return asdict(self)


@dataclass(frozen=True)
class ToolPermissionSnapshotView:
    """权限管理端可读取的不含幂等摘要的当前快照。"""

    tenant_id: str
    operator_id: str
    grant_id: str | None
    version: int
    permission_tags: tuple[str, ...]
    expires_at: datetime | None
    active: bool

    def to_dict(self) -> dict[str, object]:
        """转换为稳定响应字典，并使用列表表达权限集合。"""
        return {
            "tenant_id": self.tenant_id,
            "operator_id": self.operator_id,
            "grant_id": self.grant_id,
            "version": self.version,
            "permission_tags": list(self.permission_tags),
            "expires_at": (
                self.expires_at.isoformat()
                if self.expires_at is not None
                else None
            ),
            "active": self.active,
        }


class ToolPermissionAdminService:
    """编排权限变更、幂等指纹和事务型审计事件。"""

    def __init__(
        self,
        store: ToolPermissionAdminStorePort,
        identifier_generator: IdentifierGeneratorPort,
        clock: Clock | None = None,
    ) -> None:
        self._store = store
        self._identifier_generator = identifier_generator
        self._clock = clock or (lambda: datetime.now(UTC))

    async def get_current(
        self,
        query: GetToolPermissionsQuery,
    ) -> ToolPermissionSnapshotView:
        """读取当前权限快照；读路径不产生审计副作用。"""
        snapshot = await self._store.get_current(
            query.tenant_id,
            query.operator_id,
        )
        return ToolPermissionSnapshotView(
            tenant_id=snapshot.tenant_id,
            operator_id=snapshot.operator_id,
            grant_id=snapshot.grant_id,
            version=snapshot.version,
            permission_tags=snapshot.permission_tags,
            expires_at=snapshot.expires_at,
            active=snapshot.active,
        )

    async def set_permissions(
        self,
        command: SetToolPermissionsCommand,
    ) -> ToolPermissionChangeResult:
        """授予或完整替换权限；标签采用确定性顺序持久化。"""
        now = self._now()
        if command.expires_at is not None and command.expires_at <= now:
            raise AppValidationError("expires_at must be later than now")
        tags = tuple(sorted(command.permission_tags))
        return await self._execute(
            action=ToolPermissionChangeAction.SET,
            tenant_id=command.tenant_id,
            operator_id=command.operator_id,
            permission_tags=tags,
            expires_at=command.expires_at,
            expected_version=command.expected_version,
            idempotency_key=command.idempotency_key,
            requested_by=command.requested_by,
            trace_id=command.trace_id,
            now=now,
        )

    async def revoke_permissions(
        self,
        command: RevokeToolPermissionsCommand,
    ) -> ToolPermissionChangeResult:
        """撤销当前权限快照，不删除历史标签和操作记录。"""
        return await self._execute(
            action=ToolPermissionChangeAction.REVOKE,
            tenant_id=command.tenant_id,
            operator_id=command.operator_id,
            permission_tags=(),
            expires_at=None,
            expected_version=command.expected_version,
            idempotency_key=command.idempotency_key,
            requested_by=command.requested_by,
            trace_id=command.trace_id,
            now=self._now(),
        )

    async def _execute(
        self,
        *,
        action: ToolPermissionChangeAction,
        tenant_id: str,
        operator_id: str,
        permission_tags: tuple[str, ...],
        expires_at: datetime | None,
        expected_version: int,
        idempotency_key: str,
        requested_by: str,
        trace_id: str,
        now: datetime,
    ) -> ToolPermissionChangeResult:
        """构造不可逆指纹、变更参数和同事务审计事件。"""
        operation_id = (
            self._identifier_generator.new_permission_operation_id()
        )
        mutation = ToolPermissionMutation(
            operation_id=operation_id,
            new_grant_id=(
                self._identifier_generator.new_permission_grant_id()
                if action is ToolPermissionChangeAction.SET
                else None
            ),
            tenant_id=tenant_id,
            operator_id=operator_id,
            permission_tags=permission_tags,
            expires_at=expires_at,
            expected_version=expected_version,
            idempotency_key_hash=self._hash_text(idempotency_key),
            request_hash=self._request_hash(
                action=action,
                tenant_id=tenant_id,
                operator_id=operator_id,
                permission_tags=permission_tags,
                expires_at=expires_at,
                expected_version=expected_version,
                requested_by=requested_by,
            ),
            requested_by=requested_by,
            trace_id=trace_id,
            action=action,
            occurred_at=now,
        )
        event = self._build_audit_event(mutation)
        stored = await self._store.apply(mutation, event)
        return self._result(stored, trace_id)

    def _build_audit_event(
        self,
        mutation: ToolPermissionMutation,
    ) -> OutboxEvent:
        """构造不包含原始幂等键的版本化权限审计事件。"""
        return OutboxEvent(
            event_id=self._identifier_generator.new_event_id(),
            tenant_id=mutation.tenant_id,
            aggregate_type="ToolPermissionOperation",
            aggregate_id=mutation.operation_id,
            event_type=(
                "tool_permission.set"
                if mutation.action is ToolPermissionChangeAction.SET
                else "tool_permission.revoked"
            ),
            schema_version=1,
            payload={
                "operation_id": mutation.operation_id,
                "tenant_id": mutation.tenant_id,
                "operator_id": mutation.operator_id,
                "action": mutation.action.value,
                "permission_tags": list(mutation.permission_tags),
                "expires_at": (
                    mutation.expires_at.isoformat()
                    if mutation.expires_at is not None
                    else None
                ),
                "requested_by": mutation.requested_by,
                "result_version": mutation.expected_version + 1,
                "occurred_at": mutation.occurred_at.isoformat(),
            },
            occurred_at=mutation.occurred_at,
            trace_id=mutation.trace_id,
        )

    @staticmethod
    def _request_hash(
        *,
        action: ToolPermissionChangeAction,
        tenant_id: str,
        operator_id: str,
        permission_tags: tuple[str, ...],
        expires_at: datetime | None,
        expected_version: int,
        requested_by: str,
    ) -> str:
        """生成字段顺序稳定的权限变更请求指纹。"""
        encoded = json.dumps(
            {
                "action": action.value,
                "tenant_id": tenant_id,
                "operator_id": operator_id,
                "permission_tags": permission_tags,
                "expires_at": (
                    expires_at.isoformat()
                    if expires_at is not None
                    else None
                ),
                "expected_version": expected_version,
                "requested_by": requested_by,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return sha256(encoded).hexdigest()

    @staticmethod
    def _hash_text(value: str) -> str:
        """只持久化幂等键摘要，避免数据库泄漏调用方原文。"""
        return sha256(value.encode()).hexdigest()

    def _now(self) -> datetime:
        """读取并校验应用时钟。"""
        now = self._clock()
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise AppValidationError(
                "clock must return a timezone-aware datetime"
            )
        return now

    @staticmethod
    def _result(
        stored: ToolPermissionMutationResult,
        trace_id: str,
    ) -> ToolPermissionChangeResult:
        """转换Store结果，并使用当前调用链路标识响应幂等重放。"""
        return ToolPermissionChangeResult(
            operation_id=stored.operation_id,
            grant_id=stored.grant_id,
            tenant_id=stored.tenant_id,
            operator_id=stored.operator_id,
            action=stored.action.value,
            version=stored.version,
            trace_id=trace_id,
            is_duplicate=stored.is_duplicate,
        )
