import asyncio

import pytest
from fastapi.testclient import TestClient

from devops_agent_platform.bootstrap.app import create_app
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.infrastructure.config.settings import Settings
from devops_agent_platform.interfaces.http.rate_limit import (
    BoundedFixedWindowRateLimiter,
    RateLimitConfig,
)


def test_business_api_returns_safe_429_after_bounded_quota() -> None:
    settings = Settings(
        _env_file=None,
        http_rate_limit_requests=2,
        http_rate_limit_window_seconds=30,
    )
    client = TestClient(
        create_app(settings=settings, runtime_enabled=False)
    )

    first = client.get("/api/v1/unknown")
    second = client.get("/api/v1/unknown")
    limited = client.get(
        "/api/v1/unknown",
        headers={"X-Forwarded-For": "forged-client"},
    )

    assert first.status_code == 404
    assert second.status_code == 404
    assert limited.status_code == 429
    assert limited.json()["error"] == {
        "code": "RATE_LIMITED",
        "message": "Too many requests",
    }
    assert limited.headers["Retry-After"] == "30"
    assert limited.json()["trace_id"] == limited.headers["X-Trace-Id"]


def test_operational_endpoints_do_not_consume_business_quota() -> None:
    settings = Settings(
        _env_file=None,
        http_rate_limit_requests=1,
    )
    client = TestClient(
        create_app(settings=settings, runtime_enabled=False)
    )

    for _ in range(5):
        assert client.get("/healthz").status_code == 200
        assert client.get("/metrics").status_code == 200

    assert client.get("/api/v1/unknown").status_code == 404
    assert client.get("/api/v1/unknown").status_code == 429


async def test_limiter_bounds_key_memory_and_resets_expired_window() -> None:
    now = [100.0]
    limiter = BoundedFixedWindowRateLimiter(
        RateLimitConfig(requests=1, window_seconds=10, max_keys=2),
        clock=lambda: now[0],
    )

    assert (await limiter.check("one")).allowed is True
    assert (await limiter.check("one")).allowed is False
    assert (await limiter.check("two")).allowed is True
    assert (await limiter.check("three")).allowed is True
    assert limiter.tracked_keys == 2

    now[0] = 110.0
    assert (await limiter.check("three")).allowed is True


async def test_limiter_is_atomic_under_concurrency() -> None:
    limiter = BoundedFixedWindowRateLimiter(
        RateLimitConfig(requests=5, window_seconds=60, max_keys=10),
        clock=lambda: 100.0,
    )

    decisions = await asyncio.gather(
        *(limiter.check("same-client") for _ in range(20))
    )

    assert sum(decision.allowed for decision in decisions) == 5


@pytest.mark.parametrize(
    "config",
    [
        {"requests": 0},
        {"window_seconds": 0},
        {"max_keys": 0},
        {"requests": True},
    ],
)
def test_invalid_rate_limit_config_is_rejected(
    config: dict[str, object],
) -> None:
    with pytest.raises(AppValidationError):
        RateLimitConfig(**config)  # type: ignore[arg-type]
