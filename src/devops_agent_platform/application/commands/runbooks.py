import json
import re
from dataclasses import dataclass

from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.runbook import (
    MAX_RUNBOOK_CONTENT_BYTES,
)

_TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")


@dataclass(frozen=True)
class SaveRunbookDraftCommand:
    """创建或按乐观版本更新一个 Runbook 草稿。"""

    tenant_id: str
    runbook_key: str
    version: str
    service_name: str
    title: str
    summary: str
    priority: int
    steps: tuple[str, ...]
    tags: tuple[str, ...]
    expected_revision: int
    idempotency_key: str
    requested_by: str
    trace_id: str

    def __post_init__(self) -> None:
        """校验草稿内容、并发版本及审计身份。"""
        _validate_common(
            tenant_id=self.tenant_id,
            runbook_key=self.runbook_key,
            version=self.version,
            expected_revision=self.expected_revision,
            idempotency_key=self.idempotency_key,
            requested_by=self.requested_by,
            trace_id=self.trace_id,
        )
        _validate_text("service_name", self.service_name, 256)
        _validate_text("title", self.title, 256)
        _validate_text("summary", self.summary, 4096)
        if (
            isinstance(self.priority, bool)
            or not isinstance(self.priority, int)
            or not 0 <= self.priority <= 1000
        ):
            raise AppValidationError("priority must be between 0 and 1000")
        if not isinstance(self.steps, tuple) or not 1 <= len(self.steps) <= 20:
            raise AppValidationError("steps must contain between 1 and 20 items")
        for step in self.steps:
            _validate_text("runbook step", step, 2000)
        if (
            not isinstance(self.tags, tuple)
            or len(self.tags) > 20
            or len(set(self.tags)) != len(self.tags)
        ):
            raise AppValidationError("tags is invalid")
        for tag in self.tags:
            if (
                not isinstance(tag, str)
                or not 1 <= len(tag) <= 64
                or _TAG_PATTERN.fullmatch(tag) is None
            ):
                raise AppValidationError("runbook tag is invalid")
        _validate_content_size(self)


@dataclass(frozen=True)
class PublishRunbookCommand:
    """把一个明确版本的草稿发布为租户内唯一生效版本。"""

    tenant_id: str
    runbook_key: str
    version: str
    expected_revision: int
    idempotency_key: str
    requested_by: str
    trace_id: str

    def __post_init__(self) -> None:
        """校验发布目标、并发版本及审计身份。"""
        _validate_common(
            tenant_id=self.tenant_id,
            runbook_key=self.runbook_key,
            version=self.version,
            expected_revision=self.expected_revision,
            idempotency_key=self.idempotency_key,
            requested_by=self.requested_by,
            trace_id=self.trace_id,
        )
        if self.expected_revision < 1:
            raise AppValidationError("publish expected_revision must be positive")


def _validate_common(
    *,
    tenant_id: str,
    runbook_key: str,
    version: str,
    expected_revision: int,
    idempotency_key: str,
    requested_by: str,
    trace_id: str,
) -> None:
    """校验两类 Runbook 管理命令的公共字段。"""
    _validate_text("tenant_id", tenant_id, 128)
    _validate_text("runbook_key", runbook_key, 128)
    _validate_text("version", version, 64)
    _validate_text("idempotency_key", idempotency_key, 128)
    _validate_text("requested_by", requested_by, 128)
    _validate_text("trace_id", trace_id, 128)
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 0
    ):
        raise AppValidationError("expected_revision must be a non-negative integer")


def _validate_text(field_name: str, value: str, maximum: int) -> None:
    """拒绝空白、控制字符和超长管理输入。"""
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise AppValidationError(f"{field_name} is invalid")


def _validate_content_size(command: SaveRunbookDraftCommand) -> None:
    """按 UTF-8 实际大小限制草稿，保证发布后可进入 Evidence。"""
    encoded = json.dumps(
        {
            "title": command.title,
            "summary": command.summary,
            "steps": command.steps,
            "tags": command.tags,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > MAX_RUNBOOK_CONTENT_BYTES:
        raise AppValidationError("runbook content is too large")
