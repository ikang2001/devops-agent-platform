import asyncio
import hashlib
import math
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.interfaces.http.responses import (
    error_response,
    get_trace_id,
)

MonotonicClock = Callable[[], float]
_PROTECTED_PREFIX = "/api/v1"


@dataclass(frozen=True)
class RateLimitConfig:
    """单进程固定窗口限流配置。"""

    requests: int = 300
    window_seconds: int = 60
    max_keys: int = 10_000

    def __post_init__(self) -> None:
        for field_name, value, minimum, maximum in (
            ("requests", self.requests, 1, 100_000),
            ("window_seconds", self.window_seconds, 1, 3600),
            ("max_keys", self.max_keys, 1, 100_000),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not minimum <= value <= maximum
            ):
                raise AppValidationError(
                    f"{field_name} must be between {minimum} and {maximum}"
                )


@dataclass(frozen=True)
class RateLimitDecision:
    """一次限流判定及拒绝后的等待秒数。"""

    allowed: bool
    retry_after_seconds: int


@dataclass
class _Window:
    started_at: float
    count: int


class BoundedFixedWindowRateLimiter:
    """使用有界 LRU key 集合避免恶意来源撑爆进程内存。"""

    def __init__(
        self,
        config: RateLimitConfig,
        *,
        clock: MonotonicClock = time.monotonic,
    ) -> None:
        if not callable(clock):
            raise AppValidationError("rate limit clock must be callable")
        self._config = config
        self._clock = clock
        self._windows: OrderedDict[str, _Window] = OrderedDict()
        self._lock = asyncio.Lock()

    @property
    def tracked_keys(self) -> int:
        """返回当前内存中的来源数量，供容量测试使用。"""
        return len(self._windows)

    async def check(self, key: str) -> RateLimitDecision:
        """原子更新指定来源窗口并返回是否允许。"""
        now = self._read_clock()
        async with self._lock:
            window = self._windows.get(key)
            if (
                window is None
                or now - window.started_at >= self._config.window_seconds
            ):
                if window is None and len(self._windows) >= self._config.max_keys:
                    self._windows.popitem(last=False)
                self._windows[key] = _Window(started_at=now, count=1)
                self._windows.move_to_end(key)
                return RateLimitDecision(True, 0)

            self._windows.move_to_end(key)
            if window.count >= self._config.requests:
                remaining = max(
                    0.0,
                    self._config.window_seconds - (now - window.started_at),
                )
                return RateLimitDecision(
                    False,
                    max(1, math.ceil(remaining)),
                )
            window.count += 1
            return RateLimitDecision(True, 0)

    def _read_clock(self) -> float:
        value = self._clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
        ):
            raise AppValidationError(
                "rate limit clock must return a finite number"
            )
        return float(value)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """为业务 API 提供单进程防御性限流，运维端点不参与配额。"""

    def __init__(
        self,
        app,
        *,
        config: RateLimitConfig,
        limiter: BoundedFixedWindowRateLimiter | None = None,
    ) -> None:
        super().__init__(app)
        self._limiter = limiter or BoundedFixedWindowRateLimiter(config)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not request.url.path.startswith(_PROTECTED_PREFIX):
            return await call_next(request)

        decision = await self._limiter.check(self._client_key(request))
        if decision.allowed:
            return await call_next(request)

        request.scope["rate_limited"] = True
        trace_id = get_trace_id(request)
        return JSONResponse(
            status_code=429,
            content=error_response(
                "RATE_LIMITED",
                "Too many requests",
                trace_id,
            ),
            headers={
                "Retry-After": str(decision.retry_after_seconds),
                "X-Trace-Id": trace_id,
            },
        )

    @staticmethod
    def _client_key(request: Request) -> str:
        """散列直连地址；代理部署必须由可信网关执行全局限流。"""
        host = request.client.host if request.client is not None else "unknown"
        return hashlib.sha256(host.encode("utf-8")).hexdigest()
