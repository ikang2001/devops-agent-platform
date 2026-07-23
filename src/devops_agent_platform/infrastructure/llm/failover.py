import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from devops_agent_platform.application.exceptions import LLMProviderError
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.llm import (
    LLMReportGatewayPort,
    LLMReportRequest,
)


@dataclass(frozen=True)
class LLMProviderAttempt:
    provider_name: str
    gateway: LLMReportGatewayPort

    def __post_init__(self) -> None:
        if (
            not isinstance(self.provider_name, str)
            or not self.provider_name.strip()
            or self.provider_name != self.provider_name.strip()
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in self.provider_name
            )
        ):
            raise AppValidationError("provider_name is invalid")


class FailoverLLMReportGateway:
    """Try ordered LLM providers before surfacing one sanitized provider error."""

    def __init__(self, attempts: tuple[LLMProviderAttempt, ...]) -> None:
        if (
            not isinstance(attempts, tuple)
            or len(attempts) < 2
            or not all(isinstance(attempt, LLMProviderAttempt) for attempt in attempts)
        ):
            raise AppValidationError("at least two LLM provider attempts are required")
        self._attempts = attempts
        self._closed = False

    async def generate_report(
        self,
        request: LLMReportRequest,
    ) -> Mapping[str, Any]:
        if self._closed:
            raise LLMProviderError("LLM gateway is closed")

        last_error: LLMProviderError | None = None
        for attempt in self._attempts:
            try:
                return await attempt.gateway.generate_report(request)
            except asyncio.CancelledError:
                raise
            except LLMProviderError as exc:
                last_error = exc
                continue
        del last_error
        raise LLMProviderError("all LLM providers are unavailable")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[BaseException] = []
        for attempt in reversed(self._attempts):
            close = getattr(attempt.gateway, "close", None)
            if close is None:
                continue
            try:
                await close()
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise errors[0]
