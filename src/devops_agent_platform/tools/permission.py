import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from devops_agent_platform.application.exceptions import (
    PermissionDataSourceError,
)
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    PermissionDenied,
)
from devops_agent_platform.tools.definition import ToolDefinition

Clock = Callable[[], datetime]
_MAX_PERMISSION_TAGS = 1024
_MAX_PERMISSION_TAG_LENGTH = 128


@dataclass(frozen=True)
class ToolPermissionGrant:
    """权限数据源返回的不可变操作者授权快照。"""

    tenant_id: str
    operator_id: str
    permission_tags: frozenset[str]
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        """校验快照身份、权限集合和可选失效时间。"""
        self._validate_text("tenant_id", self.tenant_id, 128)
        self._validate_text("operator_id", self.operator_id, 128)
        if not isinstance(self.permission_tags, frozenset):
            raise AppValidationError("permission_tags must be a frozenset")
        if len(self.permission_tags) > _MAX_PERMISSION_TAGS:
            raise AppValidationError(
                f"permission_tags must not exceed {_MAX_PERMISSION_TAGS}"
            )
        for tag in self.permission_tags:
            if (
                not isinstance(tag, str)
                or not 1 <= len(tag) <= _MAX_PERMISSION_TAG_LENGTH
                or tag != tag.strip()
                or any(character.isspace() for character in tag)
                or any(
                    ord(character) < 32 or ord(character) == 127 for character in tag
                )
            ):
                raise AppValidationError("permission tag is invalid")
        if self.expires_at is not None:
            if not isinstance(self.expires_at, datetime):
                raise AppValidationError("expires_at must be a datetime or None")
            if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
                raise AppValidationError("expires_at must be timezone-aware")

    @staticmethod
    def _validate_text(
        field_name: str,
        value: str,
        maximum: int,
    ) -> None:
        """拒绝空值、超长值和首尾空白，保持授权索引稳定。"""
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise AppValidationError(f"{field_name} is invalid")


class ToolPermissionProvider(Protocol):
    """按租户和操作者读取工具权限快照的数据源端口。"""

    async def get_grant(
        self,
        tenant_id: str,
        operator_id: str,
    ) -> ToolPermissionGrant | None:
        """返回当前授权快照；没有授权时返回None。"""
        ...


class ToolPermissionChecker:
    """采用默认拒绝策略校验租户、操作者和全部权限标签。"""

    def __init__(
        self,
        provider: ToolPermissionProvider,
        clock: Clock | None = None,
    ) -> None:
        """创建检查器；数据源必须由启动装配显式提供。"""
        get_grant = getattr(provider, "get_grant", None)
        if not callable(get_grant):
            raise AppValidationError(
                "provider must provide a callable get_grant method"
            )
        if clock is not None and not callable(clock):
            raise AppValidationError("clock must be callable")
        self._provider = provider
        self._clock = clock or self._utc_now

    async def check(
        self,
        definition: ToolDefinition,
        tenant_id: str,
        operator_id: str | None,
    ) -> None:
        """校验操作者拥有工具要求的全部权限标签。

        权限数据缺失、身份不匹配、授权过期或少任意一个标签时统一拒绝。
        数据源故障单独映射为503语义，避免把系统故障误判成永久越权。
        """
        if not isinstance(definition, ToolDefinition):
            raise AppValidationError("definition must be a ToolDefinition")
        ToolPermissionGrant._validate_text(
            "tenant_id",
            tenant_id,
            128,
        )
        if operator_id is None:
            raise self._permission_denied(definition)
        ToolPermissionGrant._validate_text(
            "operator_id",
            operator_id,
            128,
        )

        try:
            grant = await self._provider.get_grant(
                tenant_id,
                operator_id,
            )
        except asyncio.CancelledError:
            # 应用关闭或租约丢失时，授权查询也必须立即停止。
            raise
        except PermissionDataSourceError:
            raise
        except Exception as exc:
            # 不传播底层连接串、SQL或IAM响应，详细根因保留在异常链中。
            raise PermissionDataSourceError(
                "Tool permission data source is unavailable"
            ) from exc

        if grant is None:
            raise self._permission_denied(definition)
        if not isinstance(grant, ToolPermissionGrant):
            raise PermissionDataSourceError(
                "Tool permission data source returned an invalid grant"
            )
        if grant.tenant_id != tenant_id or grant.operator_id != operator_id:
            raise self._permission_denied(definition)

        now = self._clock()
        self._validate_clock_value(now)
        if grant.expires_at is not None and grant.expires_at <= now:
            raise self._permission_denied(definition)
        if not set(definition.permission_tags).issubset(grant.permission_tags):
            raise self._permission_denied(definition)

    @staticmethod
    def _permission_denied(
        definition: ToolDefinition,
    ) -> PermissionDenied:
        """生成不泄漏租户、操作者和缺失标签的稳定拒绝异常。"""
        return PermissionDenied(
            f"Tool permission denied: {definition.tool_name}@{definition.version}"
        )

    @staticmethod
    def _validate_clock_value(value: datetime) -> None:
        """要求测试时钟和生产时钟都返回带时区时间。"""
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise AppValidationError("clock must return a timezone-aware datetime")

    @staticmethod
    def _utc_now() -> datetime:
        """返回带UTC时区的当前时间。"""
        return datetime.now(UTC)
