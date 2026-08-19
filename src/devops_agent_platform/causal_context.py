from __future__ import annotations

import json
import re
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

from devops_agent_platform.domain.enums import (
    EvidenceType,
    RCAConclusionStatus,
)
from devops_agent_platform.domain.models.evidence import Evidence
from devops_agent_platform.domain.models.rca_report import RCAReport
from devops_agent_platform.domain.models.topology import (
    DependencyEdge,
    ResourceNode,
    ServiceNode,
    TopologyGraph,
    TopologySource,
)

_KNOWN_SERVICE_ALIASES = frozenset(
    {"checkout", "inventory", "notification", "payment"}
)
_SERVICE_TOKEN = re.compile(
    r"\b([a-z][a-z0-9-]*?)(?:-service)?\b",
    re.IGNORECASE,
)


def normalize_causal_node(value: str) -> str:
    """将常见服务别名收敛为稳定节点 ID，资源节点保持原标识。"""

    normalized = value.strip().casefold()
    if normalized.startswith("service:"):
        service = normalized.removeprefix("service:")
        return f"service:{_canonical_service_name(service)}"
    if normalized.endswith("-service"):
        return f"service:{normalized}"
    if normalized in _KNOWN_SERVICE_ALIASES:
        return f"service:{normalized}-service"
    if normalized.startswith("resource:"):
        return normalized.removeprefix("resource:")
    return normalized


def normalize_affected_service(value: str) -> str:
    """返回用于评测集合比较的稳定服务名。"""

    normalized = normalize_causal_node(value)
    if normalized.startswith("service:"):
        return normalized.removeprefix("service:")
    return normalized


@dataclass(frozen=True)
class DeterministicCausalContext:
    root_node: str | None
    causal_chain: tuple[tuple[str, str, tuple[str, ...]], ...]
    affected_services: tuple[str, ...]
    blast_radius: tuple[tuple[str, float], ...]


