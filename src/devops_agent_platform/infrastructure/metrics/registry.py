import math
from datetime import datetime

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from devops_agent_platform.application.services import (
    ticket_submission_consumer_runner as ticket_consumer_runner,
)
from devops_agent_platform.application.services.audit_retention_worker import (
    AuditRetentionWorkerHealth,
)
from devops_agent_platform.application.services.outbox_backlog import (
    OutboxBacklogReport,
)
from devops_agent_platform.application.services.outbox_worker import (
    OutboxWorkerHealth,
)
from devops_agent_platform.application.services.rca_consumer_runner import (
    RCAConsumerHealth,
)
from devops_agent_platform.domain.enums import (
    AuditRetentionWorkerState,
    OutboxStatus,
    OutboxWorkerState,
    RCAConsumerWorkerState,
    TicketSubmissionConsumerWorkerState,
)
from devops_agent_platform.ports.rca_report import (
    LLMReportGenerationOutcome,
)
from devops_agent_platform.ports.ticketing import (
    TicketingGatewaySubmitOutcome,
)

_HTTP_METHODS = frozenset(
    {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}
)
_HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
_LLM_REPORT_BUCKETS = (0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120)
_TICKETING_GATEWAY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)
_COMPONENTS = (
    "runtime",
    "database",
    "outbox_worker",
    "rca_consumer",
    "audit_retention",
    "ticket_submission_consumer",
)
_WORKER_RESULTS = ("claimed", "published", "retried", "failed")
_RCA_CONSUMER_RESULTS = (
    "polled",
    "acknowledged",
    "retried",
    "dead_lettered",
)
_TICKET_SUBMISSION_CONSUMER_RESULTS = (
    "polled",
    "acknowledged",
    "retried",
    "dead_lettered",
    "ignored",
)
TicketSubmissionConsumerHealth = (
    ticket_consumer_runner.TicketSubmissionConsumerHealth
)
_BACKLOG_STATUSES = (
    OutboxStatus.PENDING,
    OutboxStatus.PROCESSING,
    OutboxStatus.FAILED,
)


