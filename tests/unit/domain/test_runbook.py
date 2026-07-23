from datetime import UTC, datetime

import pytest

from devops_agent_platform.domain.enums import RunbookStatus
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.runbook import Runbook

NOW = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)


def build_runbook(**changes) -> Runbook:
    """构造领域校验测试使用的已发布手册。"""
    values = {
        "runbook_id": "rb_checkout_001",
        "runbook_key": "checkout.error-rate",
        "tenant_id": "tenant_001",
        "service_name": "checkout-api",
        "title": "Checkout error-rate response",
        "summary": "Verify dependencies before considering rollback.",
        "version": "v3",
        "status": RunbookStatus.PUBLISHED,
        "revision": 1,
        "priority": 100,
        "steps": (
            "Check the error-ratio and deployment timeline.",
            "Escalate to the checkout owner before remediation.",
        ),
        "tags": ("checkout", "http-5xx"),
        "published_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return Runbook(**values)


def test_runbook_is_constructable_and_immutable() -> None:
    """合法手册应形成不可变、版本化的领域快照。"""
    runbook = build_runbook()

    assert runbook.status is RunbookStatus.PUBLISHED
    assert runbook.steps[0].startswith("Check")
    with pytest.raises(AttributeError):
        runbook.priority = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    "changes",
    [
        {"runbook_key": " checkout.error-rate"},
        {"runbook_key": "checkout.error-rate\x7fforged"},
        {"service_name": " checkout-api"},
        {"summary": "Verify dependencies\x7fforged"},
        {"status": "PUBLISHED"},
        {"revision": 0},
        {"priority": True},
        {"priority": 1001},
        {"steps": ()},
        {"steps": tuple("step" for _ in range(21))},
        {"tags": ("HTTP-5XX",)},
        {"tags": ("duplicate", "duplicate")},
        {"updated_at": datetime(2026, 7, 1, 9, 0)},
        {"status": RunbookStatus.DRAFT, "published_at": NOW},
        {"status": RunbookStatus.PUBLISHED, "published_at": None},
    ],
)
def test_runbook_rejects_invalid_contract(changes: dict) -> None:
    """领域边界应拒绝歧义身份、无界内容和非规范标签。"""
    with pytest.raises(AppValidationError):
        build_runbook(**changes)


def test_runbook_limits_actual_utf8_content_bytes() -> None:
    """中文等多字节内容不能借字符数绕过总容量限制。"""
    with pytest.raises(AppValidationError, match="too large"):
        build_runbook(
            steps=tuple("故" * 1000 for _ in range(20)),
        )
