from __future__ import annotations

import json
from datetime import datetime

from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.investigation import (
    InvestigationBudget,
    InvestigationState,
    InvestigationStopReason,
)


def serialize_investigation_state(state: InvestigationState) -> str:
    return json.dumps(
        {
            "incident_id": state.incident_id,
            "tenant_id": state.tenant_id,
            "service_name": state.service_name,
            "environment": state.environment,
            "alert_summary": state.alert_summary,
            "error_fingerprint": state.error_fingerprint,
            "recent_change_type": state.recent_change_type,
            "log_keywords": state.log_keywords,
            "trace_errors": state.trace_errors,
            "completed_steps": state.completed_steps,
            "failed_steps": state.failed_steps,
            "evidence_ids": state.evidence_ids,
            "observed_signals": state.observed_signals,
            "candidate_root_services": state.candidate_root_services,
            "visited_tools": state.visited_tools,
            "tool_call_counts": state.tool_call_counts,
            "llm_calls": state.llm_calls,
            "started_at": state.started_at.isoformat(),
            "checkpoint_version": state.checkpoint_version,
            "stop_reason": state.stop_reason.value if state.stop_reason else None,
            "remaining_budget": state.remaining_budget.__dict__,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def deserialize_investigation_state(value: str) -> InvestigationState:
    try:
        payload = json.loads(value)
        return InvestigationState(
            incident_id=payload["incident_id"],
            tenant_id=payload["tenant_id"],
            service_name=payload.get("service_name"),
            environment=payload.get("environment", "default"),
            alert_summary=payload.get("alert_summary", ""),
            error_fingerprint=payload.get("error_fingerprint", ""),
            recent_change_type=payload.get("recent_change_type", ""),
            log_keywords=list(payload.get("log_keywords", [])),
            trace_errors=list(payload.get("trace_errors", [])),
            completed_steps=list(payload.get("completed_steps", [])),
            failed_steps=list(payload.get("failed_steps", [])),
            evidence_ids=list(payload.get("evidence_ids", [])),
            observed_signals=list(payload.get("observed_signals", [])),
            candidate_root_services=list(payload.get("candidate_root_services", [])),
            visited_tools=list(payload.get("visited_tools", [])),
            tool_call_counts=dict(payload.get("tool_call_counts", {})),
            llm_calls=int(payload.get("llm_calls", 0)),
            started_at=datetime.fromisoformat(payload["started_at"]),
            checkpoint_version=int(payload.get("checkpoint_version", 1)),
            stop_reason=InvestigationStopReason(payload["stop_reason"])
            if payload.get("stop_reason")
            else None,
            remaining_budget=InvestigationBudget(**payload.get("remaining_budget", {})),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AppValidationError("investigation checkpoint is invalid") from exc
