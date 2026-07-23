import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from devops_agent_platform.bootstrap.app import create_app
from devops_agent_platform.interfaces.http.middleware import TraceIdMiddleware


def test_trace_id_is_generated_when_header_is_missing() -> None:
    client = TestClient(create_app(runtime_enabled=False))

    response = client.get("/healthz")

    assert response.headers["X-Trace-Id"].startswith("trc_")
    assert response.json()["trace_id"] == response.headers["X-Trace-Id"]


def test_trace_id_header_is_propagated() -> None:
    client = TestClient(create_app(runtime_enabled=False))
    trace_id = "trc_test_123"

    response = client.get("/healthz", headers={"X-Trace-Id": trace_id})

    assert response.headers["X-Trace-Id"] == trace_id
    assert response.json()["trace_id"] == trace_id


def test_unhandled_error_access_log_excludes_exception_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """500访问日志保留模板维度，但不能附带业务异常正文。"""
    app = FastAPI()

    @app.get("/explode/{item_id}")
    async def explode(item_id: str) -> None:
        del item_id
        raise RuntimeError("password=request-secret")

    app.add_middleware(TraceIdMiddleware)
    client = TestClient(app, raise_server_exceptions=False)

    with caplog.at_level(
        logging.ERROR,
        logger="devops_agent_platform.http.access",
    ):
        response = client.get("/explode/customer-001")

    assert response.status_code == 500
    record = next(
        item
        for item in caplog.records
        if item.getMessage() == "HTTP请求处理完成"
    )
    assert record.http_path == "/explode/{item_id}"
    assert record.http_status_code == 500
    assert record.exc_info is None
    assert "request-secret" not in caplog.text
    assert "customer-001" not in caplog.text


class FailingMetrics:
    """三个HTTP指标阶段均抛出包含秘密的异常。"""

    def start_http_request(self, method: str) -> str:
        del method
        raise RuntimeError("password=start-secret")

    def finish_http_request(self, **kwargs) -> None:
        del kwargs
        raise RuntimeError("token=finish-secret")

    def abandon_http_request(self, method: str) -> None:
        del method
        raise RuntimeError("api_key=abandon-secret")


def test_metrics_observer_logs_exclude_exception_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """非关键指标故障应固定分类记录，且不能污染业务日志。"""
    app = FastAPI()
    middleware = TraceIdMiddleware(
        app,
        metrics=FailingMetrics(),  # type: ignore[arg-type]
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/test",
            "raw_path": b"/api/v1/test",
            "root_path": "",
            "scheme": "http",
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "app": app,
        }
    )

    with caplog.at_level(
        logging.ERROR,
        logger="devops_agent_platform.http.access",
    ):
        assert middleware._start_metrics(request) is None
        middleware._finish_metrics(
            request=request,
            method="POST",
            status_code=200,
            duration_seconds=0.01,
        )
        middleware._abandon_metrics("POST")

    assert [record.getMessage() for record in caplog.records] == [
        "开始记录HTTP指标失败",
        "完成HTTP指标记录失败",
        "归还HTTP处理中指标失败",
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "start-secret" not in caplog.text
    assert "finish-secret" not in caplog.text
    assert "abandon-secret" not in caplog.text
