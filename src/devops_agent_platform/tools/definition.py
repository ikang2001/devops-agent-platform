import re
from dataclasses import dataclass

from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.domain.exceptions import AppValidationError

_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_MAX_TOOL_NAME_LENGTH = 128
_MAX_VERSION_LENGTH = 64
_MAX_PERMISSION_TAG_LENGTH = 128
_MAX_TIMEOUT_MS = 300_000


@dataclass(frozen=True)
class ToolDefinition:
    """Agent 工作流调用工具前必须具备的不可变元数据。

    工具定义通常来自启动配置或代码注册。对象在构造阶段完成校验，可以让
    注册中心、权限检查器和执行器共享同一份可信契约，避免各层重复处理脏数据。
    """

    tool_name: str
    version: str
    risk_level: ToolRiskLevel
    timeout_ms: int
    permission_tags: tuple[str, ...]

    def __post_init__(self) -> None:
        """校验工具元数据，阻止不可执行或存在歧义的定义进入注册中心。

        异常：
            AppValidationError: 字段为空、格式不合法、超时越界或权限标签重复。
        """
        self._validate_tool_name()
        self._validate_version()
        self._validate_risk_level()
        self._validate_timeout()
        self._validate_permission_tags()

    def _validate_tool_name(self) -> None:
        """校验工具名采用稳定的小写命名，避免跨系统大小写规则不一致。"""
        if not isinstance(self.tool_name, str):
            raise AppValidationError("tool_name must be a string")
        if not 1 <= len(self.tool_name) <= _MAX_TOOL_NAME_LENGTH:
            raise AppValidationError(
                f"tool_name length must be between 1 and {_MAX_TOOL_NAME_LENGTH}"
            )
        if _TOOL_NAME_PATTERN.fullmatch(self.tool_name) is None:
            raise AppValidationError(
                "tool_name must use lowercase letters, digits, dots, underscores, "
                "or hyphens"
            )

    def _validate_version(self) -> None:
        """校验版本标识可安全用于日志字段、配置键和注册表索引。"""
        if not isinstance(self.version, str):
            raise AppValidationError("version must be a string")
        if not 1 <= len(self.version) <= _MAX_VERSION_LENGTH:
            raise AppValidationError(
                f"version length must be between 1 and {_MAX_VERSION_LENGTH}"
            )
        if _VERSION_PATTERN.fullmatch(self.version) is None:
            raise AppValidationError("version contains unsupported characters")

    def _validate_risk_level(self) -> None:
        """强制使用领域枚举，避免任意字符串绕过后续风险策略。"""
        if not isinstance(self.risk_level, ToolRiskLevel):
            raise AppValidationError("risk_level must be a ToolRiskLevel")

    def _validate_timeout(self) -> None:
        """限制单次执行时长，防止错误配置长期占用连接和工作线程。"""
        if isinstance(self.timeout_ms, bool) or not isinstance(self.timeout_ms, int):
            raise AppValidationError("timeout_ms must be an integer")
        if not 1 <= self.timeout_ms <= _MAX_TIMEOUT_MS:
            raise AppValidationError(
                f"timeout_ms must be between 1 and {_MAX_TIMEOUT_MS}"
            )

    def _validate_permission_tags(self) -> None:
        """校验权限标签的类型、长度和唯一性。"""
        if not isinstance(self.permission_tags, tuple):
            raise AppValidationError("permission_tags must be a tuple")

        seen: set[str] = set()
        for tag in self.permission_tags:
            if not isinstance(tag, str):
                raise AppValidationError("permission tag must be a string")
            if not 1 <= len(tag) <= _MAX_PERMISSION_TAG_LENGTH:
                raise AppValidationError(
                    "permission tag length must be between "
                    f"1 and {_MAX_PERMISSION_TAG_LENGTH}"
                )
            if tag != tag.strip() or any(character.isspace() for character in tag):
                raise AppValidationError(
                    "permission tag must not contain surrounding or embedded whitespace"
                )
            if tag in seen:
                raise AppValidationError(f"duplicate permission tag: {tag}")
            seen.add(tag)
