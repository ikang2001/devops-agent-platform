from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_DASHSCOPE_PRICES = {
    "qwen3.7-plus": (2.0, 8.0),
}


@dataclass(frozen=True)
class SimulationLLMSettings:
    """Resolved API settings for the production-like simulation.

    The simulation deliberately reuses the project's normal secret-bearing
    environment.  It never writes the API key to an artifact or source file.
    """

    container_base_url: str
    host_base_url: str
    api_key: str
    model: str
    provider: str
    api_style: str
    input_cost_per_million: float
    output_cost_per_million: float
    pricing_basis: str


def load_env_file(path: Path, *, required: bool = True) -> dict[str, str]:
    if required and not path.is_file():
        raise ValueError(f"simulation environment file does not exist: {path}")
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def load_simulation_values(env_file: Path) -> dict[str, str]:
    """Load root secrets first, then apply explicit simulation overrides."""

    values = load_env_file(PROJECT_ROOT / ".env", required=False)
    values.update(load_env_file(env_file))
    return values


def _first_non_empty(values: dict[str, str], *names: str) -> str | None:
    for name in names:
        environment_value = os.getenv(name)
        if environment_value and environment_value.strip():
            return environment_value.strip()
        configured_value = values.get(name)
        if configured_value and configured_value.strip():
            return configured_value.strip()
    return None


def _with_v1_suffix(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return normalized
    if "dashscope.aliyuncs.com/compatible-mode" in normalized:
        return f"{normalized}/v1"
    return normalized


def resolve_llm_settings(values: dict[str, str]) -> SimulationLLMSettings:
    compatible_key = _first_non_empty(
        values,
        "SIM_LLM_API_KEY",
        "DEVOPS_AGENT_LLM_OPENAI_COMPATIBLE_API_KEY",
    )
    dashscope_key = _first_non_empty(
        values,
        "DEVOPS_AGENT_LLM_DASHSCOPE_API_KEY",
    )
    api_key = compatible_key or dashscope_key
    if api_key is None:
        raise ValueError(
            "missing simulation LLM API key; configure SIM_LLM_API_KEY or "
            "DEVOPS_AGENT_LLM_DASHSCOPE_API_KEY in the project .env"
        )

    model = _first_non_empty(
        values,
        "SIM_LLM_MODEL",
        "DEVOPS_AGENT_LLM_OPENAI_COMPATIBLE_MODEL",
        "DEVOPS_AGENT_LLM_DASHSCOPE_MODEL",
    )
    if model is None:
        raise ValueError(
            "missing simulation LLM model; configure SIM_LLM_MODEL or the "
            "DashScope model in the project .env"
        )

    configured_base_url = _first_non_empty(
        values,
        "SIM_LLM_BASE_URL",
        "DEVOPS_AGENT_LLM_OPENAI_COMPATIBLE_BASE_URL",
        "DEVOPS_AGENT_LLM_DASHSCOPE_BASE_URL",
    )
    base_url = _with_v1_suffix(configured_base_url or DEFAULT_DASHSCOPE_BASE_URL)
    host_base_url = _with_v1_suffix(
        _first_non_empty(values, "SIM_LLM_HOST_BASE_URL") or base_url
    )
    provider = _first_non_empty(values, "SIM_LLM_PROVIDER")
    if provider is None:
        provider = "openai_compatible" if compatible_key else "dashscope"
    api_style = _first_non_empty(
        values,
        "SIM_LLM_API_STYLE",
        "DEVOPS_AGENT_LLM_OPENAI_COMPATIBLE_API_STYLE",
        "DEVOPS_AGENT_LLM_DASHSCOPE_API_STYLE",
    ) or "chat_completions"
    input_cost_value = _first_non_empty(values, "SIM_LLM_INPUT_COST_PER_MILLION")
    output_cost_value = _first_non_empty(values, "SIM_LLM_OUTPUT_COST_PER_MILLION")
    default_prices = DEFAULT_DASHSCOPE_PRICES.get(model)
    if input_cost_value is None and output_cost_value is None and default_prices:
        input_cost, output_cost = default_prices
        pricing_basis = "DashScope Beijing public list price (CNY/million tokens)"
    else:
        input_cost = float(input_cost_value or "0")
        output_cost = float(output_cost_value or "0")
        pricing_basis = "explicit SIM_LLM_* price configuration"
    return SimulationLLMSettings(
        container_base_url=base_url,
        host_base_url=host_base_url,
        api_key=api_key,
        model=model,
        provider=provider,
        api_style=api_style,
        input_cost_per_million=input_cost,
        output_cost_per_million=output_cost,
        pricing_basis=pricing_basis,
    )


def apply_compose_environment(settings: SimulationLLMSettings) -> None:
    """Expose resolved values to Docker Compose without persisting secrets."""

    os.environ.update(
        {
            "SIM_LLM_BASE_URL": settings.container_base_url,
            "SIM_LLM_HOST_BASE_URL": settings.host_base_url,
            "SIM_LLM_API_KEY": settings.api_key,
            "SIM_LLM_MODEL": settings.model,
            "SIM_LLM_PROVIDER": settings.provider,
            "SIM_LLM_API_STYLE": settings.api_style,
        }
    )
