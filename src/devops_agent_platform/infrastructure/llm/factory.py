from dataclasses import dataclass

from pydantic import SecretStr

from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.infrastructure.llm.failover import (
    FailoverLLMReportGateway,
    LLMProviderAttempt,
)
from devops_agent_platform.infrastructure.llm.openai_compatible import (
    OpenAICompatibleChatCompletionsConfig,
    OpenAICompatibleChatCompletionsGateway,
    OpenAICompatibleResponsesConfig,
    OpenAICompatibleResponsesGateway,
)
from devops_agent_platform.ports.llm import LLMReportGatewayPort

LLM_PROVIDER_OPENAI = "openai"
LLM_PROVIDER_DASHSCOPE = "dashscope"
LLM_PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"
LLM_PROVIDER_CUSTOM = "custom"
LLM_API_STYLE_RESPONSES = "responses"
LLM_API_STYLE_CHAT_COMPLETIONS = "chat_completions"

SUPPORTED_LLM_PROVIDERS = frozenset(
    {
        LLM_PROVIDER_OPENAI,
        LLM_PROVIDER_DASHSCOPE,
        LLM_PROVIDER_OPENAI_COMPATIBLE,
        LLM_PROVIDER_CUSTOM,
    }
)
SUPPORTED_LLM_API_STYLES = frozenset(
    {
        LLM_API_STYLE_RESPONSES,
        LLM_API_STYLE_CHAT_COMPLETIONS,
    }
)

_DEFAULT_BASE_URLS = {
    LLM_PROVIDER_OPENAI: "https://api.openai.com",
    LLM_PROVIDER_DASHSCOPE: "https://dashscope.aliyuncs.com/compatible-mode",
}


@dataclass(frozen=True)
class LLMProviderRuntimeConfig:
    provider_name: str
    api_style: str
    base_url: str
    api_key: SecretStr
    model: str


