import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
DEPLOY_ROOT = ROOT / "ops" / "deploy"
K8S_ROOT = DEPLOY_ROOT / "kubernetes"
PRODUCT_ROOT = ROOT / "ops" / "product"


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict), path
    return value


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]["version"]


def test_root_readme_keeps_chinese_product_and_release_boundaries() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert readme.startswith("# DevOps 智能排障 Agent 平台\n")
    assert "## 项目定位与诚实边界" in readme
    assert "## 发布与供应链门禁" in readme
    assert "Example Or Blueprint Only / Not Loaded By Runtime" in readme
    assert f"--tag v{project_version()} --dist-dir dist" in readme
    assert "相比 v0.3.3 的完善" in readme


def test_step5_container_image_is_non_root_and_uses_real_app_entrypoint() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "FROM python:3.12-slim AS builder" in dockerfile
    assert "FROM python:3.12-slim AS runtime" in dockerfile
    assert "uv export --locked --no-dev" in dockerfile
    assert "pip install --no-deps ." in dockerfile
    assert dockerfile.index('pip install "uv==${UV_VERSION}"') < dockerfile.index(
        "RUN python -m venv /opt/venv"
    )
    assert "USER 10001:10001" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert '"main:app"' in dockerfile
    assert "--host" in dockerfile and "0.0.0.0" in dockerfile
    assert "tests/" in dockerignore
    assert ".env" in dockerignore
    assert "ops/deploy/secrets/" in dockerignore


def test_step5_compose_keeps_production_rehearsal_controls() -> None:
    compose = load_yaml(DEPLOY_ROOT / "docker-compose.yml")
    services = compose["services"]
    app = services["app"]

    assert {"app", "migrate", "postgres", "redpanda"} <= set(services)
    assert app["read_only"] is True
    assert "no-new-privileges:true" in app["security_opt"]
    assert app["cap_drop"] == ["ALL"]
    assert app["healthcheck"]["test"]
    assert services["migrate"]["command"] == [
        "python",
        "-m",
        "alembic",
        "upgrade",
        "head",
    ]
    assert "postgres-data" in compose["volumes"]
    assert "redpanda-data" in compose["volumes"]


