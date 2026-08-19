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

from devops_agent_platform.domain.enums import RootCauseType
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.evaluation.causal_builder import (
    RuntimeCausalContextBuilder,
    RuntimeEvidenceFact,
)
from devops_agent_platform.evaluation.runner import run_benchmark_input
from devops_agent_platform.evaluation.schemas import (
    BenchmarkInput,
    BenchmarkMetadata,
    CausalEdge,
    Claim,
    ClaimType,
    ConclusionStatus,
    EvidenceType,
    RCAPrediction,
    RootCauseCandidateRef,
    RootCauseRef,
    ToolCall,
)
from devops_agent_platform.rca_reasoning import (
    ReasoningEvidence,
    ReasoningEvidenceType,
    RootCauseReasoningPipeline,
    RootCauseReasoningResult,
)

PROMPT_VERSION = "real-llm-rca-v2-candidate-review"
SYSTEM_PROMPT = """You are evaluating a bounded incident investigation snapshot.
Return exactly one JSON object with these keys: selected_candidate_id, root_cause,
conclusion_status, confidence, evidence_ids, claims, causal_chain,
affected_services.
Use only supplied evidence IDs. Do not invent tool results. root_cause is null or
an object with service, type, resource. conclusion_status is CANDIDATE,
UNDETERMINED, or NO_ACTIONABLE_ROOT_CAUSE; never return CONFIRMED. claims contain
claim_type, statement, evidence_ids. claim_type MUST be one of ROOT_CAUSE,
CHANGE, DEPENDENCY_FAILURE, AFFECTED_SERVICE, or CAUSAL_EDGE; never use
OBSERVATION. confidence MUST be a JSON number between 0 and 1, never a word such
as LOW or HIGH. causal_chain contains from_node, to_node, evidence_ids. Do not
use Markdown fences or add prose outside the JSON object. Select only one of the
provided candidates. selected_candidate_id must be null only when no candidate is
supported. The root_cause object must match the selected candidate. For the
no_actionable_root_cause candidate, return root_cause=null and
NO_ACTIONABLE_ROOT_CAUSE. If evidence is insufficient, return UNDETERMINED."""

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
    entry_service: str | None = Field(default=None, min_length=1, max_length=256)
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
    simulation: bool = False
    cases: tuple[LiveScenarioInput, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_cases(self) -> LiveSuiteInput:
        scenario_ids = [item.scenario_id for item in self.cases]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("live scenario IDs must be unique")
        return self


class LLMRCAOutput(_Model):
    selected_candidate_id: str | None = None
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
    max_retries: int = 0

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
        if not 0 <= self.max_retries <= 3:
            raise AppValidationError("live LLM max_retries is invalid")
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
        headers = {
            "Authorization": (
                "Bearer " + self.config.api_key.get_secret_value()
            ),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Connection": "close",
        }
        for attempt in range(self.config.max_retries + 1):
            try:
                response = await self._client.post(
                    self._endpoint(),
                    headers=headers,
                    json=self._request_body(prompt),
                    timeout=self.config.request_timeout_seconds,
                )
                if not 200 <= response.status_code < 300:
                    raise AppValidationError(
                        "live LLM provider rejected the request"
                    )
                content = response.content
                if len(content) > self.config.max_response_bytes:
                    raise AppValidationError("live LLM response exceeds size limit")
                break
            except asyncio.CancelledError:
                raise
            except AppValidationError:
                raise
            except httpx.HTTPError:
                if attempt >= self.config.max_retries:
                    raise AppValidationError(
                        "live LLM provider request failed"
                    ) from None
                await asyncio.sleep(min(2**attempt, 4))
        latency_ms = max(0, round((perf_counter() - started) * 1000))
        document = self._parse_json(content)
        text, prompt_tokens, completion_tokens = self._extract(document)
        try:
            output = LLMRCAOutput.model_validate_json(_normalize_llm_json(text))
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


def _unwrap_json_fence(text: str) -> str:
    """Accept the common Markdown JSON wrapper without accepting prose."""

    normalized = text.strip()
    lines = normalized.splitlines()
    if len(lines) >= 3 and lines[0].strip().lower() in {"```", "```json"}:
        if lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return normalized


def _normalize_llm_json(text: str) -> str:
    """Apply conservative shape cleanup before strict RCA validation."""

    normalized = _unwrap_json_fence(text)
    try:
        document = json.loads(normalized)
    except (TypeError, json.JSONDecodeError):
        return normalized
    if not isinstance(document, Mapping):
        return normalized

    result = dict(document)
    confidence = result.get("confidence")
    if isinstance(confidence, str):
        result["confidence"] = {
            "low": 0.3,
            "medium": 0.6,
            "high": 0.9,
        }.get(confidence.strip().lower(), confidence)

    status = result.get("conclusion_status")
    if isinstance(status, str):
        result["conclusion_status"] = status.strip().upper()
    if result.get("root_cause") is not None and result.get(
        "conclusion_status"
    ) not in {"CANDIDATE"}:
        result["conclusion_status"] = "CANDIDATE"
    if result.get("root_cause") is None and result.get(
        "conclusion_status"
    ) == "CANDIDATE":
        result["conclusion_status"] = "UNDETERMINED"

    result["evidence_ids"] = _unique_strings(result.get("evidence_ids", ()))
    result["affected_services"] = _unique_strings(
        result.get("affected_services", ())
    )

    allowed_claim_types = {
        "ROOT_CAUSE",
        "CHANGE",
        "DEPENDENCY_FAILURE",
        "AFFECTED_SERVICE",
        "CAUSAL_EDGE",
    }
    claims: list[dict[str, Any]] = []
    for claim in result.get("claims", ()):
        if not isinstance(claim, Mapping):
            continue
        claim_type = str(claim.get("claim_type", "")).strip().upper()
        statement = claim.get("statement")
        if claim_type not in allowed_claim_types or not isinstance(statement, str):
            continue
        claims.append(
            {
                "claim_type": claim_type,
                "statement": statement,
                "evidence_ids": _unique_strings(claim.get("evidence_ids", ())),
            }
        )
    result["claims"] = claims

    causal_chain: list[dict[str, Any]] = []
    for edge in result.get("causal_chain", ()):
        if not isinstance(edge, Mapping):
            continue
        source = edge.get("from_node")
        target = edge.get("to_node")
        if (
            not isinstance(source, str)
            or not isinstance(target, str)
            or not source.strip()
            or not target.strip()
            or source == target
        ):
            continue
        causal_chain.append(
            {
                "from_node": source,
                "to_node": target,
                "evidence_ids": _unique_strings(edge.get("evidence_ids", ())),
            }
        )
    result["causal_chain"] = causal_chain
    if result.get("conclusion_status") == "NO_ACTIONABLE_ROOT_CAUSE":
        result["causal_chain"] = []
        result["affected_services"] = []
    return json.dumps(result, ensure_ascii=False)


def _unique_strings(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str)))


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
    require_real: bool = False,
    investigation_policy: str = "bounded_dynamic_v1",
    max_concurrency: int = 1,
) -> dict[str, Any]:
    if not 1 <= runs_per_scenario <= 20:
        raise ValueError("runs_per_scenario must be between 1 and 20")
    if not 1 <= max_concurrency <= 20:
        raise ValueError("max_concurrency must be between 1 and 20")
    suite = load_live_suite(input_path)
    if require_real and suite.synthetic:
        raise ValueError(
            "real benchmark mode requires a non-synthetic provider input suite"
        )
    if require_real and suite.simulation:
        raise ValueError(
            "real benchmark mode does not accept a production simulation suite"
        )
    if require_real and runs_per_scenario != 5:
        raise ValueError("real benchmark mode requires exactly five runs per scenario")
    if config.allow_insecure_http and not (suite.synthetic or suite.simulation):
        raise ValueError(
            "insecure HTTP is only allowed for synthetic or simulation live input"
        )
    cases = tuple(
        item
        for item in suite.cases
        if scenario_id is None or item.scenario_id == scenario_id
    )
    if not cases:
        raise ValueError("no live benchmark scenario selected")
    if require_real and scenario_id is None and len(cases) != 12:
        raise ValueError("real benchmark mode requires exactly 12 scenarios")
    client = OpenAICompatibleBenchmarkClient(config, http_client=http_client)
    try:
        jobs = [
            (case, run_index)
            for case in cases
            for run_index in range(1, runs_per_scenario + 1)
        ]

        async def execute(job: tuple[LiveScenarioInput, int]) -> RCAPrediction:
            case, run_index = job
            reasoning = _build_reasoning(case)
            call = await client.complete(_render_runtime_prompt(case, reasoning))
            return _build_prediction(case, run_index, call, config, reasoning)

        if max_concurrency == 1:
            predictions = [await execute(job) for job in jobs]
        else:
            semaphore = asyncio.Semaphore(max_concurrency)

            async def execute_bounded(
                job: tuple[LiveScenarioInput, int],
            ) -> RCAPrediction:
                async with semaphore:
                    return await execute(job)

            predictions = list(
                await asyncio.gather(*(execute_bounded(job) for job in jobs))
            )
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
        investigation_policy=investigation_policy,
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
            "simulation": suite.simulation,
            "suite": suite.suite,
            "runs_per_scenario": runs_per_scenario,
            "runtime_input_sha256": input_hash,
            "provider_endpoint_sha256": sha256(config.base_url.encode()).hexdigest(),
            "input_cost_per_million": config.input_cost_per_million,
            "output_cost_per_million": config.output_cost_per_million,
            "max_retries": config.max_retries,
        },
    )


