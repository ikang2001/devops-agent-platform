import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from devops_agent_platform.application.exceptions import (
    PermissionDataSourceError,
)
from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    PermissionDenied,
)
from devops_agent_platform.tools.definition import ToolDefinition
from devops_agent_platform.tools.permission import (
    ToolPermissionChecker,
    ToolPermissionGrant,
)

NOW = datetime(2026, 6, 28, 10, 0, tzinfo=UTC)


def build_definition(
    permission_tags: tuple[str, ...] = (
        "logs:read",
        "tenant:observe",
    ),
) -> ToolDefinition:
    """构造权限检查测试使用的工具定义。"""
    return ToolDefinition(
        tool_name="logs.query",
        version="v1",
        risk_level=ToolRiskLevel.LOW,
        timeout_ms=1000,
        permission_tags=permission_tags,
    )


def build_grant(
    *,
    tenant_id: str = "tenant_001",
    operator_id: str = "operator_001",
    permission_tags: frozenset[str] = frozenset(
        {"logs:read", "tenant:observe", "metrics:read"}
    ),
    expires_at: datetime | None = NOW + timedelta(minutes=5),
) -> ToolPermissionGrant:
    """构造带明确租户、主体、权限和失效时间的授权快照。"""
    return ToolPermissionGrant(
        tenant_id=tenant_id,
        operator_id=operator_id,
        permission_tags=permission_tags,
        expires_at=expires_at,
    )


class FixedProvider:
    """返回固定授权结果并记录查询参数。"""

    def __init__(self, grant: object) -> None:
        self.grant = grant
        self.calls: list[tuple[str, str]] = []

    async def get_grant(
        self,
        tenant_id: str,
        operator_id: str,
    ) -> ToolPermissionGrant | None:
        self.calls.append((tenant_id, operator_id))
        return self.grant  # type: ignore[return-value]


class FailingProvider:
    """模拟包含敏感错误文本的权限数据源故障。"""

    async def get_grant(
        self,
        tenant_id: str,
        operator_id: str,
    ) -> ToolPermissionGrant | None:
        raise RuntimeError("postgres password=do-not-leak")


class BlockingProvider:
    """保持查询阻塞，用于验证外部取消传播。"""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def get_grant(
        self,
        tenant_id: str,
        operator_id: str,
    ) -> ToolPermissionGrant | None:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return None


async def test_all_required_tags_allow_tool_execution() -> None:
    """授权包含工具要求的全部标签时检查通过。"""
    provider = FixedProvider(build_grant())
    checker = ToolPermissionChecker(provider, clock=lambda: NOW)

    await checker.check(
        build_definition(),
        "tenant_001",
        "operator_001",
    )

    assert provider.calls == [("tenant_001", "operator_001")]


async def test_extra_grant_tags_do_not_change_decision() -> None:
    """额外权限不会影响当前工具的最小标签子集判断。"""
    provider = FixedProvider(
        build_grant(
            permission_tags=frozenset(
                {
                    "logs:read",
                    "tenant:observe",
                    "unrelated:permission",
                }
            )
        )
    )
    checker = ToolPermissionChecker(provider, clock=lambda: NOW)

    await checker.check(
        build_definition(),
        "tenant_001",
        "operator_001",
    )


async def test_missing_any_required_tag_is_denied() -> None:
    """少任意一个必需标签都必须拒绝，不能采用任一标签匹配。"""
    provider = FixedProvider(build_grant(permission_tags=frozenset({"logs:read"})))
    checker = ToolPermissionChecker(provider, clock=lambda: NOW)

    with pytest.raises(PermissionDenied) as exc_info:
        await checker.check(
            build_definition(),
            "tenant_001",
            "operator_001",
        )

    assert exc_info.value.code == "PERMISSION_DENIED"
    assert "tenant:observe" not in str(exc_info.value)
    assert "operator_001" not in str(exc_info.value)


async def test_anonymous_operator_is_denied_without_querying_provider() -> None:
    """匿名主体直接拒绝，避免数据源把空主体解释为公共权限。"""
    provider = FixedProvider(build_grant())
    checker = ToolPermissionChecker(provider, clock=lambda: NOW)

    with pytest.raises(PermissionDenied):
        await checker.check(build_definition(), "tenant_001", None)

    assert provider.calls == []


async def test_missing_grant_is_permission_denial() -> None:
    """数据源明确返回无授权时映射为403语义。"""
    checker = ToolPermissionChecker(
        FixedProvider(None),
        clock=lambda: NOW,
    )

    with pytest.raises(PermissionDenied):
        await checker.check(
            build_definition(),
            "tenant_001",
            "operator_001",
        )