def test_step5_kubernetes_manifests_keep_security_and_release_gates() -> None:
    expected_kinds = {
        "namespace.yaml": "Namespace",
        "serviceaccount.yaml": "ServiceAccount",
        "configmap.yaml": "ConfigMap",
        "secret.example.yaml": "Secret",
        "deployment.yaml": "Deployment",
        "service.yaml": "Service",
        "hpa.yaml": "HorizontalPodAutoscaler",
        "pdb.yaml": "PodDisruptionBudget",
        "networkpolicy.yaml": "NetworkPolicy",
        "migration-job.yaml": "Job",
    }
    for filename, kind in expected_kinds.items():
        document = load_yaml(K8S_ROOT / filename)
        assert document["kind"] == kind
        if kind != "Namespace":
            assert document["metadata"]["namespace"] == "devops-agent"

    deployment = load_yaml(K8S_ROOT / "deployment.yaml")
    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    expected_image = (
        f"registry.example.com/devops-agent-platform:{project_version()}"
    )
    assert pod_spec["securityContext"]["runAsNonRoot"] is True
    assert pod_spec["automountServiceAccountToken"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert container["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert container["livenessProbe"]["httpGet"]["path"] == "/healthz"
    assert container["resources"]["requests"]["cpu"]
    assert container["image"] == expected_image

    migration = load_yaml(K8S_ROOT / "migration-job.yaml")
    assert (
        migration["spec"]["template"]["spec"]["containers"][0]["image"]
        == expected_image
    )
    assert migration["spec"]["template"]["spec"]["containers"][0]["command"] == [
        "python",
        "-m",
        "alembic",
        "upgrade",
        "head",
    ]
    network = load_yaml(K8S_ROOT / "networkpolicy.yaml")
    assert set(network["spec"]["policyTypes"]) == {"Ingress", "Egress"}


def test_remediation_reclaim_deployment_defaults_are_bounded_and_disabled() -> None:
    config = load_yaml(K8S_ROOT / "configmap.yaml")["data"]
    expected = {
        "DEVOPS_AGENT_REMEDIATION_RECLAIM_WORKER_ENABLED": "false",
        "DEVOPS_AGENT_REMEDIATION_RECLAIM_BATCH_SIZE": "50",
        "DEVOPS_AGENT_REMEDIATION_RECLAIM_INTERVAL_SECONDS": "30",
        "DEVOPS_AGENT_REMEDIATION_RECLAIM_ERROR_BACKOFF_INITIAL_SECONDS": "5",
        "DEVOPS_AGENT_REMEDIATION_RECLAIM_ERROR_BACKOFF_MAX_SECONDS": "300",
        "DEVOPS_AGENT_REMEDIATION_RECLAIM_SHUTDOWN_TIMEOUT_SECONDS": "30",
    }
    assert expected.items() <= config.items()

    production_env = (
        DEPLOY_ROOT / "env.production.example"
    ).read_text(encoding="utf-8")
    local_env = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name, value in expected.items():
        assignment = f"{name}={value}"
        assert assignment in production_env
        assert assignment in local_env


def test_step5_ci_pipeline_keeps_quality_supply_chain_and_migration_gates() -> None:
    workflow = load_yaml(ROOT / ".github" / "workflows" / "ci.yml")
    jobs = workflow["jobs"]

    assert {"test", "image"} <= set(jobs)
    test_steps = "\n".join(
        str(step.get("run", "")) for step in jobs["test"]["steps"]
    )
    image_steps = "\n".join(
        str(step.get("run", "")) + str(step.get("uses", ""))
        for step in jobs["image"]["steps"]
    )
    trivy_steps = [
        step
        for step in jobs["image"]["steps"]
        if "aquasecurity/trivy-action" in str(step.get("uses", ""))
    ]
    assert "uv sync --locked --extra dev" in test_steps
    assert "uv run ruff check" in test_steps
    assert 'uv run pytest -q -m "not live"' in test_steps
    assert "uv run alembic upgrade head --sql" in test_steps
    assert "docker build" in image_steps
    assert "anchore/sbom-action" in image_steps
    assert "aquasecurity/trivy-action" in image_steps
    assert len(trivy_steps) == 2
    assert trivy_steps[0]["with"]["exit-code"] == "0"
    assert trivy_steps[1]["with"] == {
        "image-ref": "devops-agent-platform:${{ github.sha }}",
        "format": "table",
        "severity": "CRITICAL,HIGH",
        "ignore-unfixed": "true",
        "exit-code": "1",
    }


def test_step5_documentation_keeps_target_environment_boundary() -> None:
    step5 = (ROOT / "STEP5_PRODUCTION.md").read_text(encoding="utf-8")
    release = (DEPLOY_ROOT / "release-runbook.md").read_text(encoding="utf-8")
    backup = (DEPLOY_ROOT / "backup-restore.md").read_text(encoding="utf-8")
    capacity = (DEPLOY_ROOT / "capacity-and-slo.md").read_text(
        encoding="utf-8"
    )

    assert "real staging" in step5
    assert "SBOM" in step5
    assert "rollout undo" in release
    assert "Restore Drill" in backup
    assert "Do not invent numbers" in capacity


def test_step6_productization_assets_define_feedback_eval_and_governance() -> None:
    step6 = (ROOT / "STEP6_PRODUCTIZATION.md").read_text(encoding="utf-8")
    console = (PRODUCT_ROOT / "console-workflows.md").read_text(
        encoding="utf-8"
    )
    loop = (PRODUCT_ROOT / "feedback-evaluation-loop.md").read_text(
        encoding="utf-8"
    )
    rag = (PRODUCT_ROOT / "rag-governance.md").read_text(encoding="utf-8")
    remediation = (PRODUCT_ROOT / "remediation-approval.md").read_text(
        encoding="utf-8"
    )

    assert "partial local product surfaces implemented" in step6
    assert "Example Or Blueprint Only" in step6
    assert "Not Loaded By Runtime" in step6
    assert "Implemented Offline (Not Loaded By Runtime)" in step6
    assert "static Ground Truth dataset" in step6
    assert "Platform-level remediation plan API" in step6
    assert "MiniShop allowlisted remediation sandbox" in step6
    assert "Incident Workbench" in console
    assert "RCA Report Review" in console
    assert "Regression Gate" in loop
    assert "Not implemented" in loop
    assert "Feature Flags" in loop
    assert (
        "GET /api/v1/admin/tenants/{tenant_id}/workflow-runs/"
        "{workflow_run_id}/feedback/{feedback_id}/evaluation-candidate"
        in loop
    )
    assert "rca_feedback:export" in loop
    assert "review_required: true" in loop
    assert "does **not** write a file" in loop
    assert "A human must" in loop
    assert "Explicit Offline Curation" in loop
    assert "approved: true" in loop
    assert "does not read PostgreSQL" in loop
    assert "governance draft only" in rag
    assert "tenant-scoped" in rag
    assert "No execution from raw LLM text" in remediation
    assert "Risk, expected effect, rollback action" in remediation
    assert "production automated-remediation engine" in remediation
    assert (ROOT / "ops/remediation/actions.example.json").is_file()
    assert (ROOT / "ops/remediation/minishop-remediation.py").is_file()

    product_readme = (PRODUCT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "Example / blueprint only" in product_readme
    assert "Not loaded by runtime" in product_readme
    assert "single-feedback read-only evaluation-candidate export" in product_readme
    assert "Automatic curation" in product_readme
    assert "Implemented and testable offline" in product_readme
    assert "curate_evaluation_candidate.py" in product_readme
    assert "compare_evaluation_reports.py" in product_readme
    assert "human release review" in product_readme
    assert (PRODUCT_ROOT / "curate_evaluation_candidate.py").is_file()
    assert (PRODUCT_ROOT / "evaluation-candidate.example.json").is_file()
    assert (PRODUCT_ROOT / "compare_evaluation_reports.py").is_file()
    assert (
        PRODUCT_ROOT / "evaluation-responses-candidate.example.yml"
    ).is_file()
    assert (
        PRODUCT_ROOT / "evaluation-curation-review.example.yml"
    ).is_file()


def test_step6_yaml_examples_are_versioned_and_rollout_gated() -> None:
    prompts = load_yaml(PRODUCT_ROOT / "prompt-registry.example.yml")
    flags = load_yaml(PRODUCT_ROOT / "feature-flags.example.yml")
    dataset = load_yaml(PRODUCT_ROOT / "evaluation-dataset.example.yml")
    baseline_responses = load_yaml(
        PRODUCT_ROOT / "evaluation-responses.example.yml"
    )
    candidate_responses = load_yaml(
        PRODUCT_ROOT / "evaluation-responses-candidate.example.yml"
    )
    gate_policy = load_yaml(
        PRODUCT_ROOT / "evaluation-gate-policy.example.yml"
    )

    prompt = prompts["prompts"][0]
    assert prompt["version"] == "v1"
    assert prompt["rollout"]["feature_flag"] == "llm_rca_report_v1"
    assert prompt["evaluation_gate"]["max_unsafe_recommendations"] == 0

    flag_keys = {flag["key"] for flag in flags["flags"]}
    assert {
        "llm_rca_report_v1",
        "historical_incident_rag_v1",
        "remediation_dry_run_v1",
    } <= flag_keys
    assert all(flag["kill_switch"] is True for flag in flags["flags"])

    assert dataset["dataset_id"] == "rca_step6_baseline"
    assert dataset["version"] == "v1"
    assert all(sample["expected"]["required_evidence"] for sample in dataset["samples"])
    assert baseline_responses["dataset_id"] == candidate_responses["dataset_id"]
    assert (
        baseline_responses["dataset_version"]
        == candidate_responses["dataset_version"]
    )
    assert baseline_responses["run"] != candidate_responses["run"]

    assert gate_policy == {
        "schema_version": 1,
        "minimum_overall_pass_rate": 0.95,
        "required_forbidden_claim_free_rate": 1.0,
        "require_no_metric_regression": True,
    }


def test_interview_document_keeps_project_story_and_no_overclaim_boundary() -> None:
    document = (ROOT / "项目面试文档.md").read_text(encoding="utf-8")

    for heading in (
        "## 1. 项目一句话介绍",
        "## 5. 系统架构",
        "## 6. 核心流程",
        "## 10. STAR 法则整理",
        "## 16. 高频追问与标准回答",
        "## 17. 不能说的低级表达",
        "## 21. 3 分钟项目介绍",
        "## 23. 面试被问“有没有上线”怎么回答",
    ):
        assert heading in document
    assert "不会把 mock 环境结果包装成生产指标" in document
    assert "可进入 staging/sandbox 验收" in document
    assert "Step5" in document
    assert "Step6" in document
    assert "Outbox" in document
    assert "租约" in document