class ApplicationMetrics:
    """持有单个应用实例的低基数Prometheus指标。"""

    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.http_requests = Counter(
            "http_requests",
            "HTTP请求总数。",
            labelnames=("method", "route", "status_class"),
            namespace="devops_agent",
            registry=self.registry,
        )
        self.http_duration = Histogram(
            "http_request_duration_seconds",
            "HTTP请求处理耗时。",
            labelnames=("method", "route"),
            namespace="devops_agent",
            buckets=_HTTP_BUCKETS,
            registry=self.registry,
        )
        self.http_in_progress = Gauge(
            "http_requests_in_progress",
            "当前正在处理的HTTP请求数。",
            labelnames=("method",),
            namespace="devops_agent",
            registry=self.registry,
        )
        self.runtime_component_ready = Gauge(
            "runtime_component_ready",
            "运行时组件是否可处理流量，1表示可用。",
            labelnames=("component",),
            namespace="devops_agent",
            registry=self.registry,
        )
        self.outbox_worker_enabled = Gauge(
            "outbox_worker_enabled",
            "当前进程是否启用Outbox Worker。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.outbox_worker_state = Gauge(
            "outbox_worker_state",
            "Outbox Worker状态，当前状态标签值为1。",
            labelnames=("state",),
            namespace="devops_agent",
            registry=self.registry,
        )
        self.outbox_worker_consecutive_failures = Gauge(
            "outbox_worker_consecutive_failures",
            "Outbox Worker连续失败次数。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.outbox_worker_messages = Gauge(
            "outbox_worker_messages",
            "当前进程内Outbox Worker累计处理消息数。",
            labelnames=("result",),
            namespace="devops_agent",
            registry=self.registry,
        )
        self.rca_consumer_enabled = Gauge(
            "rca_consumer_enabled",
            "当前进程是否启用RCA Consumer。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.rca_consumer_state = Gauge(
            "rca_consumer_state",
            "RCA Consumer状态，当前状态标签值为1。",
            labelnames=("state",),
            namespace="devops_agent",
            registry=self.registry,
        )
        self.rca_consumer_consecutive_failures = Gauge(
            "rca_consumer_consecutive_failures",
            "RCA Consumer连续失败次数。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.rca_consumer_records = Gauge(
            "rca_consumer_records",
            "当前进程内RCA Consumer累计处理记录数。",
            labelnames=("result",),
            namespace="devops_agent",
            registry=self.registry,
        )
        self.rca_consumer_last_cycle_timestamp = Gauge(
            "rca_consumer_last_cycle_timestamp_seconds",
            "RCA Consumer最近完成一轮轮询的Unix时间戳，0表示从未轮询。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.rca_consumer_last_success_timestamp = Gauge(
            "rca_consumer_last_success_timestamp_seconds",
            "RCA Consumer最近成功轮询的Unix时间戳，0表示从未成功。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.rca_consumer_lag_source_up = Gauge(
            "rca_consumer_lag_source_up",
            "RCA Consumer Lag汇总最近一次是否完整可用。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.rca_consumer_assigned_partitions = Gauge(
            "rca_consumer_assigned_partitions",
            "当前RCA Consumer实例已分配分区数。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.rca_consumer_measured_partitions = Gauge(
            "rca_consumer_measured_partitions",
            "最近一次成功读取Lag的分区数。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.rca_consumer_lag_records = Gauge(
            "rca_consumer_lag_records",
            "当前RCA Consumer实例的消息Lag汇总。",
            labelnames=("scope",),
            namespace="devops_agent",
            registry=self.registry,
        )
        self.ticket_submission_consumer_enabled = Gauge(
            "ticket_submission_consumer_enabled",
            "当前进程是否启用外部工单提交Consumer。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.ticket_submission_consumer_state = Gauge(
            "ticket_submission_consumer_state",
            "外部工单提交Consumer状态，当前状态标签值为1。",
            labelnames=("state",),
            namespace="devops_agent",
            registry=self.registry,
        )
        self.ticket_submission_consumer_consecutive_failures = Gauge(
            "ticket_submission_consumer_consecutive_failures",
            "外部工单提交Consumer连续失败次数。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.ticket_submission_consumer_records = Gauge(
            "ticket_submission_consumer_records",
            "当前进程内外部工单提交Consumer累计处理记录数。",
            labelnames=("result",),
            namespace="devops_agent",
            registry=self.registry,
        )
        self.ticket_submission_consumer_last_cycle_timestamp = Gauge(
            "ticket_submission_consumer_last_cycle_timestamp_seconds",
            "外部工单提交Consumer最近完成一轮轮询的Unix时间戳，0表示从未轮询。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.ticket_submission_consumer_last_success_timestamp = Gauge(
            "ticket_submission_consumer_last_success_timestamp_seconds",
            "外部工单提交Consumer最近成功轮询的Unix时间戳，0表示从未成功。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.ticket_submission_consumer_lag_source_up = Gauge(
            "ticket_submission_consumer_lag_source_up",
            "外部工单提交Consumer Lag汇总最近一次是否完整可用。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.ticket_submission_consumer_assigned_partitions = Gauge(
            "ticket_submission_consumer_assigned_partitions",
            "当前外部工单提交Consumer实例已分配分区数。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.ticket_submission_consumer_measured_partitions = Gauge(
            "ticket_submission_consumer_measured_partitions",
            "外部工单提交Consumer最近一次成功读取Lag的分区数。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.ticket_submission_consumer_lag_records = Gauge(
            "ticket_submission_consumer_lag_records",
            "当前外部工单提交Consumer实例的消息Lag汇总。",
            labelnames=("scope",),
            namespace="devops_agent",
            registry=self.registry,
        )
        self.audit_retention_worker_enabled = Gauge(
            "audit_retention_worker_enabled",
            "当前进程是否启用审计留存清理Worker。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.audit_retention_worker_state = Gauge(
            "audit_retention_worker_state",
            "审计留存清理Worker状态，当前状态标签值为1。",
            labelnames=("state",),
            namespace="devops_agent",
            registry=self.registry,
        )
        self.audit_retention_worker_consecutive_failures = Gauge(
            "audit_retention_worker_consecutive_failures",
            "审计留存清理Worker连续失败次数。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.audit_retention_worker_cycles = Gauge(
            "audit_retention_worker_cycles",
            "当前进程内审计留存清理Worker累计执行批次数。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.audit_retention_purged_workflow_runs = Gauge(
            "audit_retention_purged_workflow_runs",
            "当前进程内审计留存清理Worker累计清理的工作流运行数。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.audit_retention_worker_last_cycle_timestamp = Gauge(
            "audit_retention_worker_last_cycle_timestamp_seconds",
            "审计留存清理Worker最近完成一轮清理的Unix时间戳，0表示从未执行。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.audit_retention_worker_last_success_timestamp = Gauge(
            "audit_retention_worker_last_success_timestamp_seconds",
            "审计留存清理Worker最近成功清理的Unix时间戳，0表示从未成功。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.llm_report_generation = Counter(
            "llm_report_generation",
            "LLM报告生成及固定降级分类累计次数。",
            labelnames=("outcome",),
            namespace="devops_agent",
            registry=self.registry,
        )
        self.llm_report_generation_duration = Histogram(
            "llm_report_generation_duration_seconds",
            "LLM报告生成或触发降级前的耗时。",
            labelnames=("outcome",),
            namespace="devops_agent",
            buckets=_LLM_REPORT_BUCKETS,
            registry=self.registry,
        )
        self.ticketing_gateway_submit = Counter(
            "ticketing_gateway_submit",
            "外部工单网关提交调用固定结果累计次数。",
            labelnames=("outcome",),
            namespace="devops_agent",
            registry=self.registry,
        )
        self.ticketing_gateway_submit_duration = Histogram(
            "ticketing_gateway_submit_duration_seconds",
            "外部工单网关提交调用耗时。",
            labelnames=("outcome",),
            namespace="devops_agent",
            buckets=_TICKETING_GATEWAY_BUCKETS,
            registry=self.registry,
        )
        self.outbox_backlog_events = Gauge(
            "outbox_backlog_events",
            "数据库中Outbox积压事件数。",
            labelnames=("status",),
            namespace="devops_agent",
            registry=self.registry,
        )
        self.outbox_oldest_unpublished_age = Gauge(
            "outbox_oldest_unpublished_age_seconds",
            "最早未发布Outbox事件的年龄。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.outbox_backlog_source_up = Gauge(
            "outbox_backlog_source_up",
            "Outbox积压查询最近一次是否成功。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self.outbox_backlog_stale = Gauge(
            "outbox_backlog_stale",
            "Outbox积压数据是否来自旧快照。",
            namespace="devops_agent",
            registry=self.registry,
        )
        self._initialize_fixed_labels()

    def start_http_request(self, method: str) -> str:
        """增加请求处理中Gauge，并返回规范化方法标签。"""
        normalized_method = self.normalize_method(method)
        self.http_in_progress.labels(method=normalized_method).inc()
        return normalized_method

    def finish_http_request(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        """完成一次HTTP指标记录，并确保处理中Gauge归还。"""
        try:
            status_class = self.status_class(status_code)
            self.http_requests.labels(
                method=method,
                route=route,
                status_class=status_class,
            ).inc()
            self.http_duration.labels(method=method, route=route).observe(
                max(0.0, duration_seconds)
            )
        finally:
            self.http_in_progress.labels(method=method).dec()

    def abandon_http_request(self, method: str) -> None:
        """在请求被取消且没有形成HTTP响应时归还处理中Gauge。"""
        self.http_in_progress.labels(method=method).dec()

    def update_runtime(
        self,
        component_statuses: tuple[tuple[str, str], ...] | None,
        worker_health: OutboxWorkerHealth | None,
    ) -> None:
        """用Runtime内存快照刷新Gauge，不持有领域对象引用。"""
        component_values = dict(component_statuses or ())
        for component in _COMPONENTS:
            status = component_values.get(component, "unknown")
            ready = status in {"up", "disabled"}
            self.runtime_component_ready.labels(component=component).set(
                1 if ready else 0
            )

        self.outbox_worker_enabled.set(1 if worker_health is not None else 0)
        for state in OutboxWorkerState:
            active = worker_health is not None and worker_health.state is state
            self.outbox_worker_state.labels(state=state.value).set(
                1 if active else 0
            )
        failures = worker_health.consecutive_failures if worker_health else 0
        self.outbox_worker_consecutive_failures.set(failures)
        totals = {
            "claimed": worker_health.total_claimed if worker_health else 0,
            "published": worker_health.total_published if worker_health else 0,
            "retried": worker_health.total_retried if worker_health else 0,
            "failed": worker_health.total_failed if worker_health else 0,
        }
        for result, value in totals.items():
            self.outbox_worker_messages.labels(result=result).set(value)

    def update_rca_consumer(
        self,
        health: RCAConsumerHealth | None,
    ) -> None:
        """从 Runner 健康快照刷新低基数 RCA Consumer 指标。"""
        self.rca_consumer_enabled.set(1 if health is not None else 0)
        for state in RCAConsumerWorkerState:
            active = health is not None and health.state is state
            self.rca_consumer_state.labels(state=state.value).set(
                1 if active else 0
            )
        self.rca_consumer_consecutive_failures.set(
            health.consecutive_failures if health is not None else 0
        )
        totals = {
            "polled": health.total_polled if health is not None else 0,
            "acknowledged": (
                health.total_acknowledged if health is not None else 0
            ),
            "retried": health.total_retried if health is not None else 0,
            "dead_lettered": (
                health.total_dead_lettered if health is not None else 0
            ),
        }
        for result, value in totals.items():
            self.rca_consumer_records.labels(result=result).set(value)
        self.rca_consumer_last_cycle_timestamp.set(
            self._timestamp_or_zero(
                health.last_cycle_at if health is not None else None
            )
        )
        self.rca_consumer_last_success_timestamp.set(
            self._timestamp_or_zero(
                health.last_success_at if health is not None else None
            )
        )
        self.rca_consumer_lag_source_up.set(
            1 if health is not None and health.lag_source_up else 0
        )
        self.rca_consumer_assigned_partitions.set(
            health.assigned_partitions if health is not None else 0
        )
        self.rca_consumer_measured_partitions.set(
            health.measured_partitions if health is not None else 0
        )
        self.rca_consumer_lag_records.labels(scope="total").set(
            health.total_lag if health is not None else 0
        )
        self.rca_consumer_lag_records.labels(
            scope="max_partition"
        ).set(
            health.max_partition_lag if health is not None else 0
        )

    def update_ticket_submission_consumer(
        self,
        health: TicketSubmissionConsumerHealth | None,
    ) -> None:
        """从 Runner 健康快照刷新低基数工单提交 Consumer 指标。"""
        self.ticket_submission_consumer_enabled.set(
            1 if health is not None else 0
        )
        for state in TicketSubmissionConsumerWorkerState:
            active = health is not None and health.state is state
            self.ticket_submission_consumer_state.labels(
                state=state.value
            ).set(1 if active else 0)
        self.ticket_submission_consumer_consecutive_failures.set(
            health.consecutive_failures if health is not None else 0
        )
        totals = {
            "polled": health.total_polled if health is not None else 0,
            "acknowledged": (
                health.total_acknowledged if health is not None else 0
            ),
            "retried": health.total_retried if health is not None else 0,
            "dead_lettered": (
                health.total_dead_lettered if health is not None else 0
            ),
            "ignored": health.total_ignored if health is not None else 0,
        }
        for result, value in totals.items():
            self.ticket_submission_consumer_records.labels(
                result=result
            ).set(value)
        self.ticket_submission_consumer_last_cycle_timestamp.set(
            self._timestamp_or_zero(
                health.last_cycle_at if health is not None else None
            )
        )
        self.ticket_submission_consumer_last_success_timestamp.set(
            self._timestamp_or_zero(
                health.last_success_at if health is not None else None
            )
        )
        self.ticket_submission_consumer_lag_source_up.set(
            1 if health is not None and health.lag_source_up else 0
        )
        self.ticket_submission_consumer_assigned_partitions.set(
            health.assigned_partitions if health is not None else 0
        )
        self.ticket_submission_consumer_measured_partitions.set(
            health.measured_partitions if health is not None else 0
        )
        self.ticket_submission_consumer_lag_records.labels(
            scope="total"
        ).set(health.total_lag if health is not None else 0)
        self.ticket_submission_consumer_lag_records.labels(
            scope="max_partition"
        ).set(health.max_partition_lag if health is not None else 0)

    def update_audit_retention(
        self,
        health: AuditRetentionWorkerHealth | None,
    ) -> None:
        """从 Runner 健康快照刷新低基数审计留存清理指标。"""
        self.audit_retention_worker_enabled.set(1 if health is not None else 0)
        for state in AuditRetentionWorkerState:
            active = health is not None and health.state is state
            self.audit_retention_worker_state.labels(state=state.value).set(
                1 if active else 0
            )
        self.audit_retention_worker_consecutive_failures.set(
            health.consecutive_failures if health is not None else 0
        )
        self.audit_retention_worker_cycles.set(
            health.total_cycles if health is not None else 0
        )
        self.audit_retention_purged_workflow_runs.set(
            health.total_purged_workflow_runs if health is not None else 0
        )
        self.audit_retention_worker_last_cycle_timestamp.set(
            self._timestamp_or_zero(
                health.last_cycle_at if health is not None else None
            )
        )
        self.audit_retention_worker_last_success_timestamp.set(
            self._timestamp_or_zero(
                health.last_success_at if health is not None else None
            )
        )

    def observe_llm_report(
        self,
        outcome: LLMReportGenerationOutcome,
        duration_seconds: float,
    ) -> None:
        """记录固定分类的 LLM 结果和有限非负耗时。"""
        if not isinstance(outcome, LLMReportGenerationOutcome):
            raise ValueError(
                "outcome must be an LLMReportGenerationOutcome"
            )
        if (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, int | float)
            or not math.isfinite(float(duration_seconds))
            or duration_seconds < 0
        ):
            raise ValueError(
                "duration_seconds must be a finite non-negative number"
            )
        labels = {"outcome": outcome.value}
        self.llm_report_generation.labels(**labels).inc()
        self.llm_report_generation_duration.labels(**labels).observe(
            float(duration_seconds)
        )

    def observe_ticketing_gateway_submit(
        self,
        outcome: TicketingGatewaySubmitOutcome,
        duration_seconds: float,
    ) -> None:
        """记录固定分类的外部工单提交结果和有限非负耗时。"""
        if not isinstance(outcome, TicketingGatewaySubmitOutcome):
            raise ValueError(
                "outcome must be a TicketingGatewaySubmitOutcome"
            )
        if (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, int | float)
            or not math.isfinite(float(duration_seconds))
            or duration_seconds < 0
        ):
            raise ValueError(
                "duration_seconds must be a finite non-negative number"
            )
        labels = {"outcome": outcome.value}
        self.ticketing_gateway_submit.labels(**labels).inc()
        self.ticketing_gateway_submit_duration.labels(**labels).observe(
            float(duration_seconds)
        )

    def update_outbox_backlog(
        self,
        report: OutboxBacklogReport | None,
        now: datetime,
    ) -> None:
        """刷新数据库积压Gauge，并显式标记查询失败和陈旧快照。"""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must include timezone information")
        snapshot = report.snapshot if report is not None else None
        for status in _BACKLOG_STATUSES:
            count = snapshot.count_for(status) if snapshot is not None else 0
            self.outbox_backlog_events.labels(status=status.value).set(count)

        oldest = snapshot.oldest_unpublished_at if snapshot is not None else None
        age_seconds = (
            max(0.0, (now - oldest).total_seconds())
            if oldest is not None
            else 0.0
        )
        self.outbox_oldest_unpublished_age.set(age_seconds)
        self.outbox_backlog_source_up.set(
            1 if report is not None and report.source_up else 0
        )
        self.outbox_backlog_stale.set(
            1 if report is not None and report.stale else 0
        )

    @staticmethod
    def normalize_method(method: str) -> str:
        """把任意HTTP方法归并到有限标签集合。"""
        normalized = method.upper()
        return normalized if normalized in _HTTP_METHODS else "OTHER"

    @staticmethod
    def status_class(status_code: int) -> str:
        """把状态码归并为有限的1xx到5xx类别。"""
        if 100 <= status_code <= 599:
            return f"{status_code // 100}xx"
        return "other"

    def _initialize_fixed_labels(self) -> None:
        """预创建固定标签，保证服务空闲时监控面板仍能查询到零值。"""
        for method in sorted(_HTTP_METHODS | {"OTHER"}):
            self.http_in_progress.labels(method=method).set(0)
        for component in _COMPONENTS:
            self.runtime_component_ready.labels(component=component).set(0)
        for state in OutboxWorkerState:
            self.outbox_worker_state.labels(state=state.value).set(0)
        for result in _WORKER_RESULTS:
            self.outbox_worker_messages.labels(result=result).set(0)
        self.rca_consumer_enabled.set(0)
        for state in RCAConsumerWorkerState:
            self.rca_consumer_state.labels(state=state.value).set(0)
        self.rca_consumer_consecutive_failures.set(0)
        for result in _RCA_CONSUMER_RESULTS:
            self.rca_consumer_records.labels(result=result).set(0)
        self.rca_consumer_last_cycle_timestamp.set(0)
        self.rca_consumer_last_success_timestamp.set(0)
        self.rca_consumer_lag_source_up.set(0)
        self.rca_consumer_assigned_partitions.set(0)
        self.rca_consumer_measured_partitions.set(0)
        self.rca_consumer_lag_records.labels(scope="total").set(0)
        self.rca_consumer_lag_records.labels(
            scope="max_partition"
        ).set(0)
        self.ticket_submission_consumer_enabled.set(0)
        for state in TicketSubmissionConsumerWorkerState:
            self.ticket_submission_consumer_state.labels(
                state=state.value
            ).set(0)
        self.ticket_submission_consumer_consecutive_failures.set(0)
        for result in _TICKET_SUBMISSION_CONSUMER_RESULTS:
            self.ticket_submission_consumer_records.labels(
                result=result
            ).set(0)
        self.ticket_submission_consumer_last_cycle_timestamp.set(0)
        self.ticket_submission_consumer_last_success_timestamp.set(0)
        self.ticket_submission_consumer_lag_source_up.set(0)
        self.ticket_submission_consumer_assigned_partitions.set(0)
        self.ticket_submission_consumer_measured_partitions.set(0)
        self.ticket_submission_consumer_lag_records.labels(
            scope="total"
        ).set(0)
        self.ticket_submission_consumer_lag_records.labels(
            scope="max_partition"
        ).set(0)
        self.audit_retention_worker_enabled.set(0)
        for state in AuditRetentionWorkerState:
            self.audit_retention_worker_state.labels(state=state.value).set(0)
        self.audit_retention_worker_consecutive_failures.set(0)
        self.audit_retention_worker_cycles.set(0)
        self.audit_retention_purged_workflow_runs.set(0)
        self.audit_retention_worker_last_cycle_timestamp.set(0)
        self.audit_retention_worker_last_success_timestamp.set(0)
        for outcome in LLMReportGenerationOutcome:
            labels = {"outcome": outcome.value}
            self.llm_report_generation.labels(**labels)
            self.llm_report_generation_duration.labels(**labels)
        for outcome in TicketingGatewaySubmitOutcome:
            labels = {"outcome": outcome.value}
            self.ticketing_gateway_submit.labels(**labels)
            self.ticketing_gateway_submit_duration.labels(**labels)
        for status in _BACKLOG_STATUSES:
            self.outbox_backlog_events.labels(status=status.value).set(0)
        self.outbox_oldest_unpublished_age.set(0)
        self.outbox_backlog_source_up.set(0)
        self.outbox_backlog_stale.set(0)

    @staticmethod
    def _timestamp_or_zero(value: datetime | None) -> float:
        """把带时区时间转换为时间戳，缺失或非法快照返回0。"""
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            return 0.0
        return value.timestamp()
