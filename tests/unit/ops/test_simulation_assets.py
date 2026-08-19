from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import yaml

from ops.evaluation.build_reference_inputs import build_reference_suite
from ops.simulation import ticketing_server
from ops.simulation.llm_config import resolve_llm_settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCENARIO_ROOT = PROJECT_ROOT / "MiniShop 电商下单故障演练靶场" / "scenarios"


def test_simulation_compose_uses_dynamic_runtime_and_persistent_ticketing() -> None:
    compose = yaml.safe_load(
        (PROJECT_ROOT / "ops/simulation/docker-compose.yml").read_text(
            encoding="utf-8"
        )
    )
    services = compose["services"]
    environment = services["agent"]["environment"]
    ticketing = services["ticketing-simulation"]

    assert "simulation-ticketing-data" in compose["volumes"]
    assert ticketing["user"] == "0:0"
    assert "../simulation/ticketing_server.py:/simulation/ticketing_server.py:ro" in (
        ticketing["volumes"]
    )
    assert environment["DEVOPS_AGENT_RCA_INVESTIGATION_POLICY"] == (
        "bounded_dynamic_v1"
    )
    assert "provider-mock" not in environment[
        "DEVOPS_AGENT_LLM_OPENAI_COMPATIBLE_BASE_URL"
    ]
    assert environment["DEVOPS_AGENT_TICKETING_HTTP_JSON_ENDPOINT_URL"] == (
        "http://ticketing-simulation:8080/api/tickets"
    )


def test_simulation_suite_is_not_a_synthetic_or_real_acceptance_suite() -> None:
    suite = build_reference_suite(SCENARIO_ROOT, simulation=True)

    assert suite["synthetic"] is False
    assert suite["simulation"] is True
    assert len(suite["cases"]) == 12


def test_reference_runtime_maps_knowledge_to_registered_search_tool() -> None:
    suite = build_reference_suite(SCENARIO_ROOT, simulation=True)

    calls_by_scenario = {
        case["scenario_id"]: {call["tool_type"] for call in case["tool_calls"]}
        for case in suite["cases"]
    }
    assert "topology.query@v1" in calls_by_scenario["cascading-failure"]
    assert "knowledge.search@v1" in calls_by_scenario["known-error-repeat"]
    assert "knowledge.search@v1" in calls_by_scenario["misleading-history"]


def test_simulation_llm_defaults_to_configured_dashscope_api() -> None:
    settings = resolve_llm_settings(
        {
            "DEVOPS_AGENT_LLM_DASHSCOPE_API_KEY": "dashscope-test-key",
            "DEVOPS_AGENT_LLM_DASHSCOPE_MODEL": "qwen-test",
            "DEVOPS_AGENT_LLM_DASHSCOPE_API_STYLE": "chat_completions",
        }
    )

    assert settings.provider == "dashscope"
    assert settings.model == "qwen-test"
    assert settings.api_style == "chat_completions"
    assert settings.container_base_url.endswith("/compatible-mode/v1")
    assert settings.api_key == "dashscope-test-key"


def test_qwen_simulation_uses_public_beijing_price_when_not_overridden() -> None:
    settings = resolve_llm_settings(
        {
            "DEVOPS_AGENT_LLM_DASHSCOPE_API_KEY": "dashscope-test-key",
            "DEVOPS_AGENT_LLM_DASHSCOPE_MODEL": "qwen3.7-plus",
        }
    )

    assert settings.input_cost_per_million == 2.0
    assert settings.output_cost_per_million == 8.0
    assert "DashScope Beijing" in settings.pricing_basis


def test_ticketing_simulation_persists_idempotent_ticket(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ticketing_server, "DATABASE_PATH", tmp_path / "tickets.sqlite3")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        ticketing_server.TicketingSimulationHandler,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/tickets"
        headers = {"Idempotency-Key": "ticket-test-v1"}
        first = httpx.post(url, headers=headers, json={"title": "DB timeout"})
        second = httpx.post(url, headers=headers, json={"title": "DB timeout"})
        first.raise_for_status()
        second.raise_for_status()

        assert first.json()["external_ticket_id"] == second.json()[
            "external_ticket_id"
        ]
        assert first.json()["idempotent"] is False
        assert second.json()["idempotent"] is True
        assert (tmp_path / "tickets.sqlite3").is_file()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
