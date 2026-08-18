# Alert Correlation

告警关联综合 tenant、环境、时间窗口、服务、告警类型、严重度和拓扑关系。相同服务优先，拓扑直接依赖次之；不相关服务不会因为 fingerprint 相同而合并。Incident 记录 `primary_alert_id`、`correlated_alert_count` 和 `correlation_reason`，主告警评分是确定性的。
