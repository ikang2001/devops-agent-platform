import logging
import re
from collections.abc import Awaitable, Callable
from time import perf_counter
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import NoMatchFound

from devops_agent_platform.infrastructure.logging.context import (
    clear_trace_id,
    set_trace_id,
)
from devops_agent_platform.infrastructure.metrics import ApplicationMetrics

TRACE_ID_HEADER = "X-Trace-Id"
_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_OPERATIONAL_PATHS = frozenset({"/healthz", "/readyz", "/metrics"})
_PATH_LIMIT = 512
logger = logging.getLogger("devops_agent_platform.http.access")


class TraceIdMiddleware(BaseHTTPMiddleware):
    """绑定trace_id并输出不含请求敏感数据的结构化访问日志。

    输入规则：
        优先使用调用方传入的 X-Trace-Id；如果请求头不存在，则生成
        trc_ 前缀的 UUID。非法或超长值不会进入日志上下文。

    输出规则：
        响应头必须返回同一个 X-Trace-Id，便于前端、网关、日志系统串联排查。
    """

    def __init__(
        self,
        app,
        *,
        log_health_endpoints: bool = False,
        metrics: ApplicationMetrics | None = None,
    ) -> None:
        super().__init__(app)
        self._log_health_endpoints = log_health_endpoints
        self._metrics = metrics

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """管理单个请求的日志上下文，并保证异常路径也执行清理。"""
        trace_id = self._resolve_trace_id(request)
        request.state.trace_id = trace_id
        context_token = set_trace_id(trace_id)
        started_at = perf_counter()
        metrics_method = self._start_metrics(request)
        metrics_finished = False
        try:
            try:
                response = await call_next(request)
            except Exception:
                duration_seconds = self._elapsed_seconds(started_at)
                if metrics_method is not None:
                    self._finish_metrics(
                        request=request,
                        method=metrics_method,
                        status_code=500,
                        duration_seconds=duration_seconds,
                    )
                    metrics_finished = True
                self._write_access_log(
                    request=request,
                    status_code=500,
                    duration_ms=duration_seconds * 1000,
                )
                raise
            response.headers[TRACE_ID_HEADER] = trace_id
            duration_seconds = self._elapsed_seconds(started_at)
            if metrics_method is not None:
                self._finish_metrics(
                    request=request,
                    method=metrics_method,
                    status_code=response.status_code,
                    duration_seconds=duration_seconds,
                )
                metrics_finished = True
            self._write_access_log(
                request=request,
                status_code=response.status_code,
                duration_ms=duration_seconds * 1000,
            )
            return response
        finally:
            if metrics_method is not None and not metrics_finished:
                self._abandon_metrics(metrics_method)
            clear_trace_id(context_token)

    @staticmethod
    def _resolve_trace_id(request: Request) -> str:
        """校验外部追踪标识，拒绝控制字符和无界高基数字段。"""
        candidate = request.headers.get(TRACE_ID_HEADER)
        if candidate and _TRACE_ID_PATTERN.fullmatch(candidate):
            return candidate
        return f"trc_{uuid4().hex}"

    def _write_access_log(
        self,
        *,
        request: Request,
        status_code: int,
        duration_ms: float,
    ) -> None:
        """按状态码选择级别，并使用路由模板控制日志基数。"""
        if (
            not self._log_health_endpoints
            and request.url.path in _OPERATIONAL_PATHS
        ):
            return

        extra = {
            "event": "http_request_completed",
            "http_method": request.method,
            "http_path": self._route_path(request),
            "http_status_code": status_code,
            "duration_ms": duration_ms,
        }
        level = self._log_level(status_code)
        logger.log(
            level,
            "HTTP请求处理完成",
            extra=extra,
        )

    @staticmethod
    def _route_path(request: Request) -> str:
        """优先返回路由模板，避免资源ID造成日志标签高基数。"""
        if request.scope.get("rate_limited") is True:
            return "rate_limited"
        route = request.scope.get("route")
        route_name = getattr(route, "name", None)
        value = request.url.path
        if isinstance(route_name, str):
            placeholders = {
                name: f"{{{name}}}"
                for name in request.path_params
            }
            try:
                value = str(
                    request.app.url_path_for(
                        route_name,
                        **placeholders,
                    )
                )
            except NoMatchFound:
                route_path = getattr(route, "path", None)
                if isinstance(route_path, str):
                    value = route_path
        normalized = "".join(
            character if character.isprintable() else "?"
            for character in value
        )
        return normalized[:_PATH_LIMIT]

    def _start_metrics(self, request: Request) -> str | None:
        """为业务请求增加处理中Gauge，运维端点不参与自身统计。"""
        if self._metrics is None or request.url.path in _OPERATIONAL_PATHS:
            return None
        try:
            return self._metrics.start_http_request(request.method)
        except Exception:
            logger.error("开始记录HTTP指标失败")
            return None

    def _finish_metrics(
        self,
        *,
        request: Request,
        method: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        """完成HTTP指标记录；失败不能改变原始响应。"""
        if self._metrics is None:
            return
        try:
            self._metrics.finish_http_request(
                method=method,
                route=self._metric_route(request),
                status_code=status_code,
                duration_seconds=duration_seconds,
            )
        except Exception:
            logger.error("完成HTTP指标记录失败")

    def _abandon_metrics(self, method: str) -> None:
        """请求取消等BaseException路径只归还处理中Gauge。"""
        if self._metrics is None:
            return
        try:
            self._metrics.abandon_http_request(method)
        except Exception:
            logger.error("归还HTTP处理中指标失败")

    @classmethod
    def _metric_route(cls, request: Request) -> str:
        """只允许已匹配路由模板进入指标标签。"""
        route = request.scope.get("route")
        if not isinstance(getattr(route, "name", None), str):
            return "unmatched"
        return cls._route_path(request)

    @staticmethod
    def _log_level(status_code: int) -> int:
        """按HTTP状态码映射访问日志级别。"""
        if status_code >= 500:
            return logging.ERROR
        if status_code >= 400:
            return logging.WARNING
        return logging.INFO

    @staticmethod
    def _elapsed_seconds(started_at: float) -> float:
        """返回非负的秒级请求耗时，Prometheus统一使用基本单位秒。"""
        return max(0.0, perf_counter() - started_at)
