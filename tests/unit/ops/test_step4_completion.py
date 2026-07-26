import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOT = PROJECT_ROOT / "src" / "devops_agent_platform"
ACCEPTANCE_ROOT = PROJECT_ROOT / "ops" / "acceptance"


def _python_sources() -> list[Path]:
    """返回生产源码，排除缓存和测试文件。"""
    return sorted(SOURCE_ROOT.rglob("*.py"))


def test_production_logging_does_not_attach_raw_exceptions() -> None:
    """Step 4 日志边界禁止重新引入 traceback 或原始异常正文。"""
    violations: list[str] = []
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if (
                isinstance(function, ast.Attribute)
                and function.attr == "exception"
                and isinstance(function.value, ast.Name)
                and function.value.id == "logger"
            ):
                violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
            for keyword in node.keywords:
                if keyword.arg != "exc_info":
                    continue
                if isinstance(keyword.value, ast.Constant) and keyword.value.value in {
                    None,
                    False,
                }:
                    continue
                violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")

    assert violations == []


def test_skeleton_adapters_do_not_claim_stage_work_is_unfinished() -> None:
    """Skeleton 是长期 fail-closed 模式，不能再标记成阶段遗留任务。"""
    stale_markers = (
        "Step 3 placeholder",
        "Step 4 placeholder",
        "Step 3 只暴露",
        "用于 Step 3",
    )
    violations: list[str] = []
    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in stale_markers):
            violations.append(str(path.relative_to(PROJECT_ROOT)))

    assert violations == []


def test_text_dataclasses_keep_c0_and_del_validation() -> None:
    """领域、命令和消息的文本对象必须保留 C0 与 DEL 双重校验。"""
    roots = (
        SOURCE_ROOT / "domain" / "models",
        SOURCE_ROOT / "application" / "commands",
        SOURCE_ROOT / "application" / "messages",
    )
    violations: list[str] = []
    audited_classes = 0
    for root in roots:
        for path in sorted(root.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            text_dataclasses = [
                node
                for node in tree.body
                if isinstance(node, ast.ClassDef)
                and any(
                    "dataclass" in ast.unparse(decorator)
                    for decorator in node.decorator_list
                )
                and any(
                    isinstance(item, ast.AnnAssign)
                    and "str" in ast.unparse(item.annotation)
                    for item in node.body
                )
            ]
            if not text_dataclasses:
                continue
            audited_classes += len(text_dataclasses)
            if "ord(character) < 32" not in source or "== 127" not in source:
                violations.extend(
                    f"{path.relative_to(PROJECT_ROOT)}:{item.name}"
                    for item in text_dataclasses
                )

    # New bounded DTO/command/domain records may legitimately increase this
    # count; the dynamic scan above is the guard, not a frozen class total.
    assert audited_classes >= 28
    assert violations == []


def test_step4_live_acceptance_assets_keep_required_scenarios() -> None:
    """发布门禁必须保留真实依赖、负载阈值和故障恢复入口。"""
    runner = (ACCEPTANCE_ROOT / "run-step4-live.ps1").read_text(
        encoding="utf-8"
    )
    fault_runner = (ACCEPTANCE_ROOT / "invoke-step4-fault.ps1").read_text(
        encoding="utf-8"
    )
    load_test = (
        PROJECT_ROOT / "ops" / "load" / "step4-acceptance.js"
    ).read_text(encoding="utf-8")

    for required in (
        "DEVOPS_AGENT_TEST_POSTGRES_URL",
        "DEVOPS_AGENT_TEST_KAFKA_BOOTSTRAP_SERVERS",
        "DEVOPS_AGENT_TEST_PROMETHEUS_URL",
        "DEVOPS_AGENT_TEST_LOKI_URL",
        "DEVOPS_AGENT_TEST_TEMPO_URL",
        "DEVOPS_AGENT_TEST_OIDC_TOKEN",
        "DEVOPS_AGENT_TEST_LLM_API_KEY",
        "DEVOPS_AGENT_TEST_TICKETING_ENDPOINT_URL",
    ):
        assert required in runner
    assert "Ready $false" in fault_runner
    assert "Ready $true" in fault_runner
    assert 'http_req_failed: ["rate<0.01"]' in load_test
    assert '"p(95)<500"' in load_test
    assert '"p(99)<1000"' in load_test
    assert 'dropped_iterations: ["count==0"]' in load_test


def test_local_acceptance_stack_keeps_real_stateful_dependencies() -> None:
    """本地沙箱必须保留真实PostgreSQL/Kafka和OIDC边界说明。"""
    compose = (ACCEPTANCE_ROOT / "docker-compose.step4.yml").read_text(
        encoding="utf-8"
    )
    runner = (ACCEPTANCE_ROOT / "run-step4-local.ps1").read_text(
        encoding="utf-8"
    )
    mock_http = (ACCEPTANCE_ROOT / "mock_http.py").read_text(
        encoding="utf-8"
    )

    assert "postgres:16-alpine" in compose
    assert "redpandadata/redpanda" in compose
    assert "15432:5432" in compose
    assert "19092:19092" in compose
    assert "STEP4_ACCEPTANCE_DATA_ROOT" in compose
    assert "http-sandbox" in compose
    assert "DEVOPS_AGENT_TEST_POSTGRES_URL" in runner
    assert "DEVOPS_AGENT_TEST_KAFKA_BOOTSTRAP_SERVERS" in runner
    assert "$DataRoot" in runner
    assert "STEP4_ACCEPTANCE_DATA_ROOT" in runner
    assert "podman" in runner
    assert "docker" in runner
    assert "-ContainerCli podman" in (
        ACCEPTANCE_ROOT / "README.md"
    ).read_text(encoding="utf-8")
    assert 'pytest tests/live -m live -q -k "not oidc"' in runner
    assert "-IncludeOidc" in runner
    assert "production authenticator requires HTTPS JWKS" in (
        ACCEPTANCE_ROOT / "README.md"
    ).read_text(encoding="utf-8")
    assert "/api/v1/query_range" in mock_http
    assert "/loki/api/v1/query_range" in mock_http
    assert "/api/search" in mock_http
    assert "/v1/responses" in mock_http
    assert "/api/tickets" in mock_http
