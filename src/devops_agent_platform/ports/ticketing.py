from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from devops_agent_platform.domain.exceptions import AppValidationError


@dataclass(frozen=True)
class TicketingSubmitRequest:
    """提交到外部工单系统前的受控请求载荷。

    外部适配器只能从这个对象读取字段，不能直接访问数据库模型，避免把 ORM
    状态泄漏到 Jira、ServiceNow 等 SDK 边界。
    """

    tenant_id: str
    ticket_submission_id: str
    ticket_draft_id: str
    target_system: str
    title: str
    description: str
    priority: str
    evidence_ids: tuple[str, ...]
    recommendations: tuple[str, ...]
    idempotency_key: str
    trace_id: str

    def __post_init__(self) -> None:
        """限制外部提交载荷大小，防止适配器无界放大请求。"""
        for field_name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("ticket_submission_id", self.ticket_submission_id, 64),
            ("ticket_draft_id", self.ticket_draft_id, 64),
            ("target_system", self.target_system, 64),
            ("title", self.title, 256),
            ("priority", self.priority, 16),
            ("idempotency_key", self.idempotency_key, 128),
            ("trace_id", self.trace_id, 128),
        ):
            self._validate_single_line_text(field_name, value, maximum)
        self._validate_multiline_text("description", self.description, 16_384)
        self._validate_tuple(
            "evidence_ids",
            self.evidence_ids,
            maximum=100,
            item_maximum=64,
        )
        self._validate_tuple(
            "recommendations",
            self.recommendations,
            maximum=20,
            item_maximum=1024,
        )

    @staticmethod
    def _validate_single_line_text(
        field_name: str,
        value: str,
        maximum: int,
    ) -> None:
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise AppValidationError(f"{field_name} is invalid")

    @staticmethod
    def _validate_multiline_text(
        field_name: str,
        value: str,
        maximum: int,
    ) -> None:
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or value != value.strip()
            or any(
                (ord(character) < 32 and character not in {"\n"})
                or ord(character) == 127
                for character in value
            )
        ):
            raise AppValidationError(f"{field_name} is invalid")

    @classmethod
    def _validate_tuple(
        cls,
        field_name: str,
        value: tuple[str, ...],
        *,
        maximum: int,
        item_maximum: int,
    ) -> None:
        if not isinstance(value, tuple) or len(value) > maximum:
            raise AppValidationError(f"{field_name} is invalid")
        for item in value:
            cls._validate_single_line_text(field_name, item, item_maximum)


@dataclass(frozen=True)
class TicketingSubmitOutcome:
    """外部工单系统返回的规范化提交结果。"""

    succeeded: bool
    external_ticket_id: str | None = None
    external_ticket_url: str | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        """区分成功和失败结果，禁止半成功结构进入应用层。"""
        if not isinstance(self.succeeded, bool):
            raise AppValidationError("succeeded must be a boolean")
        if self.succeeded:
            self._validate_required("external_ticket_id", self.external_ticket_id, 256)
            if self.external_ticket_url is not None:
                self._validate_required(
                    "external_ticket_url",
                    self.external_ticket_url,
                    2048,
                )
            if self.failure_reason is not None:
                raise AppValidationError(
                    "successful outcome must not contain failure_reason"
                )
            return
        self._validate_required("failure_reason", self.failure_reason, 2048)
        if self.external_ticket_id is not None or self.external_ticket_url is not None:
            raise AppValidationError(
                "failed outcome must not contain external ticket fields"
            )

    @staticmethod
    def _validate_required(
        field_name: str,
        value: str | None,
        maximum: int,
    ) -> None:
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise AppValidationError(f"{field_name} is invalid")


class TicketingGatewayPort(Protocol):
    """外部工单系统适配器必须实现的最小提交能力。"""

    async def submit_ticket(
        self,
        request: TicketingSubmitRequest,
    ) -> TicketingSubmitOutcome:
        """提交工单并返回规范化结果；暂态异常应直接抛出供上层重试。"""
        ...


class TicketingGatewaySubmitOutcome(StrEnum):
    """外部工单网关提交调用的固定低基数运维结果。"""

    SUCCESS = "SUCCESS"
    BUSINESS_FAILURE = "BUSINESS_FAILURE"
    GATEWAY_ERROR = "GATEWAY_ERROR"
    CANCELLED = "CANCELLED"


class TicketingGatewayObserverPort(Protocol):
    """接收外部工单网关提交结果与耗时的非关键路径观察端口。"""

    def observe_ticketing_gateway_submit(
        self,
        outcome: TicketingGatewaySubmitOutcome,
        duration_seconds: float,
    ) -> None:
        """记录一次固定分类结果；实现不得依赖业务动态标签。"""
        ...
