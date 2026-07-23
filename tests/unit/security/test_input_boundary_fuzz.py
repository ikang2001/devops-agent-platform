from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from devops_agent_platform.application.commands.workflow_execution import (
    ExecuteRCAWorkflowCommand,
)
from devops_agent_platform.application.exceptions import MessageContractError
from devops_agent_platform.application.messages.rca_requested import (
    RCARequestedEventV1,
)
from devops_agent_platform.application.messages.ticket_submission_requested import (
    TicketSubmissionRequestedEventV1,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.tools.sanitization import sanitize_evidence_payload

NOW = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)
CONTROL_CHARACTER = st.sampled_from(
    tuple(chr(value) for value in (*range(32), 127))
)
SAFE_SECRET = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters="-_.",
    ),
    min_size=8,
    max_size=64,
)


def _rca_envelope() -> dict[str, Any]:
    return {
        "event_id": "evt_rca_001",
        "event_type": "rca.requested",
        "schema_version": 1,
        "tenant_id": "tenant_001",
        "aggregate_type": "WorkflowRun",
        "aggregate_id": "wfr_001",
        "occurred_at": NOW.isoformat(),
        "trace_id": "trace_001",
        "payload": {
            "tenant_id": "tenant_001",
            "workflow_run_id": "wfr_001",
            "incident_id": "inc_001",
            "operator_id": "operator_001",
            "requested_at": NOW.isoformat(),
        },
    }


def _ticket_envelope() -> dict[str, Any]:
    return {
        "event_id": "evt_ticket_001",
        "event_type": "ticket_submission.requested",
        "schema_version": 1,
        "tenant_id": "tenant_001",
        "aggregate_type": "TicketSubmission",
        "aggregate_id": "tsb_001",
        "occurred_at": NOW.isoformat(),
        "trace_id": "trace_001",
        "payload": {
            "ticket_submission_id": "tsb_001",
            "ticket_draft_id": "tdf_001",
            "workflow_run_id": "wfr_001",
            "target_system": "jira",
            "draft_version": 1,
            "requested_by": "operator_001",
            "requested_at": NOW.isoformat(),
        },
    }


@settings(max_examples=66, deadline=None)
@given(
    character=CONTROL_CHARACTER,
    field_name=st.sampled_from(
        (
            "tenant_id",
            "workflow_run_id",
            "incident_id",
            "operator_id",
            "trace_id",
        )
    ),
)
def test_rca_execution_command_rejects_every_ascii_control_character(
    character: str,
    field_name: str,
) -> None:
    """命令边界必须拒绝 ASCII C0 与 DEL，不能只覆盖常见换行符。"""
    values: dict[str, object] = {
        "tenant_id": "tenant_001",
        "workflow_run_id": "wfr_001",
        "incident_id": "inc_001",
        "operator_id": "operator_001",
        "worker_id": "worker_001",
        "execution_attempt": 1,
        "trace_id": "trace_001",
    }
    values[field_name] = f"safe{character}forged"

    with pytest.raises(AppValidationError, match="control characters"):
        ExecuteRCAWorkflowCommand(**values)  # type: ignore[arg-type]


@settings(max_examples=80, deadline=None)
@given(
    character=CONTROL_CHARACTER,
    location=st.sampled_from(
        (
            ("envelope", "event_id"),
            ("envelope", "trace_id"),
            ("payload", "incident_id"),
            ("payload", "operator_id"),
        )
    ),
)
def test_rca_message_rejects_control_character_in_every_trust_zone(
    character: str,
    location: tuple[str, str],
) -> None:
    """Kafka Envelope 与 Payload 都是外部输入，必须执行同一字符策略。"""
    envelope = deepcopy(_rca_envelope())
    zone, field_name = location
    target = envelope if zone == "envelope" else envelope["payload"]
    assert isinstance(target, dict)
    target[field_name] = f"safe{character}forged"

    with pytest.raises(MessageContractError, match="control characters"):
        RCARequestedEventV1.from_envelope(envelope)


@settings(max_examples=100, deadline=None)
@given(
    character=CONTROL_CHARACTER,
    location=st.sampled_from(
        (
            ("envelope", "event_id"),
            ("envelope", "trace_id"),
            ("payload", "ticket_draft_id"),
            ("payload", "workflow_run_id"),
            ("payload", "target_system"),
            ("payload", "requested_by"),
        )
    ),
)
def test_ticket_message_rejects_control_character_in_every_trust_zone(
    character: str,
    location: tuple[str, str],
) -> None:
    """工单消费者不能信任同进程生产者，反序列化后仍需完整复验。"""
    envelope = deepcopy(_ticket_envelope())
    zone, field_name = location
    target = envelope if zone == "envelope" else envelope["payload"]
    assert isinstance(target, dict)
    target[field_name] = f"safe{character}forged"

    with pytest.raises(MessageContractError, match="control characters"):
        TicketSubmissionRequestedEventV1.from_envelope(envelope)


@settings(max_examples=80, deadline=None)
@given(
    secret=SAFE_SECRET,
    key=st.sampled_from(
        ("password", "api_key", "authorization", "client_secret", "token")
    ),
)
def test_evidence_sanitizer_never_retains_generated_secret(
    secret: str,
    key: str,
) -> None:
    """敏感键整值与普通文本中的赋值形式都不能保留秘密原文。"""
    result = sanitize_evidence_payload(
        {
            key: secret,
            "message": f"provider rejected request: {key}={secret}",
            "nested": [{"cookie": secret}],
        }
    )
    rendered = repr(result.payload)

    assert secret not in rendered
    assert result.redacted_values >= 3
