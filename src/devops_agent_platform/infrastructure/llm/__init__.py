"""外部大模型供应商适配器。"""

from devops_agent_platform.infrastructure.llm.factory import (
    LLM_API_STYLE_CHAT_COMPLETIONS,
    LLM_API_STYLE_RESPONSES,
    LLM_PROVIDER_CUSTOM,
    LLM_PROVIDER_DASHSCOPE,
    LLM_PROVIDER_OPENAI,
    LLM_PROVIDER_OPENAI_COMPATIBLE,
    SUPPORTED_LLM_API_STYLES,
    SUPPORTED_LLM_PROVIDERS,
    LLMProviderRuntimeConfig,
    build_llm_provider_configs,
    build_llm_report_gateway,
    build_llm_report_gateway_from_configs,
    normalize_llm_api_style,
    normalize_llm_provider,
    parse_llm_provider_order,
    resolve_llm_base_url,
)
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

__all__ = [
    "LLM_API_STYLE_CHAT_COMPLETIONS",
    "LLM_API_STYLE_RESPONSES",
    "LLM_PROVIDER_CUSTOM",
    "LLM_PROVIDER_DASHSCOPE",
    "LLM_PROVIDER_OPENAI",
    "LLM_PROVIDER_OPENAI_COMPATIBLE",
    "LLMProviderRuntimeConfig",
    "OpenAICompatibleChatCompletionsConfig",
    "OpenAICompatibleChatCompletionsGateway",
    "FailoverLLMReportGateway",
    "LLMProviderAttempt",
    "OpenAICompatibleResponsesConfig",
    "OpenAICompatibleResponsesGateway",
    "SUPPORTED_LLM_API_STYLES",
    "SUPPORTED_LLM_PROVIDERS",
    "build_llm_provider_configs",
    "build_llm_report_gateway",
    "build_llm_report_gateway_from_configs",
    "normalize_llm_api_style",
    "normalize_llm_provider",
    "parse_llm_provider_order",
    "resolve_llm_base_url",
]
