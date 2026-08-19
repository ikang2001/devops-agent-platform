from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from devops_agent_platform.application.exceptions import PersistenceError
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.investigation import InvestigationState
from devops_agent_platform.infrastructure.database.models.checkpoint import (
    InvestigationCheckpointRecord,
)
from devops_agent_platform.ports.investigation import InvestigationCheckpointPort
from devops_agent_platform.ports.investigation_codec import (
    deserialize_investigation_state,
    serialize_investigation_state,
)


class SQLAlchemyInvestigationCheckpoint(InvestigationCheckpointPort):
    """持久化动态调查状态，使用版本号拒绝过期 Worker 覆盖新 checkpoint。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def save(self, state: InvestigationState) -> None:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    record = await session.scalar(
                        select(InvestigationCheckpointRecord)
                        .where(
                            InvestigationCheckpointRecord.tenant_id == state.tenant_id,
                            InvestigationCheckpointRecord.incident_id
                            == state.incident_id,
                        )
                        .with_for_update()
                    )
                    if record is None:
                        session.add(
                            InvestigationCheckpointRecord(
                                tenant_id=state.tenant_id,
                                incident_id=state.incident_id,
                                checkpoint_version=state.checkpoint_version,
                                state_json=serialize_investigation_state(state),
                                updated_at=datetime.now().astimezone(),
                            )
                        )
                    elif state.checkpoint_version >= record.checkpoint_version:
                        record.checkpoint_version = state.checkpoint_version
                        record.state_json = serialize_investigation_state(state)
                        record.updated_at = datetime.now().astimezone()
        except SQLAlchemyError as exc:
            raise PersistenceError(
                "could not persist investigation checkpoint"
            ) from exc

    async def load(
        self,
        tenant_id: str,
        incident_id: str,
    ) -> InvestigationState | None:
        try:
            async with self._session_factory() as session:
                record = await session.scalar(
                    select(InvestigationCheckpointRecord).where(
                        InvestigationCheckpointRecord.tenant_id == tenant_id,
                        InvestigationCheckpointRecord.incident_id == incident_id,
                    )
                )
        except SQLAlchemyError as exc:
            raise PersistenceError("could not load investigation checkpoint") from exc
        if record is None:
            return None
        try:
            return deserialize_investigation_state(record.state_json)
        except (AppValidationError, KeyError, TypeError, ValueError) as exc:
            raise PersistenceError(
                "stored investigation checkpoint is invalid"
            ) from exc
