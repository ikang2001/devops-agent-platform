from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from devops_agent_platform.evaluation.blackbox import (
    BlackBoxE2ERunner,
    _cross_incident_leak_rate,
    _write_runtime_snapshot,
)
from devops_agent_platform.evaluation.generalization import (
    GeneralizationVariant,
    generalization_gap,
    mutate_case,
    robustness_drop,
)
from devops_agent_platform.evaluation.generalization_report import (
    ResultSummary,
    build_generalization_document,
    build_report_provenance,
    load_multi_incident_integrity,
    load_result,
    write_generalization_artifacts,
)
from devops_agent_platform.evaluation.generalization_runner import (
    build_variant_suite,
    transformed_private_directory,
)
from devops_agent_platform.evaluation.integrity import (
    BenchmarkLeakageError,
    BenchmarkLeakageGuard,
)
from devops_agent_platform.evaluation.live_runner import (
    LiveEvidence,
    LiveScenarioInput,
    LiveSuiteInput,
)
from devops_agent_platform.evaluation.scenario_catalog import (
    load_private_ground_truth,
    load_public_scenarios,
    split_manifest,
    validate_holdout_design,
)
from devops_agent_platform.evaluation.schemas import (
    ConclusionStatus,
    EvidenceType,
    RCAPrediction,
    ToolCall,
    ToolStatus,
)


def combined_manifest() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "scenario_id": "holdout-timeout",
        "title": "Storage timeout",
        "description": "A generic storage dependency timeout.",
        "service_name": "checkout-api",
        "fault_type": "dependency_timeout",
        "injection": {"method": "POST", "path": "/faults/enable"},
        "trigger": {"method": "POST", "path": "/checkout"},
        "cleanup": {"method": "POST", "path": "/faults/reset"},
        "alert_mapping": {"service_name": "checkout-api"},
        "ground_truth": {
            "root_cause": {
                "service_name": "checkout-api",
                "root_cause_type": "dependency_timeout",
                "root_cause_resource": "mysql-primary",
            },
            "required_evidence": ["ev-log"],
            "required_evidence_types": ["LOG"],
            "optional_evidence_types": [],
            "causal_chain": [],
            "affected_services": ["checkout-api"],
            "forbidden_claims": ["storage corruption is confirmed"],
            "expected_tool_types": ["logs.query@v1"],
            "forbidden_tool_types": ["shell"],
        },
    }


def runtime_case() -> LiveScenarioInput:
    evidence = LiveEvidence(
        evidence_id="ev-log",
        evidence_type=EvidenceType.LOG,
        source="loki",
        summary="storage operation exceeded deadline",
    )
    return LiveScenarioInput(
        scenario_id="holdout-timeout",
        incident_id="incident-1",
        service_name="checkout-api",
        entry_service="checkout-api",
        summary="checkout request failed after an upstream deadline",
        evidence=(evidence,),
        tool_calls=(
            ToolCall(
                tool_type="logs.query@v1",
                status=ToolStatus.SUCCEEDED,
                evidence_ids=("ev-log",),
            ),
        ),
        investigation_steps=1,
    )


def test_split_manifest_and_loaders_keep_answers_private(tmp_path: Path) -> None:
    public, private = split_manifest(combined_manifest())
    assert "ground_truth" not in public
    assert "expected_signals" not in public
    public_dir = tmp_path / "public"
    private_dir = tmp_path / "private"
    public_dir.mkdir()
    private_dir.mkdir()
    (public_dir / "holdout-timeout.json").write_text(
        json.dumps(public), encoding="utf-8"
    )
    (private_dir / "holdout-timeout.json").write_text(
        json.dumps(private), encoding="utf-8"
    )
    assert load_public_scenarios(public_dir)[0].scenario_id == "holdout-timeout"
    assert load_private_ground_truth(private_dir)[0].root_cause is not None


def test_public_loader_rejects_expected_signals(tmp_path: Path) -> None:
    public, _ = split_manifest(combined_manifest())
    public["expected_signals"] = []
    (tmp_path / "scenario.json").write_text(json.dumps(public), encoding="utf-8")
    with pytest.raises(ValueError, match="private field expected_signals"):
        load_public_scenarios(tmp_path)


def test_repository_hidden_holdout_meets_design_minimums() -> None:
    root = Path("MiniShop 电商下单故障演练靶场/scenarios/holdout")
    summary = validate_holdout_design(load_public_scenarios(root / "public"))
    assert summary.scenario_count >= 8
    assert summary.resource_count >= 4
    assert summary.service_count >= 4
    assert summary.topology_count >= 2


