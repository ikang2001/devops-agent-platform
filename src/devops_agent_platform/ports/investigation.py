from typing import Protocol

from devops_agent_platform.domain.models.investigation import InvestigationState


class InvestigationCheckpointPort(Protocol):
    async def save(self, state: InvestigationState) -> None: ...

    async def load(
        self, tenant_id: str, incident_id: str
    ) -> InvestigationState | None: ...


class InMemoryInvestigationCheckpoint:
    def __init__(self) -> None:
        self._states: dict[tuple[str, str], InvestigationState] = {}

    async def save(self, state: InvestigationState) -> None:
        key = (state.tenant_id, state.incident_id)
        current = self._states.get(key)
        if current is None or state.checkpoint_version >= current.checkpoint_version:
            self._states[key] = state

    async def load(self, tenant_id: str, incident_id: str) -> InvestigationState | None:
        return self._states.get((tenant_id, incident_id))
