from typing import Any

from pydantic import BaseModel


class ApiError(BaseModel):
    """面向调用方和前端的稳定错误结构。"""

    code: str
    message: str


class ResponseEnvelope(BaseModel):
    """统一 API 响应外壳。

    所有 HTTP 处理器都应返回 success、data、error、trace_id。
    这样前端和外部调用方可以用一致方式处理正常响应与异常响应。
    """

    success: bool
    data: Any | None
    error: ApiError | None
    trace_id: str


def success_response(data: Any, trace_id: str) -> dict[str, Any]:
    """构建成功响应外壳。"""
    return {
        "success": True,
        "data": data,
        "error": None,
        "trace_id": trace_id,
    }


def error_response(code: str, message: str, trace_id: str) -> dict[str, Any]:
    """构建错误响应外壳。"""
    return {
        "success": False,
        "data": None,
        "error": {
            "code": code,
            "message": message,
        },
        "trace_id": trace_id,
    }


def get_trace_id(request) -> str:
    """读取 TraceIdMiddleware 写入请求上下文的 trace_id。"""
    return getattr(request.state, "trace_id", "trc_missing")
