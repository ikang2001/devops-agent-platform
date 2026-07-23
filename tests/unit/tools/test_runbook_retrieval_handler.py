from datetime import UTC, datetime
from typing import Any

import pytest

from devops_agent_platform.application.exceptions import RunbookSourceError
from devops_agent_platform.domain.enums import RunbookStatus
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.runbook import Runbook
from devops_agent_platform.ports.observability import ObservabilityTarget
from devops_agent_platform.ports.runbooks import RunbookSearchResult
from devops_agent_platform.tools.executor import ToolExecutor
from devops_agent_platform.tools.handler_registry import ToolHandlerRegistry
from devops_agent_platform.tools.handlers.runbooks import (
    RunbookRetrievalHandler,
    RunbookRetrievalHandlerConfig,
    register_runbook_retrieval_tool,
)

NOW = datetime(2026, 7, 1, 11, 0, tzinfo=UTC)


class FixedTargetResolver:
    """返回租户内可信事故服务目标。"""

    async def resolve(
        self,
        tenant_id: str,
        incident_id: str,
    ) -> ObservabilityTarget:
        return ObservabilityTarget(
            tenant_id=tenant_id,
            incident_id=incident_id,
            service_name="checkout-api",
        )


class RecordingRunbookSearch:
    """记录检索参数并返回可配置结果。"""

    def __init__(self, result: RunbookSearchResult) -> None:
        self.result = result
        self.calls: list[tuple[str, str, int]] = []

    async def search(
        self,
        tenant_id: str,
        service_name: str,
        limit: int,
    ) -> RunbookSearchResult:
        self.calls.append((tenant_id, service_name, limit))
        return self.result


def build_runbook(
    runbook_id: str = "rb_001",
    **changes: Any,
) -> Runbook:
    """构造工具测试使用的发布手册。"""
    values: dict[str, Any] = {
        "runbook_id": runbook_id,
        "runbook_key": runbook_id,
        "tenant_id": "tenant_001",
        "service_name": "checkout-api",
        "title": "Checkout incident response",
        "summary": "Review dependencies and recent deployments.",
        "version": "v2",
        "status": RunbookStatus.PUBLISHED,
        "revision": 1,
        "priority": 100,
        "steps": ("Inspect the current service state.",),
        "tags": ("checkout",),
        "published_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    if "published_at" not in changes:
        values["published_at"] = (
            None if values["status"] is RunbookStatus.DRAFT else NOW
        )
    return Runbook(**values)


def build_payload(**changes: Any) -> dict[str, Any]:
    """构造受控工作流注入的完整 Runbook Payload。"""
    payload: dict[str, Any] = {
        "tenant_id": "tenant_001",
        "incident_id": "inc_001",
        "max_results": 5,
        "workflow_run_id": "wfr_001",
        "operator_id": "operator_001",
        "worker_id": "worker_001",
        "execution_attempt": 1,
        "trace_id": "trc_001",
        "plan_id": "default.observability-rca",
        "plan_version": "v2",
        "step_id": "retrieve.runbooks",
    }
    payload.update(changes)
    return payload


async def test_handler_returns_bounded_reviewed_runbooks() -> None:
    """工具应返回服务专属及租户通用手册，不暴露管理优先级。"""
    search = RecordingRunbookSearch(
        RunbookSearchResult(
            items=(
                build_runbook(),
                build_runbook("rb_generic", service_name="*"),
            ),
            possibly_truncated=False,
        )
    )
    handler = RunbookRetrievalHandler(FixedTargetResolver(), search)

    result = await handler.execute(build_payload(), "trc_001")

    assert search.calls == [("tenant_001", "checkout-api", 5)]
    assert result["returned_results"] == 2
    assert result["runbooks"][0]["scope"] == "SERVICE"
    assert result["runbooks"][1]["scope"] == "TENANT"
    assert "priority" not in result["runbooks"][0]
    assert result["possibly_truncated"] is False


@pytest.mark.parametrize(
    "runbook",
    [
        build_runbook(tenant_id="tenant_other"),
        build_runbook(service_name="payment-api"),
        build_runbook(status=RunbookStatus.DRAFT),
    ],
)
async def test_handler_fails_closed_on_source_boundary_violation(
    runbook: Runbook,
) -> None:
    """适配器返回跨租户、无关服务或草稿时必须整体拒绝。"""
    handler = RunbookRetrievalHandler(
        FixedTargetResolver(),
        RecordingRunbookSearch(
            RunbookSearchResult(
                items=(runbook,),
                possibly_truncated=False,
            )
        ),
    )

    with pytest.raises(RunbookSourceError, match="trust boundary"):
        await handler.execute(build_payload(), "trc_001")


async def test_handler_stops_before_output_byte_limit() -> None:
    """多份大手册不能超过 Evidence 可接收的 UTF-8 容量。"""
    large_steps = tuple("x" * 2000 for _ in range(10))
    runbooks = tuple(
        build_runbook(f"rb_{index}", steps=large_steps) for index in range(3)
    )
    handler = RunbookRetrievalHandler(
        FixedTargetResolver(),
        RecordingRunbookSearch(
            RunbookSearchResult(
                items=runbooks,
                possibly_truncated=False,
            )
        ),
        RunbookRetrievalHandlerConfig(max_output_bytes=48 * 1024),
    )

    result = await handler.execute(build_payload(), "trc_001")

    assert result["returned_results"] < 3
    assert result["possibly_truncated"] is True


@pytest.mark.parametrize(
    "changes",
    [
        {"max_results": 0},
        {"max_results": 11},
        {"tenant_id": " tenant_001"},
        {"tenant_id": "tenant_001\x7fforged"},
        {"incident_id": "inc_001\x7fforged"},
        {"query": "DROP TABLE runbooks"},
    ],
)
async def test_invalid_payload_fails_before_search(
    changes: dict[str, Any],
) -> None:
    """非法身份、容量和任意查询字段不能到达检索端口。"""
    search = RecordingRunbookSearch(
        RunbookSearchResult(items=(), possibly_truncated=False)
    )
    handler = RunbookRetrievalHandler(FixedTargetResolver(), search)

    with pytest.raises(AppValidationError):
        await handler.execute(build_payload(**changes), "trc_001")

    assert search.calls == []


async def test_registered_handler_runs_through_unified_executor() -> None:
    """真实工具定义应通过统一执行器完成契约和输出校验。"""
    registry = ToolHandlerRegistry()
    handler = RunbookRetrievalHandler(
        FixedTargetResolver(),
        RecordingRunbookSearch(
            RunbookSearchResult(
                items=(build_runbook(),),
                possibly_truncated=False,
            )
        ),
    )
    definition = register_runbook_retrieval_tool(registry, handler)

    result = await ToolExecutor(registry).execute(
        definition,
        build_payload(),
        "trc_001",
    )

    assert result["source"] == "runbook_catalog"
    assert definition.permission_tags == (
        "runbooks:read",
        "tenant:observe",
    )
