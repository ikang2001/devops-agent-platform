from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import ScenarioGroundTruth

_PRIVATE_KEYS = frozenset(
    {
        "ground_truth",
        "expected_signals",
        "root_cause",
        "expected_root_service",
        "expected_root_type",
        "expected_root_resource",
        "required_evidence",
        "required_evidence_types",
        "optional_evidence_types",
        "causal_chain",
        "affected_services",
        "forbidden_claims",
        "expected_tool_types",
        "forbidden_tool_types",
        "scenario_answer",
    }
)


class PublicScenario(BaseModel):
    """公开给故障执行器的场景描述，不包含答案或预期 Evidence。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(min_length=1, max_length=32)
    scenario_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=4096)
    service_name: str = Field(min_length=1, max_length=128)
    fault_type: str = Field(min_length=1, max_length=128)
    injection: dict[str, Any]
    trigger: dict[str, Any]
    cleanup: dict[str, Any]
    alert_mapping: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_public_payload(self) -> PublicScenario:
        for field_name in ("injection", "trigger", "cleanup"):
            if not getattr(self, field_name):
                raise ValueError(f"public scenario {field_name} must not be empty")
        return self


@dataclass(frozen=True)
class HoldoutDesignSummary:
    scenario_count: int
    resource_count: int
    service_count: int
    topology_count: int


def validate_holdout_design(
    scenarios: tuple[PublicScenario, ...],
) -> HoldoutDesignSummary:
    """校验 Holdout 至少覆盖未知资源、服务和拓扑变体。"""

    if len(scenarios) < 8:
        raise ValueError("hidden holdout requires at least eight scenarios")
    resources = {
        str(item.environment.get("resource_system"))
        for item in scenarios
        if item.environment.get("resource_system")
    }
    services = {item.service_name for item in scenarios}
    topologies = {
        str(item.environment.get("topology_variant"))
        for item in scenarios
        if item.environment.get("topology_variant")
    }
    if len(resources) < 4:
        raise ValueError("hidden holdout requires at least four resource systems")
    if len(services) < 4:
        raise ValueError("hidden holdout requires at least four service names")
    if len(topologies) < 2:
        raise ValueError("hidden holdout requires at least two topology variants")
    return HoldoutDesignSummary(
        scenario_count=len(scenarios),
        resource_count=len(resources),
        service_count=len(services),
        topology_count=len(topologies),
    )


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read scenario document {path}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"scenario document {path} must be an object")
    return value


def _find_private_key(value: object, path: str = "$") -> tuple[str, str] | None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            if key_text.casefold() in _PRIVATE_KEYS:
                return f"{path}.{key_text}", key_text
            found = _find_private_key(nested, f"{path}.{key_text}")
            if found is not None:
                return found
    elif isinstance(value, list | tuple):
        for index, nested in enumerate(value):
            found = _find_private_key(nested, f"{path}[{index}]")
            if found is not None:
                return found
    return None


def load_public_scenarios(directory: Path) -> tuple[PublicScenario, ...]:
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ValueError(f"no public scenario manifests found in {directory}")
    scenarios: list[PublicScenario] = []
    for path in paths:
        document = _load_json(path)
        private_key = _find_private_key(document)
        if private_key is not None:
            location, key = private_key
            raise ValueError(
                f"public scenario {path} contains private field {key} "
                f"at {location}"
            )
        try:
            scenarios.append(PublicScenario.model_validate(document))
        except ValueError as exc:
            raise ValueError(f"invalid public scenario {path}") from exc
    ids = [item.scenario_id for item in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate public scenario_id")
    return tuple(scenarios)


def load_private_ground_truth(
    directory: Path,
) -> tuple[ScenarioGroundTruth, ...]:
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ValueError(f"no private ground truth manifests found in {directory}")
    catalog: list[ScenarioGroundTruth] = []
    for path in paths:
        document = _load_json(path)
        try:
            catalog.append(ScenarioGroundTruth.from_manifest(document))
        except ValueError as exc:
            raise ValueError(f"invalid private ground truth {path}") from exc
    ids = [item.scenario_id for item in catalog]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate private ground truth scenario_id")
    return tuple(catalog)


def validate_separated_catalog(
    public_scenarios: tuple[PublicScenario, ...],
    private_ground_truth: tuple[ScenarioGroundTruth, ...],
) -> None:
    public_ids = {item.scenario_id for item in public_scenarios}
    private_ids = {item.scenario_id for item in private_ground_truth}
    if public_ids != private_ids:
        raise ValueError(
            "public/private scenario IDs differ: "
            f"public_only={sorted(public_ids - private_ids)}, "
            f"private_only={sorted(private_ids - public_ids)}"
        )


def split_manifest(
    document: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """将历史合并 Manifest 拆成 v0.7 Public/Private 两个文档。"""

    required = (
        "schema_version",
        "scenario_id",
        "title",
        "description",
        "service_name",
        "fault_type",
        "injection",
        "trigger",
        "cleanup",
    )
    missing = [name for name in required if name not in document]
    if missing:
        raise ValueError(f"scenario manifest missing fields: {', '.join(missing)}")
    ground_truth = document.get("ground_truth")
    if not isinstance(ground_truth, Mapping):
        raise ValueError("scenario manifest ground_truth must be an object")
    public = {
        name: document[name]
        for name in (
            *required,
            "alert_mapping",
            "environment",
        )
        if name in document
    }
    private = {
        "schema_version": document["schema_version"],
        "scenario_id": document["scenario_id"],
        "ground_truth": dict(ground_truth),
    }
    PublicScenario.model_validate(public)
    ScenarioGroundTruth.from_manifest(private)
    return public, private
