import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse

import httpx
from pydantic import SecretStr

from devops_agent_platform.application.exceptions import MetricsSourceError
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.metrics import (
    MetricSeries,
    MetricsRangeResult,
)


@dataclass(frozen=True)
class PrometheusRangeClientConfig:
    """Prometheus范围查询客户端的网络和容量配置。"""

    base_url: str
    request_timeout_seconds: float = 3.0
    evaluation_timeout_seconds: int = 2
    max_response_bytes: int = 512 * 1024
    tenant_header_name: str | None = "X-Scope-OrgID"
    bearer_token: SecretStr | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """校验固定端点、超时、容量和可选租户头。"""
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
            "evaluation_timeout_seconds",
            self.evaluation_timeout_seconds,
            30,
        )
        self._validate_positive_int(
            "max_response_bytes",
            self.max_response_bytes,
            4 * 1024 * 1024,
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


class PrometheusRangeClient:
    """使用有界异步HTTP请求调用Prometheus query_range API。"""

    def __init__(
        self,
        config: PrometheusRangeClientConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """创建客户端；外部传入AsyncClient时不接管其生命周期。"""
        if not isinstance(config, PrometheusRangeClientConfig):
            raise AppValidationError("config must be a PrometheusRangeClientConfig")
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
        self._endpoint = f"{config.base_url.rstrip('/')}/api/v1/query_range"
        self._closed = False

    async def query_range(
        self,
        *,
        tenant_id: str,
        query: str,
        start: datetime,
        end: datetime,
        step_seconds: int,
        series_limit: int,
        samples_per_series_limit: int,
        trace_id: str,
    ) -> MetricsRangeResult:
        """执行参数绑定的范围查询并严格解析矩阵响应。"""
        self._validate_request(
            tenant_id=tenant_id,
            query=query,
            start=start,
            end=end,
            step_seconds=step_seconds,
            series_limit=series_limit,
            samples_per_series_limit=samples_per_series_limit,
            trace_id=trace_id,
        )
        if self._closed:
            raise MetricsSourceError("Prometheus client is closed")
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
        form_data = {
            "query": query,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "step": str(step_seconds),
            "timeout": (f"{self._config.evaluation_timeout_seconds}s"),
            "limit": str(series_limit),
        }
        content = await self._post_bounded(headers, form_data)
        return self._parse_response(
            content,
            series_limit=series_limit,
            samples_per_series_limit=samples_per_series_limit,
        )

    async def close(self) -> None:
        """关闭内部连接池；重复调用安全。"""
        if self._closed:
            return
        self._closed = True
        if self._owns_http_client:
            await self._http_client.aclose()

    async def _post_bounded(
        self,
        headers: dict[str, str],
        form_data: dict[str, str],
    ) -> bytes:
        """流式读取有限响应，网络和HTTP错误统一脱敏。"""
        content = bytearray()
        try:
            async with self._http_client.stream(
                "POST",
                self._endpoint,
                headers=headers,
                data=form_data,
                timeout=self._config.request_timeout_seconds,
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self._config.max_response_bytes:
                        raise MetricsSourceError(
                            "Prometheus response exceeds size limit"
                        )
        except asyncio.CancelledError:
            raise
        except MetricsSourceError:
            raise
        except httpx.HTTPError as exc:
            raise MetricsSourceError("Prometheus query request failed") from exc
        if not content:
            raise MetricsSourceError("Prometheus returned an empty response")
        return bytes(content)

    def _parse_response(
        self,
        content: bytes,
        *,
        series_limit: int,
        samples_per_series_limit: int,
    ) -> MetricsRangeResult:
        """校验Prometheus envelope、矩阵类型、序列和样本容量。"""
        try:
            document = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MetricsSourceError("Prometheus returned invalid JSON") from exc
        if not isinstance(document, dict):
            raise MetricsSourceError("Prometheus response must be an object")
        if document.get("status") != "success":
            raise MetricsSourceError("Prometheus query was not successful")
        data = document.get("data")
        if (
            not isinstance(data, dict)
            or data.get("resultType") != "matrix"
            or not isinstance(data.get("result"), list)
        ):
            raise MetricsSourceError("Prometheus returned an unsupported result")
        raw_series = data["result"]
        if len(raw_series) > series_limit:
            raise MetricsSourceError("Prometheus returned too many series")
        series = tuple(
            self._parse_series(item, samples_per_series_limit) for item in raw_series
        )
        warnings = self._parse_warnings(document.get("warnings", []))
        return MetricsRangeResult(
            series=series,
            warnings=warnings,
            possibly_truncated=(len(series) == series_limit),
        )

    @staticmethod
    def _parse_series(
        raw_series: object,
        samples_per_series_limit: int,
    ) -> MetricSeries:
        """转换单条矩阵序列并拒绝标签或样本结构漂移。"""
        if not isinstance(raw_series, dict):
            raise MetricsSourceError("Prometheus series must be an object")
        metric = raw_series.get("metric")
        values = raw_series.get("values")
        if not isinstance(metric, dict) or not isinstance(values, list):
            raise MetricsSourceError("Prometheus series structure is invalid")
        if len(metric) > 100 or len(values) > samples_per_series_limit:
            raise MetricsSourceError("Prometheus series exceeds capacity limits")
        labels: list[tuple[str, str]] = []
        for name, value in metric.items():
            if (
                not isinstance(name, str)
                or not isinstance(value, str)
                or not 1 <= len(name) <= 128
                or len(value) > 512
            ):
                raise MetricsSourceError("Prometheus label is invalid")
            labels.append((name, value))
        samples: list[tuple[float, str]] = []
        for sample in values:
            if (
                not isinstance(sample, list)
                or len(sample) != 2
                or isinstance(sample[0], bool)
                or not isinstance(sample[0], int | float)
                or not isinstance(sample[1], str)
                or len(sample[1]) > 128
            ):
                raise MetricsSourceError("Prometheus sample is invalid")
            samples.append((float(sample[0]), sample[1]))
        return MetricSeries(
            labels=tuple(sorted(labels)),
            samples=tuple(samples),
        )

    @staticmethod
    def _parse_warnings(value: object) -> tuple[str, ...]:
        """保留有限服务端警告，避免大文本污染工具输出。"""
        if not isinstance(value, list) or len(value) > 20:
            raise MetricsSourceError("Prometheus warnings are invalid")
        warnings: list[str] = []
        for warning in value:
            if not isinstance(warning, str) or len(warning) > 512:
                raise MetricsSourceError("Prometheus warning is invalid")
            warnings.append(warning)
        return tuple(warnings)

    @staticmethod
    def _validate_request(
        *,
        tenant_id: str,
        query: str,
        start: datetime,
        end: datetime,
        step_seconds: int,
        series_limit: int,
        samples_per_series_limit: int,
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
        if end <= start:
            raise AppValidationError("end must be later than start")
        for field_name, value, maximum in (
            ("step_seconds", step_seconds, 3600),
            ("series_limit", series_limit, 1000),
            (
                "samples_per_series_limit",
                samples_per_series_limit,
                10000,
            ),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= maximum
            ):
                raise AppValidationError(f"{field_name} is invalid")
