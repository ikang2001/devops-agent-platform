from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.evaluation.runner import run_benchmark_input
from devops_agent_platform.evaluation.schemas import (
    BenchmarkInput,
    BenchmarkMetadata,
    CausalEdge,
    Claim,
    ConclusionStatus,
    EvidenceType,
    RCAPrediction,
    RootCauseRef,
    ToolCall,
)

PROMPT_VERSION = "real-llm-rca-v1"
SYSTEM_PROMPT = """You are evaluating a bounded incident investigation snapshot.
Return exactly one JSON object with these keys: root_cause, conclusion_status,
confidence, evidence_ids, claims, causal_chain, affected_services.
Use only supplied evidence IDs. Do not invent tool results. root_cause is null or
an object with service, type, resource. conclusion_status is CANDIDATE,
UNDETERMINED, or NO_ACTIONABLE_ROOT_CAUSE; never return CONFIRMED. claims contain
claim_type, statement, evidence_ids. causal_chain contains from_node, to_node,
evidence_ids. If evidence is insufficient, return no actionable root cause."""

_FORBIDDEN_INPUT_KEYS = frozenset(
    {
        "ground_truth",
        "forbidden_claims",
        "expected_tools",
        "expected_tool_types",
        "root_cause_answer",
        "authorization",
        "api_key",
        "password",
        "private_key",
        "secret",
        "token",
    }
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LiveEvidence(_Model):
    evidence_id: str = Field(min_length=1, max_length=128)
    evidence_type: EvidenceType
    source: str = Field(min_length=1, max_length=64)
    summary: str = Field(min_length=1, max_length=4096)


class LiveScenarioInput(_Model):
    scenario_id: str = Field(min_length=1, max_length=64)
    incident_id: str = Field(min_length=1, max_length=128)
    service_name: str = Field(min_length=1, max_length=256)
    summary: str = Field(min_length=1, max_length=4096)
    evidence: tuple[LiveEvidence, ...] = Field(min_length=1, max_length=100)
    tool_calls: tuple[ToolCall, ...] = Field(default=(), max_length=100)
    investigation_steps: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def validate_case(self) -> LiveScenarioInput:
        evidence_ids = {item.evidence_id for item in self.evidence}
        if len(evidence_ids) != len(self.evidence):
            raise ValueError("live evidence IDs must be unique")
        referenced = {
            evidence_id
            for tool_call in self.tool_calls
            for evidence_id in tool_call.evidence_ids
        }
        if not referenced.issubset(evidence_ids):
            raise ValueError("tool calls reference unknown evidence")
        if self.investigation_steps < len(self.tool_calls):
            raise ValueError("investigation_steps cannot be smaller than tool calls")
        return self


class LiveSuiteInput(_Model):
    schema_version: str = Field(pattern=r"^1\.0$")
    suite: str = Field(min_length=1, max_length=64)
    scenario_version: str = Field(min_length=1, max_length=32)
    synthetic: bool
    cases: tuple[LiveScenarioInput, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_cases(self) -> LiveSuiteInput:
        scenario_ids = [item.scenario_id for item in self.cases]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("live scenario IDs must be unique")
        return self


class LLMRCAOutput(_Model):
    root_cause: RootCauseRef | None
    conclusion_status: ConclusionStatus
    confidence: float = Field(ge=0, le=1)
    evidence_ids: tuple[str, ...] = ()
    claims: tuple[Claim, ...] = ()
    causal_chain: tuple[CausalEdge, ...] = ()
    affected_services: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_conclusion(self) -> LLMRCAOutput:
        if self.conclusion_status is ConclusionStatus.CONFIRMED:
            raise ValueError("live LLM benchmark cannot claim CONFIRMED")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence_ids must be unique")
        if len(self.affected_services) != len(set(self.affected_services)):
            raise ValueError("affected_services must be unique")
        if (
            self.root_cause is None
            and self.conclusion_status is ConclusionStatus.CANDIDATE
        ):
            raise ValueError("candidate conclusion requires root_cause")
        if (
            self.root_cause is not None
            and self.conclusion_status is not ConclusionStatus.CANDIDATE
        ):
            raise ValueError("root_cause requires CANDIDATE conclusion")
        return self


@dataclass(frozen=True)
class LiveLLMConfig:
    base_url: str
    api_key: SecretStr = field(repr=False)
    provider: str
    model: str
    api_style: str = "chat_completions"
    temperature: float = 0.0
    top_p: float = 1.0
    max_tokens: int = 2048
    request_timeout_seconds: float = 60.0
    max_response_bytes: int = 256 * 1024
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    allow_insecure_http: bool = False

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        schemes = {"https", "http"} if self.allow_insecure_http else {"https"}
        if (
            not isinstance(self.base_url, str)
            or self.base_url != self.base_url.strip()
            or self.base_url.endswith("/")
            or parsed.scheme not in schemes
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise AppValidationError("live LLM base_url is invalid")
        if self.api_style not in {"chat_completions", "responses"}:
            raise AppValidationError("live LLM api_style is invalid")
        for name, value, maximum in (
            ("provider", self.provider, 64),
            ("model", self.model, 128),
        ):
            if not isinstance(value, str) or not 1 <= len(value) <= maximum:
                raise AppValidationError(f"live LLM {name} is invalid")
        if (
            not isinstance(self.api_key, SecretStr)
            or not self.api_key.get_secret_value()
        ):
            raise AppValidationError("live LLM api_key is invalid")
        if not 0 <= self.temperature <= 2 or not 0 <= self.top_p <= 1:
            raise AppValidationError("live LLM sampling configuration is invalid")
        if not 1 <= self.max_tokens <= 1_000_000:
            raise AppValidationError("live LLM max_tokens is invalid")
        if not 0 < self.request_timeout_seconds <= 600:
            raise AppValidationError("live LLM request timeout is invalid")
        if not 1 <= self.max_response_bytes <= 4 * 1024 * 1024:
            raise AppValidationError("live LLM max response size is invalid")
        if self.input_cost_per_million < 0 or self.output_cost_per_million < 0:
            raise AppValidationError("live LLM pricing must not be negative")


@dataclass(frozen=True)
class ModelCallResult:
    output: LLMRCAOutput
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


class OpenAICompatibleBenchmarkClient:
    def __init__(
        self,
        config: LiveLLMConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.request_timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    async def complete(self, prompt: str) -> ModelCallResult:
        started = perf_counter()
        content = bytearray()
        try:
            async with self._client.stream(
                "POST",
                self._endpoint(),
                headers={
                    "Authorization": f"Bearer {self.config.api_key.get_secret_value()}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json=self._request_body(prompt),
                timeout=self.config.request_timeout_seconds,
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise AppValidationError("live LLM provider rejected the request")
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self.config.max_response_bytes:
                        raise AppValidationError("live LLM response exceeds size limit")
        except asyncio.CancelledError:
            raise
        except AppValidationError:
            raise
        except httpx.HTTPError:
            raise AppValidationError("live LLM provider request failed") from None
        latency_ms = max(0, round((perf_counter() - started) * 1000))
        document = self._parse_json(content)
        text, prompt_tokens, completion_tokens = self._extract(document)
        try:
            output = LLMRCAOutput.model_validate_json(text)
        except ValueError as exc:
            raise AppValidationError("live LLM output violates RCA schema") from exc
        return ModelCallResult(output, prompt_tokens, completion_tokens, latency_ms)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _endpoint(self) -> str:
        suffix = (
            "/chat/completions"
            if self.config.api_style == "chat_completions"
            else "/responses"
        )
        return self.config.base_url + suffix

    def _request_body(self, prompt: str) -> dict[str, Any]:
        common = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "top_p": self.config.top_p,
        }
        if self.config.api_style == "chat_completions":
            return {
                **common,
                "max_tokens": self.config.max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            }
        return {
            **common,
            "max_output_tokens": self.config.max_tokens,
            "input": f"{SYSTEM_PROMPT}\n\n{prompt}",
        }

    @staticmethod
    def _parse_json(content: bytes) -> Mapping[str, Any]:
        try:
            value = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppValidationError("live LLM provider returned invalid JSON") from exc
        if not isinstance(value, Mapping):
            raise AppValidationError("live LLM provider response must be an object")
        return value

    def _extract(self, document: Mapping[str, Any]) -> tuple[str, int, int]:
        if self.config.api_style == "chat_completions":
            text = self._chat_text(document)
            usage = document.get("usage")
            prompt_key, completion_key = "prompt_tokens", "completion_tokens"
        else:
            text = self._responses_text(document)
            usage = document.get("usage")
            prompt_key, completion_key = "input_tokens", "output_tokens"
        if not isinstance(usage, Mapping):
            raise AppValidationError("live LLM provider did not report token usage")
        prompt_tokens = usage.get(prompt_key)
        completion_tokens = usage.get(completion_key)
        if (
            isinstance(prompt_tokens, bool)
            or not isinstance(prompt_tokens, int)
            or prompt_tokens < 0
            or isinstance(completion_tokens, bool)
            or not isinstance(completion_tokens, int)
            or completion_tokens < 0
        ):
            raise AppValidationError("live LLM token usage is invalid")
        return text, prompt_tokens, completion_tokens

    @staticmethod
    def _chat_text(document: Mapping[str, Any]) -> str:
        try:
            text = document["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AppValidationError("live LLM chat response is incomplete") from exc
        if not isinstance(text, str) or not text:
            raise AppValidationError("live LLM chat content is invalid")
        return text

    @staticmethod
    def _responses_text(document: Mapping[str, Any]) -> str:
        try:
            outputs = document["output"]
            texts = [
                item["text"]
                for output in outputs
                for item in output.get("content", [])
                if item.get("type") == "output_text"
            ]
        except (KeyError, TypeError) as exc:
            raise AppValidationError(
                "live LLM responses payload is incomplete"
            ) from exc
        if len(texts) != 1 or not isinstance(texts[0], str) or not texts[0]:
            raise AppValidationError("live LLM responses content is invalid")
        return texts[0]


def load_live_suite(path: Path) -> LiveSuiteInput:
    try:
        raw = path.read_bytes()
        document = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read live benchmark input {path}") from exc
    forbidden = _find_forbidden_key(document)
    if forbidden is not None:
        raise ValueError(f"live benchmark input contains forbidden key: {forbidden}")
    return LiveSuiteInput.model_validate(document)


async def run_live_benchmark(
    *,
    scenario_directory: Path,
    input_path: Path,
    output_directory: Path,
    git_commit: str,
    runs_per_scenario: int,
    config: LiveLLMConfig,
    scenario_id: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    if not 1 <= runs_per_scenario <= 20:
        raise ValueError("runs_per_scenario must be between 1 and 20")
    suite = load_live_suite(input_path)
    if config.allow_insecure_http and not suite.synthetic:
        raise ValueError("insecure HTTP is only allowed for synthetic live input")
    cases = tuple(
        item
        for item in suite.cases
        if scenario_id is None or item.scenario_id == scenario_id
    )
    if not cases:
        raise ValueError("no live benchmark scenario selected")
    client = OpenAICompatibleBenchmarkClient(config, http_client=http_client)
    try:
        predictions = []
        for case in cases:
            for run_index in range(1, runs_per_scenario + 1):
                call = await client.complete(_render_runtime_prompt(case))
                predictions.append(_build_prediction(case, run_index, call, config))
    finally:
        await client.close()
    input_hash = sha256(input_path.read_bytes()).hexdigest()
    metadata = BenchmarkMetadata(
        benchmark_version="2.0-live",
        git_commit=git_commit,
        model_provider=config.provider,
        model_name=config.model,
        temperature=config.temperature,
        top_p=config.top_p,
        max_tokens=config.max_tokens,
        prompt_version=PROMPT_VERSION,
        investigation_policy="bounded_dynamic_v1",
        scenario_version=suite.scenario_version,
        timestamp=datetime.now(UTC),
        scenario_hash=_hash_scenarios(scenario_directory),
        config_hash=_config_hash(config),
        prompt_hash=sha256(SYSTEM_PROMPT.encode()).hexdigest(),
    )
    benchmark_input = BenchmarkInput(benchmark=metadata, runs=tuple(predictions))
    return run_benchmark_input(
        scenario_directory,
        benchmark_input,
        output_directory,
        include_extended=True,
        scenario_id=scenario_id,
        execution_metadata={
            "mode": "live_llm",
            "synthetic": suite.synthetic,
            "suite": suite.suite,
            "runs_per_scenario": runs_per_scenario,
            "runtime_input_sha256": input_hash,
            "provider_endpoint_sha256": sha256(config.base_url.encode()).hexdigest(),
            "input_cost_per_million": config.input_cost_per_million,
            "output_cost_per_million": config.output_cost_per_million,
        },
    )


def _render_runtime_prompt(case: LiveScenarioInput) -> str:
    payload = {
        "incident": {
            "incident_id": case.incident_id,
            "service_name": case.service_name,
            "summary": case.summary,
        },
        "evidence": [item.model_dump(mode="json") for item in case.evidence],
        "executed_tool_calls": [
            item.model_dump(mode="json") for item in case.tool_calls
        ],
    }
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    forbidden = _find_forbidden_key(payload)
    if forbidden is not None:
        raise ValueError(f"runtime prompt contains forbidden key: {forbidden}")
    return (
        "Analyze this runtime snapshot and return the required JSON object:\n"
        + serialized
    )


def _build_prediction(
    case: LiveScenarioInput,
    run_index: int,
    call: ModelCallResult,
    config: LiveLLMConfig,
) -> RCAPrediction:
    allowed_ids = {item.evidence_id for item in case.evidence}
    referenced_ids = set(call.output.evidence_ids)
    referenced_ids.update(
        evidence_id
        for claim in call.output.claims
        for evidence_id in claim.evidence_ids
    )
    referenced_ids.update(
        evidence_id
        for edge in call.output.causal_chain
        for evidence_id in edge.evidence_ids
    )
    if not referenced_ids.issubset(allowed_ids):
        raise AppValidationError("live LLM output references unknown evidence")
    selected_types = tuple(
        dict.fromkeys(
            item.evidence_type
            for item in case.evidence
            if item.evidence_id in call.output.evidence_ids
        )
    )
    total_tokens = call.prompt_tokens + call.completion_tokens
    estimated_cost = (
        call.prompt_tokens * config.input_cost_per_million
        + call.completion_tokens * config.output_cost_per_million
    ) / 1_000_000
    return RCAPrediction(
        scenario_id=case.scenario_id,
        run_id=f"live-{case.scenario_id}-{run_index:02d}",
        root_cause=call.output.root_cause,
        conclusion_status=call.output.conclusion_status,
        confidence=call.output.confidence,
        evidence_ids=call.output.evidence_ids,
        evidence_types=selected_types,
        claims=call.output.claims,
        causal_chain=call.output.causal_chain,
        affected_services=call.output.affected_services,
        tool_calls=case.tool_calls,
        investigation_steps=case.investigation_steps,
        llm_calls=1,
        latency_ms=call.latency_ms,
        total_tokens=total_tokens,
        estimated_cost=round(estimated_cost, 12),
    )


def _find_forbidden_key(value: object) -> str | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key).casefold()
            if key_text in _FORBIDDEN_INPUT_KEYS:
                return str(key)
            nested = _find_forbidden_key(item)
            if nested is not None:
                return nested
    elif isinstance(value, list | tuple):
        for item in value:
            nested = _find_forbidden_key(item)
            if nested is not None:
                return nested
    return None


def _hash_scenarios(directory: Path) -> str:
    digest = sha256()
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ValueError("scenario directory is empty")
    for path in paths:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _config_hash(config: LiveLLMConfig) -> str:
    safe = {
        "endpoint_sha256": sha256(config.base_url.encode()).hexdigest(),
        "provider": config.provider,
        "model": config.model,
        "api_style": config.api_style,
        "temperature": config.temperature,
        "top_p": config.top_p,
        "max_tokens": config.max_tokens,
        "input_cost_per_million": config.input_cost_per_million,
        "output_cost_per_million": config.output_cost_per_million,
    }
    return sha256(json.dumps(safe, sort_keys=True).encode()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run repeated real-LLM RCA benchmark")
    parser.add_argument("--scenarios", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument(
        "--api-style",
        choices=("chat_completions", "responses"),
        default="chat_completions",
    )
    parser.add_argument("--api-key-env", default="DEVOPS_AGENT_BENCHMARK_API_KEY")
    parser.add_argument("--runs-per-scenario", type=int, default=5)
    parser.add_argument("--scenario")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--input-cost-per-million", type=float, required=True)
    parser.add_argument("--output-cost-per-million", type=float, required=True)
    parser.add_argument("--allow-insecure-http", action="store_true")
    args = parser.parse_args(argv)
    api_key = os.getenv(args.api_key_env)
    if not api_key:
        print(
            f"missing API key environment variable: {args.api_key_env}", file=sys.stderr
        )
        return 2
    try:
        result = asyncio.run(
            run_live_benchmark(
                scenario_directory=args.scenarios,
                input_path=args.input,
                output_directory=args.output,
                git_commit=args.git_commit,
                runs_per_scenario=args.runs_per_scenario,
                scenario_id=args.scenario,
                config=LiveLLMConfig(
                    base_url=args.base_url,
                    api_key=SecretStr(api_key),
                    provider=args.provider,
                    model=args.model,
                    api_style=args.api_style,
                    temperature=args.temperature,
                    top_p=args.top_p,
                    max_tokens=args.max_tokens,
                    input_cost_per_million=args.input_cost_per_million,
                    output_cost_per_million=args.output_cost_per_million,
                    allow_insecure_http=args.allow_insecure_http,
                ),
            )
        )
    except (AppValidationError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "passed": result["summary"]["passed_runs"]
                == result["summary"]["total_runs"],
                "runs": result["summary"]["total_runs"],
                "synthetic": result["execution"]["synthetic"],
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return (
        0 if result["summary"]["passed_runs"] == result["summary"]["total_runs"] else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
