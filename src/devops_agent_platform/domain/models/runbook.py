import json
import re
from dataclasses import dataclass
from datetime import datetime

from devops_agent_platform.domain.enums import RunbookStatus
from devops_agent_platform.domain.exceptions import AppValidationError

_TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")
MAX_RUNBOOK_CONTENT_BYTES = 48 * 1024


@dataclass(frozen=True)
class Runbook:
    """经过版本化和发布审核的不可变排障手册快照。

    ``service_name`` 为 ``*`` 时表示租户内通用手册，不能表示跨租户公共数据。
    步骤只是供运维人员阅读的建议，不具备命令执行语义。
    """

    runbook_id: str
    runbook_key: str
    tenant_id: str
    service_name: str
    title: str
    summary: str
    version: str
    status: RunbookStatus
    revision: int
    priority: int
    steps: tuple[str, ...]
    tags: tuple[str, ...]
    published_at: datetime | None
    updated_at: datetime

    def __post_init__(self) -> None:
        """在数据进入检索与 Evidence 边界前完成容量和格式校验。"""
        self._validate_text("runbook_id", self.runbook_id, 64)
        self._validate_text("runbook_key", self.runbook_key, 128)
        self._validate_text("tenant_id", self.tenant_id, 128)
        self._validate_text("service_name", self.service_name, 256)
        self._validate_text("title", self.title, 256)
        self._validate_text("summary", self.summary, 4096)
        self._validate_text("version", self.version, 64)
        if not isinstance(self.status, RunbookStatus):
            raise AppValidationError("status must be a RunbookStatus")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise AppValidationError("revision must be a positive integer")
        if (
            isinstance(self.priority, bool)
            or not isinstance(self.priority, int)
            or not 0 <= self.priority <= 1000
        ):
            raise AppValidationError("priority must be between 0 and 1000")
        self._validate_steps()
        self._validate_tags()
        self._validate_content_size()
        if self.published_at is not None and (
            not isinstance(self.published_at, datetime)
            or self.published_at.tzinfo is None
            or self.published_at.utcoffset() is None
        ):
            raise AppValidationError("published_at must include timezone information")
        if (
            not isinstance(self.updated_at, datetime)
            or self.updated_at.tzinfo is None
            or self.updated_at.utcoffset() is None
        ):
            raise AppValidationError("updated_at must include timezone information")
        if self.status is RunbookStatus.DRAFT:
            if self.published_at is not None:
                raise AppValidationError("draft runbook must not have published_at")
        elif self.published_at is None:
            raise AppValidationError(
                "published or archived runbook requires published_at"
            )
        if self.published_at is not None and self.published_at > self.updated_at:
            raise AppValidationError("published_at must not be later than updated_at")

    @staticmethod
    def _validate_text(
        field_name: str,
        value: str,
        maximum: int,
    ) -> None:
        """拒绝空白、超长和控制字符，避免污染索引与日志。"""
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise AppValidationError(f"{field_name} is invalid")

    def _validate_steps(self) -> None:
        """限制步骤数量和单步大小，防止检索结果挤爆工作流上下文。"""
        if not isinstance(self.steps, tuple) or not 1 <= len(self.steps) <= 20:
            raise AppValidationError("steps must contain between 1 and 20 items")
        for step in self.steps:
            self._validate_text("runbook step", step, 2000)

    def _validate_tags(self) -> None:
        """标签采用规范化小写形式，保证筛选和审计结果稳定。"""
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

    def _validate_content_size(self) -> None:
        """按 UTF-8 实际字节限制单份手册，覆盖中文等多字节内容。"""
        encoded = json.dumps(
            {
                "title": self.title,
                "summary": self.summary,
                "steps": self.steps,
                "tags": self.tags,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > MAX_RUNBOOK_CONTENT_BYTES:
            raise AppValidationError("runbook content is too large")
