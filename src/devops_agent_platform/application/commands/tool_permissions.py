from dataclasses import dataclass
from datetime import datetime

from devops_agent_platform.domain.exceptions import AppValidationError

_MAX_PERMISSION_TAGS = 256
_MAX_PERMISSION_TAG_LENGTH = 128


def _contains_ascii_control(value: str) -> bool:
    """识别权限命令中的不可见ASCII控制字符，包含DEL。"""
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _validate_text(
    field_name: str,
    value: str,
    maximum: int,
) -> None:
    """校验权限管理命令中的稳定索引字段。"""
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or _contains_ascii_control(value)
    ):
        raise AppValidationError(f"{field_name} is invalid")


def _validate_expected_version(value: int) -> None:
    """要求调用方显式提交非负期望版本。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AppValidationError(
            "expected_version must be a non-negative integer"
        )


@dataclass(frozen=True)
class SetToolPermissionsCommand:
    """授予或完整替换某个租户操作者的工具权限。"""

    tenant_id: str
    operator_id: str
    permission_tags: tuple[str, ...]
    expires_at: datetime | None
    expected_version: int
    idempotency_key: str
    requested_by: str
    trace_id: str

    def __post_init__(self) -> None:
        """校验身份、并发版本、幂等键和完整权限集合。"""
        _validate_common_fields(
            tenant_id=self.tenant_id,
            operator_id=self.operator_id,
            expected_version=self.expected_version,
            idempotency_key=self.idempotency_key,
            requested_by=self.requested_by,
            trace_id=self.trace_id,
        )
        if self.expires_at is not None and (
            not isinstance(self.expires_at, datetime)
            or self.expires_at.tzinfo is None
            or self.expires_at.utcoffset() is None
        ):
            raise AppValidationError(
                "expires_at must be timezone-aware or None"
            )
        if not isinstance(self.permission_tags, tuple):
            raise AppValidationError(
                "permission_tags must be a tuple"
            )
        if not 1 <= len(self.permission_tags) <= _MAX_PERMISSION_TAGS:
            raise AppValidationError(
                "permission_tags must contain between 1 and "
                f"{_MAX_PERMISSION_TAGS} items"
            )
        seen: set[str] = set()
        for tag in self.permission_tags:
            if (
                not isinstance(tag, str)
                or not 1 <= len(tag) <= _MAX_PERMISSION_TAG_LENGTH
                or tag != tag.strip()
                or _contains_ascii_control(tag)
                or any(character.isspace() for character in tag)
            ):
                raise AppValidationError("permission tag is invalid")
            if tag in seen:
                raise AppValidationError(
                    f"duplicate permission tag: {tag}"
                )
            seen.add(tag)


@dataclass(frozen=True)
class RevokeToolPermissionsCommand:
    """撤销某个租户操作者当前生效的全部工具权限。"""

    tenant_id: str
    operator_id: str
    expected_version: int
    idempotency_key: str
    requested_by: str
    trace_id: str

    def __post_init__(self) -> None:
        """校验撤销目标、并发版本、幂等键和审计身份。"""
        _validate_common_fields(
            tenant_id=self.tenant_id,
            operator_id=self.operator_id,
            expected_version=self.expected_version,
            idempotency_key=self.idempotency_key,
            requested_by=self.requested_by,
            trace_id=self.trace_id,
        )


def _validate_common_fields(
    *,
    tenant_id: str,
    operator_id: str,
    expected_version: int,
    idempotency_key: str,
    requested_by: str,
    trace_id: str,
) -> None:
    """复用两类权限变更命令的公共约束。"""
    _validate_text("tenant_id", tenant_id, 128)
    _validate_text("operator_id", operator_id, 128)
    _validate_text("idempotency_key", idempotency_key, 128)
    _validate_text("requested_by", requested_by, 128)
    _validate_text("trace_id", trace_id, 128)
    _validate_expected_version(expected_version)
