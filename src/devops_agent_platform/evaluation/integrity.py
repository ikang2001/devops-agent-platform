from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .schemas import RCAPrediction

LEAKAGE_KEYS = frozenset(
    {
        "ground_truth",
        "expected_root_cause",
        "expected_root_service",
        "expected_root_type",
        "expected_root_resource",
        "required_evidence",
        "required_evidence_types",
        "optional_evidence_types",
        "forbidden_claims",
        "expected_tool_types",
        "forbidden_tool_types",
        "scenario_answer",
    }
)
_LEAKAGE_TEXT = re.compile(
    r"\b(?:ground_truth|expected_root_cause|expected_root_service|"
    r"expected_root_type|expected_root_resource|required_evidence|"
    r"scenario_answer)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LeakageViolation:
    source: str
    path: str
    value: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class BenchmarkLeakageError(ValueError):
    def __init__(self, violations: tuple[LeakageViolation, ...]) -> None:
        self.violations = violations
        details = "; ".join(
            f"{item.source}:{item.path} ({item.reason})" for item in violations
        )
        super().__init__(f"benchmark leakage detected: {details}")


class BenchmarkLeakageGuard:
    """只拦截 Benchmark 专属答案字段，不把普通 timeout/mysql 误报为泄漏。"""

    def scan(self, value: object, *, source: str) -> tuple[LeakageViolation, ...]:
        violations: list[LeakageViolation] = []
        self._scan(value, source=source, path="$", violations=violations)
        return tuple(violations)

    def assert_clean(self, value: object, *, source: str) -> None:
        violations = self.scan(value, source=source)
        if violations:
            raise BenchmarkLeakageError(violations)

    def _scan(
        self,
        value: object,
        *,
        source: str,
        path: str,
        violations: list[LeakageViolation],
    ) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                key_text = str(key)
                if key_text.casefold() in LEAKAGE_KEYS:
                    violations.append(
                        LeakageViolation(
                            source,
                            f"{path}.{key_text}",
                            key_text,
                            "benchmark answer field",
                        )
                    )
                self._scan(
                    nested,
                    source=source,
                    path=f"{path}.{key_text}",
                    violations=violations,
                )
            return
        if isinstance(value, list | tuple):
            for index, nested in enumerate(value):
                self._scan(
                    nested,
                    source=source,
                    path=f"{path}[{index}]",
                    violations=violations,
                )
            return
        if isinstance(value, str):
            match = _LEAKAGE_TEXT.search(value)
            if match:
                violations.append(
                    LeakageViolation(
                        source,
                        path,
                        match.group(0),
                        "benchmark answer token",
                    )
                )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: object) -> str:
    return sha256_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")
    )


def hash_directory(directory: Path, *, pattern: str = "*.json") -> str:
    digest = hashlib.sha256()
    paths = sorted(directory.glob(pattern))
    if not paths:
        raise ValueError(f"no files matching {pattern} in {directory}")
    for path in paths:
        digest.update(path.relative_to(directory).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def cross_incident_evidence_leak_rate(
    predictions: tuple[RCAPrediction, ...] | list[RCAPrediction],
) -> float:
    evidence_by_incident: dict[str, set[str]] = {}
    leaks = 0
    for prediction in predictions:
        evidence = set(prediction.evidence_ids)
        incident = prediction.incident_id or prediction.scenario_id
        if any(
            evidence & other
            for other_incident, other in evidence_by_incident.items()
            if other_incident != incident
        ):
            leaks += 1
        evidence_by_incident[incident] = evidence
    return leaks / len(predictions) if predictions else 0.0


@dataclass(frozen=True)
class BenchmarkProvenance:
    run_id: str
    benchmark_version: str
    git_commit: str
    public_scenario_hash: str
    private_ground_truth_hash: str
    prompt_hash: str
    taxonomy_hash: str
    reasoning_pipeline_hash: str
    knowledge_hash: str
    runbook_hash: str
    model_provider: str
    model: str
    temperature: float
    runs_per_scenario: int
    timestamp: str
    leakage_violation_count: int = 0

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        benchmark_version: str,
        git_commit: str,
        public_scenario_hash: str,
        private_ground_truth_hash: str,
        prompt_hash: str,
        taxonomy_hash: str = "",
        reasoning_pipeline_hash: str = "",
        knowledge_hash: str = "",
        runbook_hash: str = "",
        model_provider: str = "unknown",
        model: str = "unknown",
        temperature: float = 0.0,
        runs_per_scenario: int = 1,
        leakage_violation_count: int = 0,
    ) -> BenchmarkProvenance:
        if runs_per_scenario < 1:
            raise ValueError("runs_per_scenario must be positive")
        return cls(
            run_id=run_id,
            benchmark_version=benchmark_version,
            git_commit=git_commit,
            public_scenario_hash=public_scenario_hash,
            private_ground_truth_hash=private_ground_truth_hash,
            prompt_hash=prompt_hash,
            taxonomy_hash=taxonomy_hash,
            reasoning_pipeline_hash=reasoning_pipeline_hash,
            knowledge_hash=knowledge_hash,
            runbook_hash=runbook_hash,
            model_provider=model_provider,
            model=model,
            temperature=temperature,
            runs_per_scenario=runs_per_scenario,
            timestamp=datetime.now(UTC).isoformat(),
            leakage_violation_count=leakage_violation_count,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
