import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr

from devops_agent_platform.domain.enums import EvidenceType
from devops_agent_platform.infrastructure.auth.oidc import (
    OIDCAdministratorAuthenticator,
    OIDCAuthenticatorConfig,
)
from devops_agent_platform.infrastructure.llm.openai_compatible import (
    OpenAICompatibleResponsesConfig,
    OpenAICompatibleResponsesGateway,
)
from devops_agent_platform.infrastructure.observability.loki import (
    LokiRangeClient,
    LokiRangeClientConfig,
)
from devops_agent_platform.infrastructure.observability.prometheus import (
    PrometheusRangeClient,
    PrometheusRangeClientConfig,
)
from devops_agent_platform.infrastructure.observability.tempo import (
    TempoSearchClient,
    TempoSearchClientConfig,
)
from devops_agent_platform.infrastructure.ticketing.http_json import (
    HttpJsonTicketingGateway,
    HttpJsonTicketingGatewayConfig,
)
from devops_agent_platform.ports.llm import (
    LLMReportEvidence,
    LLMReportRequest,
)
from devops_agent_platform.ports.ticketing import TicketingSubmitRequest

pytestmark = pytest.mark.live


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        pytest.fail(f"{name} is required in live mode", pytrace=False)
    return value


def _optional_secret(name: str) -> SecretStr | None:
    value = os.getenv(name, "").strip()
    return SecretStr(value) if value else None


def _window() -> tuple[datetime, datetime]:
    end = datetime.now(UTC)
    return end - timedelta(minutes=5), end


async def test_live_prometheus_query_range_contract() -> None:
    """Prometheus 必须接受范围查询并返回受限矩阵结构。"""
    client = PrometheusRangeClient(
        PrometheusRangeClientConfig(
            base_url=_required("DEVOPS_AGENT_TEST_PROMETHEUS_URL"),
            bearer_token=_optional_secret(
                "DEVOPS_AGENT_TEST_OBSERVABILITY_TOKEN"
            ),
        )
    )
    start, end = _window()
    try:
        result = await client.query_range(
            tenant_id=_required("DEVOPS_AGENT_TEST_TENANT_ID"),
            query="up",
            start=start,
            end=end,
            step_seconds=30,
            series_limit=20,
            samples_per_series_limit=20,
            trace_id="step4_prometheus_acceptance",
        )
        assert len(result.series) <= 20
    finally:
        await client.close()


async def test_live_loki_query_range_contract() -> None:
    """Loki 必须接受有界 LogQL 查询并返回 streams 契约。"""
    client = LokiRangeClient(
        LokiRangeClientConfig(
            base_url=_required("DEVOPS_AGENT_TEST_LOKI_URL"),
            bearer_token=_optional_secret(
                "DEVOPS_AGENT_TEST_OBSERVABILITY_TOKEN"
            ),
        )
    )
    start, end = _window()
    try:
        result = await client.query_range(
            tenant_id=_required("DEVOPS_AGENT_TEST_TENANT_ID"),
            query='{service_name=~".+"}',
            start=start,
            end=end,
            limit=20,
            trace_id="step4_loki_acceptance",
        )
        assert len(result.streams) <= 20
    finally:
        await client.close()


async def test_live_tempo_search_contract() -> None:
    """Tempo 必须接受 TraceQL 搜索并返回受限 trace 摘要。"""
    client = TempoSearchClient(
        TempoSearchClientConfig(
            base_url=_required("DEVOPS_AGENT_TEST_TEMPO_URL"),
            bearer_token=_optional_secret(
                "DEVOPS_AGENT_TEST_OBSERVABILITY_TOKEN"
            ),
        )
    )
    start, end = _window()
    try:
        result = await client.search(
            tenant_id=_required("DEVOPS_AGENT_TEST_TENANT_ID"),
            query="{ true }",
            start=start,
            end=end,
            limit=20,
            spans_per_span_set=3,
            trace_id="step4_tempo_acceptance",
        )
        assert len(result.traces) <= 20
    finally:
        await client.close()


async def test_live_oidc_token_and_jwks_contract() -> None:
    """真实 JWKS、签名、issuer、audience 与管理员 claims 必须共同通过。"""
    authenticator = OIDCAdministratorAuthenticator(
        OIDCAuthenticatorConfig(
            issuer=_required("DEVOPS_AGENT_TEST_OIDC_ISSUER"),
            audience=_required("DEVOPS_AGENT_TEST_OIDC_AUDIENCE"),
            jwks_url=_required("DEVOPS_AGENT_TEST_OIDC_JWKS_URL"),
        )
    )
    try:
        principal = await authenticator.authenticate(
            _required("DEVOPS_AGENT_TEST_OIDC_TOKEN")
        )
        assert principal.subject
        assert principal.scopes
    finally:
        await authenticator.close()


async def test_live_llm_responses_contract() -> None:
    """模型供应商必须支持项目要求的严格 Responses JSON Schema。"""
    gateway = OpenAICompatibleResponsesGateway(
        OpenAICompatibleResponsesConfig(
            base_url=_required("DEVOPS_AGENT_TEST_LLM_BASE_URL"),
            api_key=SecretStr(_required("DEVOPS_AGENT_TEST_LLM_API_KEY")),
            model=_required("DEVOPS_AGENT_TEST_LLM_MODEL"),
            max_output_tokens=512,
        )
    )
    suffix = uuid4().hex[:12]
    request = LLMReportRequest(
        tenant_id="step4_acceptance",
        incident_id=f"inc_{suffix}",
        workflow_run_id=f"wfr_{suffix}",
        execution_attempt=1,
        trace_id=f"trace_{suffix}",
        prompt_version="step4-live-v1",
        evidence=(
            LLMReportEvidence(
                evidence_id=f"evd_{suffix}",
                evidence_type=EvidenceType.METRIC,
                source="acceptance",
                summary="Synthetic service latency increased.",
                confidence=0.9,
            ),
        ),
    )
    try:
        response = await gateway.generate_report(request)
        assert isinstance(response, dict)
        assert response
    finally:
        await gateway.close()


async def test_live_ticketing_idempotency_contract() -> None:
    """沙箱工单端点必须接受项目的幂等头和最小 JSON 契约。"""
    suffix = uuid4().hex[:12]
    gateway = HttpJsonTicketingGateway(
        HttpJsonTicketingGatewayConfig(
            endpoint_url=_required(
                "DEVOPS_AGENT_TEST_TICKETING_ENDPOINT_URL"
            ),
            bearer_token=_optional_secret(
                "DEVOPS_AGENT_TEST_TICKETING_TOKEN"
            ),
        )
    )
    request = TicketingSubmitRequest(
        tenant_id=_required("DEVOPS_AGENT_TEST_TENANT_ID"),
        ticket_submission_id=f"tsb_{suffix}",
        ticket_draft_id=f"tdf_{suffix}",
        target_system="acceptance-sandbox",
        title=f"[STEP4 ACCEPTANCE] {suffix}",
        description="Synthetic contract verification; safe to close.",
        priority="P4",
        evidence_ids=(f"evd_{suffix}",),
        recommendations=("Close this synthetic acceptance ticket.",),
        idempotency_key=f"step4-{suffix}",
        trace_id=f"trace_{suffix}",
    )
    try:
        first = await gateway.submit_ticket(request)
        second = await gateway.submit_ticket(request)
        assert first.succeeded is True
        assert second.succeeded is True
        assert first.external_ticket_id == second.external_ticket_id
    finally:
        await gateway.close()
