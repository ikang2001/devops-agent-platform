from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.evaluation.live_runner import (
    LiveLLMConfig,
    _normalize_llm_json,
    _unwrap_json_fence,
    load_live_suite,
    run_live_benchmark,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCENARIO_ROOT = PROJECT_ROOT / "MiniShop 电商下单故障演练靶场" / "scenarios"


def live_input() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "suite": "minishop-v2-test",
        "scenario_version": "1.0",
        "synthetic": True,
        "cases": [
            {
                "scenario_id": "payment-error",
                "incident_id": "inc-live-payment",
                "service_name": "checkout-service",
                "summary": "Checkout requests fail after the payment dependency call.",
                "evidence": [
                    {
                        "evidence_id": "ev-metric",
                        "evidence_type": "METRIC",
                        "source": "prometheus",
                        "summary": "payment-service error counter increased.",
                    },
                    {
                        "evidence_id": "ev-log",
                        "evidence_type": "LOG",
                        "source": "loki",
                        "summary": "payment-service emitted PAYMENT_GATEWAY_ERROR.",
                    },
                    {
                        "evidence_id": "ev-trace",
                        "evidence_type": "TRACE",
                        "source": "tempo",
                        "summary": "payment-service failed before checkout-service.",
                    },
                ],
                "tool_calls": [
                    {
                        "tool_type": "metrics.query@v1",
                        "status": "SUCCEEDED",
                        "evidence_ids": ["ev-metric"],
                    },
                    {
                        "tool_type": "logs.query@v1",
                        "status": "SUCCEEDED",
                        "evidence_ids": ["ev-log"],
                    },
                    {
                        "tool_type": "traces.query@v1",
                        "status": "SUCCEEDED",
                        "evidence_ids": ["ev-trace"],
                    },
                    {
                        "tool_type": "runbooks.retrieve@v1",
                        "status": "SUCCEEDED",
                        "evidence_ids": [],
                    },
                ],
                "investigation_steps": 4,
            }
        ],
    }


def llm_output() -> dict[str, object]:
    return {
        "root_cause": {
            "service": "payment-service",
            "type": "application_error",
            "resource": None,
        },
        "conclusion_status": "CANDIDATE",
        "confidence": 0.8,
        "evidence_ids": ["ev-metric", "ev-log", "ev-trace"],
        "claims": [
            {
                "claim_type": "ROOT_CAUSE",
                "statement": "payment-service emitted a payment application error.",
                "evidence_ids": ["ev-log", "ev-trace"],
            }
        ],
        "causal_chain": [
            {
                "from_node": "payment-service",
                "to_node": "checkout-service",
                "evidence_ids": ["ev-trace"],
            }
        ],
        "affected_services": ["payment-service", "checkout-service"],
    }


def write_input(tmp_path: Path, document: dict[str, object]) -> Path:
    path = tmp_path / "runtime-input.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_live_runner_unwraps_only_json_markdown_fences() -> None:
    assert _unwrap_json_fence("```json\n{\"ok\": true}\n```") == (
        '{"ok": true}'
    )
    assert _unwrap_json_fence('{"ok": true}') == '{"ok": true}'
    assert _unwrap_json_fence("prefix\n```json\n{}\n```") == (
        "prefix\n```json\n{}\n```"
    )


def test_live_runner_drops_invalid_self_loop_and_unknown_claim_type() -> None:
    normalized = json.loads(
        _normalize_llm_json(
            json.dumps(
                {
                    "root_cause": None,
                    "conclusion_status": "undetermined",
                    "confidence": "LOW",
                    "evidence_ids": ["ev-1", "ev-1"],
                    "claims": [
                        {
                            "claim_type": "OBSERVATION",
                            "statement": "ignored",
                            "evidence_ids": [],
                        }
                    ],
                    "causal_chain": [
                        {
                            "from_node": "checkout",
                            "to_node": "checkout",
                            "evidence_ids": [],
                        }
                    ],
                    "affected_services": ["checkout", "checkout"],
                }
            )
        )
    )

    assert normalized["confidence"] == 0.3
    assert normalized["claims"] == []
    assert normalized["causal_chain"] == []
    assert normalized["evidence_ids"] == ["ev-1"]
    assert normalized["affected_services"] == ["checkout"]


