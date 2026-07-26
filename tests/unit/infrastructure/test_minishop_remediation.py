import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
)
from devops_agent_platform.infrastructure.remediation import (
    MiniShopRemediationConfig,
    MiniShopRemediationService,
    RemediationStatus,
    SQLiteRemediationApprovalStore,
)

NOW = datetime(2026, 7, 23, 16, 0, tzinfo=UTC)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS = PROJECT_ROOT / "MiniShop 电商下单故障演练靶场" / "scenarios"


def build_service(
    tmp_path: Path,
    handler,
    *,
    enabled: bool = True,
    clock=lambda: NOW,
    scenarios: Path = SCENARIOS,
) -> MiniShopRemediationService:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://minishop.test",
    )
    return MiniShopRemediationService(
        MiniShopRemediationConfig(
            scenario_directory=scenarios,
            target_base_url="http://minishop.test",
            enabled=enabled,
            allow_insecure_http=True,
        ),
        SQLiteRemediationApprovalStore(tmp_path / "approvals.sqlite3"),
        http_client=client,
        clock=clock,
    )


def create_and_approve(service: MiniShopRemediationService):
    plan = service.create_plan(
        scenario_id="inventory-db-timeout",
        created_by="operator_author",
        trace_id="trc_plan_001",
    )
    approved = service.approve(
        plan_id=plan.plan_id,
        expected_version=plan.version,
        approved_by="operator_reviewer",
        idempotency_key="approve_key_001",
        trace_id="trc_approve_001",
    )
    return plan, approved


