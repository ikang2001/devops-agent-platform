from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from devops_agent_platform.application.exceptions import (
    PermissionDataSourceError,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.infrastructure.database.models.tool_permission import (
    ToolPermissionGrantRecord,
    ToolPermissionTagRecord,
)
from devops_agent_platform.tools.permission import ToolPermissionGrant

_MAX_PERMISSION_TAGS = 1024


class SQLAlchemyToolPermissionProvider:
    """从关系数据库读取租户隔离的操作者权限快照。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """保存共享Session工厂，每次查询创建独立短生命周期Session。"""
        if not callable(session_factory):
            raise AppValidationError("session_factory must be callable")
        self._session_factory = session_factory

    async def get_grant(
        self,
        tenant_id: str,
        operator_id: str,
    ) -> ToolPermissionGrant | None:
        """读取未撤销授权及其有限标签集合。

        查询始终同时包含租户和操作者条件，并使用参数绑定。过期时间返回给
        ToolPermissionChecker按统一应用时钟判断，避免数据库与应用时钟竞态。
        """
        self._validate_text("tenant_id", tenant_id, 128)
        self._validate_text("operator_id", operator_id, 128)
        statement = (
            select(
                ToolPermissionGrantRecord.grant_id,
                ToolPermissionGrantRecord.tenant_id,
                ToolPermissionGrantRecord.operator_id,
                ToolPermissionGrantRecord.expires_at,
                ToolPermissionTagRecord.permission_tag,
            )
            .outerjoin(
                ToolPermissionTagRecord,
                ToolPermissionTagRecord.grant_id == ToolPermissionGrantRecord.grant_id,
            )
            .where(
                ToolPermissionGrantRecord.tenant_id == tenant_id,
                ToolPermissionGrantRecord.operator_id == operator_id,
                ToolPermissionGrantRecord.revoked_at.is_(None),
            )
            .order_by(ToolPermissionTagRecord.permission_tag.asc())
            .limit(_MAX_PERMISSION_TAGS + 1)
        )
        try:
            async with self._session_factory() as session:
                rows = (await session.execute(statement)).all()
            if not rows:
                return None

            first = rows[0]
            if any(row.grant_id != first.grant_id for row in rows):
                raise PermissionDataSourceError(
                    "Permission source returned multiple active grants"
                )
            permission_tags = frozenset(
                row.permission_tag for row in rows if row.permission_tag is not None
            )
            return ToolPermissionGrant(
                tenant_id=first.tenant_id,
                operator_id=first.operator_id,
                permission_tags=permission_tags,
                expires_at=first.expires_at,
            )
        except PermissionDataSourceError:
            raise
        except (SQLAlchemyError, AppValidationError) as exc:
            raise PermissionDataSourceError(
                "Could not load tool permission grant"
            ) from exc

    @staticmethod
    def _validate_text(
        field_name: str,
        value: str,
        maximum: int,
    ) -> None:
        """在访问数据库前拒绝空值、超长值和首尾空白。"""
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise AppValidationError(f"{field_name} is invalid")
