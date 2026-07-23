from contextvars import ContextVar

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def set_trace_id(trace_id: str) -> None:
    """将 trace_id 绑定到当前执行上下文。"""
    _trace_id.set(trace_id)


def get_current_trace_id() -> str | None:
    """返回当前执行上下文绑定的 trace_id。"""
    return _trace_id.get()


def clear_trace_id() -> None:
    """请求处理完成后清理 trace_id 上下文。"""
    _trace_id.set(None)
