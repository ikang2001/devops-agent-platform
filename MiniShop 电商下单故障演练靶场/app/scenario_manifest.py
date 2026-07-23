from __future__ import annotations

import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Dict, List, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)


SCENARIO_DIRECTORY = Path(__file__).resolve().parents[1] / "scenarios"

SUPPORTED_FAULTS = {
    "checkout-service": frozenset({"latency"}),
    "inventory-service": frozenset({"db_timeout"}),
    "payment-service": frozenset({"payment_error"}),
}
FAULT_ENDPOINTS = {
    ("checkout-service", "latency"): "/faults/checkout-latency",
    ("inventory-service", "db_timeout"): "/faults/inventory-db-timeout",
    ("payment-service", "payment_error"): "/faults/payment-error",
}
ALERT_SEVERITY_MAP = {"P1": "CRITICAL", "P2": "WARNING", "P3": "INFO"}


def _safe_api_path(value: str) -> str:
    if not value.startswith("/") or value.startswith("//"):
        raise ValueError("API path must be root-relative")
    if "\\" in value or "?" in value or "#" in value:
        raise ValueError("API path must not contain a backslash, query, or fragment")
    segments = value[1:].split("/")
    if any(part in {"", ".", ".."} for part in segments):
        raise ValueError("API path must not contain empty or dot segments")
    if any(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._~-]*", part) is None for part in segments):
        raise ValueError("API path contains an unsafe segment")
    return value


def _safe_project_path(value: str) -> str:
    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise ValueError("project path must be relative")
    if "\\" in value or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("project path must use safe POSIX segments")
    if any(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", part) is None for part in value.split("/")):
        raise ValueError("project path contains an unsafe segment")
    if not value.startswith("runbooks/") or posix_path.suffix != ".md":
        raise ValueError("runbook path must point to a Markdown file under runbooks/")
    return value


ApiPath = Annotated[str, AfterValidator(_safe_api_path)]
RunbookPath = Annotated[str, AfterValidator(_safe_project_path)]
FaultService = Literal["checkout-service", "inventory-service", "payment-service"]
EvidenceSource = Literal["http", "loki", "prometheus", "tempo"]


class ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FaultIdentity(ManifestModel):
    service_name: FaultService
    fault_type: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")

    @model_validator(mode="after")
    def validate_supported_fault(self) -> FaultIdentity:
        if self.fault_type not in SUPPORTED_FAULTS[self.service_name]:
            raise ValueError(
                f"unsupported fault_type {self.fault_type!r} for {self.service_name!r}"
            )
        return self


class HttpAction(ManifestModel):
    method: Literal["POST"]
    path: ApiPath
    json_body: Dict[str, JsonValue] = Field(default_factory=dict)
    expected_status: int = Field(ge=100, le=599)


class FaultInjection(FaultIdentity, HttpAction):
    pass


class AlertMapping(ManifestModel):
    source: Literal["alertmanager"]
    alert_name: str = Field(min_length=1, max_length=128)
    source_severity: Literal["P1", "P2", "P3"]
    platform_severity: Literal["INFO", "WARNING", "CRITICAL"]
    service_name: FaultService
    summary: str = Field(min_length=1, max_length=512)
    fingerprint: str = Field(min_length=1, max_length=256)
    external_event_id_prefix: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )

    @model_validator(mode="after")
    def validate_severity_mapping(self) -> AlertMapping:
        expected = ALERT_SEVERITY_MAP[self.source_severity]
        if self.platform_severity != expected:
            raise ValueError(f"{self.source_severity} must map to platform severity {expected}")
        return self


class ExpectedSignal(ManifestModel):
    evidence_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^ev-[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    source: EvidenceSource
    locator: str = Field(min_length=1, max_length=1024)
    assertion: str = Field(min_length=1, max_length=1024)


class RootCause(FaultIdentity):
    category: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    summary: str = Field(min_length=1, max_length=1024)
    causal_chain: List[str] = Field(min_length=2, max_length=8)
    runbook_path: RunbookPath


class GroundTruth(ManifestModel):
    root_cause: RootCause
    required_evidence: List[str] = Field(min_length=1, max_length=20)
    forbidden_claims: List[str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_unique_items(self) -> GroundTruth:
        if len(self.required_evidence) != len(set(self.required_evidence)):
            raise ValueError("required_evidence must contain unique IDs")
        if len(self.forbidden_claims) != len(set(self.forbidden_claims)):
            raise ValueError("forbidden_claims must be unique")
        return self


class ScenarioManifest(FaultIdentity):
    schema_version: Literal["1.0"]
    scenario_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    title: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=1024)
    injection: FaultInjection
    trigger: HttpAction
    cleanup: HttpAction
    alert_mapping: AlertMapping
    expected_signals: List[ExpectedSignal] = Field(min_length=1, max_length=20)
    ground_truth: GroundTruth

    @model_validator(mode="after")
    def validate_cross_references(self) -> ScenarioManifest:
        identity = (self.service_name, self.fault_type)
        if (self.injection.service_name, self.injection.fault_type) != identity:
            raise ValueError("injection identity must match scenario identity")
        if self.injection.path != FAULT_ENDPOINTS[identity]:
            raise ValueError("injection path must match the supported fault endpoint")
        if self.cleanup.path != "/faults/reset":
            raise ValueError("cleanup path must reset MiniShop faults")
        if self.alert_mapping.service_name != self.service_name:
            raise ValueError("alert_mapping service_name must match scenario service_name")
        root_cause = self.ground_truth.root_cause
        if (root_cause.service_name, root_cause.fault_type) != identity:
            raise ValueError("root cause identity must match scenario identity")

        signal_ids = [signal.evidence_id for signal in self.expected_signals]
        if len(signal_ids) != len(set(signal_ids)):
            raise ValueError("expected_signals evidence_id values must be unique")
        unknown_evidence = set(self.ground_truth.required_evidence) - set(signal_ids)
        if unknown_evidence:
            names = ", ".join(sorted(unknown_evidence))
            raise ValueError(f"required_evidence references unknown signal IDs: {names}")
        return self


class ScenarioCatalog(ManifestModel):
    schema_version: Literal["1.0"] = "1.0"
    scenarios: List[ScenarioManifest] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_scenario_ids(self) -> ScenarioCatalog:
        scenario_ids = [scenario.scenario_id for scenario in self.scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("scenario_id values must be unique")
        return self


def load_scenario(path: Path) -> ScenarioManifest:
    return ScenarioManifest.model_validate_json(path.read_text(encoding="utf-8"))


def load_scenario_catalog(directory: Path = SCENARIO_DIRECTORY) -> ScenarioCatalog:
    scenario_paths = sorted(directory.glob("*.json"))
    if not scenario_paths:
        raise ValueError(f"no scenario manifests found in {directory}")
    return ScenarioCatalog(scenarios=[load_scenario(path) for path in scenario_paths])
