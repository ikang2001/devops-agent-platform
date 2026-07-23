import asyncio
import json
import logging

from devops_agent_platform.infrastructure.logging.context import (
    clear_trace_id,
    get_current_trace_id,
    set_trace_id,
)
from devops_agent_platform.infrastructure.logging.formatter import (
    JsonLogFormatter,
)


def test_json_formatter_outputs_whitelisted_fields_and_context() -> None:
    formatter = JsonLogFormatter(
        service_name="devops-agent-platform",
        environment="test",
    )
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=20,
        msg="告警处理完成",
        args=(),
        exc_info=None,
    )
    record.http_method = "POST"
    record.http_status_code = 202
    record.authorization = "Bearer must-not-appear"
    token = set_trace_id("trc_formatter_test")
    try:
        payload = json.loads(formatter.format(record))
    finally:
        clear_trace_id(token)

    assert payload["trace_id"] == "trc_formatter_test"
    assert payload["service"] == "devops-agent-platform"
    assert payload["environment"] == "test"
    assert payload["http_method"] == "POST"
    assert payload["http_status_code"] == 202
    assert "authorization" not in payload
    assert "\n" not in formatter.format(record)


def test_json_formatter_limits_message_and_exception_size() -> None:
    formatter = JsonLogFormatter("service", "test")
    try:
        raise ValueError("x" * 3000)
    except ValueError as exc:
        record = logging.LogRecord(
            name="test.logger",
            level=logging.ERROR,
            pathname=__file__,
            lineno=50,
            msg="m" * 5000,
            args=(),
            exc_info=(type(exc), exc, exc.__traceback__),
        )

    payload = json.loads(formatter.format(record))

    assert payload["message"].endswith("...[truncated]")
    assert payload["exception"]["message"].endswith("...[truncated]")
    assert len(payload["exception"]["stack_trace"]) <= 16400


def test_json_formatter_redacts_common_credentials() -> None:
    formatter = JsonLogFormatter("service", "test")
    try:
        raise RuntimeError(
            "password=plain token:abc "
            "postgresql://devops:database-secret@db/prod"
        )
    except RuntimeError as exc:
        record = logging.LogRecord(
            name="test.logger",
            level=logging.ERROR,
            pathname=__file__,
            lineno=75,
            msg="authorization=Bearer-secret",
            args=(),
            exc_info=(type(exc), exc, exc.__traceback__),
        )

    serialized = formatter.format(record)

    assert "plain" not in serialized
    assert "abc" not in serialized
    assert "database-secret" not in serialized
    assert "Bearer-secret" not in serialized
    assert serialized.count("[REDACTED]") >= 4


def test_nested_trace_context_restores_previous_value() -> None:
    outer_token = set_trace_id("trc_outer")
    inner_token = set_trace_id("trc_inner")

    clear_trace_id(inner_token)
    assert get_current_trace_id() == "trc_outer"

    clear_trace_id(outer_token)
    assert get_current_trace_id() is None


async def test_trace_context_is_isolated_between_concurrent_tasks() -> None:
    ready = asyncio.Event()
    entered = 0
    lock = asyncio.Lock()

    async def read_context(trace_id: str) -> str | None:
        nonlocal entered
        token = set_trace_id(trace_id)
        try:
            async with lock:
                entered += 1
                if entered == 2:
                    ready.set()
            await ready.wait()
            await asyncio.sleep(0)
            return get_current_trace_id()
        finally:
            clear_trace_id(token)

    results = await asyncio.gather(
        read_context("trc_task_a"),
        read_context("trc_task_b"),
    )

    assert results == ["trc_task_a", "trc_task_b"]
    assert get_current_trace_id() is None