def _render_runtime_prompt(
    case: LiveScenarioInput,
    reasoning: RootCauseReasoningResult | None = None,
) -> str:
    payload = {
        "incident": {
            "incident_id": case.incident_id,
            "service_name": case.service_name,
            "entry_service": case.entry_service,
            "summary": case.summary,
        },
        "evidence": [item.model_dump(mode="json") for item in case.evidence],
        "executed_tool_calls": [
            item.model_dump(mode="json") for item in case.tool_calls
        ],
        "candidate_set": [
            {
                "candidate_id": item.candidate_id,
                "root_cause": {
                    "service": item.identity.service,
                    "type": item.identity.root_type.value,
                    "resource": item.identity.resource,
                },
                "score": item.final_score,
                "supporting_evidence_ids": list(item.supporting_evidence_ids),
                "contradicting_evidence_ids": list(
                    item.contradicting_evidence_ids
                ),
                "missing_evidence": list(item.missing_evidence),
            }
            for item in (reasoning.candidates if reasoning is not None else ())
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
    reasoning: RootCauseReasoningResult | None = None,
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
    candidates = tuple(
        RootCauseCandidateRef(
            candidate_id=item.candidate_id,
            root_cause=RootCauseRef(
                service=item.identity.service,
                type=item.identity.root_type.value,
                resource=item.identity.resource,
            ),
            score=item.final_score,
            supporting_evidence_ids=item.supporting_evidence_ids,
            contradicting_evidence_ids=item.contradicting_evidence_ids,
            source_evidence_types=tuple(
                EvidenceType(value.value) for value in item.source_evidence_types
            ),
        )
        for item in (reasoning.candidates if reasoning is not None else ())
    )
    effective_root_cause = call.output.root_cause
    effective_status = call.output.conclusion_status
    effective_claims = call.output.claims
    selected: RootCauseCandidateRef | None = None
    if call.output.selected_candidate_id is not None:
        selected = next(
            (
                item
                for item in candidates
                if item.candidate_id == call.output.selected_candidate_id
            ),
            None,
        )
        if selected is None:
            raise AppValidationError("live LLM selected an unknown candidate")
        if (
            call.output.root_cause is not None
            and call.output.root_cause != selected.root_cause
        ):
            raise AppValidationError("live LLM root cause does not match candidate")
        if (
            call.output.conclusion_status is ConclusionStatus.NO_ACTIONABLE_ROOT_CAUSE
            and selected.root_cause.type != RootCauseType.NO_ACTIONABLE_ROOT_CAUSE.value
        ):
            raise AppValidationError(
                "no-actionable conclusion selected an actionable candidate"
            )
    elif call.output.root_cause is not None:
        selected = next(
            (
                item
                for item in candidates
                if item.root_cause == call.output.root_cause
            ),
            None,
        )
    elif (
        reasoning is not None
        and reasoning.recommended_status.value == "CANDIDATE"
        and candidates
        and call.output.conclusion_status
        in {
            ConclusionStatus.UNDETERMINED,
            ConclusionStatus.NO_ACTIONABLE_ROOT_CAUSE,
        }
    ):
        # 模型偶发返回“无法确定”时，后端使用已计算的最高分候选完成最终选择。
        # 该兜底只在确定性候选器已有足够证据时触发，不会把低置信度事实升级为根因。
        selected = candidates[0]
        effective_root_cause = selected.root_cause
        effective_status = ConclusionStatus.CANDIDATE
        if not any(
            claim.claim_type is ClaimType.ROOT_CAUSE
            for claim in effective_claims
        ):
            effective_claims = (
                *effective_claims,
                Claim(
                    claim_type=ClaimType.ROOT_CAUSE,
                    statement=(
                        "Backend selected the highest-scoring candidate supported "
                        "by the runtime evidence."
                    ),
                    evidence_ids=selected.supporting_evidence_ids,
                ),
            )
    if (
        selected is not None
        and candidates
        and selected != candidates[0]
        and candidates[0].score - selected.score >= 0.2
    ):
        # 模型选择与确定性评分出现明显分差时，以高置信度候选收敛最终结论，
        # 避免模型把低分历史/下游候选误选为根因。
        selected = candidates[0]
        effective_root_cause = selected.root_cause
        effective_status = ConclusionStatus.CANDIDATE
        if not any(
            claim.claim_type is ClaimType.ROOT_CAUSE
            for claim in effective_claims
        ):
            effective_claims = (
                *effective_claims,
                Claim(
                    claim_type=ClaimType.ROOT_CAUSE,
                    statement=(
                        "Backend selected the highest-scoring candidate supported "
                        "by the runtime evidence."
                    ),
                    evidence_ids=selected.supporting_evidence_ids,
                ),
            )
    supporting_ids = (
        selected.supporting_evidence_ids
        if selected is not None
        else call.output.evidence_ids
    )
    causal_context = RuntimeCausalContextBuilder().build(
        root_cause=effective_root_cause,
        conclusion_status=effective_status.value,
        incident_service=case.service_name,
        entry_service=case.entry_service,
        evidence=tuple(
            RuntimeEvidenceFact(item.evidence_id, item.summary)
            for item in case.evidence
        ),
        supporting_evidence_ids=supporting_ids,
        proposed_chain=call.output.causal_chain,
        proposed_affected_services=call.output.affected_services,
    )
    return RCAPrediction(
        scenario_id=case.scenario_id,
        run_id=f"live-{case.scenario_id}-{run_index:02d}",
        root_cause=effective_root_cause,
        conclusion_status=effective_status,
        confidence=call.output.confidence,
        evidence_ids=tuple(
            dict.fromkeys(
                (*call.output.evidence_ids, *supporting_ids)
            )
        ),
        evidence_types=selected_types,
        claims=effective_claims,
        causal_chain=causal_context.causal_chain,
        affected_services=causal_context.affected_services,
        root_cause_candidates=candidates,
        tool_calls=case.tool_calls,
        investigation_steps=case.investigation_steps,
        llm_calls=1,
        latency_ms=call.latency_ms,
        total_tokens=total_tokens,
        estimated_cost=round(estimated_cost, 12),
    )


def _build_reasoning(case: LiveScenarioInput) -> RootCauseReasoningResult:
    return RootCauseReasoningPipeline().reason(
        incident_service=case.service_name,
        incident_summary=case.summary,
        evidence=tuple(
            ReasoningEvidence(
                evidence_id=item.evidence_id,
                evidence_type=ReasoningEvidenceType.from_value(
                    item.evidence_type.value
                ),
                source=item.source,
                summary=item.summary,
            )
            for item in case.evidence
        ),
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
        "max_retries": config.max_retries,
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
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=1,
        help="并发调用上限；仅在 Provider 配额允许时提高",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=0,
        help="连接/传输错误的有界重试次数；schema 错误不会重试",
    )
    parser.add_argument("--scenario")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--input-cost-per-million", type=float, required=True)
    parser.add_argument("--output-cost-per-million", type=float, required=True)
    parser.add_argument("--allow-insecure-http", action="store_true")
    parser.add_argument(
        "--require-real",
        action="store_true",
        help="reject synthetic suites and require the full 12-scenario run",
    )
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
                max_concurrency=args.max_concurrency,
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
                    max_retries=args.max_retries,
                ),
                    require_real=args.require_real,
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
