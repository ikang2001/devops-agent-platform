from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .integrity import (
    BenchmarkLeakageGuard,
    BenchmarkProvenance,
    cross_incident_evidence_leak_rate,
    hash_directory,
)
from .live_runner import (
    LiveLLMConfig,
    LiveScenarioInput,
    LiveSuiteInput,
    run_live_benchmark,
)
from .runner import run_benchmark_input
from .scenario_catalog import (
    PublicScenario,
    load_private_ground_truth,
    load_public_scenarios,
    validate_separated_catalog,
)
from .schemas import BenchmarkInput, BenchmarkMetadata, RCAPrediction

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


class BlackBoxExecutor(Protocol):
    """故障执行器接口；实现方只能收到 PublicScenario。"""

    async def execute(
        self, scenario: PublicScenario
    ) -> LiveScenarioInput | BlackBoxExecution:
        """完成 cleanup/inject/trigger/wait/collect，并在返回前清理故障。"""


@dataclass(frozen=True)
class BlackBoxExecution:
    """已经完成平台 RCA Workflow 的黑盒终态及其运行时快照。"""

    runtime: LiveScenarioInput
    prediction: RCAPrediction


class JsonSnapshotExecutor:
    """读取独立的 Runtime 快照，供本地 Reference/黑盒回放使用。"""

    produces_terminal_prediction = False

    def __init__(self, path: Path) -> None:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, Mapping):
            raise ValueError("runtime snapshot document must be an object")
        suite = LiveSuiteInput.model_validate(document)
        self._cases = {case.scenario_id: case for case in suite.cases}

    async def execute(self, scenario: PublicScenario) -> LiveScenarioInput:
        try:
            return self._cases[scenario.scenario_id]
        except KeyError as exc:
            raise ValueError(
                f"runtime snapshot missing public scenario {scenario.scenario_id}"
            ) from exc


