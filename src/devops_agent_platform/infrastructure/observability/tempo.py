import asyncio
import json
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from pydantic import SecretStr

from devops_agent_platform.application.exceptions import TracesSourceError
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.traces import (
    TraceSearchResult,
    TraceSummary,
)

_TRACE_ID = re.compile(r"^[0-9a-fA-F]{1,32}$")


@dataclass(frozen=True)
class TempoSearchClientConfig:
    """Tempo搜索客户端的网络、租户和容量配置。"""

    base_url: str
    request_timeout_seconds: float = 4.0
    max_response_bytes: int = 1024 * 1024
    max_name_bytes: int = 2048
    tenant_header_name: str | None = "X-Scope-OrgID"
    bearer_token: SecretStr | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """校验固定端点、超时、容量及可选认证配置。"""
        if not isinstance(self.base_url, str):
            raise AppValidationError("base_url must be a string")
        parsed = urlparse(self.base_url)
        if (
            self.base_url != self.base_url.strip()
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in self.base_url
            )
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise AppValidationError("base_url must be an HTTP or HTTPS URL")
        if (
            isinstance(self.request_timeout_seconds, bool)
            or not isinstance(self.request_timeout_seconds, int | float)
            or not 0 < self.request_timeout_seconds <= 30
        ):
            raise AppValidationError("request_timeout_seconds must be between 0 and 30")
        self._validate_positive_int(
            "max_response_bytes",
            self.max_response_bytes,
            8 * 1024 * 1024,
        )
        self._validate_positive_int(
            "max_name_bytes",
            self.max_name_bytes,
            16 * 1024,
        )
        if self.tenant_header_name is not None and (
            not isinstance(self.tenant_header_name, str)
            or not 1 <= len(self.tenant_header_name) <= 128
            or self.tenant_header_name != self.tenant_header_name.strip()
            or any(
                character.isspace() or ord(character) == 127
                for character in self.tenant_header_name
            )
        ):
            raise AppValidationError("tenant_header_name is invalid")
        if self.bearer_token is not None and not isinstance(
            self.bearer_token,
            SecretStr,
        ):
            raise AppValidationError("bearer_token must be a SecretStr or None")
        if isinstance(self.bearer_token, SecretStr):
            secret = self.bearer_token.get_secret_value()
            if (
                not 1 <= len(secret) <= 8192
                or secret != secret.strip()
                or any(
                    ord(character) < 32 or ord(character) == 127 for character in secret
                )
            ):
                raise AppValidationError("bearer_token is invalid")

    @staticmethod
    def _validate_positive_int(
        field_name: str,
        value: int,
        maximum: int,
    ) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= maximum
        ):
            raise AppValidationError(f"{field_name} must be between 1 and {maximum}")


