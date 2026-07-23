from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from devops_agent_platform.domain.enums import (
    AlertSeverity,
    IncidentCreationAction,
    IncidentDecisionReason,
    IncidentStatus,
)
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.alert import Alert
from devops_agent_platform.domain.models.incident import Incident


@dataclass(frozen=True)
class IncidentCreationPolicyConfig:
    """事故创建策略的不可变配置。

    配置对象可以按租户或环境构造，但进入策略前必须完成校验，避免运行过程中
    出现负窗口、空状态集合等无法解释的规则。
    """

    minimum_severity: AlertSeverity = AlertSeverity.WARNING
    correlation_window: timedelta = timedelta(minutes=15)
    clock_skew_tolerance: timedelta = timedelta(minutes=2)
    active_statuses: frozenset[IncidentStatus] = field(
        default_factory=lambda: frozenset(
            {IncidentStatus.OPEN, IncidentStatus.ANALYZING}
        )
    )

    def __post_init__(self) -> None:
        """校验策略阈值和时间边界。"""
        if not isinstance(self.minimum_severity, AlertSeverity):
            raise AppValidationError(
                "minimum_severity must be an AlertSeverity"
            )
        if (
            not isinstance(self.correlation_window, timedelta)
            or self.correlation_window <= timedelta(0)
        ):
            raise AppValidationError("correlation_window must be positive")
        if (
            not isinstance(self.clock_skew_tolerance, timedelta)
            or self.clock_skew_tolerance < timedelta(0)
        ):
            raise AppValidationError(
                "clock_skew_tolerance must not be negative"
            )
        if not isinstance(self.active_statuses, frozenset):
            raise AppValidationError("active_statuses must be a frozenset")
        if not self.active_statuses:
            raise AppValidationError("active_statuses must not be empty")
        if not all(
            isinstance(status, IncidentStatus)
            for status in self.active_statuses
        ):
            raise AppValidationError(
                "active_statuses must contain only IncidentStatus values"
            )


@dataclass(frozen=True)
class IncidentCreationDecision:
    """事故创建策略的三态决策结果。"""

    action: IncidentCreationAction
    reason: IncidentDecisionReason
    matched_incident_id: str | None = None

    def __post_init__(self) -> None:
        """保证动作、原因和事故 ID 始终保持一致。"""
        expected_reasons = {
            IncidentCreationAction.CREATE: (
                IncidentDecisionReason.NO_MATCHING_ACTIVE_INCIDENT
            ),
            IncidentCreationAction.ATTACH: (
                IncidentDecisionReason.ACTIVE_INCIDENT_MATCHED
            ),
            IncidentCreationAction.IGNORE: (
                IncidentDecisionReason.BELOW_SEVERITY_THRESHOLD
            ),
        }
        if not isinstance(self.action, IncidentCreationAction):
            raise AppValidationError(
                "action must be an IncidentCreationAction"
            )
        if not isinstance(self.reason, IncidentDecisionReason):
            raise AppValidationError(
                "reason must be an IncidentDecisionReason"
            )
        if self.reason is not expected_reasons[self.action]:
            raise AppValidationError(
                "decision action and reason are inconsistent"
            )

        if self.action is IncidentCreationAction.ATTACH:
            if not self.matched_incident_id:
                raise AppValidationError(
                    "ATTACH decision requires matched_incident_id"
                )
        elif self.matched_incident_id is not None:
            raise AppValidationError(
                "Only ATTACH decision may contain matched_incident_id"
            )


class IncidentCreationPolicy:
    """根据告警事实和开放事故候选生成纯领域决策。"""

    def __init__(
        self,
        config: IncidentCreationPolicyConfig | None = None,
    ) -> None:
        self._config = config or IncidentCreationPolicyConfig()

    def decide(
        self,
        alert: Alert,
        candidate_incidents: Iterable[Incident],
    ) -> IncidentCreationDecision:
        """决定忽略告警、创建新事故或关联已有事故。"""
        if not self._meets_severity_threshold(alert.severity):
            return IncidentCreationDecision(
                action=IncidentCreationAction.IGNORE,
                reason=IncidentDecisionReason.BELOW_SEVERITY_THRESHOLD,
            )

        matched = self._find_best_match(alert, candidate_incidents)
        if matched is not None:
            return IncidentCreationDecision(
                action=IncidentCreationAction.ATTACH,
                reason=IncidentDecisionReason.ACTIVE_INCIDENT_MATCHED,
                matched_incident_id=matched.incident_id,
            )

        return IncidentCreationDecision(
            action=IncidentCreationAction.CREATE,
            reason=IncidentDecisionReason.NO_MATCHING_ACTIVE_INCIDENT,
        )

    def _meets_severity_threshold(self, severity: AlertSeverity) -> bool:
        """按显式等级映射比较阈值，避免依赖枚举字符串排序。"""
        return severity.rank >= self._config.minimum_severity.rank

    def requires_candidate_lookup(self, alert: Alert) -> bool:
        """返回该告警是否需要查询事故候选。"""
        return self._meets_severity_threshold(alert.severity)

    def candidate_time_bounds(self, alert: Alert) -> tuple[datetime, datetime]:
        """计算与策略匹配公式一致的创建上界和更新下界。"""
        created_before = alert.starts_at + self._config.clock_skew_tolerance
        updated_after = alert.starts_at - self._config.correlation_window
        return created_before, updated_after

    @property
    def active_statuses(self) -> frozenset[IncidentStatus]:
        """返回不可变的可关联事故状态集合。"""
        return self._config.active_statuses

    def _find_best_match(
        self,
        alert: Alert,
        candidate_incidents: Iterable[Incident],
    ) -> Incident | None:
        """单次遍历选择最近更新的匹配事故，额外空间保持 O(1)。"""
        best_match: Incident | None = None
        for incident in candidate_incidents:
            if not self._is_match(alert, incident):
                continue
            if best_match is None or self._is_more_recent(incident, best_match):
                best_match = incident
        return best_match

    def _is_match(self, alert: Alert, incident: Incident) -> bool:
        """校验租户、服务、活动状态和时间关联窗口。"""
        if incident.tenant_id != alert.tenant_id:
            return False
        if incident.service_name != alert.service_name:
            return False
        if incident.status not in self._config.active_statuses:
            return False

        earliest_time = (
            incident.created_at - self._config.clock_skew_tolerance
        )
        latest_time = incident.updated_at + self._config.correlation_window
        return earliest_time <= alert.starts_at <= latest_time

    @staticmethod
    def _is_more_recent(candidate: Incident, current: Incident) -> bool:
        """使用更新时间和事故 ID 建立确定性的候选优先级。"""
        return (candidate.updated_at, candidate.incident_id) > (
            current.updated_at,
            current.incident_id,
        )
