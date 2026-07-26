import json

import pytest

from devops_agent_platform.domain.enums import ToolRiskLevel
from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ResourceNotFound,
)
from devops_agent_platform.infrastructure.remediation.catalog import (
    JsonRemediationActionCatalog,
)


def write_catalog(tmp_path, document):
    path = tmp_path / "remediation-actions.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def base_document(**overrides):
    document = {
        "schema_version": 1,
        "actions": [
            {
                "action_key": "restart_inventory",
                "rollback_action_key": "restore_inventory_revision",
                "risk_level": "MEDIUM",
                "expected_effect": "Restore inventory availability.",
                "allowed_targets": ["inventory"],
            }
        ],
    }
    document.update(overrides)
    return document


def test_loads_registered_action_and_enforces_target(tmp_path) -> None:
    catalog = JsonRemediationActionCatalog(write_catalog(tmp_path, base_document()))

    definition = catalog.get("restart_inventory")

    assert definition.rollback_action_key == "restore_inventory_revision"
    assert definition.risk_level is ToolRiskLevel.MEDIUM
    definition.require_target("inventory")
    with pytest.raises(AppValidationError, match="target is not allowed"):
        definition.require_target("checkout")


def test_unknown_action_is_not_found(tmp_path) -> None:
    catalog = JsonRemediationActionCatalog(write_catalog(tmp_path, base_document()))

    with pytest.raises(ResourceNotFound, match="not registered"):
        catalog.get("restart_checkout")


@pytest.mark.parametrize(
    "document",
    [
        {"schema_version": 1, "actions": [], "extra": True},
        {"schema_version": 2, "actions": []},
        {
            "schema_version": 1,
            "actions": [
                {
                    "action_key": "restart_inventory",
                    "rollback_action_key": "restore_inventory_revision",
                    "risk_level": "MEDIUM",
                    "expected_effect": "Restore inventory availability.",
                    "allowed_targets": ["inventory"],
                    "extra": True,
                }
            ],
        },
    ],
)
def test_rejects_unknown_fields_and_contract_mismatches(
    tmp_path,
    document,
) -> None:
    with pytest.raises(AppValidationError):
        JsonRemediationActionCatalog(write_catalog(tmp_path, document))


def test_rejects_duplicate_action_and_rollback_keys(tmp_path) -> None:
    duplicate_action = base_document(
        actions=[
            base_document()["actions"][0],
            {
                "action_key": "restart_inventory",
                "rollback_action_key": "restore_inventory_backup",
                "risk_level": "LOW",
                "expected_effect": "Restore inventory backup.",
                "allowed_targets": ["inventory"],
            },
        ]
    )
    with pytest.raises(AppValidationError, match="action_key is duplicated"):
        JsonRemediationActionCatalog(write_catalog(tmp_path, duplicate_action))

    duplicate_rollback = base_document(
        actions=[
            base_document()["actions"][0],
            {
                "action_key": "scale_inventory",
                "rollback_action_key": "restore_inventory_revision",
                "risk_level": "LOW",
                "expected_effect": "Scale inventory workers.",
                "allowed_targets": ["inventory"],
            },
        ]
    )
    with pytest.raises(AppValidationError, match="rollback_action_key is duplicated"):
        JsonRemediationActionCatalog(write_catalog(tmp_path, duplicate_rollback))


def test_rejects_invalid_json_and_oversized_catalog(tmp_path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(AppValidationError, match="invalid JSON"):
        JsonRemediationActionCatalog(invalid)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (256 * 1024 + 1))
    with pytest.raises(AppValidationError, match="size is invalid"):
        JsonRemediationActionCatalog(oversized)


def test_rejects_more_than_one_hundred_actions(tmp_path) -> None:
    actions = []
    for index in range(101):
        actions.append(
            {
                "action_key": f"action_{index}",
                "rollback_action_key": f"rollback_{index}",
                "risk_level": "LOW",
                "expected_effect": f"Effect {index}.",
                "allowed_targets": [f"target_{index}"],
            }
        )

    with pytest.raises(AppValidationError, match="contract is invalid"):
        JsonRemediationActionCatalog(
            write_catalog(tmp_path, base_document(actions=actions))
        )
