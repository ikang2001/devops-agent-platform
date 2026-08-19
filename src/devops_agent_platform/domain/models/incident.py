from dataclasses import dataclass
from datetime import datetime

from devops_agent_platform.domain.enums import AlertSeverity, IncidentStatus
from devops_agent_platform.domain.exceptions import AppValidationError, ConflictError
from devops_agent_platform.domain.models.alert import Alert


@dataclass
class Incident:
    """生产事故聚合根。

    构造阶段只校验不会随业务策略变化的基本不变量。状态流转是否合法仍由后续
    领域行为负责，避免把生命周期规则分散到接口层或数据库适配器。
    """

    incident_id: str
    tenant_id: str
    service_name: str
    severity: AlertSeverity
    status: IncidentStatus
    title: str
    created_at: datetime
    updated_at: datetime
    version: int = 1
    resolved_by: str | None = None
    resolution_reason: str | None = None
    resolved_at: datetime | None = None
    resolution_idempotency_key_hash: str | None = None
    resolution_request_hash: str | None = None
    resolution_trace_id: str | None = None
    closed_by: str | None = None
    closure_reason: str | None = None
    closed_at: datetime | None = None
    closure_idempotency_key_hash: str | None = None
    closure_request_hash: str | None = None
    closure_trace_id: str | None = None
    primary_alert_id: str | None = None
    correlated_alert_count: int = 1
    correlation_reason: str = "PRIMARY_ALERT"
    environment: str = "default"
    affected_services: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """校验事故基础字段，阻止内部调用绕过 HTTP 校验写入脏数据。"""
        self._validate_text("incident_id", self.incident_id, 64)
        self._validate_text("tenant_id", self.tenant_id, 128)
        self._validate_text("service_name", self.service_name, 256)
        self._validate_text("title", self.title, 512)

        if not isinstance(self.severity, AlertSeverity):
            raise AppValidationError("severity must be an AlertSeverity")
        if not isinstance(self.status, IncidentStatus):
            raise AppValidationError("status must be an IncidentStatus")
        self._validate_timestamp("created_at", self.created_at)
        self._validate_timestamp("updated_at", self.updated_at)
        if self.updated_at < self.created_at:
            raise AppValidationError("updated_at must not be earlier than created_at")
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise AppValidationError("version must be an integer")
        if self.version < 1:
            raise AppValidationError("version must be greater than or equal to 1")
        self._validate_resolution_state()
        self._validate_closure_state()
        if self.primary_alert_id is not None:
            self._validate_text("primary_alert_id", self.primary_alert_id, 64)
        if (
            isinstance(self.correlated_alert_count, bool)
            or not isinstance(self.correlated_alert_count, int)
            or self.correlated_alert_count < 1
        ):
            raise AppValidationError("correlated_alert_count must be positive")
        self._validate_text("correlation_reason", self.correlation_reason, 512)
        self._validate_text("environment", self.environment, 64)
        if not isinstance(self.affected_services, tuple):
            raise AppValidationError("affected_services must be a tuple")
        if not self.affected_services:
            self.affected_services = (self.service_name,)
        for service in self.affected_services:
            self._validate_text("affected_service", service, 256)
        if self.service_name not in self.affected_services:
            self.affected_services = (self.service_name, *self.affected_services)
        if len(set(self.affected_services)) != len(self.affected_services):
            raise AppValidationError("affected_services must be unique")

    @staticmethod
    def _validate_text(field_name: str, value: str, max_length: int) -> None:
        """校验必填文本的类型、长度和首尾空白。"""
        if not isinstance(value, str):
            raise AppValidationError(f"{field_name} must be a string")
        if not 1 <= len(value) <= max_length:
            raise AppValidationError(
                f"{field_name} length must be between 1 and {max_length}"
            )
        if value != value.strip():
            raise AppValidationError(
                f"{field_name} must not contain surrounding whitespace"
            )
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise AppValidationError(
                f"{field_name} must not contain control characters"
            )

    @staticmethod
    def _validate_timestamp(field_name: str, value: datetime) -> None:
        """要求时间携带时区，避免跨服务器比较时产生歧义。"""
        if not isinstance(value, datetime):
            raise AppValidationError(f"{field_name} must be a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise AppValidationError(f"{field_name} must include timezone information")

    def mark_analyzing(self, now: datetime) -> None:
        """将开放事故推进到分析中；重复分析请求保持幂等。"""
        self._validate_timestamp("now", now)
        if now < self.updated_at:
            raise AppValidationError("now must not be earlier than updated_at")
        if self.status is IncidentStatus.ANALYZING:
            return
        if self.status is not IncidentStatus.OPEN:
            raise ConflictError("Incident cannot enter analyzing state")
        self.status = IncidentStatus.ANALYZING
        self.updated_at = now

    def attach_alert(
        self,
        alert: Alert,
        *,
        topology_related: bool = False,
        affected_services: tuple[str, ...] = (),
    ) -> None:
        """关联同一租户和服务的告警，并推进事故聚合摘要。

        该方法只更新聚合自身状态，不负责保存 Alert-Incident 外键。调用方必须在
        同一个 Unit of Work 中持久化事故和告警，才能保证关联原子性。
        """
        if alert.tenant_id != self.tenant_id:
            raise ConflictError("Alert and incident tenant do not match")
        if alert.environment != self.environment:
            raise ConflictError("Alert and incident environment do not match")
        if alert.service_name != self.service_name and not topology_related:
            raise ConflictError("Alert and incident service do not match")
        if self.status not in {IncidentStatus.OPEN, IncidentStatus.ANALYZING}:
            raise ConflictError("Incident is not active")

        if alert.severity.rank > self.severity.rank:
            self.severity = alert.severity
        if alert.starts_at > self.updated_at:
            self.updated_at = alert.starts_at
        if self.primary_alert_id is None:
            self.primary_alert_id = alert.alert_id
        self.correlated_alert_count += 1
        self.correlation_reason = (
            "TOPOLOGY_OR_TIME_WINDOW_MATCH"
            if topology_related
            else "TIME_WINDOW_SERVICE_MATCH"
        )
        self.affected_services = tuple(
            dict.fromkeys(
                (*self.affected_services, alert.service_name, *affected_services)
            )
        )

    def mark_resolved(
        self,
        *,
        resolved_by: str,
        reason: str,
        resolved_at: datetime,
        idempotency_key_hash: str,
        request_hash: str,
        trace_id: str,
    ) -> None:
        """由人工确认把活动事故推进到已解决终态。"""
        if self.status not in {
            IncidentStatus.OPEN,
            IncidentStatus.ANALYZING,
        }:
            raise ConflictError("Only active incidents can be resolved")
        self._validate_text("resolved_by", resolved_by, 128)
        self._validate_text("resolution_reason", reason, 2048)
        self._validate_text("resolution_trace_id", trace_id, 128)
        self._validate_hash("idempotency_key_hash", idempotency_key_hash)
        self._validate_hash("request_hash", request_hash)
        self._validate_timestamp("resolved_at", resolved_at)
        if resolved_at < self.updated_at:
            raise AppValidationError("resolved_at must not be earlier than updated_at")
        self.status = IncidentStatus.RESOLVED
        self.updated_at = resolved_at
        self.resolved_by = resolved_by
        self.resolution_reason = reason
        self.resolved_at = resolved_at
        self.resolution_idempotency_key_hash = idempotency_key_hash
        self.resolution_request_hash = request_hash
        self.resolution_trace_id = trace_id

    def mark_closed(
        self,
        *,
        closed_by: str,
        reason: str,
        closed_at: datetime,
        idempotency_key_hash: str,
        request_hash: str,
        trace_id: str,
    ) -> None:
        """由人工确认把已解决事故推进到最终关闭状态。"""
        if self.status is not IncidentStatus.RESOLVED:
            raise ConflictError("Only resolved incidents can be closed")
        self._validate_text("closed_by", closed_by, 128)
        self._validate_text("closure_reason", reason, 2048)
        self._validate_text("closure_trace_id", trace_id, 128)
        self._validate_hash("idempotency_key_hash", idempotency_key_hash)
        self._validate_hash("request_hash", request_hash)
        self._validate_timestamp("closed_at", closed_at)
        if closed_at < self.updated_at:
            raise AppValidationError("closed_at must not be earlier than updated_at")
        self.status = IncidentStatus.CLOSED
        self.updated_at = closed_at
        self.closed_by = closed_by
        self.closure_reason = reason
        self.closed_at = closed_at
        self.closure_idempotency_key_hash = idempotency_key_hash
        self.closure_request_hash = request_hash
        self.closure_trace_id = trace_id

    def _validate_resolution_state(self) -> None:
        """允许旧终态无元数据，但拒绝半套或活动态解决事实。"""
        fields = (
            self.resolved_by,
            self.resolution_reason,
            self.resolved_at,
            self.resolution_idempotency_key_hash,
            self.resolution_request_hash,
            self.resolution_trace_id,
        )
        if not any(value is not None for value in fields):
            return
        if any(value is None for value in fields):
            raise AppValidationError("resolution metadata must be complete")
        if self.status not in {
            IncidentStatus.RESOLVED,
            IncidentStatus.CLOSED,
        }:
            raise AppValidationError("resolution metadata requires a terminal incident")
        assert self.resolved_by is not None
        assert self.resolution_reason is not None
        assert self.resolved_at is not None
        assert self.resolution_idempotency_key_hash is not None
        assert self.resolution_request_hash is not None
        assert self.resolution_trace_id is not None
        self._validate_text("resolved_by", self.resolved_by, 128)
        self._validate_text(
            "resolution_reason",
            self.resolution_reason,
            2048,
        )
        self._validate_timestamp("resolved_at", self.resolved_at)
        if self.resolved_at < self.created_at or self.resolved_at > self.updated_at:
            raise AppValidationError("resolved_at is invalid")
        self._validate_hash(
            "resolution_idempotency_key_hash",
            self.resolution_idempotency_key_hash,
        )
        self._validate_hash(
            "resolution_request_hash",
            self.resolution_request_hash,
        )
        self._validate_text(
            "resolution_trace_id",
            self.resolution_trace_id,
            128,
        )

    def _validate_closure_state(self) -> None:
        """允许历史关闭态无元数据，但拒绝半套或非关闭态关闭事实。"""
        fields = (
            self.closed_by,
            self.closure_reason,
            self.closed_at,
            self.closure_idempotency_key_hash,
            self.closure_request_hash,
            self.closure_trace_id,
        )
        if not any(value is not None for value in fields):
            return
        if any(value is None for value in fields):
            raise AppValidationError("closure metadata must be complete")
        if self.status is not IncidentStatus.CLOSED:
            raise AppValidationError("closure metadata requires a closed incident")
        assert self.closed_by is not None
        assert self.closure_reason is not None
        assert self.closed_at is not None
        assert self.closure_idempotency_key_hash is not None
        assert self.closure_request_hash is not None
        assert self.closure_trace_id is not None
        self._validate_text("closed_by", self.closed_by, 128)
        self._validate_text("closure_reason", self.closure_reason, 2048)
        self._validate_timestamp("closed_at", self.closed_at)
        if self.closed_at < self.created_at or self.closed_at > self.updated_at:
            raise AppValidationError("closed_at is invalid")
        self._validate_hash(
            "closure_idempotency_key_hash",
            self.closure_idempotency_key_hash,
        )
        self._validate_hash(
            "closure_request_hash",
            self.closure_request_hash,
        )
        self._validate_text("closure_trace_id", self.closure_trace_id, 128)

    @staticmethod
    def _validate_hash(field_name: str, value: str) -> None:
        """校验不可逆幂等摘要。"""
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise AppValidationError(f"{field_name} must be a SHA-256 hex digest")