async def test_allowlisted_plan_executes_cleanup_and_rolls_back_injection(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    service = build_service(tmp_path, handler)
    try:
        plan, approved = create_and_approve(service)
        executed = await service.execute(
            plan_id=plan.plan_id,
            expected_version=approved.version,
            actor="operator_executor",
            idempotency_key="execute_key_001",
            trace_id="trc_execute_001",
        )
        rolled_back = await service.rollback(
            plan_id=plan.plan_id,
            expected_version=executed.version,
            actor="operator_executor",
            idempotency_key="rollback_key_001",
            trace_id="trc_rollback_001",
        )
    finally:
        await service.close()

    assert executed.status is RemediationStatus.EXECUTED
    assert rolled_back.status is RemediationStatus.ROLLED_BACK
    assert [request.url.path for request in requests] == [
        "/faults/reset",
        "/faults/inventory-db-timeout",
    ]
    assert json.loads(requests[0].content) == {}
    assert json.loads(requests[1].content)["delay_ms"] == 1200
    assert requests[0].headers["idempotency-key"] == "execute_key_001"
    actions = [item.action for item in service.list_audit(plan.plan_id)]
    assert actions == [
        "PLAN_CREATED",
        "PLAN_APPROVED",
        "EXECUTION_STARTED",
        "EXECUTION_SUCCEEDED",
        "ROLLBACK_STARTED",
        "ROLLBACK_SUCCEEDED",
    ]


async def test_kill_switch_and_human_approval_are_mandatory(
    tmp_path: Path,
) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    disabled = build_service(tmp_path, handler, enabled=False)
    try:
        plan = disabled.create_plan(
            scenario_id="payment-error",
            created_by="operator_author",
            trace_id="trc_plan_002",
        )
        with pytest.raises(ConflictError, match="kill switch"):
            await disabled.execute(
                plan_id=plan.plan_id,
                expected_version=plan.version,
                actor="operator_executor",
                idempotency_key="execute_key_002",
                trace_id="trc_execute_002",
            )
        with pytest.raises(ConflictError, match="different operator"):
            disabled.approve(
                plan_id=plan.plan_id,
                expected_version=plan.version,
                approved_by="operator_author",
                idempotency_key="approve_key_002",
                trace_id="trc_approve_002",
            )
    finally:
        await disabled.close()
    assert calls == 0


async def test_execution_requires_approved_unexpired_plan(
    tmp_path: Path,
) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    service = build_service(tmp_path, handler)
    try:
        plan = service.create_plan(
            scenario_id="checkout-latency",
            created_by="operator_author",
            trace_id="trc_plan_003",
            ttl_seconds=60,
        )
        with pytest.raises(ConflictError, match="must be APPROVED"):
            await service.execute(
                plan_id=plan.plan_id,
                expected_version=plan.version,
                actor="operator_executor",
                idempotency_key="execute_key_003",
                trace_id="trc_execute_003",
            )
    finally:
        await service.close()
    assert calls == 0

    expired = build_service(
        tmp_path / "expired",
        handler,
        clock=lambda: NOW + timedelta(minutes=2),
    )
    # Construct the plan with the earlier clock, then move the clock past expiry.
    expired._clock = lambda: NOW  # noqa: SLF001 - controlled boundary test
    try:
        plan = expired.create_plan(
            scenario_id="checkout-latency",
            created_by="operator_author",
            trace_id="trc_plan_004",
            ttl_seconds=60,
        )
        expired._clock = lambda: NOW + timedelta(minutes=2)  # noqa: SLF001
        with pytest.raises(ConflictError, match="expired"):
            expired.approve(
                plan_id=plan.plan_id,
                expected_version=plan.version,
                approved_by="operator_reviewer",
                idempotency_key="approve_key_004",
                trace_id="trc_approve_004",
            )
    finally:
        await expired.close()


async def test_manifest_change_blocks_execution_before_network(
    tmp_path: Path,
) -> None:
    copied_scenarios = tmp_path / "scenarios"
    copied_scenarios.mkdir()
    for source in SCENARIOS.glob("*.json"):
        (copied_scenarios / source.name).write_text(
            source.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    service = build_service(
        tmp_path,
        handler,
        scenarios=copied_scenarios,
    )
    try:
        plan, approved = create_and_approve(service)
        manifest_path = copied_scenarios / "inventory-db-timeout.json"
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        document["title"] = "Changed after approval"
        manifest_path.write_text(
            json.dumps(document),
            encoding="utf-8",
        )
        with pytest.raises(ConflictError, match="changed"):
            await service.execute(
                plan_id=plan.plan_id,
                expected_version=approved.version,
                actor="operator_executor",
                idempotency_key="execute_key_005",
                trace_id="trc_execute_005",
            )
    finally:
        await service.close()
    assert calls == 0


async def test_provider_failure_is_bounded_and_audited(
    tmp_path: Path,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="secret provider body")

    service = build_service(tmp_path, handler)
    try:
        plan, approved = create_and_approve(service)
        failed = await service.execute(
            plan_id=plan.plan_id,
            expected_version=approved.version,
            actor="operator_executor",
            idempotency_key="execute_key_006",
            trace_id="trc_execute_006",
        )
        audit = service.list_audit(plan.plan_id)
    finally:
        await service.close()

    assert failed.status is RemediationStatus.FAILED
    assert failed.last_failure_code == "unexpected_status_503"
    assert "secret provider body" not in json.dumps([item.to_dict() for item in audit])


async def test_unknown_scenarios_and_arbitrary_targets_are_rejected(
    tmp_path: Path,
) -> None:
    store = SQLiteRemediationApprovalStore(tmp_path / "approval.sqlite3")
    service = MiniShopRemediationService(
        MiniShopRemediationConfig(
            scenario_directory=SCENARIOS,
            target_base_url="http://minishop.test",
            enabled=True,
            allow_insecure_http=True,
        ),
        store,
    )
    with pytest.raises(AppValidationError, match="allowlisted"):
        service.create_plan(
            scenario_id="database-outage",
            created_by="operator_author",
            trace_id="trc_plan_007",
        )
    with pytest.raises(AppValidationError, match="scenario_id"):
        service.create_plan(
            scenario_id="../../admin",
            created_by="operator_author",
            trace_id="trc_plan_008",
        )
    await service.close()
    with pytest.raises(AppValidationError, match="target_base_url"):
        MiniShopRemediationConfig(
            scenario_directory=SCENARIOS,
            target_base_url="https://minishop.test/admin?command=delete",
        )
