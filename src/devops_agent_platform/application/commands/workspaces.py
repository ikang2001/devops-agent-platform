from __future__ import annotations

from dataclasses import dataclass

from devops_agent_platform.domain.exceptions import AppValidationError


@dataclass(frozen=True)
class UpsertWorkspaceCommand:
    tenant_id: str
    workspace_id: str
    name: str
    prometheus_target: str
    loki_target: str
    tempo_target: str
    knowledge_scope: str
    investigation_policy: str
    allowed_tools: tuple[str, ...]
    llm_provider_policy: str
    retention_days: int
    expected_revision: int
    idempotency_key: str
    requested_by: str
    trace_id: str

    def __post_init__(self) -> None:
        for field_name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("workspace_id", self.workspace_id, 64),
            ("idempotency_key", self.idempotency_key, 128),
            ("requested_by", self.requested_by, 128),
            ("trace_id", self.trace_id, 128),
        ):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= maximum
                or value != value.strip()
                or any(
                    ord(character) < 32 or ord(character) == 127 for character in value
                )
            ):
                raise AppValidationError(f"{field_name} is invalid")
        if (
            isinstance(self.expected_revision, bool)
            or not isinstance(self.expected_revision, int)
            or self.expected_revision < 0
        ):
            raise AppValidationError("expected_revision must be non-negative")
        if not isinstance(self.allowed_tools, tuple):
            raise AppValidationError("allowed_tools must be a tuple")
