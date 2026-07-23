import asyncio
import os
import sys
from collections.abc import Callable, Mapping

import pytest


def _new_selector_event_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop()


def pytest_asyncio_loop_factories(
    config: pytest.Config,
    item: pytest.Item,
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
    del config, item
    if sys.platform == "win32":
        return {"selector": _new_selector_event_loop}
    return {"default": asyncio.new_event_loop}


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """日常测试可跳过真实依赖；验收模式缺配置必须在各测试中失败。"""
    del config
    if os.getenv("DEVOPS_AGENT_RUN_LIVE_TESTS") == "1":
        return
    marker = pytest.mark.skip(
        reason="set DEVOPS_AGENT_RUN_LIVE_TESTS=1 for live acceptance"
    )
    for item in items:
        if "tests/live/" in item.nodeid.replace("\\", "/"):
            item.add_marker(marker)
