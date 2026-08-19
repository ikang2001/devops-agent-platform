from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from devops_agent_platform.evaluation.schemas import (
    BenchmarkInput,
    BenchmarkMetadata,
    ConclusionStatus,
    RCAPrediction,
)
from ops.evaluation.merge_live_benchmark_shards import merge_benchmark_shards


def _metadata() -> BenchmarkMetadata:
    return BenchmarkMetadata(
        benchmark_version="2.0-live",
        git_commit="1234567",
        model_provider="provider",
        model_name="model",
        temperature=0,
        prompt_version="prompt-v1",
        investigation_policy="bounded_dynamic_v1",
        scenario_version="1.0",
        timestamp=datetime(2026, 8, 19, tzinfo=UTC),
    )


def _prediction(run_id: str, *, total_tokens: int) -> RCAPrediction:
    return RCAPrediction(
        scenario_id="scenario-a",
        run_id=run_id,
        root_cause=None,
        conclusion_status=ConclusionStatus.UNDETERMINED,
        confidence=0,
        investigation_steps=0,
        llm_calls=1,
        latency_ms=1,
        total_tokens=total_tokens,
        estimated_cost=0,
    )


def _write_input(path: Path, runs: tuple[RCAPrediction, ...]) -> None:
    document = BenchmarkInput(benchmark=_metadata(), runs=runs)
    path.write_text(
        json.dumps(document.model_dump(mode="json")),
        encoding="utf-8",
    )


def test_merge_benchmark_shards_replaces_only_matching_run_ids(tmp_path: Path) -> None:
    base = tmp_path / "base.json"
    shard = tmp_path / "shard.json"
    output = tmp_path / "merged.json"
    provenance = tmp_path / "provenance.json"
    _write_input(
        base,
        (
            _prediction("live-scenario-a-01", total_tokens=10),
            _prediction("live-scenario-a-02", total_tokens=20),
        ),
    )
    _write_input(
        shard,
        (_prediction("live-scenario-a-02", total_tokens=99),),
    )

    merged = merge_benchmark_shards(
        (base, shard),
        output_path=output,
        provenance_path=provenance,
        required_runs=2,
        runs_per_scenario=2,
    )

    assert [item.total_tokens for item in merged.runs] == [10, 99]
    evidence = json.loads(provenance.read_text(encoding="utf-8"))
    assert evidence["replaced_run_ids"] == ["live-scenario-a-02"]
    assert [item["selected_runs"] for item in evidence["inputs"]] == [1, 1]
