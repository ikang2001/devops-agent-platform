from __future__ import annotations

import re
from dataclasses import dataclass

from devops_agent_platform.causal_context import (
    normalize_affected_service,
    normalize_causal_node,
)
from devops_agent_platform.domain.enums import RootCauseType
from devops_agent_platform.evaluation.schemas import CausalEdge, RootCauseRef

_SERVICE_NAME = re.compile(
    r"\b(checkout|inventory|notification|payment)(?:-service)?\b",
    re.IGNORECASE,
)
_PROPAGATING_ROOT_TYPES = frozenset(
    {
        RootCauseType.APPLICATION_ERROR.value,
        RootCauseType.DEPLOYMENT_REGRESSION.value,
        RootCauseType.KNOWN_ERROR.value,
    }
)


@dataclass(frozen=True)
class RuntimeEvidenceFact:
    evidence_id: str
    summary: str


@dataclass(frozen=True)
class RuntimeCausalContext:
    causal_chain: tuple[CausalEdge, ...]
    affected_services: tuple[str, ...]


class RuntimeCausalContextBuilder:
    """在 Ground Truth 之外，用候选、入口服务和 Evidence 收敛模型输出。"""

    def build(
        self,
        *,
        root_cause: RootCauseRef | None,
        conclusion_status: str,
        incident_service: str,
        entry_service: str | None,
        evidence: tuple[RuntimeEvidenceFact, ...],
        supporting_evidence_ids: tuple[str, ...],
        proposed_chain: tuple[CausalEdge, ...],
        proposed_affected_services: tuple[str, ...],
    ) -> RuntimeCausalContext:
        if conclusion_status == "NO_ACTIONABLE_ROOT_CAUSE" or root_cause is None:
            return RuntimeCausalContext((), ())

        allowed_evidence_ids = {item.evidence_id for item in evidence}
        support = tuple(
            evidence_id
            for evidence_id in supporting_evidence_ids
            if evidence_id in allowed_evidence_ids
        )
        root_node = normalize_causal_node(root_cause.service)
        resource_node = (
            normalize_causal_node(root_cause.resource)
            if root_cause.resource is not None
            else None
        )
        entry_node = (
            normalize_causal_node(entry_service)
            if entry_service is not None
            else normalize_causal_node(incident_service)
        )
        observed_nodes = self._observed_service_nodes(evidence)
        allowed_nodes = {root_node, entry_node, *observed_nodes}
        if resource_node is not None:
            allowed_nodes.add(resource_node)

        edges: list[CausalEdge] = []
        if resource_node is not None and resource_node != root_node and support:
            edges.append(
                CausalEdge(
                    from_node=resource_node,
                    to_node=root_node,
                    evidence_ids=support,
                )
            )

        propagation = self._propagation_edge(
            root_cause=root_cause,
            root_node=root_node,
            entry_node=entry_node,
            evidence=evidence,
            support=support,
        )
        if propagation is not None:
            edges.append(propagation)

        for edge in proposed_chain:
            normalized = self._validate_proposed_edge(
                edge,
                root_node=root_node,
                resource_node=resource_node,
                allowed_nodes=allowed_nodes,
                observed_nodes=observed_nodes,
                allowed_evidence_ids=allowed_evidence_ids,
            )
            if normalized is not None:
                edges.append(normalized)
        causal_chain = self._deduplicate_edges(edges)

        affected = [normalize_affected_service(root_cause.service)]
        affected.append(normalize_affected_service(entry_service or incident_service))
        affected.extend(
            normalize_affected_service(edge.to_node)
            for edge in causal_chain
            if edge.to_node.startswith("service:")
        )
        for service in proposed_affected_services:
            node = normalize_causal_node(service)
            if node in allowed_nodes and node.startswith("service:"):
                affected.append(normalize_affected_service(node))
        return RuntimeCausalContext(
            causal_chain,
            tuple(dict.fromkeys(affected)),
        )

    @staticmethod
    def _observed_service_nodes(
        evidence: tuple[RuntimeEvidenceFact, ...],
    ) -> set[str]:
        return {
            normalize_causal_node(match.group(0))
            for item in evidence
            for match in _SERVICE_NAME.finditer(item.summary)
        }

    @staticmethod
    def _propagation_edge(
        *,
        root_cause: RootCauseRef,
        root_node: str,
        entry_node: str,
        evidence: tuple[RuntimeEvidenceFact, ...],
        support: tuple[str, ...],
    ) -> CausalEdge | None:
        if not support:
            return None
        if root_node == entry_node:
            if root_cause.type != "cascading_failure":
                return None
            downstream = RuntimeCausalContextBuilder._downstream_edge(
                root_node=root_node,
                evidence=evidence,
                support=support,
            )
            if downstream is None:
                return None
            target, evidence_ids = downstream
            return CausalEdge(
                from_node=root_node,
                to_node=target,
                evidence_ids=evidence_ids,
            )
        entry_alias = normalize_affected_service(entry_node).removesuffix("-service")
        direct_support = tuple(
            item.evidence_id
            for item in evidence
            if re.search(
                rf"(?<![a-z0-9]){re.escape(entry_alias)}(?:-service)?(?![a-z0-9])",
                item.summary,
                re.IGNORECASE,
            )
            and item.evidence_id in support
        )
        if direct_support:
            return CausalEdge(
                from_node=root_node,
                to_node=entry_node,
                evidence_ids=direct_support,
            )
        if root_cause.type in _PROPAGATING_ROOT_TYPES:
            return CausalEdge(
                from_node=root_node,
                to_node=entry_node,
                evidence_ids=support,
            )
        return None

    @staticmethod
    def _downstream_edge(
        *,
        root_node: str,
        evidence: tuple[RuntimeEvidenceFact, ...],
        support: tuple[str, ...],
    ) -> tuple[str, tuple[str, ...]] | None:
        """Extract an explicit downstream service edge for a cascade incident."""
        root_service = normalize_affected_service(root_node)
        for item in evidence:
            if item.evidence_id not in support:
                continue
            match = re.search(
                rf"{re.escape(root_service)}\s*[-=]>\s*"
                r"([a-z][a-z0-9-]*(?:-service)?)",
                item.summary,
                re.IGNORECASE,
            )
            if match is None:
                continue
            target = normalize_causal_node(match.group(1))
            if target == root_node:
                continue
            return target, (item.evidence_id,)
        return None

    @staticmethod
    def _validate_proposed_edge(
        edge: CausalEdge,
        *,
        root_node: str,
        resource_node: str | None,
        allowed_nodes: set[str],
        observed_nodes: set[str],
        allowed_evidence_ids: set[str],
    ) -> CausalEdge | None:
        source = normalize_causal_node(edge.from_node)
        target = normalize_causal_node(edge.to_node)
        evidence_ids = tuple(
            evidence_id
            for evidence_id in edge.evidence_ids
            if evidence_id in allowed_evidence_ids
        )
        if (
            not evidence_ids
            or source not in allowed_nodes
            or target not in allowed_nodes
            or source == target
        ):
            return None
        resource_to_root = resource_node is not None and (
            source == resource_node and target == root_node
        )
        root_to_service = (
            source == root_node
            and target.startswith("service:")
            and target in observed_nodes
        )
        if not (resource_to_root or root_to_service):
            return None
        return CausalEdge(
            from_node=source,
            to_node=target,
            evidence_ids=evidence_ids,
        )

    @staticmethod
    def _deduplicate_edges(edges: list[CausalEdge]) -> tuple[CausalEdge, ...]:
        deduplicated: dict[tuple[str, str], CausalEdge] = {}
        for edge in edges:
            key = edge.from_node, edge.to_node
            existing = deduplicated.get(key)
            if existing is None:
                deduplicated[key] = edge
                continue
            evidence_ids = tuple(
                dict.fromkeys((*existing.evidence_ids, *edge.evidence_ids))
            )
            deduplicated[key] = CausalEdge(
                from_node=edge.from_node,
                to_node=edge.to_node,
                evidence_ids=evidence_ids,
            )
        return tuple(deduplicated.values())