@pytest.mark.asyncio
async def test_live_runner_repeats_calls_without_ground_truth_leak(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(llm_output()),
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await run_live_benchmark(
            scenario_directory=SCENARIO_ROOT,
            input_path=write_input(tmp_path, live_input()),
            output_directory=tmp_path / "artifacts",
            git_commit="42665bd",
            runs_per_scenario=2,
            scenario_id="payment-error",
            config=LiveLLMConfig(
                base_url="http://llm.test/v1",
                api_key=SecretStr("test-key"),
                provider="reference",
                model="synthetic-test",
                input_cost_per_million=1.0,
                output_cost_per_million=2.0,
                allow_insecure_http=True,
            ),
            http_client=client,
        )
    finally:
        await client.aclose()

    assert len(requests) == 2
    assert result["summary"]["total_runs"] == 2
    assert result["summary"]["passed_runs"] == 2
    assert result["execution"]["synthetic"] is True
    assert result["runs"][0]["estimated_cost"] == pytest.approx(0.0002)
    assert (tmp_path / "artifacts" / "predictions.json").is_file()
    serialized_requests = b"".join(request.content for request in requests).decode()
    for forbidden in ("ground_truth", "forbidden_claims", "expected_tool_types"):
        assert forbidden not in serialized_requests


@pytest.mark.asyncio
async def test_live_runner_retries_transient_provider_transport_error(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadError("temporary disconnect", request=request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(llm_output())}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await run_live_benchmark(
            scenario_directory=SCENARIO_ROOT,
            input_path=write_input(tmp_path, live_input()),
            output_directory=tmp_path / "artifacts",
            git_commit="42665bd",
            runs_per_scenario=1,
            scenario_id="payment-error",
            config=LiveLLMConfig(
                base_url="http://llm.test/v1",
                api_key=SecretStr("test-key"),
                provider="reference",
                model="synthetic-test",
                allow_insecure_http=True,
                max_retries=1,
            ),
            http_client=client,
        )
    finally:
        await client.aclose()

    assert len(requests) == 2
    assert result["summary"]["total_runs"] == 1


def test_live_input_rejects_ground_truth_at_any_depth(tmp_path: Path) -> None:
    document = live_input()
    cases = document["cases"]
    assert isinstance(cases, list)
    cases[0]["ground_truth"] = {"root_cause": "leak"}

    with pytest.raises(ValueError, match="forbidden key: ground_truth"):
        load_live_suite(write_input(tmp_path, document))


@pytest.mark.asyncio
async def test_real_mode_rejects_synthetic_suite_before_calling_provider(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="non-synthetic"):
        await run_live_benchmark(
            scenario_directory=SCENARIO_ROOT,
            input_path=write_input(tmp_path, live_input()),
            output_directory=tmp_path / "artifacts",
            git_commit="42665bd",
            runs_per_scenario=5,
            config=LiveLLMConfig(
                base_url="https://llm.test/v1",
                api_key=SecretStr("test-key"),
                provider="reference",
                model="synthetic-test",
            ),
            require_real=True,
        )


@pytest.mark.asyncio
async def test_real_mode_requires_five_runs_per_scenario(tmp_path: Path) -> None:
    document = live_input()
    document["synthetic"] = False

    with pytest.raises(ValueError, match="exactly five runs"):
        await run_live_benchmark(
            scenario_directory=SCENARIO_ROOT,
            input_path=write_input(tmp_path, document),
            output_directory=tmp_path / "artifacts",
            git_commit="42665bd",
            runs_per_scenario=4,
            scenario_id="payment-error",
            config=LiveLLMConfig(
                base_url="https://llm.test/v1",
                api_key=SecretStr("test-key"),
                provider="real-provider",
                model="real-model",
            ),
            require_real=True,
        )


@pytest.mark.asyncio
async def test_real_mode_rejects_simulation_suite(tmp_path: Path) -> None:
    document = live_input()
    document["synthetic"] = False
    document["simulation"] = True

    with pytest.raises(ValueError, match="simulation suite"):
        await run_live_benchmark(
            scenario_directory=SCENARIO_ROOT,
            input_path=write_input(tmp_path, document),
            output_directory=tmp_path / "artifacts",
            git_commit="42665bd",
            runs_per_scenario=5,
            scenario_id="payment-error",
            config=LiveLLMConfig(
                base_url="https://llm.test/v1",
                api_key=SecretStr("test-key"),
                provider="simulation-provider",
                model="simulation-model",
            ),
            require_real=True,
        )


@pytest.mark.asyncio
async def test_live_runner_rejects_unknown_evidence_reference(tmp_path: Path) -> None:
    output = llm_output()
    output["evidence_ids"] = ["ev-not-supplied"]
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": json.dumps(output)}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )
        )
    )
    try:
        with pytest.raises(AppValidationError, match="unknown evidence"):
            await run_live_benchmark(
                scenario_directory=SCENARIO_ROOT,
                input_path=write_input(tmp_path, live_input()),
                output_directory=tmp_path / "artifacts",
                git_commit="42665bd",
                runs_per_scenario=1,
                scenario_id="payment-error",
                config=LiveLLMConfig(
                    base_url="http://llm.test/v1",
                    api_key=SecretStr("test-key"),
                    provider="reference",
                    model="synthetic-test",
                    allow_insecure_http=True,
                ),
                http_client=client,
            )
    finally:
        await client.aclose()