class TempoSearchClient:
    """使用有界异步HTTP请求调用Tempo TraceQL搜索API。"""

    def __init__(
        self,
        config: TempoSearchClientConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """创建客户端；外部传入AsyncClient时不接管生命周期。"""
        if not isinstance(config, TempoSearchClientConfig):
            raise AppValidationError("config must be a TempoSearchClientConfig")
        self._config = config
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.request_timeout_seconds),
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
            ),
            trust_env=False,
        )
        self._endpoint = f"{config.base_url.rstrip('/')}/api/search"
        self._closed = False

    async def search(
        self,
        *,
        tenant_id: str,
        query: str,
        start: datetime,
        end: datetime,
        limit: int,
        spans_per_span_set: int,
        trace_id: str,
    ) -> TraceSearchResult:
        """执行TraceQL范围搜索并返回有限Trace摘要。"""
        self._validate_request(
            tenant_id=tenant_id,
            query=query,
            start=start,
            end=end,
            limit=limit,
            spans_per_span_set=spans_per_span_set,
            trace_id=trace_id,
        )
        if self._closed:
            raise TracesSourceError("Tempo client is closed")
        headers = {
            "Accept": "application/json",
            "X-Trace-Id": trace_id,
        }
        if self._config.tenant_header_name is not None:
            headers[self._config.tenant_header_name] = tenant_id
        if self._config.bearer_token is not None:
            headers["Authorization"] = (
                f"Bearer {self._config.bearer_token.get_secret_value()}"
            )
        params = {
            "q": query,
            "start": str(int(start.astimezone(UTC).timestamp())),
            "end": str(int(end.astimezone(UTC).timestamp())),
            "limit": str(limit),
            "spss": str(spans_per_span_set),
        }
        content = await self._get_bounded(headers, params)
        return self._parse_response(
            content,
            limit=limit,
            spans_per_span_set=spans_per_span_set,
        )

    async def close(self) -> None:
        """关闭内部HTTP连接池；重复调用安全。"""
        if self._closed:
            return
        self._closed = True
        if self._owns_http_client:
            await self._http_client.aclose()

    async def _get_bounded(
        self,
        headers: dict[str, str],
        params: dict[str, str],
    ) -> bytes:
        """流式读取有限响应，网络错误统一脱敏。"""
        content = bytearray()
        try:
            async with self._http_client.stream(
                "GET",
                self._endpoint,
                headers=headers,
                params=params,
                timeout=self._config.request_timeout_seconds,
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self._config.max_response_bytes:
                        raise TracesSourceError("Tempo response exceeds size limit")
        except asyncio.CancelledError:
            raise
        except TracesSourceError:
            raise
        except httpx.HTTPError as exc:
            raise TracesSourceError("Tempo search request failed") from exc
        if not content:
            raise TracesSourceError("Tempo returned an empty response")
        return bytes(content)

    def _parse_response(
        self,
        content: bytes,
        *,
        limit: int,
        spans_per_span_set: int,
    ) -> TraceSearchResult:
        """校验Tempo搜索响应并主动丢弃完整Span属性。"""
        try:
            document = json.loads(content)
        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            RecursionError,
        ) as exc:
            raise TracesSourceError("Tempo returned invalid JSON") from exc
        if not isinstance(document, dict):
            raise TracesSourceError("Tempo response must be an object")
        raw_traces = document.get("traces")
        raw_metrics = document.get("metrics", {})
        if not isinstance(raw_traces, list) or not isinstance(
            raw_metrics,
            dict,
        ):
            raise TracesSourceError("Tempo search structure is invalid")
        if len(raw_traces) > limit:
            raise TracesSourceError("Tempo returned too many traces")
        traces, invalid_tail = self._parse_recent_trace_prefix(
            raw_traces,
            spans_per_span_set,
        )
        inspected_traces = self._optional_nonnegative_int(
            raw_metrics.get("inspectedTraces"),
            "inspectedTraces",
        )
        inspected_bytes = self._optional_nonnegative_int(
            raw_metrics.get("inspectedBytes"),
            "inspectedBytes",
        )
        completed_jobs = self._optional_nonnegative_int(
            raw_metrics.get("completedJobs"),
            "completedJobs",
        )
        total_jobs = self._optional_nonnegative_int(
            raw_metrics.get("totalJobs"),
            "totalJobs",
        )
        if (
            completed_jobs is not None
            and total_jobs is not None
            and completed_jobs > total_jobs
        ):
            raise TracesSourceError("Tempo search job metrics are inconsistent")
        partial_search = (
            completed_jobs is not None
            and total_jobs is not None
            and completed_jobs < total_jobs
        )
        return TraceSearchResult(
            traces=traces,
            inspected_traces=inspected_traces,
            inspected_bytes=inspected_bytes,
            possibly_truncated=(
                len(traces) == limit or partial_search or invalid_tail
            ),
        )

    def _parse_recent_trace_prefix(
        self,
        raw_traces: list[object],
        spans_per_span_set: int,
    ) -> tuple[tuple[TraceSummary, ...], bool]:
        """保留按时间倒序的合法前缀，拒绝非法最新结果。"""
        traces: list[TraceSummary] = []
        for raw_trace in raw_traces:
            try:
                traces.append(self._parse_trace(raw_trace, spans_per_span_set))
            except TracesSourceError:
                if not traces:
                    raise
                return tuple(traces), True
        return tuple(traces), False

    def _parse_trace(
        self,
        raw_trace: object,
        spans_per_span_set: int,
    ) -> TraceSummary:
        """解析单个Trace摘要，不复制Span attributes。"""
        if not isinstance(raw_trace, dict):
            raise TracesSourceError("Tempo trace must be an object")
        trace_id = raw_trace.get("traceID")
        root_service_name = raw_trace.get("rootServiceName", "")
        root_trace_name = raw_trace.get("rootTraceName", "")
        start_time = raw_trace.get("startTimeUnixNano")
        duration_ms = raw_trace.get("durationMs")
        normalized_trace_id = self._normalize_trace_id(trace_id)
        normalized_duration = 0.0 if duration_ms is None else duration_ms
        if (
            normalized_trace_id is None
            or not self._valid_name(root_service_name)
            or not self._valid_name(root_trace_name)
            or not isinstance(start_time, str)
            or not start_time.isdigit()
            or not 1 <= len(start_time) <= 20
            or isinstance(normalized_duration, bool)
            or not isinstance(normalized_duration, int | float)
            or not math.isfinite(float(normalized_duration))
            or float(normalized_duration) < 0
        ):
            raise TracesSourceError("Tempo trace summary is invalid")
        span_sets = raw_trace.get("spanSets", [])
        if not isinstance(span_sets, list) or len(span_sets) > 32:
            raise TracesSourceError("Tempo span sets are invalid")
        matched_spans = 0
        for span_set in span_sets:
            if not isinstance(span_set, dict):
                raise TracesSourceError("Tempo span set is invalid")
            matched = span_set.get("matched", 0)
            spans = span_set.get("spans", [])
            if (
                isinstance(matched, bool)
                or not isinstance(matched, int)
                or not 0 <= matched <= 1_000_000
                or not isinstance(spans, list)
                or len(spans) > spans_per_span_set
            ):
                raise TracesSourceError("Tempo span set is invalid")
            matched_spans += matched
        return TraceSummary(
            trace_id=normalized_trace_id,
            root_service_name=root_service_name,
            root_trace_name=root_trace_name,
            start_time_unix_nano=start_time,
            duration_ms=float(normalized_duration),
            matched_spans=matched_spans,
        )

    @staticmethod
    def _normalize_trace_id(value: object) -> str | None:
        """Restore leading zeroes omitted by some Tempo search responses."""
        if not isinstance(value, str) or _TRACE_ID.fullmatch(value) is None:
            return None
        canonical_width = 16 if len(value) <= 16 else 32
        return value.lower().zfill(canonical_width)

    def _valid_name(self, value: object) -> bool:
        return (
            isinstance(value, str)
            and len(value.encode()) <= self._config.max_name_bytes
        )

    @staticmethod
    def _optional_nonnegative_int(
        value: object,
        field_name: str,
    ) -> int | None:
        if value is None:
            return None
        if isinstance(value, str) and value.isdigit():
            value = int(value)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= 2**63 - 1
        ):
            raise TracesSourceError(f"Tempo {field_name} is invalid")
        return value

    @staticmethod
    def _validate_request(
        *,
        tenant_id: str,
        query: str,
        start: datetime,
        end: datetime,
        limit: int,
        spans_per_span_set: int,
        trace_id: str,
    ) -> None:
        """在网络调用前拒绝无效身份、时间和容量参数。"""
        for field_name, value, maximum in (
            ("tenant_id", tenant_id, 128),
            ("trace_id", trace_id, 128),
        ):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= maximum
                or value != value.strip()
                or any(
                    ord(character) < 32 or ord(character) == 127 for character in value
                )
            ):
                raise AppValidationError(f"{field_name} is invalid")
        if (
            not isinstance(query, str)
            or not 1 <= len(query) <= 8192
            or query != query.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in query)
        ):
            raise AppValidationError("query is invalid")
        for field_name, value in (("start", start), ("end", end)):
            if (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() is None
            ):
                raise AppValidationError(f"{field_name} must be timezone-aware")
        if start < datetime(1970, 1, 1, tzinfo=UTC) or end <= start:
            raise AppValidationError("trace search time range is invalid")
        for field_name, value, maximum in (
            ("limit", limit, 1000),
            ("spans_per_span_set", spans_per_span_set, 100),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= maximum
            ):
                raise AppValidationError(f"{field_name} is invalid")
