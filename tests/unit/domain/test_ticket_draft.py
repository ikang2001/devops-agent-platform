from dataclasses import replace
from datetime import UTC, datetime

import pytest

from devops_agent_platform.domain.enums import (
    TicketDecision,
    TicketDraftStatus,
    TicketPriority,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.ticket_draft import TicketDraft

NOW = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)


def build_draft(**overrides: object) -> TicketDraft:
    """构造有效工单草稿，单测只覆盖自己关心的字段。"""
    values: dict[str, object] = {
        "ticket_draft_id": "tdr_001",
        "tenant_id": "tenant_001",
        "incident_id": "inc_001",
        "workflow_run_id": "wfr_001",
        "report_id": "rpt_001",
        "status": TicketDraftStatus.DRAFT,
        "priority": TicketPriority.P1,
        "title": "Checkout dependency timeout",
        "description": (
            "RCA conclusion: CANDIDATE\n"
            "Confidence: 0.7200\n\n"
            "Payment dependency latency increased."
        ),
        "evidence_ids": ("evd_001",),
        "recommendations": ("Confirm dependency health.",),
        "created_by": "admin_001",
        "idempotency_key_hash": "a" * 64,
        "request_hash": "b" * 64,
        "trace_id": "trc_001",
        "created_at": NOW,
    }
    values.update(overrides)
    return TicketDraft(**values)  # type: ignore[arg-type]


def test_ticket_draft_allows_multiline_description() -> None:
    """工单正文允许段落换行，但仍由容量边界保护。"""
    draft = build_draft()

    assert "\n\n" in draft.description


def test_rejection_reason_allows_multiline_human_text() -> None:
    """人工拒绝原因可保留换行语义，供上层服务按需清洗。"""
    draft = build_draft().decide(
        TicketDecision.REJECT,
        decided_by="approver_001",
        reason="Need owner review\nbefore action.",
        decided_at=NOW,
        idempotency_key_hash="c" * 64,
        request_hash="d" * 64,
        trace_id="trc_decision_001",
    )

    assert draft.decision_reason == "Need owner review\nbefore action."


@pytest.mark.parametrize(
    "overrides",
    [
        {"tenant_id": "tenant\n001"},
        {"tenant_id": "tenant\x7f001"},
        {"title": "Checkout\nforged"},
        {"created_by": "admin\n001"},
        {"trace_id": "trc\t001"},
        {"evidence_ids": ("evd\n001",)},
        {"recommendations": ("Confirm dependency\nhealth.",)},
        {"description": "Valid line\x7fforged"},
        {"description": "Valid line\tforged"},
    ],
)
def test_ticket_draft_rejects_control_characters_in_single_line_fields(
    overrides: dict[str, object],
) -> None:
    """单行字段和非换行控制字符不能进入工单草稿。"""
    with pytest.raises(AppValidationError):
        build_draft(**overrides)


def test_decided_identity_fields_reject_control_characters() -> None:
    """审批身份和 Trace 字段仍然是单行审计字段。"""
    draft = build_draft()

    with pytest.raises(AppValidationError):
        replace(
            draft,
            status=TicketDraftStatus.APPROVED,
            version=2,
            decided_by="approver\x7f001",
            decided_at=NOW,
            decision_idempotency_key_hash="c" * 64,
            decision_request_hash="d" * 64,
            decision_trace_id="trc_decision_001",
        )
