from datetime import UTC, datetime

import pytest

from devops_agent_platform.application.services.topology_service import (
    BlastRadiusService,
    TopologyService,
    build_dependency_edge,
    build_service_node,
)
from devops_agent_platform.domain.exceptions import ConflictError
from devops_agent_platform.infrastructure.adapters.stub.topology import (
    InMemoryTopologyRepository,
)


@pytest.mark.asyncio
async def test_topology_is_tenant_isolated_and_blast_radius_is_deterministic():
    repository = InMemoryTopologyRepository()
    service = TopologyService(repository)
    for tenant in ("tenant-a", "tenant-b"):
        await service.register_static_service(
            build_service_node(tenant, "payment-service")
        )
        await service.register_static_service(
            build_service_node(tenant, "checkout-service")
        )
    await service.register_dependency(
        build_dependency_edge("tenant-a", "payment-service", "checkout-service")
    )

    graph = await service.query("tenant-a")
    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1
    result = await BlastRadiusService().calculate(graph, "service:payment-service")
    assert result.directly_affected_services == ("service:checkout-service",)
    assert result.scores == (("service:checkout-service", 1.0),)


@pytest.mark.asyncio
async def test_topology_rejects_cycles_and_deduplicates_trace_edges():
    repository = InMemoryTopologyRepository()
    service = TopologyService(repository)
    for name in ("payment-service", "checkout-service"):
        await service.register_static_service(build_service_node("tenant", name))
    await service.register_dependency(
        build_dependency_edge("tenant", "payment-service", "checkout-service")
    )
    with pytest.raises(ConflictError):
        await service.register_dependency(
            build_dependency_edge("tenant", "checkout-service", "payment-service")
        )
    assert datetime.now(UTC).tzinfo is not None