def test_repository_hidden_holdout_v2_ground_truth_is_contract_valid() -> None:
    root = Path("MiniShop 电商下单故障演练靶场/scenarios/holdout-v2")
    summary = validate_holdout_design(load_public_scenarios(root / "public"))
    ground_truth = load_private_ground_truth(root / "private")

    assert summary.scenario_count == 10
    assert len(ground_truth) == 10
    no_actionable = next(
        item
        for item in ground_truth
        if item.expected_conclusion_status is ConclusionStatus.NO_ACTIONABLE_ROOT_CAUSE
    )
    assert no_actionable.root_cause is None
    assert no_actionable.causal_chain == ()
    assert no_actionable.affected_services == ()


def test_leakage_guard_allows_operational_words_but_rejects_answer_fields() -> None:
    guard = BenchmarkLeakageGuard()
    guard.assert_clean(
        {"summary": "mysql timeout exceeded", "error.type": "deadline"},
        source="runtime",
    )
    with pytest.raises(BenchmarkLeakageError):
        guard.assert_clean(
            {"evidence": {"ground_truth": "checkout-api"}},
            source="prompt",
        )


def test_generalization_variants_are_deterministic_and_bounded() -> None:
    case = runtime_case()
    assert mutate_case(case, GeneralizationVariant.CLEAN, seed=2) == case
    renamed = mutate_case(case, GeneralizationVariant.SERVICE_RENAME, seed=2)
    assert renamed.service_name == "checkout-api-blue"
    assert renamed.evidence[0].summary == "storage operation exceeded deadline"

    noisy = mutate_case(case, GeneralizationVariant.NOISE_30, seed=2)
    assert len(noisy.evidence) > len(case.evidence)
    missing = mutate_case(case, GeneralizationVariant.MISSING_TRACES)
    assert len(missing.evidence) == len(case.evidence)
    shifted = mutate_case(case, GeneralizationVariant.TOPOLOGY_SHIFT, seed=2)
    assert EvidenceType.TOPOLOGY in {
        item.evidence_type for item in shifted.evidence
    }
    assert shifted.investigation_steps == len(shifted.tool_calls)
    composite = mutate_case(case, GeneralizationVariant.COMPOSITE_FAULT, seed=2)
    assert composite.investigation_steps == len(composite.tool_calls)
    assert generalization_gap(0.95, 0.70) == pytest.approx(0.25)
    assert robustness_drop(0.80, 0.68) == pytest.approx(0.12)


