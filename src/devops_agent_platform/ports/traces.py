from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class TraceSummary:
    """Tempo搜索返回的有限Trace摘要，不包含完整Span属性。"""

    trace_id: str
    root_service_name: str
    root_trace_name: str
    start_time_unix_nano: str
    duration_ms: float
    matched_spans: int


@dataclass(frozen=True)
class TraceSearchResult:
    """一次有界Trace搜索结果及其扫描成本提示。"""

    traces: tuple[TraceSummary, ...]
    inspected_traces: int | None
    inspected_bytes: int | None
    possibly_truncated: bool


class TraceSearchPort(Protocol):
    """应用内Trace工具依赖的范围搜索端口。"""

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
        """搜索有限Trace摘要，禁止返回完整Trace负载。"""
        ...
