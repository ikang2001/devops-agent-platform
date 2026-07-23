from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from devops_agent_platform.infrastructure.logging.context import (
    clear_trace_id,
    set_trace_id,
)

TRACE_ID_HEADER = "X-Trace-Id"


class TraceIdMiddleware(BaseHTTPMiddleware):
    """为每个请求和响应绑定 trace_id。

    输入规则：
        优先使用调用方传入的 X-Trace-Id；如果请求头不存在，则生成
        trc_ 前缀的 UUID，保证每次请求都可追踪。

    输出规则：
        响应头必须返回同一个 X-Trace-Id，便于前端、网关、日志系统串联排查。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        trace_id = request.headers.get(TRACE_ID_HEADER) or f"trc_{uuid4().hex}"
        request.state.trace_id = trace_id
        set_trace_id(trace_id)
        try:
            response = await call_next(request)
            response.headers[TRACE_ID_HEADER] = trace_id
            return response
        finally:
            clear_trace_id()
