from datetime import UTC, datetime, timedelta

import pytest

from devops_agent_platform.domain.enums import TicketSubmissionStatus
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.ticket_submission import TicketSubmission

NOW = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)


def build_submission(**overrides: object) -> TicketSubmission:
    """构造默认未完成的外部工单提交请求。"""
    values: dict[str, object] = {
        "ticket_submission_id": "tsb_001",
        "tenant_id": "tenant_001",
        "ticket_draft_id": "tdf_001",
        "workflow_run_id": "wfr_001",
        "target_system": "jira",
        "status": TicketSubmissionStatus.REQUESTED,
        "idempotency_key_hash": "a" * 64,
        "request_hash": "b" * 64,
        "requested_by": "admin_001",
        "trace_id": "trc_submission_001",
        "requested_at": NOW,
    }
    values.update(overrides)
    return TicketSubmission(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [
        {"tenant_id": "tenant\n001"},
        {"ticket_draft_id": "tdf\t001"},
        {"workflow_run_id": "wfr\r001"},
        {"requested_by": "admin\n001"},
        {"trace_id": "trc\x7f001"},
        {"trace_id": "trc\t001"},
    ],
)
def test_ticket_submission_rejects_control_character_identities(
    overrides: dict[str, object],
) -> None:
    """提交请求身份和链路字段必须保持单行可审计。"""
    with pytest.raises(AppValidationError, match="invalid"):
        build_submission(**overrides)


def test_submitted_result_rejects_control_character_external_fields() -> None:
    """外部系统返回的工单字段不能污染后续查询和审计输出。"""
    submission = build_submission()

    with pytest.raises(AppValidationError, match="external_ticket_id"):
        submission.mark_submitted(
            external_ticket_id="JIRA-101\x7fforged",
            external_ticket_url="https://jira.example/browse/JIRA-101",
            completed_at=NOW + timedelta(minutes=1),
            idempotency_key_hash="c" * 64,
            request_hash="d" * 64,
            trace_id="trc_result_001",
            completed_by="ticket-worker-001",
        )

    with pytest.raises(AppValidationError, match="external_ticket_url"):
        submission.mark_submitted(
            external_ticket_id="JIRA-101",
            external_ticket_url=("https://jira.example/browse/JIRA-101\tforged"),
            completed_at=NOW + timedelta(minutes=1),
            idempotency_key_hash="c" * 64,
            request_hash="d" * 64,
            trace_id="trc_result_001",
            completed_by="ticket-worker-001",
        )


def test_failed_result_rejects_control_character_reason() -> None:
    """失败原因会进入状态查询，不能包含伪造换行。"""
    submission = build_submission()

    with pytest.raises(AppValidationError, match="failure_reason"):
        submission.mark_failed(
            failure_reason="Provider rejected request\x7fforged",
            completed_at=NOW + timedelta(minutes=1),
            idempotency_key_hash="c" * 64,
            request_hash="d" * 64,
            trace_id="trc_result_001",
            completed_by="ticket-worker-001",
        )
