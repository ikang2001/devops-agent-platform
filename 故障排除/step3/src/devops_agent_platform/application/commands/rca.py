from dataclasses import dataclass


@dataclass(frozen=True)
class StartRCACommand:
    """启动 RCA 工作流用例的应用层入参。"""

    incident_id: str
    tenant_id: str
    operator_id: str
    trace_id: str
