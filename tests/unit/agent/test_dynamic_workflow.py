import pytest

from devops_agent_platform.agent.dynamic_workflow import (
    BoundedDynamicRCAWorkflow,
    DynamicIncidentContext,
)
from devops_agent_platform.application.commands.workflow_execution import (
    ExecuteRCAWorkflowCommand,
)
from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.domain.models.topology import (
    DependencyEdge,
    ServiceNode,
    TopologyGraph,
    TopologySource,
)
from devops_agent_platform.tools.definition import ToolDefinition
from devops_agent_platform.tools.executor import ToolExecutor
from devops_agent_platform.tools.handler_registry import ToolHandlerRegistry
from devops_agent_platform.tools.permission import (
    ToolPermissionChecker,
    ToolPermissionGrant,
)
from devops_agent_platform.tools.registry import ToolRegistry


class PermissionProvider:
    async def get_grant(self, tenant_id: str, operator_id: str):
        return ToolPermissionGrant(
            tenant_id=tenant_id,
            operator_id=operator_id,
            permission_tags=frozenset(
                {
                    "tenant:observe",
                    "metrics:read",
                    "logs:read",
                    "traces:read",
                    "changes:read",
                    "topology:read",
                    "knowledge:read",
                    "runbooks:read",
                }
            ),
        )


class RecordingHandler:
    def __init__(self, *, fail_knowledge: bool = False) -> None:
        self.calls: list[str] = []
        self.fail_knowledge = fail_knowledge

    async def execute(self, payload, trace_id):
        del trace_id
        tool_name = payload.get("step_id", "")
        self.calls.append(tool_name)
        if self.fail_knowledge and "knowledge-search" in tool_name:
            raise TimeoutError("knowledge provider timed out")
        return {
            "source": "test",
            "signals": [{"name": "errors", "count": 1}],
            "results": [],
        }


def _build_workflow(handler: RecordingHandler) -> BoundedDynamicRCAWorkflow:
    names = (
        "topology.query",
        "metrics.query",
        "changes.query",
        "logs.query",
        "traces.query",
        "knowledge.search",
        "runbooks.retrieve",
    )
    definitions = [
        ToolDefinition(
            tool_name=name,
            version="v1",
            risk_level=ToolRiskLevel.LOW,
            timeout_ms=1000,
            permission_tags=("tenant:observe",),
        )
        for name in names
    ]
    registry = ToolRegistry(definitions)
    handlers = ToolHandlerRegistry()
    for definition in definitions:
        handlers.register(definition, handler)
    graph = TopologyGraph(
        tenant_id="tenant-1",
        nodes=(
            ServiceNode(
                node_id="service:checkout-api",
                tenant_id="tenant-1",
                service_name="checkout-api",
                environment="default",
                source=TopologySource.STATIC,
            ),
            ServiceNode(
                node_id="service:inventory-api",
                tenant_id="tenant-1",
                service_name="inventory-api",
                environment="default",
                source=TopologySource.STATIC,
            ),
        ),
        edges=(
            DependencyEdge(
                edge_id="edge-1",
                tenant_id="tenant-1",
                source_node_id="service:checkout-api",
                target_node_id="service:inventory-api",
            ),
        ),
    )

    async def load_context(tenant_id: str, incident_id: str):
        assert (tenant_id, incident_id) == ("tenant-1", "incident-1")
        return (
            DynamicIncidentContext(
                service_name="checkout-api",
                summary="checkout error rate",
            ),
            graph,
        )

    return BoundedDynamicRCAWorkflow(
        registry=registry,
        permission_checker=ToolPermissionChecker(PermissionProvider()),
        tool_executor=ToolExecutor(handlers),
        context_loader=load_context,
    )


@pytest.mark.asyncio
async def test_dynamic_runtime_calls_topology_and_knowledge() -> None:
    handler = RecordingHandler()
    workflow = _build_workflow(handler)
    result = await workflow.execute(
        ExecuteRCAWorkflowCommand(
            tenant_id="tenant-1",
            workflow_run_id="workflow-1",
            incident_id="incident-1",
            operator_id="operator-1",
            worker_id="worker-1",
            execution_attempt=1,
            trace_id="trace-1",
        )
    )

    tool_names = {item.tool_name for item in result.invocations}
    assert "topology.query" in tool_names
    assert "knowledge.search" in tool_names
    assert {item.evidence_type.value for item in result.evidence} >= {
        "TOPOLOGY",
        "KNOWLEDGE",
    }
    assert result.report is not None
    assert result.report.suspected_root_node == "service:checkout-api"
    assert result.report.causal_chain
    assert result.report.affected_services == ("inventory-api",)
    assert result.report.blast_radius


@pytest.mark.asyncio
async def test_dynamic_runtime_keeps_partial_report_when_knowledge_times_out() -> None:
    handler = RecordingHandler(fail_knowledge=True)
    workflow = _build_workflow(handler)
    result = await workflow.execute(
        ExecuteRCAWorkflowCommand(
            tenant_id="tenant-1",
            workflow_run_id="workflow-2",
            incident_id="incident-1",
            operator_id="operator-1",
            worker_id="worker-1",
            execution_attempt=1,
            trace_id="trace-2",
        )
    )

    assert result.report is not None
    assert result.evidence
    assert any("knowledge-search" in call for call in handler.calls)
    assert "Partial collection" in result.report.summary


@pytest.mark.asyncio
async def test_dynamic_runtime_degrades_when_topology_is_empty() -> None:
    handler = RecordingHandler()
    workflow = _build_workflow(handler)

    async def load_without_topology(tenant_id: str, incident_id: str):
        assert (tenant_id, incident_id) == ("tenant-1", "incident-1")
        return (
            DynamicIncidentContext(
                service_name="checkout-api",
                summary="checkout error rate",
            ),
            None,
        )

    workflow._context_loader = load_without_topology
    result = await workflow.execute(
        ExecuteRCAWorkflowCommand(
            tenant_id="tenant-1",
            workflow_run_id="workflow-3",
            incident_id="incident-1",
            operator_id="operator-1",
            worker_id="worker-1",
            execution_attempt=1,
            trace_id="trace-3",
        )
    )

    assert result.evidence
    assert result.report is not None
    assert result.report.suspected_root_node is None
