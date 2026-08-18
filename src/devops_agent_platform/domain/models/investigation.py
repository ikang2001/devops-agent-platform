from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from devops_agent_platform.domain.exceptions import AppValidationError


class InvestigationStopReason(StrEnum):
    EVIDENCE_SUFFICIENT = "EVIDENCE_SUFFICIENT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    NO_ACTIONABLE_ROOT_CAUSE = "NO_ACTIONABLE_ROOT_CAUSE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class InvestigationBudget:
    max_steps: int = 8
    max_total_duration_ms: int = 30_000
    max_tool_calls_per_type: int = 2
    max_evidence_count: int = 100
    max_llm_calls: int = 8

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("max_steps", self.max_steps, 100),
            ("max_total_duration_ms", self.max_total_duration_ms, 600_000),
            ("max_tool_calls_per_type", self.max_tool_calls_per_type, 20),
            ("max_evidence_count", self.max_evidence_count, 1000),
            ("max_llm_calls", self.max_llm_calls, 100),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= maximum
            ):
                raise AppValidationError(f"{name} is outside the permitted budget")


@dataclass(frozen=True)
class StepDecision:
    next_tool: str | None
    reason_code: str
    target_service: str | None = None
    required_evidence_types: tuple[str, ...] = ()
    stop: bool = False
    intent: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.next_tool is not None and (
            not isinstance(self.next_tool, str) or not self.next_tool.strip()
        ):
            raise AppValidationError("next_tool must be non-empty when supplied")
        if self.next_tool is not None:
            _validate_text("next_tool", self.next_tool, 128)
        _validate_text("reason_code", self.reason_code, 128)
        if not isinstance(self.reason_code, str) or not self.reason_code.strip():
            raise AppValidationError("reason_code is required")
        if self.target_service is not None and not self.target_service.strip():
            raise AppValidationError("target_service must not be blank")
        if self.target_service is not None:
            _validate_text("target_service", self.target_service, 256)
        if not isinstance(self.intent, dict):
            raise AppValidationError("intent must be an object")


@dataclass
class InvestigationState:
    incident_id: str
    tenant_id: str
    completed_steps: list[str] = field(default_factory=list)
    failed_steps: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    remaining_budget: InvestigationBudget = field(default_factory=InvestigationBudget)
    observed_signals: list[str] = field(default_factory=list)
    candidate_root_services: list[str] = field(default_factory=list)
    visited_tools: list[str] = field(default_factory=list)
    tool_call_counts: dict[str, int] = field(default_factory=dict)
    llm_calls: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    checkpoint_version: int = 1
    stop_reason: InvestigationStopReason | None = None

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("incident_id", self.incident_id, 64),
            ("tenant_id", self.tenant_id, 128),
        ):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= maximum
                or value != value.strip()
                or any(
                    ord(character) < 32 or ord(character) == 127 for character in value
                )
            ):
                raise AppValidationError(f"{name} is invalid")
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise AppValidationError("started_at must include timezone information")
        if self.checkpoint_version < 1:
            raise AppValidationError("checkpoint_version must be positive")

    @property
    def step_count(self) -> int:
        return len(self.completed_steps) + len(self.failed_steps)

    @property
    def evidence_count(self) -> int:
        return len(self.evidence_ids)

    def can_continue(self, now: datetime) -> bool:
        if self.stop_reason is not None:
            return False
        if (
            self.step_count >= self.remaining_budget.max_steps
            or self.evidence_count >= self.remaining_budget.max_evidence_count
            or self.llm_calls >= self.remaining_budget.max_llm_calls
        ):
            return False
        if now.tzinfo is None or now.utcoffset() is None:
            raise AppValidationError("now must include timezone information")
        return (
            now - self.started_at
        ).total_seconds() * 1000 <= self.remaining_budget.max_total_duration_ms

    def record_tool(
        self, tool_name: str, *, succeeded: bool, evidence_ids: tuple[str, ...] = ()
    ) -> None:
        self.tool_call_counts[tool_name] = self.tool_call_counts.get(tool_name, 0) + 1
        if succeeded:
            self.completed_steps.append(tool_name)
        else:
            self.failed_steps.append(tool_name)
        self.visited_tools.append(tool_name)
        for evidence_id in evidence_ids:
            if (
                evidence_id not in self.evidence_ids
                and len(self.evidence_ids) < self.remaining_budget.max_evidence_count
            ):
                self.evidence_ids.append(evidence_id)
        self.checkpoint_version += 1

    def mark_stop(self, reason: InvestigationStopReason) -> None:
        if not isinstance(reason, InvestigationStopReason):
            raise AppValidationError("invalid stop reason")
        self.stop_reason = reason
        self.checkpoint_version += 1


def _validate_text(name: str, value: str, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise AppValidationError(f"{name} is invalid")