class TopologyCausalContextBuilder:
    """只使用已采集 Evidence 和有向拓扑生成因果链与影响面。"""

    def build(
        self,
        *,
        report: RCAReport,
        incident_service: str,
        topology: TopologyGraph | None,
        evidence: tuple[Evidence, ...],
    ) -> DeterministicCausalContext:
        if report.conclusion_status is RCAConclusionStatus.NO_ACTIONABLE_ROOT_CAUSE:
            return DeterministicCausalContext(None, (), (), ())
        if topology is None:
            return DeterministicCausalContext(
                report.suspected_root_node,
                report.causal_chain,
                report.affected_services,
                report.blast_radius,
            )

        aliases = _TopologyAliases(topology)
        root_node = self._resolve_root(report, incident_service, aliases)
        if root_node is None:
            return DeterministicCausalContext(None, (), (), ())

        topology_evidence_ids = tuple(
            item.evidence_id
            for item in evidence
            if item.tool_name == "topology.query"
            and item.evidence_id in report.evidence_ids
        )
        if not topology_evidence_ids:
            return DeterministicCausalContext(root_node, (), (), ())

        observed_nodes = self._observed_nodes(evidence, aliases)
        requested_edges = self._requested_edges(report, aliases, evidence)
        traversed = self._traverse(
            topology,
            root_node,
            observed_nodes=observed_nodes,
            requested_edges=requested_edges,
        )
        causal_chain = tuple(
            (
                edge.source_node_id,
                edge.target_node_id,
                requested_edges.get(edge.key[:2], topology_evidence_ids),
            )
            for edge, _, _ in traversed
        )
        affected_services, blast_radius = self._impact(
            topology,
            traversed,
            root_node=root_node,
            include_root=report.root_cause_type is not None,
        )
        return DeterministicCausalContext(
            root_node,
            causal_chain,
            affected_services,
            blast_radius,
        )

    @staticmethod
    def _resolve_root(
        report: RCAReport,
        incident_service: str,
        aliases: _TopologyAliases,
    ) -> str | None:
        if report.root_cause_resource is not None:
            resource = aliases.resolve(report.root_cause_resource)
            if resource is not None:
                return resource
        return aliases.resolve(
            report.suspected_root_node or incident_service,
        )

    @staticmethod
    def _requested_edges(
        report: RCAReport,
        aliases: _TopologyAliases,
        evidence: tuple[Evidence, ...],
    ) -> dict[tuple[str, str], tuple[str, ...]]:
        evidence_by_id = {item.evidence_id: item for item in evidence}
        requested: dict[tuple[str, str], tuple[str, ...]] = {}
        for source, target, evidence_ids in report.causal_chain:
            normalized_source = aliases.resolve(source)
            normalized_target = aliases.resolve(target)
            if normalized_source is None or normalized_target is None:
                continue
            supported_ids = tuple(
                evidence_id
                for evidence_id in evidence_ids
                if evidence_id in report.evidence_ids
                and evidence_id in evidence_by_id
                and evidence_by_id[evidence_id].evidence_type
                in {EvidenceType.TOPOLOGY, EvidenceType.TRACE}
            )
            if supported_ids:
                requested[(normalized_source, normalized_target)] = supported_ids
        return requested

    @staticmethod
    def _observed_nodes(
        evidence: tuple[Evidence, ...],
        aliases: _TopologyAliases,
    ) -> set[str]:
        observed: set[str] = set()
        for item in evidence:
            if item.evidence_type is EvidenceType.TOPOLOGY:
                continue
            try:
                content = json.loads(item.content_json)
            except (TypeError, json.JSONDecodeError):
                content = {}
            text = " ".join((item.summary, *_string_values(content))).casefold()
            observed.update(aliases.find_in_text(text))
        return observed

    @staticmethod
    def _traverse(
        topology: TopologyGraph,
        root_node: str,
        *,
        observed_nodes: set[str],
        requested_edges: dict[tuple[str, str], tuple[str, ...]],
    ) -> tuple[tuple[DependencyEdge, int, float], ...]:
        queue: deque[tuple[str, int, float]] = deque([(root_node, 0, 1.0)])
        visited = {root_node}
        traversed: list[tuple[DependencyEdge, int, float]] = []
        while queue:
            current, depth, path_confidence = queue.popleft()
            if depth >= topology.max_depth:
                continue
            outgoing = topology.outgoing(current)
            for edge in outgoing:
                if edge.target_node_id in visited:
                    continue
                requested = (
                    edge.source_node_id,
                    edge.target_node_id,
                ) in requested_edges
                observed = edge.target_node_id in observed_nodes
                trace_edge = edge.source is TopologySource.TRACE
                unambiguous = len(outgoing) == 1
                if not (requested or observed or trace_edge or unambiguous):
                    continue
                confidence = path_confidence * float(edge.confidence)
                visited.add(edge.target_node_id)
                traversed.append((edge, depth + 1, confidence))
                queue.append((edge.target_node_id, depth + 1, confidence))
        return tuple(traversed)

    @staticmethod
    def _impact(
        topology: TopologyGraph,
        traversed: tuple[tuple[DependencyEdge, int, float], ...],
        *,
        root_node: str,
        include_root: bool,
    ) -> tuple[tuple[str, ...], tuple[tuple[str, float], ...]]:
        node_by_id = topology.node_by_id
        ordered: list[tuple[str, float]] = []
        root = node_by_id[root_node]
        if include_root and isinstance(root, ServiceNode):
            ordered.append((root.service_name, float(root.confidence)))
        for edge, depth, confidence in traversed:
            target = node_by_id[edge.target_node_id]
            if not isinstance(target, ServiceNode):
                continue
            score = round(
                min(confidence, float(target.confidence)) / max(depth, 1),
                6,
            )
            ordered.append((target.service_name, score))
        deduplicated = tuple(dict(ordered).items())
        return tuple(item[0] for item in deduplicated), deduplicated


class _TopologyAliases:
    def __init__(self, topology: TopologyGraph) -> None:
        aliases: dict[str, str] = {}
        searchable: dict[str, str] = {}
        for node in topology.nodes:
            aliases[node.node_id.casefold()] = node.node_id
            if isinstance(node, ServiceNode):
                service = node.service_name.casefold()
                for alias in (
                    service,
                    f"service:{service}",
                    service.removesuffix("-service"),
                ):
                    aliases.setdefault(alias, node.node_id)
                    searchable[alias] = node.node_id
            elif isinstance(node, ResourceNode):
                resource = node.resource_name.casefold()
                for alias in (resource, f"resource:{resource}"):
                    aliases.setdefault(alias, node.node_id)
                    searchable[alias] = node.node_id
        self._aliases = aliases
        self._searchable = searchable

    def resolve(self, value: str) -> str | None:
        return self._aliases.get(value.strip().casefold())

    def find_in_text(self, text: str) -> set[str]:
        return {
            node_id
            for alias, node_id in self._searchable.items()
            if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text)
        }


def _canonical_service_name(value: str) -> str:
    normalized = value.strip().casefold()
    if normalized.endswith("-service"):
        return normalized
    if normalized in _KNOWN_SERVICE_ALIASES:
        return f"{normalized}-service"
    return normalized


def _string_values(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _string_values(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _string_values(item)