def test_generalization_benchmark_versions_fit_persistence_contract() -> None:
    path = Path("ops/evaluation/run_generalization_variant.py")
    spec = importlib.util.spec_from_file_location("run_generalization_variant", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert all(
        len(module._benchmark_version(variant)) <= 32
        for variant in GeneralizationVariant
    )


def test_variant_runner_renames_runtime_and_private_ground_truth(
    tmp_path: Path,
) -> None:
    runtime = LiveSuiteInput(
        schema_version="1.0",
        suite="blackbox-real-runtime-snapshot",
        scenario_version="1.0",
        synthetic=False,
        simulation=False,
        cases=(runtime_case(),),
    )
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text(
        json.dumps(runtime.model_dump(mode="json")), encoding="utf-8"
    )
    suite, aliases = build_variant_suite(
        runtime_path,
        GeneralizationVariant.SERVICE_RENAME,
        seed=2,
    )
    assert suite.cases[0].service_name == "checkout-api-blue"
    assert aliases == {"checkout-api": "checkout-api-blue"}

    private_source = tmp_path / "private"
    private_source.mkdir()
    _, private = split_manifest(combined_manifest())
    (private_source / "holdout-timeout.json").write_text(
        json.dumps(private), encoding="utf-8"
    )
    with transformed_private_directory(
        private_source,
        aliases,
        temporary_root=tmp_path / "temp",
    ) as directory:
        transformed = json.loads(
            (Path(directory) / "holdout-timeout.json").read_text(encoding="utf-8")
        )
    assert transformed["ground_truth"]["root_cause"]["service_name"] == (
        "checkout-api-blue"
    )


def test_blackbox_runtime_snapshot_keeps_one_real_case_per_scenario(
    tmp_path: Path,
) -> None:
    case = runtime_case()
    _write_runtime_snapshot(
        tmp_path / "runtime-snapshot.json",
        [(object(), case), (object(), case)],  # type: ignore[list-item]
        scenario_version="1.0",
    )
    snapshot = json.loads(
        (tmp_path / "runtime-snapshot.json").read_text(encoding="utf-8")
    )
    assert snapshot["synthetic"] is False
    assert snapshot["simulation"] is False
    assert len(snapshot["cases"]) == 1


def result_document(top1: float) -> dict[str, object]:
    return {
        "summary": {
            "total_runs": 8,
            "rca_top1_accuracy": top1,
            "strict_rca_accuracy": top1,
            "root_service_accuracy": top1,
            "evidence_recall": 1.0,
            "candidate_recall_at_3": 0.9,
            "unsupported_claim_rate": 0.0,
            "forbidden_claim_rate": 0.0,
        }
    }


def test_generalization_report_is_derived_from_result_artifacts(tmp_path: Path) -> None:
    known_path = tmp_path / "known" / "results.json"
    holdout_path = tmp_path / "holdout" / "results.json"
    known_path.parent.mkdir()
    holdout_path.parent.mkdir()
    known_path.write_text(json.dumps(result_document(0.9)), encoding="utf-8")
    holdout_path.write_text(json.dumps(result_document(0.7)), encoding="utf-8")
    document = build_generalization_document(
        known=load_result(known_path, name="known-clean"),
        holdout=load_result(holdout_path, name="hidden-holdout"),
    )
    assert document["metrics"]["root_resource_ood_accuracy"] == pytest.approx(0.0)
    write_generalization_artifacts(tmp_path / "report", document)
    metrics = json.loads(
        (tmp_path / "report" / "resume-metrics.json").read_text(encoding="utf-8")
    )
    assert metrics["root_resource_ood_accuracy"] == pytest.approx(0.0)
    assert metrics["metrics"]["generalization_gap"] == pytest.approx(0.2)
    assert "不接受手工填写指标" in (
        tmp_path / "report" / "generalization-report.md"
    ).read_text(encoding="utf-8")
    assert metrics["holdout_rca_top1"] == pytest.approx(0.7)
    assert metrics["generalization_gap_pp"] == pytest.approx(20.0)
    assert (tmp_path / "report" / "generalization-bad-cases.jsonl").exists()
    assert (tmp_path / "report" / "benchmark-provenance.json").exists()


def test_generalization_report_derives_worst_components_and_safe_fallback(
    tmp_path: Path,
) -> None:
    result = result_document(0.5)
    result["runs"] = [
        {
            "ground_truth_root_type": "dependency_timeout",
            "ground_truth_root_resource": "provider-a",
            "root_type_correct": True,
            "root_resource_correct": True,
        },
        {
            "ground_truth_root_type": "resource_exhaustion",
            "ground_truth_root_resource": "pool-b",
            "root_type_correct": False,
            "root_resource_correct": False,
        },
    ]
    path = tmp_path / "holdout" / "results.json"
    path.parent.mkdir()
    path.write_text(json.dumps(result), encoding="utf-8")
    (path.parent / "predictions.json").write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "root_cause": None,
                        "conclusion_status": "UNDETERMINED",
                    },
                    {
                        "root_cause": {
                            "service": "svc",
                            "type": "dependency_timeout",
                            "resource": "provider-a",
                        },
                        "conclusion_status": "CANDIDATE",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    summary = load_result(path, name="missing_logs")

    assert summary.application_error_prediction_count == 0
    assert summary.undetermined_prediction_count == 1
    document = build_generalization_document(known=summary, holdout=summary)
    assert document["metrics"]["worst_root_types"][0]["name"] == (
        "resource_exhaustion"
    )
    assert document["metrics"]["worst_root_resources"][0]["name"] == "pool-b"


def test_report_provenance_hashes_all_formal_sources(tmp_path: Path) -> None:
    result_path = tmp_path / "known" / "results.json"
    result_path.parent.mkdir()
    result_path.write_text(json.dumps(result_document(0.9)), encoding="utf-8")
    (result_path.parent / "benchmark-provenance.json").write_text(
        json.dumps({"leakage_violation_count": 0}), encoding="utf-8"
    )

    provenance = build_report_provenance({"known-clean": result_path})

    assert provenance["source_count"] == 1
    assert provenance["leakage_violation_count"] == 0
    assert provenance["sources"]["known-clean"]["results_sha256"]


def test_generalization_variants_use_comparable_clean_baseline(
    tmp_path: Path,
) -> None:
    paths = {}
    for name, top1 in (
        ("known", 0.9),
        ("holdout", 0.7),
        ("variant-clean", 0.8),
        ("noise-30", 0.68),
        ("multi", 0.5),
    ):
        path = tmp_path / name / "results.json"
        path.parent.mkdir()
        document = result_document(top1)
        if name == "multi":
            document["execution"] = {
                "cross_incident_evidence_leak_rate": 0.0,
                "benchmark_leakage_violation_count": 0,
            }
        path.write_text(json.dumps(document), encoding="utf-8")
        paths[name] = path

    document = build_generalization_document(
        known=load_result(paths["known"], name="known-clean"),
        holdout=load_result(paths["holdout"], name="hidden-holdout"),
        variant_baseline=load_result(
            paths["variant-clean"], name="variant-clean"
        ),
        multi_incident=load_result(paths["multi"], name="multi-incident"),
        variants=(load_result(paths["noise-30"], name="noise-30"),),
    )

    assert document["variants"]["noise-30"]["robustness_drop"] == pytest.approx(
        0.12
    )
    assert document["metrics"]["cross_incident_evidence_leak_rate"] == 0.0


def test_generalization_report_includes_multi_incident_integrity(
    tmp_path: Path,
) -> None:
    integrity_path = tmp_path / "multi-incident-integrity.json"
    integrity_path.write_text(
        json.dumps(
            {
                "summary": {
                    "pair_count": 5,
                    "distinct_incident_pair_count": 5,
                    "distinct_workflow_pair_count": 5,
                    "distinct_trace_pair_count": 5,
                    "overlapped_workflow_pair_count": 0,
                    "successful_pair_count": 0,
                    "failed_pair_count": 5,
                }
            }
        ),
        encoding="utf-8",
    )
    document = build_generalization_document(
        known=ResultSummary.from_result("known-clean", result_document(0.9)),
        holdout=ResultSummary.from_result("hidden-holdout", result_document(0.7)),
        multi_incident=ResultSummary.from_result(
            "multi-incident", result_document(0.0)
        ),
        multi_incident_integrity=load_multi_incident_integrity(integrity_path),
    )
    write_generalization_artifacts(tmp_path / "report", document)
    report = (tmp_path / "report" / "generalization-report.md").read_text(
        encoding="utf-8"
    )
    assert "Successful Pairs" in report
    assert "Failed Pairs" in report
    assert "RCA Top-1" in report
    assert "Strict RCA" in report


@pytest.mark.asyncio
async def test_blackbox_runner_executes_public_snapshot_before_private_validation(
    tmp_path: Path,
) -> None:
    public, private = split_manifest(combined_manifest())
    public_dir = tmp_path / "public"
    private_dir = tmp_path / "private"
    public_dir.mkdir()
    private_dir.mkdir()
    (public_dir / "holdout-timeout.json").write_text(
        json.dumps(public), encoding="utf-8"
    )
    (private_dir / "holdout-timeout.json").write_text(
        json.dumps(private), encoding="utf-8"
    )

    class Executor:
        def __init__(self) -> None:
            self.seen_public = False

        async def execute(self, scenario: object) -> LiveScenarioInput:
            assert not hasattr(scenario, "ground_truth")
            self.seen_public = True
            return runtime_case()

    executor = Executor()
    runner = BlackBoxE2ERunner(
        public_directory=public_dir,
        private_directory=private_dir,
        executor=executor,
    )
    suite = await runner.collect()
    assert executor.seen_public is True
    assert suite.cases[0].scenario_id == "holdout-timeout"


@pytest.mark.asyncio
async def test_blackbox_run_validates_catalog_before_external_execution(
    tmp_path: Path,
) -> None:
    public, private = split_manifest(combined_manifest())
    private["ground_truth"]["root_cause"] = None  # type: ignore[index]
    private["ground_truth"]["expected_conclusion_status"] = (  # type: ignore[index]
        "NO_ACTIONABLE_ROOT_CAUSE"
    )
    public_dir = tmp_path / "public"
    private_dir = tmp_path / "private"
    public_dir.mkdir()
    private_dir.mkdir()
    (public_dir / "holdout-timeout.json").write_text(
        json.dumps(public), encoding="utf-8"
    )
    (private_dir / "holdout-timeout.json").write_text(
        json.dumps(private), encoding="utf-8"
    )

    class Executor:
        called = False

        async def execute(self, scenario: object) -> LiveScenarioInput:
            self.called = True
            return runtime_case()

    executor = Executor()
    runner = BlackBoxE2ERunner(
        public_directory=public_dir,
        private_directory=private_dir,
        executor=executor,
    )

    with pytest.raises(ValueError, match="invalid private ground truth"):
        await runner.run(output_directory=tmp_path / "output", git_commit="test")
    assert executor.called is False


def test_cross_incident_leak_rate_detects_reused_evidence_ids() -> None:
    def prediction(run_id: str) -> RCAPrediction:
            return RCAPrediction(
                scenario_id="holdout-timeout",
                incident_id=run_id,
                run_id=run_id,
            root_cause=None,
            conclusion_status=ConclusionStatus.UNDETERMINED,
            confidence=0,
            evidence_ids=("ev-log",),
            investigation_steps=1,
            llm_calls=0,
            latency_ms=0,
            total_tokens=0,
            estimated_cost=0,
        )

    assert _cross_incident_leak_rate(
        (prediction("incident-a-01"), prediction("incident-b-01"))
    ) == pytest.approx(0.5)
