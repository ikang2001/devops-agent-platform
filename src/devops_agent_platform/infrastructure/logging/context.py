from contextvars import ContextVar, Token

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def set_trace_id(trace_id: str) -> Token[str | None]:
    """将trace_id绑定到当前上下文，并返回可精确恢复的令牌。"""
    return _trace_id.set(trace_id)


def get_current_trace_id() -> str | None:
    """返回当前执行上下文绑定的 trace_id。"""
    return _trace_id.get()


def clear_trace_id(token: Token[str | None] | None = None) -> None:
    """清理trace_id；嵌套上下文优先恢复进入前的原值。"""
    if token is None:
        _trace_id.set(None)
        return
    _trace_id.reset(token)