class BlackBoxE2ERunner:
    """先执行公开场景，再在 Workflow terminal 后加载 Private GT。"""

    def __init__(
        self,
        *,
        public_directory: Path,
        private_directory: Path,
        executor: BlackBoxExecutor,
        leakage_guard: BenchmarkLeakageGuard | None = None,
    ) -> None:
        self.public_directory = public_directory
        self.private_directory = private_directory
        self.executor = executor
        self.leakage_guard = leakage_guard or BenchmarkLeakageGuard()

    async def collect(self) -> LiveSuiteInput:
        executions = await self._collect_executions(runs_per_scenario=1)
        public_scenarios = load_public_scenarios(self.public_directory)
        return LiveSuiteInput(
            schema_version="1.0",
            suite="blackbox-e2e-runtime",
            scenario_version=public_scenarios[0].schema_version,
            synthetic=False,
            simulation=False,
            cases=tuple(_runtime_of(item) for _, item in executions),
        )

    async def _collect_executions(
        self,
        *,
        runs_per_scenario: int,
        scenario_id: str | None = None,
    ) -> list[tuple[PublicScenario, LiveScenarioInput | BlackBoxExecution]]:
        public_scenarios = load_public_scenarios(self.public_directory)
        if scenario_id is not None:
            public_scenarios = tuple(
                item for item in public_scenarios if item.scenario_id == scenario_id
            )
            if not public_scenarios:
                raise ValueError(f"unknown public scenario: {scenario_id}")
        executions: list[
            tuple[PublicScenario, LiveScenarioInput | BlackBoxExecution]
        ] = []
        for scenario in public_scenarios:
            for _ in range(runs_per_scenario):
                case = await self.executor.execute(scenario)
                runtime = _runtime_of(case)
                if runtime.scenario_id != scenario.scenario_id:
                    raise ValueError(
                        "black-box executor returned a different scenario_id: "
                        f"expected={scenario.scenario_id}, actual={runtime.scenario_id}"
                    )
                self.leakage_guard.assert_clean(
                    runtime.model_dump(mode="json"),
                    source=f"runtime:{scenario.scenario_id}",
                )
                executions.append((scenario, case))
        return executions

    async def run(
        self,
        *,
        output_directory: Path,
        git_commit: str,
        config: LiveLLMConfig | None = None,
        runs_per_scenario: int = 5,
        scenario_id: str | None = None,
        temporary_root: Path | None = None,
        benchmark_version: str = "0.7.0-blackbox",
    ) -> dict[str, object]:
        public_catalog = load_public_scenarios(self.public_directory)
        private_catalog = load_private_ground_truth(self.private_directory)
        if scenario_id is None:
            validate_separated_catalog(public_catalog, private_catalog)

        terminal_executor = bool(
            getattr(self.executor, "produces_terminal_prediction", False)
        )
        execution_runs = runs_per_scenario if terminal_executor and config else 1
        executions = await self._collect_executions(
            runs_per_scenario=execution_runs,
            scenario_id=scenario_id,
        )
        output_directory.parent.mkdir(parents=True, exist_ok=True)

        terminal_predictions = tuple(
            item.prediction
            for _, item in executions
            if isinstance(item, BlackBoxExecution)
        )
        if len(terminal_predictions) == len(executions):
            if config is None:
                return {
                    "schema_version": "1.0",
                    "suite": "blackbox-workflow-terminal",
                    "predictions": [
                        item.model_dump(mode="json")
                        for item in terminal_predictions
                    ],
                }
            result = self._score_terminal_predictions(
                terminal_predictions,
                output_directory=output_directory,
                git_commit=git_commit,
                config=config,
                runs_per_scenario=runs_per_scenario,
                benchmark_version=benchmark_version,
                scenario_id=scenario_id,
            )
            _write_runtime_snapshot(
                output_directory / "runtime-snapshot.json",
                executions,
                scenario_version=public_catalog[0].schema_version,
            )
            return result

        runtime_suite = LiveSuiteInput(
            schema_version="1.0",
            suite="blackbox-e2e-runtime",
            scenario_version=public_catalog[0].schema_version,
            synthetic=False,
            simulation=False,
            cases=tuple(_runtime_of(item) for _, item in executions),
        )
        if config is None:
            return runtime_suite.model_dump(mode="json")

        temp_parent = temporary_root or Path("D:/DevOpsAgentTemp")
        temp_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="blackbox-runtime-", dir=temp_parent
        ) as temp_dir:
            input_path = Path(temp_dir) / "runtime-input.json"
            input_path.write_text(
                json.dumps(
                    runtime_suite.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            result = await run_live_benchmark(
                scenario_directory=self.private_directory,
                input_path=input_path,
                output_directory=output_directory,
                git_commit=git_commit,
                config=config,
                runs_per_scenario=runs_per_scenario,
                scenario_id=scenario_id,
                require_real=True,
                required_scenario_count=None,
                benchmark_version=benchmark_version,
            )
        provenance = BenchmarkProvenance.create(
            run_id=output_directory.name,
            benchmark_version=benchmark_version,
            git_commit=git_commit,
            public_scenario_hash=hash_directory(self.public_directory),
            private_ground_truth_hash=hash_directory(self.private_directory),
            prompt_hash=str(result.get("benchmark", {}).get("prompt_hash", "")),
            taxonomy_hash=_hash_file(
                _PROJECT_ROOT / "src/devops_agent_platform/rca_reasoning/taxonomy.py"
            ),
            reasoning_pipeline_hash=_hash_file(
                _PROJECT_ROOT / "src/devops_agent_platform/rca_reasoning/pipeline.py"
            ),
            knowledge_hash=_optional_hash_directory(
                _PROJECT_ROOT / "ops/knowledge/base", pattern="*.md"
            ),
            runbook_hash=_optional_hash_directory(
                _PROJECT_ROOT / "ops/runbooks/base", pattern="*.md"
            ),
            model_provider=config.provider,
            model=config.model,
            temperature=config.temperature,
            runs_per_scenario=runs_per_scenario,
        )
        provenance.write(output_directory / "benchmark-provenance.json")
        _write_runtime_snapshot(
            output_directory / "runtime-snapshot.json",
            executions,
            scenario_version=public_catalog[0].schema_version,
        )
        return result

    def _score_terminal_predictions(
        self,
        predictions: tuple[RCAPrediction, ...],
        *,
        output_directory: Path,
        git_commit: str,
        config: LiveLLMConfig,
        runs_per_scenario: int,
        benchmark_version: str,
        scenario_id: str | None,
    ) -> dict[str, object]:
        metadata = BenchmarkMetadata(
            benchmark_version=benchmark_version,
            git_commit=git_commit,
            model_provider=config.provider,
            model_name=config.model,
            temperature=config.temperature,
            top_p=config.top_p,
            max_tokens=config.max_tokens,
            prompt_version="platform-workflow-terminal-report",
            investigation_policy="platform-blackbox-terminal-v1",
            scenario_version="1.0",
            timestamp=datetime.now(UTC),
            scenario_hash=_hash_directory(self.public_directory),
            prompt_hash=hashlib.sha256(
                b"platform-workflow-terminal-report"
            ).hexdigest(),
        )
        result = run_benchmark_input(
            self.private_directory,
            BenchmarkInput(benchmark=metadata, runs=predictions),
            output_directory,
            include_extended=True,
            scenario_id=scenario_id,
            execution_metadata={
                "mode": "blackbox_workflow_terminal",
                "runs_per_scenario": runs_per_scenario,
                "cross_incident_evidence_leak_rate": _cross_incident_leak_rate(
                    predictions
                ),
                "benchmark_leakage_violation_count": 0,
            },
        )
        provenance = BenchmarkProvenance.create(
            run_id=output_directory.name,
            benchmark_version=benchmark_version,
            git_commit=git_commit,
            public_scenario_hash=hash_directory(self.public_directory),
            private_ground_truth_hash=hash_directory(self.private_directory),
            prompt_hash=metadata.prompt_hash,
            taxonomy_hash=_hash_file(
                _PROJECT_ROOT / "src/devops_agent_platform/rca_reasoning/taxonomy.py"
            ),
            reasoning_pipeline_hash=_hash_file(
                _PROJECT_ROOT / "src/devops_agent_platform/rca_reasoning/pipeline.py"
            ),
            knowledge_hash=_optional_hash_directory(
                _PROJECT_ROOT / "ops/knowledge/base", pattern="*.md"
            ),
            runbook_hash=_optional_hash_directory(
                _PROJECT_ROOT / "ops/runbooks/base", pattern="*.md"
            ),
            model_provider=config.provider,
            model=config.model,
            temperature=config.temperature,
            runs_per_scenario=runs_per_scenario,
        )
        provenance.write(output_directory / "benchmark-provenance.json")
        return result


def _runtime_of(item: LiveScenarioInput | BlackBoxExecution) -> LiveScenarioInput:
    return item.runtime if isinstance(item, BlackBoxExecution) else item


def _write_runtime_snapshot(
    path: Path,
    executions: list[
        tuple[PublicScenario, LiveScenarioInput | BlackBoxExecution]
    ],
    *,
    scenario_version: str,
) -> None:
    """Persist one leakage-checked real runtime snapshot per scenario."""

    cases_by_scenario: dict[str, LiveScenarioInput] = {}
    for _, execution in executions:
        runtime = _runtime_of(execution)
        cases_by_scenario.setdefault(runtime.scenario_id, runtime)
    suite = LiveSuiteInput(
        schema_version="1.0",
        suite="blackbox-real-runtime-snapshot",
        scenario_version=scenario_version,
        synthetic=False,
        simulation=False,
        cases=tuple(cases_by_scenario.values()),
    )
    if path.exists():
        raise ValueError(f"refusing to overwrite runtime snapshot {path}")
    path.write_text(
        json.dumps(suite.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_directory(directory: Path, *, pattern: str = "*.json") -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.glob(pattern)):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _optional_hash_directory(directory: Path, *, pattern: str) -> str:
    return _hash_directory(directory, pattern=pattern) if directory.exists() else ""


def _cross_incident_leak_rate(predictions: tuple[RCAPrediction, ...]) -> float:
    return cross_incident_evidence_leak_rate(predictions)
