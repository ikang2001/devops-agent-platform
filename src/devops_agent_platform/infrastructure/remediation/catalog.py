import json
from pathlib import Path
from typing import Any

from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ResourceNotFound,
)
from devops_agent_platform.ports.remediation import (
    RemediationActionDefinition,
)

_MAX_CATALOG_BYTES = 256 * 1024
_MAX_ACTIONS = 100
_ROOT_KEYS = {"schema_version", "actions"}
_ACTION_KEYS = {
    "action_key",
    "rollback_action_key",
    "risk_level",
    "expected_effect",
    "allowed_targets",
}


class JsonRemediationActionCatalog:
    """从受部署控制的 JSON 文件加载不可由请求正文改写的动作目录。"""

    def __init__(self, path: str | Path) -> None:
        self._definitions = _load_definitions(Path(path))

    def get(self, action_key: str) -> RemediationActionDefinition:
        if not isinstance(action_key, str):
            raise AppValidationError("action_key is invalid")
        definition = self._definitions.get(action_key)
        if definition is None:
            raise ResourceNotFound("Remediation action is not registered")
        return definition


def _load_definitions(path: Path) -> dict[str, RemediationActionDefinition]:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise AppValidationError(
            "Remediation action catalog cannot be read"
        ) from exc
    if not content or len(content) > _MAX_CATALOG_BYTES:
        raise AppValidationError("Remediation action catalog size is invalid")
    try:
        document = json.loads(
            content.decode("utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AppValidationError(
            "Remediation action catalog is invalid JSON"
        ) from exc
    return _parse_document(document)


def _parse_document(document: Any) -> dict[str, RemediationActionDefinition]:
    if not isinstance(document, dict) or set(document) != _ROOT_KEYS:
        raise AppValidationError("Remediation action catalog root is invalid")
    actions = document.get("actions")
    if (
        document.get("schema_version") != 1
        or not isinstance(actions, list)
        or not 1 <= len(actions) <= _MAX_ACTIONS
    ):
        raise AppValidationError("Remediation action catalog contract is invalid")

    definitions: dict[str, RemediationActionDefinition] = {}
    rollback_keys: set[str] = set()
    for item in actions:
        definition = _parse_action(item)
        if definition.action_key in definitions:
            raise AppValidationError("Remediation action_key is duplicated")
        if definition.rollback_action_key in rollback_keys:
            raise AppValidationError(
                "Remediation rollback_action_key is duplicated"
            )
        definitions[definition.action_key] = definition
        rollback_keys.add(definition.rollback_action_key)
    return definitions


def _parse_action(document: Any) -> RemediationActionDefinition:
    if not isinstance(document, dict) or set(document) != _ACTION_KEYS:
        raise AppValidationError("Remediation action definition is invalid")
    targets = document.get("allowed_targets")
    if not isinstance(targets, list) or not all(
        isinstance(item, str) for item in targets
    ):
        raise AppValidationError("Remediation allowed_targets are invalid")
    try:
        return RemediationActionDefinition(
            action_key=document["action_key"],
            rollback_action_key=document["rollback_action_key"],
            risk_level=ToolRiskLevel(document["risk_level"]),
            expected_effect=document["expected_effect"],
            allowed_targets=tuple(targets),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AppValidationError(
            "Remediation action definition is invalid"
        ) from exc


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")
