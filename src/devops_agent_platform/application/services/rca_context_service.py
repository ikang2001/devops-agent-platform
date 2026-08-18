from __future__ import annotations

from dataclasses import dataclass

from devops_agent_platform.application.services.topology_service import (
    BlastRadiusResult,
)
from devops_agent_platform.domain.enums import RCAConclusionStatus


@dataclass(frozen=True)
class RCAContext:
    suspected_root_node: str | None
    causal_chain: tuple[tuple[str, str, tuple[str, ...]], ...]
    affected_services: tuple[str, ...]
    blast_radius: tuple[tuple[str, float], ...]
    knowledge_document_ids: tuple[str, ...] = ()


class RCAContextAssembler:
    def from_blast_radius(
        self,
        result: BlastRadiusResult,
        *,
        evidence_by_edge: dict[tuple[str, str], tuple[str, ...]] | None = None,
        knowledge_document_ids: tuple[str, ...] = (),
    ) -> RCAContext:
        evidence_by_edge = evidence_by_edge or {}
        chain = tuple(
            (source, target, tuple(evidence_by_edge.get((source, target), ())))
            for source, target in result.causal_chain
        )
        return RCAContext(
            suspected_root_node=result.root_node,
            causal_chain=chain,
            affected_services=result.affected_services,
            blast_radius=result.scores,
            knowledge_document_ids=knowledge_document_ids,
        )

    @staticmethod
    def conclusion_status(
        *, evidence_ids: tuple[str, ...], root_node: str | None
    ) -> RCAConclusionStatus:
        if not evidence_ids or root_node is None:
            return RCAConclusionStatus.UNDETERMINED
        return RCAConclusionStatus.CANDIDATE
