import json

from devops_agent_platform.domain.enums import AlertSeverity
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.alert import Alert
from devops_agent_platform.infrastructure.database.models.alert import AlertRecord

# 保存数据库前：
# 把业务告警 Alert 翻译成数据库记录 AlertRecord。

# 从数据库读取后：
# 把数据库记录 AlertRecord 翻译回业务告警 Alert。


class AlertMapper:
    """在纯领域对象与 SQLAlchemy 记录之间执行显式转换。"""

    @staticmethod
    def to_record(alert: Alert) -> AlertRecord:
        """把领域告警转换为待持久化记录。

        数据库使用带时区时间戳。这里拒绝无时区时间，避免应用服务器默认时区不同
        时写入相同字面值却代表不同时刻。
        """
        if alert.starts_at.tzinfo is None or alert.starts_at.utcoffset() is None:
            raise AppValidationError("starts_at must include timezone information")

        return AlertRecord(
            alert_id=alert.alert_id,
            tenant_id=alert.tenant_id,
            source=alert.source,
            service_name=alert.service_name,
            severity=alert.severity.value,
            summary=alert.summary,
            starts_at=alert.starts_at,
            fingerprint=alert.fingerprint,
            external_event_id=alert.external_event_id,
            incident_id=alert.incident_id,
            environment=alert.environment,
            alert_type=alert.alert_type,
            labels_json=json.dumps(
                alert.labels, ensure_ascii=False, separators=(",", ":")
            ),
        )

    @staticmethod
    def to_domain(record: AlertRecord) -> Alert:
        """把数据库记录恢复为不依赖 ORM 的领域对象。"""
        return Alert(
            alert_id=record.alert_id,
            tenant_id=record.tenant_id,
            source=record.source,
            service_name=record.service_name,
            severity=AlertSeverity(record.severity),
            summary=record.summary,
            starts_at=record.starts_at,
            fingerprint=record.fingerprint,
            external_event_id=record.external_event_id,
            incident_id=record.incident_id,
            environment=getattr(record, "environment", "default") or "default",
            alert_type=getattr(record, "alert_type", "generic") or "generic",
            labels=tuple(
                tuple(item)
                for item in json.loads(getattr(record, "labels_json", "[]") or "[]")
            ),
        )