def normalize_llm_provider(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized not in SUPPORTED_LLM_PROVIDERS:
        raise AppValidationError("llm_provider is not supported")
    return normalized


def normalize_llm_api_style(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {"chat", "chat_completion"}:
        normalized = LLM_API_STYLE_CHAT_COMPLETIONS
    if normalized not in SUPPORTED_LLM_API_STYLES:
        raise AppValidationError("llm_api_style is not supported")
    return normalized


def parse_llm_provider_order(value: str) -> tuple[str, ...]:
    providers = tuple(
        normalize_llm_provider(item)
        for item in value.split(",")
        if item.strip()
    )
    ordered = tuple(dict.fromkeys(providers))
    if not ordered:
        raise AppValidationError("llm_provider_order must not be empty")
    return ordered


def resolve_llm_base_url(provider: str, base_url: str | None) -> str:
    normalized_provider = normalize_llm_provider(provider)
    if base_url is not None and base_url.strip():
        return base_url
    default_base_url = _DEFAULT_BASE_URLS.get(normalized_provider)
    if default_base_url is None:
        raise AppValidationError("llm_base_url is required for this provider")
    return default_base_url


def build_llm_provider_configs(
    *,
    provider_order: str,
    legacy_provider: str,
    legacy_api_style: str,
    legacy_base_url: str | None,
    legacy_api_key: SecretStr | None,
    legacy_model: str | None,
    openai_api_style: str,
    openai_base_url: str | None,
    openai_api_key: SecretStr | None,
    openai_model: str | None,
    dashscope_api_style: str,
    dashscope_base_url: str | None,
    dashscope_api_key: SecretStr | None,
    dashscope_model: str | None,
    openai_compatible_api_style: str,
    openai_compatible_base_url: str | None,
    openai_compatible_api_key: SecretStr | None,
    openai_compatible_model: str | None,
    custom_api_style: str,
    custom_base_url: str | None,
    custom_api_key: SecretStr | None,
    custom_model: str | None,
) -> tuple[LLMProviderRuntimeConfig, ...]:
    provider_order_items = parse_llm_provider_order(provider_order)
    candidates = {
        LLM_PROVIDER_OPENAI: (
            openai_api_style,
            openai_base_url,
            openai_api_key,
            openai_model,
        ),
        LLM_PROVIDER_DASHSCOPE: (
            dashscope_api_style,
            dashscope_base_url,
            dashscope_api_key,
            dashscope_model,
        ),
        LLM_PROVIDER_OPENAI_COMPATIBLE: (
            openai_compatible_api_style,
            openai_compatible_base_url,
            openai_compatible_api_key,
            openai_compatible_model,
        ),
        LLM_PROVIDER_CUSTOM: (
            custom_api_style,
            custom_base_url,
            custom_api_key,
            custom_model,
        ),
    }

    configs: list[LLMProviderRuntimeConfig] = []
    configured_providers: set[str] = set()
    for provider_name in provider_order_items:
        api_style, base_url, api_key, model = candidates[provider_name]
        config = _build_optional_provider_config(
            provider_name=provider_name,
            api_style=api_style,
            base_url=base_url,
            api_key=api_key,
            model=model,
        )
        if config is None:
            continue
        configs.append(config)
        configured_providers.add(config.provider_name)

    legacy_config = _build_optional_provider_config(
        provider_name=legacy_provider,
        api_style=legacy_api_style,
        base_url=legacy_base_url,
        api_key=legacy_api_key,
        model=legacy_model,
    )
    if (
        legacy_config is not None
        and legacy_config.provider_name not in configured_providers
    ):
        configs.append(legacy_config)
    return tuple(configs)


def build_llm_report_gateway_from_configs(
    configs: tuple[LLMProviderRuntimeConfig, ...],
    *,
    request_timeout_seconds: float,
    max_response_bytes: int,
    max_output_tokens: int,
) -> LLMReportGatewayPort:
    if not configs:
        raise AppValidationError("no usable LLM provider credentials configured")

    attempts = tuple(
        LLMProviderAttempt(
            provider_name=config.provider_name,
            gateway=build_llm_report_gateway(
                provider=config.provider_name,
                api_style=config.api_style,
                base_url=config.base_url,
                api_key=config.api_key,
                model=config.model,
                request_timeout_seconds=request_timeout_seconds,
                max_response_bytes=max_response_bytes,
                max_output_tokens=max_output_tokens,
            ),
        )
        for config in configs
    )
    if len(attempts) == 1:
        return attempts[0].gateway
    return FailoverLLMReportGateway(attempts)


def build_llm_report_gateway(
    *,
    provider: str,
    api_style: str,
    base_url: str | None,
    api_key: SecretStr,
    model: str,
    request_timeout_seconds: float,
    max_response_bytes: int,
    max_output_tokens: int,
) -> LLMReportGatewayPort:
    resolved_base_url = resolve_llm_base_url(provider, base_url)
    normalized_style = normalize_llm_api_style(api_style)
    if normalized_style == LLM_API_STYLE_RESPONSES:
        return OpenAICompatibleResponsesGateway(
            OpenAICompatibleResponsesConfig(
                base_url=resolved_base_url,
                api_key=api_key,
                model=model,
                request_timeout_seconds=request_timeout_seconds,
                max_response_bytes=max_response_bytes,
                max_output_tokens=max_output_tokens,
            )
        )
    if normalized_style == LLM_API_STYLE_CHAT_COMPLETIONS:
        return OpenAICompatibleChatCompletionsGateway(
            OpenAICompatibleChatCompletionsConfig(
                base_url=resolved_base_url,
                api_key=api_key,
                model=model,
                request_timeout_seconds=request_timeout_seconds,
                max_response_bytes=max_response_bytes,
                max_output_tokens=max_output_tokens,
            )
        )
    raise AppValidationError("llm_api_style is not supported")


def _build_optional_provider_config(
    *,
    provider_name: str,
    api_style: str,
    base_url: str | None,
    api_key: SecretStr | None,
    model: str | None,
) -> LLMProviderRuntimeConfig | None:
    if api_key is None or model is None or not model.strip():
        return None
    normalized_provider = normalize_llm_provider(provider_name)
    return LLMProviderRuntimeConfig(
        provider_name=normalized_provider,
        api_style=normalize_llm_api_style(api_style),
        base_url=resolve_llm_base_url(normalized_provider, base_url),
        api_key=api_key,
        model=model,
    )
