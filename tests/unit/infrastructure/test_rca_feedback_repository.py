from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.domain.enums import (
    EvidenceType,
    RCAFeedbackVerdict,
)
from devops_agent_platform.domain.exceptions import ConflictError
from devops_agent_platform.domain.models.rca_feedback import RCAFeedback
from devops_agent_platform.infrastructure.adapters.sqlalchemy import (
    SQLAlchemyRCAFeedbackRepository,
)
from devops_agent_platform.infrastructure.database.base import Base

NOW = datetime(2026, 7, 23, 13, 0, tzinfo=UTC)


def build_feedback(feedback_id: str = "rcf_001") -> RCAFeedback:
    return RCAFeedback(
        feedback_id=feedback_id,
        tenant_id="tenant_001",
        workflow_run_id="wfr_001",
        report_id="rpt_001",
        verdict=RCAFeedbackVerdict.PARTIAL,
        corrected_root_cause="Inventory connection pool exhausted.",
        missing_evidence_types=(EvidenceType.TRACE,),
        unsafe_recommendation_indexes=(0,),
        follow_up_label="needs-runbook",
        notes="Verify rollback.",
        created_by="admin_001",
        idempotency_key_hash="a" * 64,
        request_hash="b" * 64,
        trace_id="trc_feedback_001",
        created_at=NOW,
    )


async def test_repository_round_trips_feedback_and_enforces_idempotency() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            repository = SQLAlchemyRCAFeedbackRepository(session)
            await repository.save(build_feedback())
            await session.commit()

        async with factory() as session:
            repository = SQLAlchemyRCAFeedbackRepository(session)
            items = await repository.list_by_workflow_run(
                "tenant_001",
                "wfr_001",
                10,
            )
            restored = await repository.get_by_idempotency_key_hash(
                "tenant_001",
                "a" * 64,
            )
            by_id = await repository.get_by_id(
                "tenant_001",
                "rcf_001",
            )
            cross_tenant = await repository.get_by_id(
                "tenant_other",
                "rcf_001",
            )

        assert [item.feedback_id for item in items] == ["rcf_001"]
        assert restored is not None
        assert restored.missing_evidence_types == (EvidenceType.TRACE,)
        assert by_id is not None
        assert by_id.feedback_id == "rcf_001"
        assert cross_tenant is None

        async with factory() as session:
            repository = SQLAlchemyRCAFeedbackRepository(session)
            with pytest.raises(ConflictError):
                await repository.save(build_feedback("rcf_002"))
    finally:
        await engine.dispose()
