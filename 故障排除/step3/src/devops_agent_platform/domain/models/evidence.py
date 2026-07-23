from dataclasses import dataclass
from datetime import datetime

from devops_agent_platform.domain.enums import EvidenceType


@dataclass(frozen=True)
class Evidence:
    """绑定到单个事故 RCA 过程的结构化证据。"""

    evidence_id: str
    incident_id: str
    evidence_type: EvidenceType
    source: str
    summary: str
    confidence: float
    collected_at: datetime
