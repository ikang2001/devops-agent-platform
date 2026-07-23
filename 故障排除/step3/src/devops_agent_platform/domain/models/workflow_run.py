from dataclasses import dataclass
from datetime import datetime

from devops_agent_platform.domain.enums import WorkflowRunStatus


@dataclass
class WorkflowRun:
    """一次 RCA 工作流执行记录。"""

    workflow_run_id: str
    incident_id: str
    status: WorkflowRunStatus
    started_at: datetime
    ended_at: datetime | None
    step_count: int
