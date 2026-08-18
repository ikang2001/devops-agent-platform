from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse

from devops_agent_platform.domain.exceptions import AppValidationError


@dataclass(frozen=True)
class WorkspaceConfig:
    workspace_id: str
    tenant_id: str
    name: str
    prometheus_target: str
    loki_target: str
    tempo_target: str
    knowledge_scope: str = "tenant"
    investigation_policy: str = "fixed_default"
    allowed_tools: tuple[str, ...] = ()
    llm_provider_policy: str = "deterministic"
    retention_days: int = 30
    revision: int = 0
    updated_at: datetime = field(default_factory=lambda: datetime.now().astimezone())

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("workspace_id", self.workspace_id, 64),
            ("tenant_id", self.tenant_id, 128),
            ("name", self.name, 128),
            ("prometheus_target", self.prometheus_target, 512),
            ("loki_target", self.loki_target, 512),
            ("tempo_target", self.tempo_target, 512),
            ("knowledge_scope", self.knowledge_scope, 128),
            ("investigation_policy", self.investigation_policy, 64),
            ("llm_provider_policy", self.llm_provider_policy, 128),
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
        if (
            not isinstance(self.allowed_tools, tuple)
            or len(self.allowed_tools) > 100
            or any(
                not isinstance(tool, str)
                or not 1 <= len(tool) <= 128
                or tool != tool.strip()
                or any(character.isspace() for character in tool)
                for tool in self.allowed_tools
            )
        ):
            raise AppValidationError("allowed_tools is invalid")
        if len(self.allowed_tools) != len(set(self.allowed_tools)):
            raise AppValidationError("allowed_tools must be unique")
        if (
            isinstance(self.retention_days, bool)
            or not isinstance(self.retention_days, int)
            or not 1 <= self.retention_days <= 3650
        ):
            raise AppValidationError("retention_days must be between 1 and 3650")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 0
        ):
            raise AppValidationError("revision must be a non-negative integer")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise AppValidationError("updated_at must include timezone information")
        for field_name in ("prometheus_target", "loki_target", "tempo_target"):
            self._validate_target(field_name, getattr(self, field_name))

    @staticmethod
    def _validate_target(field_name: str, value: str) -> None:
        parsed = urlparse(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise AppValidationError(f"{field_name} must be a safe HTTP endpoint")


class InMemoryWorkspaceRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], WorkspaceConfig] = {}

    async def save(self, workspace: WorkspaceConfig) -> None:
        self._items[(workspace.tenant_id, workspace.workspace_id)] = workspace

    async def get(self, tenant_id: str, workspace_id: str) -> WorkspaceConfig | None:
        item = self._items.get((tenant_id, workspace_id))
        return item if item is None or item.tenant_id == tenant_id else None

    async def list_for_tenant(self, tenant_id: str) -> tuple[WorkspaceConfig, ...]:
        return tuple(
            sorted(
                (
                    item
                    for (item_tenant, _), item in self._items.items()
                    if item_tenant == tenant_id
                ),
                key=lambda item: item.workspace_id,
            )
        )