@pytest.mark.parametrize(
    "grant",
    [
        build_grant(tenant_id="tenant_other"),
        build_grant(operator_id="operator_other"),
    ],
)
async def test_cross_identity_grant_is_denied(
    grant: ToolPermissionGrant,
) -> None:
    """跨租户或跨操作者快照不能被当前调用复用。"""
    checker = ToolPermissionChecker(
        FixedProvider(grant),
        clock=lambda: NOW,
    )

    with pytest.raises(PermissionDenied):
        await checker.check(
            build_definition(),
            "tenant_001",
            "operator_001",
        )


@pytest.mark.parametrize(
    "expires_at",
    [
        NOW,
        NOW - timedelta(microseconds=1),
    ],
)
async def test_expired_grant_is_denied(expires_at: datetime) -> None:
    """到期时刻及其后的授权都不能继续使用。"""
    checker = ToolPermissionChecker(
        FixedProvider(build_grant(expires_at=expires_at)),
        clock=lambda: NOW,
    )

    with pytest.raises(PermissionDenied):
        await checker.check(
            build_definition(),
            "tenant_001",
            "operator_001",
        )


async def test_non_expiring_grant_is_supported() -> None:
    """长期服务账户可以通过显式None表达无固定过期时间。"""
    checker = ToolPermissionChecker(
        FixedProvider(build_grant(expires_at=None)),
        clock=lambda: NOW,
    )

    await checker.check(
        build_definition(),
        "tenant_001",
        "operator_001",
    )


async def test_provider_failure_is_retryable_and_sanitized() -> None:
    """数据源故障映射为503，并且不泄漏底层错误文本。"""
    checker = ToolPermissionChecker(
        FailingProvider(),
        clock=lambda: NOW,
    )

    with pytest.raises(PermissionDataSourceError) as exc_info:
        await checker.check(
            build_definition(),
            "tenant_001",
            "operator_001",
        )

    assert exc_info.value.code == "PERMISSION_SOURCE_UNAVAILABLE"
    assert "password" not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, RuntimeError)


async def test_invalid_provider_result_is_source_error_not_denial() -> None:
    """适配器返回错误类型属于系统故障，不能归咎于操作者。"""
    checker = ToolPermissionChecker(
        FixedProvider({"permissions": ["logs:read"]}),
        clock=lambda: NOW,
    )

    with pytest.raises(PermissionDataSourceError):
        await checker.check(
            build_definition(),
            "tenant_001",
            "operator_001",
        )


async def test_outer_cancellation_reaches_permission_provider() -> None:
    """租约丢失或关闭信号必须取消正在进行的授权查询。"""
    provider = BlockingProvider()
    checker = ToolPermissionChecker(provider, clock=lambda: NOW)
    task = asyncio.create_task(
        checker.check(
            build_definition(),
            "tenant_001",
            "operator_001",
        )
    )
    await provider.started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert provider.cancelled is True


def test_grant_requires_immutable_tags_and_aware_expiry() -> None:
    """授权快照拒绝可变权限集合和无时区时间。"""
    with pytest.raises(AppValidationError, match="frozenset"):
        ToolPermissionGrant(
            tenant_id="tenant_001",
            operator_id="operator_001",
            permission_tags={"logs:read"},  # type: ignore[arg-type]
        )
    with pytest.raises(AppValidationError, match="timezone-aware"):
        build_grant(expires_at=datetime(2026, 6, 28, 10, 0))


@pytest.mark.parametrize(
    "overrides",
    [
        {"tenant_id": "tenant_001\nforged"},
        {"tenant_id": "tenant_001\x7fforged"},
        {"operator_id": "operator_001\tforged"},
        {"operator_id": "operator_001\x7fforged"},
    ],
)
def test_grant_rejects_control_character_identities(
    overrides: dict[str, str],
) -> None:
    """权限数据源返回的身份快照也必须保持单行。"""
    with pytest.raises(AppValidationError, match="invalid"):
        build_grant(**overrides)


def test_grant_rejects_control_character_permission_tags() -> None:
    """权限标签来自数据源时也不能携带不可见控制字符。"""
    with pytest.raises(AppValidationError, match="permission tag"):
        build_grant(permission_tags=frozenset({"logs:read\x7f"}))


async def test_clock_must_return_aware_datetime() -> None:
    """错误测试时钟或生产时钟应在比较前明确失败。"""
    checker = ToolPermissionChecker(
        FixedProvider(build_grant()),
        clock=lambda: datetime(2026, 6, 28, 10, 0),
    )

    with pytest.raises(AppValidationError, match="timezone-aware"):
        await checker.check(
            build_definition(),
            "tenant_001",
            "operator_001",
        )
