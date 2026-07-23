import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from pydantic import SecretStr

from devops_agent_platform.application.exceptions import LogsSourceError
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.ports.logs import LogsRangeResult, LogStream


@dataclass(frozen=True)
class LokiRangeClientConfig:
    """Loki范围查询客户端的网络和容量配置。"""

    base_url: str
    request_timeout_seconds: float = 3.0
    max_response_bytes: int = 1024 * 1024
    max_line_bytes: int = 16 * 1024
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
            "max_line_bytes",
            self.max_line_bytes,
            64 * 1024,
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


class LokiRangeClient:
    """使用有界异步HTTP请求调用Loki query_range API。"""

    def __init__(
        self,
        config: LokiRangeClientConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """创建客户端；外部传入AsyncClient时不接管生命周期。"""
        if not isinstance(config, LokiRangeClientConfig):
            raise AppValidationError("config must be a LokiRangeClientConfig")
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
        self._endpoint = f"{config.base_url.rstrip('/')}/loki/api/v1/query_range"
        self._closed = False

    async def query_range(
        self,
        *,
        tenant_id: str,
        query: str,
        start: datetime,
        end: datetime,
        limit: int,
        trace_id: str,
    ) -> LogsRangeResult:
        """执行倒序日志查询并严格解析streams响应。"""
        self._validate_request(
            tenant_id=tenant_id,
            query=query,
            start=start,
            end=end,
            limit=limit,
            trace_id=trace_id,
        )
        if self._closed:
            raise LogsSourceError("Loki client is closed")
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
            "query": query,
            "start": self._to_unix_nanoseconds(start),
            "end": self._to_unix_nanoseconds(end),
            "limit": str(limit),
            "direction": "backward",
        }
        content = await self._get_bounded(headers, params)
        return self._parse_response(content, limit)

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
                        raise LogsSourceError("Loki response exceeds size limit")
        except asyncio.CancelledError:
            raise
        except LogsSourceError:
            raise
        except httpx.HTTPError as exc:
            raise LogsSourceError("Loki query request failed") from exc
        if not content:
            raise LogsSourceError("Loki returned an empty response")
        return bytes(content)

    def _parse_response(
        self,
        content: bytes,
        limit: int,
    ) -> LogsRangeResult:
        """校验Loki envelope、streams类型和全局条目数量。"""
        try:
            document = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LogsSourceError("Loki returned invalid JSON") from exc
        if not isinstance(document, dict):
            raise LogsSourceError("Loki response must be an object")
        if document.get("status") != "success":
            raise LogsSourceError("Loki query was not successful")
        data = document.get("data")
        if (
            not isinstance(data, dict)
            or data.get("resultType") != "streams"
            or not isinstance(data.get("result"), list)
        ):
            raise LogsSourceError("Loki returned an unsupported result")
        raw_streams = data["result"]
        if len(raw_streams) > limit:
            raise LogsSourceError("Loki returned too many streams")
        streams: list[LogStream] = []
        total_entries = 0
        for raw_stream in raw_streams:
            stream = self._parse_stream(raw_stream)
            total_entries += len(stream.entries)
            if total_entries > limit:
                raise LogsSourceError("Loki returned too many entries")
            streams.append(stream)
        return LogsRangeResult(
            streams=tuple(streams),
            possibly_truncated=(total_entries == limit),
        )

    def _parse_stream(self, raw_stream: object) -> LogStream:
        """转换单条日志流并拒绝标签或日志行结构漂移。"""
        if not isinstance(raw_stream, dict):
            raise LogsSourceError("Loki stream must be an object")
        labels = raw_stream.get("stream")
        values = raw_stream.get("values")
        if not isinstance(labels, dict) or not isinstance(values, list):
            raise LogsSourceError("Loki stream structure is invalid")
        if len(labels) > 100:
            raise LogsSourceError("Loki stream has too many labels")
        parsed_labels: list[tuple[str, str]] = []
        for name, value in labels.items():
            if (
                not isinstance(name, str)
                or not isinstance(value, str)
                or not 1 <= len(name) <= 128
                or len(value.encode()) > 1024
            ):
                raise LogsSourceError("Loki stream label is invalid")
            parsed_labels.append((name, value))
        entries: list[tuple[str, str]] = []
        for entry in values:
            if (
                not isinstance(entry, list)
                or len(entry) != 2
                or not isinstance(entry[0], str)
                or not entry[0].isdigit()
                or len(entry[0]) > 20
                or not isinstance(entry[1], str)
                or len(entry[1].encode()) > self._config.max_line_bytes
            ):
                raise LogsSourceError("Loki log entry is invalid")
            entries.append((entry[0], entry[1]))
        return LogStream(
            labels=tuple(sorted(parsed_labels)),
            entries=tuple(entries),
        )

    @staticmethod
    def _to_unix_nanoseconds(value: datetime) -> str:
        """把带时区时间转换为不受浮点精度影响的纳秒时间戳。"""
        utc_value = value.astimezone(UTC)
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        delta = utc_value - epoch
        nanoseconds = (
            delta.days * 86_400 * 1_000_000_000
            + delta.seconds * 1_000_000_000
            + delta.microseconds * 1000
        )
        return str(nanoseconds)

    @staticmethod
    def _validate_request(
        *,
        tenant_id: str,
        query: str,
        start: datetime,
        end: datetime,
        limit: int,
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
            raise AppValidationError("log query time range is invalid")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 5000
        ):
            raise AppValidationError("limit is invalid")
