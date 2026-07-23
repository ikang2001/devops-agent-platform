import pytest

from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.identity import validate_worker_id


def test_validate_worker_id_returns_valid_identity() -> None:
    """合法Worker身份应原样返回，调用方不做隐式清洗。"""
    assert validate_worker_id("worker-001") == "worker-001"


@pytest.mark.parametrize(
    "worker_id",
    [
        None,
        "",
        " worker-001",
        "worker\nforged",
        "worker\tforged",
        "worker\x7fforged",
        "worker forged",
        "w" * 129,
    ],
)
def test_validate_worker_id_rejects_unsafe_identity(
    worker_id: object,
) -> None:
    """Worker身份不能污染租约、日志或健康快照。"""
    with pytest.raises(AppValidationError, match="worker_id"):
        validate_worker_id(worker_id)
