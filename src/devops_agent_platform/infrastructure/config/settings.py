import re
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.identity import validate_worker_id
from devops_agent_platform.infrastructure.llm.factory import (
    LLM_PROVIDER_OPENAI_COMPATIBLE,
    LLMProviderRuntimeConfig,
    build_llm_provider_configs,
    normalize_llm_api_style,
    normalize_llm_provider,
    parse_llm_provider_order,
)

_KAFKA_TOPIC_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,248}$")
_KAFKA_SECURITY_PROTOCOLS = frozenset(
    {"PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"}
)
_KAFKA_SASL_MECHANISMS = frozenset({"PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512"})
_PROMETHEUS_METRIC_IDENTIFIER = re.compile(r"^[A-Za-z_:][A-Za-z0-9_:]*$")
_PROMETHEUS_LABEL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RCA_INVESTIGATION_POLICIES = frozenset(
    {
        "fixed_default",
        "fixed_no_traces",
        "fixed_metrics_logs_runbooks",
    }
)


# 我现在是本地环境还是生产环境？
# 我的服务名字叫什么？
# 我要连接哪个数据库？
# 日志打印 INFO 还是 DEBUG？
class Settings(BaseSettings):
    """集中声明并校验应用运行时配置。

    本地环境可以从 ``.env`` 加载；生产环境应由部署平台注入环境变量或密钥引用，
    不能把真实凭据写入代码仓库和镜像层。
    """

    model_config = SettingsConfigDict(env_file=".env", env_prefix="DEVOPS_AGENT_")

    app_env: str = Field(default="local")
    service_name: str = Field(default="devops-agent-platform")
    database_url: str = Field(
        default="postgresql+psycopg://devops:devops@localhost:5432/devops_agent"
    )
    log_level: str = Field(default="INFO")
    alert_webhook_auth_enabled: bool = Field(default=False)
    alert_webhook_secret: SecretStr | None = Field(default=None)
    alert_webhook_tolerance_seconds: int = Field(
        default=300,
        ge=10,
        le=3600,
    )
    alert_webhook_max_body_bytes: int = Field(
        default=64 * 1024,
        ge=1,
        le=1024 * 1024,
    )
    kafka_bootstrap_servers: str = Field(default="localhost:9092")
    kafka_topic: str = Field(default="devops-agent.events.v1")
    kafka_client_id: str = Field(default="devops-agent-platform")
    kafka_security_protocol: str = Field(default="PLAINTEXT")
    kafka_sasl_mechanism: str | None = Field(default=None)
    kafka_sasl_username: str | None = Field(default=None)
    kafka_sasl_password: SecretStr | None = Field(default=None)
    outbox_worker_enabled: bool = Field(default=False)
    outbox_worker_id: str | None = Field(default=None)
    outbox_batch_size: int = Field(default=20, ge=1, le=100)
    outbox_max_attempts: int = Field(default=5, ge=1, le=100)
    outbox_lease_seconds: int = Field(default=120, ge=1, le=3600)
    outbox_publish_timeout_seconds: int = Field(default=5, ge=1, le=120)
    outbox_shutdown_timeout_seconds: int = Field(default=130, ge=1, le=3600)
    rca_consumer_enabled: bool = Field(default=False)
    rca_consumer_worker_id: str | None = Field(default=None)
    rca_consumer_group_id: str = Field(default="devops-agent-rca-v1")
    rca_consumer_client_id: str = Field(default="devops-agent-rca-consumer")
    rca_dead_letter_topic: str = Field(default="devops-agent.rca.dead-letter.v1")
    rca_consumer_poll_timeout_ms: int = Field(
        default=1000,
        ge=1,
        le=60_000,
    )
    rca_consumer_lag_timeout_ms: int = Field(
        default=500,
        ge=10,
        le=5000,
    )
    rca_consumer_max_lag_partitions: int = Field(
        default=1000,
        ge=1,
        le=10_000,
    )
    rca_consumer_lag_sample_interval_seconds: float = Field(
        default=5,
        ge=0.1,
        le=300,
    )
    rca_consumer_lease_seconds: int = Field(
        default=120,
        ge=10,
        le=3600,
    )
    rca_consumer_heartbeat_seconds: int = Field(
        default=30,
        ge=1,
        le=3599,
    )
    rca_consumer_execution_timeout_seconds: int = Field(
        default=1800,
        ge=2,
        le=86_400,
    )
    rca_continue_on_step_failure: bool = Field(default=False)
    rca_investigation_policy: str = Field(default="fixed_default")
    rca_consumer_shutdown_timeout_seconds: int = Field(
        default=30,
        ge=1,
        le=3600,
    )
    ticket_submission_consumer_enabled: bool = Field(default=False)
    ticket_submission_consumer_worker_id: str | None = Field(default=None)
    ticket_submission_consumer_group_id: str = Field(
        default="devops-agent-ticket-submission-v1"
    )
    ticket_submission_consumer_client_id: str = Field(
        default="devops-agent-ticket-submission-consumer"
    )
    ticket_submission_dead_letter_topic: str = Field(
        default="devops-agent.ticket-submission.dead-letter.v1"
    )
    ticket_submission_consumer_poll_timeout_ms: int = Field(
        default=1000,
        ge=1,
        le=60_000,
    )
    ticket_submission_consumer_shutdown_timeout_seconds: int = Field(
        default=30,
        ge=1,
        le=3600,
    )
    ticketing_http_json_enabled: bool = Field(default=False)
    ticketing_http_json_endpoint_url: str | None = Field(default=None)
    ticketing_http_json_bearer_token: SecretStr | None = Field(default=None)
    ticketing_http_json_request_timeout_seconds: float = Field(
        default=5,
        gt=0,
        le=60,
    )
    ticketing_http_json_max_response_bytes: int = Field(
        default=256 * 1024,
        ge=1,
        le=1024 * 1024,
    )
    ticketing_jira_enabled: bool = Field(default=False)
    ticketing_jira_base_url: str | None = Field(default=None)
    ticketing_jira_user_email: str | None = Field(default=None)
    ticketing_jira_api_token: SecretStr | None = Field(default=None)
    ticketing_jira_project_key: str | None = Field(default=None)
    ticketing_jira_issue_type: str = Field(default="Task")
    ticketing_jira_request_timeout_seconds: float = Field(
        default=5,
        gt=0,
        le=60,
    )
    ticketing_jira_max_response_bytes: int = Field(
        default=256 * 1024,
        ge=1,
        le=1024 * 1024,
    )
    ticketing_servicenow_enabled: bool = Field(default=False)
    ticketing_servicenow_base_url: str | None = Field(default=None)
    ticketing_servicenow_username: str | None = Field(default=None)
    ticketing_servicenow_password: SecretStr | None = Field(default=None)
    ticketing_servicenow_table: str = Field(default="incident")
    ticketing_servicenow_request_timeout_seconds: float = Field(
        default=5,
        gt=0,
        le=60,
    )
    ticketing_servicenow_max_response_bytes: int = Field(
        default=256 * 1024,
        ge=1,
        le=1024 * 1024,
    )
    remediation_controller_base_url: str | None = Field(default=None)
    remediation_controller_bearer_token: SecretStr | None = Field(default=None)
    remediation_request_timeout_seconds: float = Field(
        default=10,
        gt=0,
        le=60,
    )
    # 执行/回滚认领租约时长；必须严格大于 request timeout，崩溃后靠过期收口。
    remediation_lease_seconds: int = Field(
        default=60,
        ge=5,
        le=3600,
    )
    remediation_max_response_bytes: int = Field(
        default=64 * 1024,
        ge=1,
        le=1024 * 1024,
    )
    remediation_execution_enabled: bool = Field(default=False)
    remediation_allowed_tenants: tuple[str, ...] = Field(default=())
    remediation_action_catalog_path: str | None = Field(default=None)
    remediation_reclaim_worker_enabled: bool = Field(default=False)
    remediation_reclaim_worker_id: str | None = Field(default=None)
    remediation_reclaim_batch_size: int = Field(default=50, ge=1, le=1000)
    remediation_reclaim_interval_seconds: float = Field(
        default=30,
        gt=0,
        le=3600,
    )
    remediation_reclaim_error_backoff_initial_seconds: float = Field(
        default=5,
        gt=0,
        le=3600,
    )
    remediation_reclaim_error_backoff_max_seconds: float = Field(
        default=300,
        gt=0,
        le=86400,
    )
    remediation_reclaim_shutdown_timeout_seconds: float = Field(
        default=30,
        gt=0,
        le=3600,
    )
    remediation_maintenance_start_hour_utc: int = Field(
        default=0,
        ge=0,
        le=23,
    )
    remediation_maintenance_end_hour_utc: int = Field(
        default=24,
        ge=1,
        le=24,
    )
    notification_slack_webhook_url: SecretStr | None = Field(default=None)
    notification_teams_webhook_url: SecretStr | None = Field(default=None)
    notification_pagerduty_routing_key: SecretStr | None = Field(default=None)
    notification_request_timeout_seconds: float = Field(
        default=5,
        gt=0,
        le=60,
    )
    notification_max_response_bytes: int = Field(
        default=64 * 1024,
        ge=1,
        le=1024 * 1024,
    )
    prometheus_base_url: str | None = Field(default=None)
    prometheus_bearer_token: SecretStr | None = Field(default=None)
    loki_base_url: str | None = Field(default=None)
    loki_bearer_token: SecretStr | None = Field(default=None)
    tempo_base_url: str | None = Field(default=None)
    tempo_bearer_token: SecretStr | None = Field(default=None)
    metrics_query_tenant_label: str = Field(default="tenant_id")
    metrics_query_service_label: str = Field(default="service")
    metrics_query_status_label: str = Field(default="status")
    metrics_query_requests_metric: str = Field(default="http_requests_total")
    metrics_query_latency_bucket_metric: str = Field(
        default="http_request_duration_seconds_bucket"
    )
    metrics_query_availability_metric: str = Field(default="up")
    llm_report_enabled: bool = Field(default=False)
    llm_provider: str = Field(default=LLM_PROVIDER_OPENAI_COMPATIBLE)
    llm_api_style: str = Field(default="responses")
    llm_base_url: str | None = Field(default=None)
    llm_api_key: SecretStr | None = Field(default=None)
    llm_model: str | None = Field(default=None)
    llm_provider_order: str = Field(default="openai,dashscope")
    llm_openai_api_style: str = Field(default="responses")
    llm_openai_base_url: str | None = Field(default=None)
    llm_openai_api_key: SecretStr | None = Field(default=None)
    llm_openai_model: str | None = Field(default=None)
    llm_dashscope_api_style: str = Field(default="chat_completions")
    llm_dashscope_base_url: str | None = Field(default=None)
    llm_dashscope_api_key: SecretStr | None = Field(default=None)
    llm_dashscope_model: str | None = Field(default=None)
    llm_openai_compatible_api_style: str = Field(default="responses")
    llm_openai_compatible_base_url: str | None = Field(default=None)
    llm_openai_compatible_api_key: SecretStr | None = Field(default=None)
    llm_openai_compatible_model: str | None = Field(default=None)
    llm_custom_api_style: str = Field(default="chat_completions")
    llm_custom_base_url: str | None = Field(default=None)
    llm_custom_api_key: SecretStr | None = Field(default=None)
    llm_custom_model: str | None = Field(default=None)
    llm_request_timeout_seconds: float = Field(
        default=30,
        gt=0,
        le=120,
    )
    llm_max_response_bytes: int = Field(
        default=256 * 1024,
        ge=1,
        le=4 * 1024 * 1024,
    )
    llm_max_output_tokens: int = Field(
        default=2048,
        ge=1,
        le=16_384,
    )
    llm_failure_threshold: int = Field(default=3, ge=1, le=100)
    llm_recovery_timeout_seconds: float = Field(
        default=60,
        gt=0,
        le=3600,
    )
    audit_retention_worker_enabled: bool = Field(default=False)
    audit_retention_worker_id: str | None = Field(default=None)
    audit_retention_days: int = Field(default=30, ge=1, le=3650)
    audit_retention_batch_size: int = Field(default=100, ge=1, le=1000)
    audit_retention_active_interval_seconds: float = Field(
        default=1,
        gt=0,
        le=3600,
    )
    audit_retention_idle_interval_seconds: float = Field(
        default=3600,
        gt=0,
        le=86400,
    )
    audit_retention_error_backoff_initial_seconds: float = Field(
        default=5,
        gt=0,
        le=3600,
    )
    audit_retention_error_backoff_max_seconds: float = Field(
        default=300,
        gt=0,
        le=86400,
    )
    audit_retention_shutdown_timeout_seconds: float = Field(
        default=30,
        gt=0,
        le=3600,
    )
    readiness_database_timeout_seconds: float = Field(default=1.0, gt=0, le=10)
    readiness_cache_ttl_seconds: float = Field(default=2.0, ge=0, le=30)
    http_access_log_health_endpoints: bool = Field(default=False)
    http_rate_limit_enabled: bool = Field(default=True)
    http_rate_limit_requests: int = Field(default=300, ge=1, le=100_000)
    http_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    http_rate_limit_max_keys: int = Field(default=10_000, ge=1, le=100_000)
    metrics_enabled: bool = Field(default=True)
    metrics_outbox_query_timeout_seconds: float = Field(
        default=1.0,
        gt=0,
        le=10,
    )
    metrics_outbox_cache_ttl_seconds: float = Field(
        default=5.0,
        ge=0,
        le=60,
    )
    admin_oidc_enabled: bool = Field(default=False)
    admin_demo_enabled: bool = Field(default=False)
    admin_demo_token: SecretStr | None = Field(default=None)
    admin_demo_tenant_id: str = Field(default="demo-tenant")
    admin_demo_admin_id: str = Field(default="demo-admin")
    admin_oidc_issuer: str | None = Field(default=None)
    admin_oidc_audience: str | None = Field(default=None)
    admin_oidc_jwks_url: str | None = Field(default=None)
    admin_oidc_algorithms: str = Field(default="RS256")
    admin_oidc_jwks_cache_ttl_seconds: float = Field(
        default=300,
        gt=0,
        le=86400,
    )
    admin_oidc_unknown_kid_cache_seconds: float = Field(
        default=30,
        gt=0,
        le=300,
    )
    admin_oidc_request_timeout_seconds: float = Field(
        default=3,
        gt=0,
        le=30,
    )
    admin_oidc_leeway_seconds: float = Field(
        default=30,
        ge=0,
        le=300,
    )
    admin_oidc_subject_claim: str = Field(default="sub")
    admin_oidc_scopes_claim: str = Field(default="scope")
    admin_oidc_tenants_claim: str = Field(default="tenant_ids")
    admin_oidc_all_tenants_claim: str = Field(default="all_tenants")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        """规范化日志级别，并在启动前拒绝无效配置。"""
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("log_level is not supported")
        return normalized

    @field_validator("rca_investigation_policy")
    @classmethod
    def validate_rca_investigation_policy(cls, value: str) -> str:
        """只接受服务端发布的固定计划键，未知策略不得静默回落。"""
        if value != value.strip() or value not in _RCA_INVESTIGATION_POLICIES:
            allowed = ", ".join(sorted(_RCA_INVESTIGATION_POLICIES))
            raise ValueError(f"rca_investigation_policy must be one of: {allowed}")
        return value

    @field_validator("llm_provider")
    @classmethod
    def validate_llm_provider(cls, value: str) -> str:
        try:
            return normalize_llm_provider(value)
        except AppValidationError as exc:
            raise ValueError(exc.message) from exc

    @field_validator("llm_api_style")
    @classmethod
    def validate_llm_api_style(cls, value: str) -> str:
        try:
            return normalize_llm_api_style(value)
        except AppValidationError as exc:
            raise ValueError(exc.message) from exc

    @field_validator("llm_provider_order")
    @classmethod
    def validate_llm_provider_order(cls, value: str) -> str:
        try:
            return ",".join(parse_llm_provider_order(value))
        except AppValidationError as exc:
            raise ValueError(exc.message) from exc

    @field_validator(
        "llm_openai_api_style",
        "llm_dashscope_api_style",
        "llm_openai_compatible_api_style",
        "llm_custom_api_style",
    )
    @classmethod
    def validate_provider_llm_api_style(cls, value: str) -> str:
        try:
            return normalize_llm_api_style(value)
        except AppValidationError as exc:
            raise ValueError(exc.message) from exc

    @field_validator(
        "kafka_topic",
        "rca_dead_letter_topic",
        "ticket_submission_dead_letter_topic",
    )
    @classmethod
    def validate_kafka_topic(cls, value: str) -> str:
        """Kafka Topic 名必须在配置加载阶段就符合 Broker 约束。"""
        if _KAFKA_TOPIC_PATTERN.fullmatch(value) is None:
            raise ValueError("Kafka topic contains unsupported characters")
        return value

    @field_validator(
        "metrics_query_tenant_label",
        "metrics_query_service_label",
        "metrics_query_status_label",
    )
    @classmethod
    def validate_metrics_query_label(cls, value: str) -> str:
        if _PROMETHEUS_LABEL_IDENTIFIER.fullmatch(value) is None:
            raise ValueError("Metrics query label is invalid")
        return value

    @field_validator(
        "metrics_query_requests_metric",
        "metrics_query_latency_bucket_metric",
        "metrics_query_availability_metric",
    )
    @classmethod
    def validate_metrics_query_metric(cls, value: str) -> str:
        if _PROMETHEUS_METRIC_IDENTIFIER.fullmatch(value) is None:
            raise ValueError("Metrics query metric is invalid")
        return value

    @field_validator("kafka_security_protocol")
    @classmethod
    def validate_kafka_security_protocol(cls, value: str) -> str:
        """Kafka安全协议必须在配置加载阶段归一化并校验。"""
        if value != value.strip():
            raise ValueError("Kafka security_protocol is invalid")
        normalized = value.upper()
        if normalized not in _KAFKA_SECURITY_PROTOCOLS:
            raise ValueError("Kafka security_protocol is not supported")
        return normalized

    @field_validator("kafka_sasl_mechanism")
    @classmethod
    def validate_kafka_sasl_mechanism(
        cls,
        value: str | None,
    ) -> str | None:
        """SASL机制必须是aiokafka适配器明确支持的枚举值。"""
        if value is None:
            return None
        cls._validate_plain_text_identifier("Kafka sasl_mechanism", value, 64)
        normalized = value.upper()
        if normalized not in _KAFKA_SASL_MECHANISMS:
            raise ValueError("Kafka sasl_mechanism is not supported")
        return normalized

    @field_validator("kafka_sasl_username")
    @classmethod
    def validate_kafka_sasl_username(
        cls,
        value: str | None,
    ) -> str | None:
        """SASL用户名会进入连接配置，不能携带空白或日志污染字符。"""
        if value is None:
            return None
        cls._validate_plain_text_identifier("Kafka sasl_username", value, 256)
        return value

    @field_validator(
        "kafka_client_id",
        "rca_consumer_client_id",
        "ticket_submission_consumer_client_id",
    )
    @classmethod
    def validate_kafka_client_id(cls, value: str) -> str:
        """Kafka client_id 会进入Broker日志，必须短小且不可污染日志。"""
        cls._validate_plain_text_identifier("kafka client_id", value, 128)
        return value

    @field_validator(
        "rca_consumer_group_id",
        "ticket_submission_consumer_group_id",
    )
    @classmethod
    def validate_kafka_consumer_group_id(cls, value: str) -> str:
        """共享Topic消费者的 group_id 必须可审计且不会污染日志。"""
        cls._validate_plain_text_identifier("Kafka consumer group_id", value, 255)
        return value

    @field_validator(
        "outbox_worker_id",
        "rca_consumer_worker_id",
        "ticket_submission_consumer_worker_id",
        "audit_retention_worker_id",
        "remediation_reclaim_worker_id",
    )
    @classmethod
    def validate_optional_worker_id(
        cls,
        value: str | None,
    ) -> str | None:
        """显式 Worker ID 必须稳定可审计，不能静默截断或清洗。"""
        if value is None:
            return None
        try:
            return validate_worker_id(value)
        except AppValidationError as exc:
            raise ValueError(exc.message) from exc

    @field_validator(
        "ticketing_http_json_endpoint_url",
        "ticketing_jira_base_url",
        "ticketing_servicenow_base_url",
        "remediation_controller_base_url",
    )
    @classmethod
    def validate_ticketing_endpoint_url(
        cls,
        value: str | None,
    ) -> str | None:
        """外部工单网关地址必须是固定 HTTP(S) 端点。"""
        if value is None:
            return None
        parsed = urlparse(value)
        if (
            value != value.strip()
            or cls._contains_ascii_control(value)
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("ticketing endpoint URL is invalid")
        return value

    @field_validator("remediation_action_catalog_path")
    @classmethod
    def validate_remediation_action_catalog_path(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        if (
            not 1 <= len(value) <= 1024
            or value != value.strip()
            or cls._contains_ascii_control(value)
        ):
            raise ValueError("remediation_action_catalog_path is invalid")
        return value

    @field_validator(
        "ticketing_jira_base_url",
        "ticketing_servicenow_base_url",
    )
    @classmethod
    def validate_ticketing_vendor_base_url(
        cls,
        value: str | None,
    ) -> str | None:
        """供应商 base URL 后续会拼固定 API 路径，禁止尾部斜杠。"""
        if value is not None and value.endswith("/"):
            raise ValueError("ticketing vendor base URL must not end with slash")
        return value

    @model_validator(mode="after")
    def validate_admin_authentication_configuration(self) -> "Settings":
        """校验生产OIDC与本地演练认证之间的安全边界。"""
        production_environment = self.app_env.strip().lower() in {
            "prod",
            "production",
        }
        if self.admin_oidc_enabled and self.admin_demo_enabled:
            raise ValueError(
                "OIDC and demo administrator authentication are mutually exclusive"
            )
        if production_environment and self.admin_demo_enabled:
            raise ValueError(
                "Demo administrator authentication is not allowed in production"
            )
        if production_environment and not self.alert_webhook_auth_enabled:
            raise ValueError(
                "Alert webhook authentication must be enabled in production"
            )
        if self.alert_webhook_auth_enabled:
            if self.alert_webhook_secret is None:
                raise ValueError(
                    "Alert webhook configuration is incomplete: alert_webhook_secret"
                )
            webhook_secret = self.alert_webhook_secret.get_secret_value()
            if (
                len(webhook_secret) < 32
                or webhook_secret != webhook_secret.strip()
                or self._contains_ascii_control(webhook_secret)
            ):
                raise ValueError("Alert webhook secret is invalid")
        if self.admin_oidc_enabled:
            required = {
                "admin_oidc_issuer": self.admin_oidc_issuer,
                "admin_oidc_audience": self.admin_oidc_audience,
                "admin_oidc_jwks_url": self.admin_oidc_jwks_url,
            }
            missing = [
                field_name
                for field_name, value in required.items()
                if value is None or not value.strip()
            ]
            if missing:
                raise ValueError(
                    f"OIDC configuration is incomplete: {', '.join(missing)}"
                )
            self._validate_https_endpoint(
                "admin_oidc_issuer",
                self.admin_oidc_issuer or "",
            )
            self._validate_plain_text_identifier(
                "admin_oidc_audience",
                self.admin_oidc_audience or "",
                512,
            )
            self._validate_https_endpoint(
                "admin_oidc_jwks_url",
                self.admin_oidc_jwks_url or "",
            )
            for field_name, value in (
                ("admin_oidc_subject_claim", self.admin_oidc_subject_claim),
                ("admin_oidc_scopes_claim", self.admin_oidc_scopes_claim),
                ("admin_oidc_tenants_claim", self.admin_oidc_tenants_claim),
                (
                    "admin_oidc_all_tenants_claim",
                    self.admin_oidc_all_tenants_claim,
                ),
            ):
                self._validate_plain_text_identifier(field_name, value, 128)
        if self.admin_demo_enabled:
            if self.admin_demo_token is None:
                raise ValueError(
                    "Demo administrator configuration is incomplete: admin_demo_token"
                )
            demo_token = self.admin_demo_token.get_secret_value()
            if (
                not 32 <= len(demo_token) <= 4096
                or not demo_token.isascii()
                or demo_token != demo_token.strip()
                or self._contains_ascii_control(demo_token)
                or any(character.isspace() for character in demo_token)
            ):
                raise ValueError("admin_demo_token is invalid")
            self._validate_demo_identity(
                "admin_demo_tenant_id",
                self.admin_demo_tenant_id,
            )
            self._validate_demo_identity(
                "admin_demo_admin_id",
                self.admin_demo_admin_id,
            )
        # 即使功能关闭，也提前拒绝无法解析的算法列表，避免开启时才失败。
        if not self.admin_oidc_allowed_algorithms:
            raise ValueError("admin_oidc_algorithms must not be empty")
        self._validate_kafka_sasl_configuration()
        if (
            self.audit_retention_error_backoff_initial_seconds
            > self.audit_retention_error_backoff_max_seconds
        ):
            raise ValueError(
                "audit retention error backoff initial must not exceed max"
            )
        if (
            self.remediation_reclaim_error_backoff_initial_seconds
            > self.remediation_reclaim_error_backoff_max_seconds
        ):
            raise ValueError(
                "remediation reclaim error backoff initial must not exceed max"
            )
        if (
            self.rca_consumer_heartbeat_seconds
            >= self.rca_consumer_execution_timeout_seconds
        ):
            raise ValueError("RCA heartbeat must be shorter than execution timeout")
        if self.rca_consumer_heartbeat_seconds >= self.rca_consumer_lease_seconds:
            raise ValueError("RCA heartbeat must be shorter than workflow lease")
        if self.rca_consumer_enabled:
            required_observability_endpoints = {
                "prometheus_base_url": self.prometheus_base_url,
                "loki_base_url": self.loki_base_url,
            }
            if self.rca_investigation_policy == "fixed_default":
                required_observability_endpoints["tempo_base_url"] = self.tempo_base_url
            self._require_non_empty_fields(
                required_observability_endpoints,
                "RCA consumer configuration is incomplete",
            )
            self._validate_http_endpoint(
                "prometheus_base_url",
                self.prometheus_base_url or "",
            )
            self._validate_http_endpoint(
                "loki_base_url",
                self.loki_base_url or "",
            )
            if self.tempo_base_url is not None:
                self._validate_http_endpoint(
                    "tempo_base_url",
                    self.tempo_base_url,
                )
            self._validate_optional_secret(
                "prometheus_bearer_token",
                self.prometheus_bearer_token,
            )
            self._validate_optional_secret(
                "loki_bearer_token",
                self.loki_bearer_token,
            )
            self._validate_optional_secret(
                "tempo_bearer_token",
                self.tempo_bearer_token,
            )
            self._require_dead_letter_topic_distinct(
                "rca_dead_letter_topic",
                self.rca_dead_letter_topic,
            )
        if self.ticket_submission_consumer_enabled:
            self._require_dead_letter_topic_distinct(
                "ticket_submission_dead_letter_topic",
                self.ticket_submission_dead_letter_topic,
            )
        if self.rca_consumer_enabled and self.ticket_submission_consumer_enabled:
            self._require_consumer_group_ids_distinct()
            self._require_dead_letter_topics_distinct()
        if self.ticketing_http_json_enabled:
            if not self.ticket_submission_consumer_enabled:
                raise ValueError(
                    "Ticketing HTTP JSON gateway requires the ticket "
                    "submission consumer to be enabled"
                )
            self._require_non_empty_fields(
                {
                    "ticketing_http_json_endpoint_url": (
                        self.ticketing_http_json_endpoint_url
                    )
                },
                "Ticketing HTTP JSON configuration is incomplete",
            )
            if self.ticketing_http_json_bearer_token is not None:
                token = self.ticketing_http_json_bearer_token.get_secret_value()
                if (
                    not token.strip()
                    or token != token.strip()
                    or self._contains_ascii_control(token)
                ):
                    raise ValueError("Ticketing HTTP JSON bearer token is invalid")
        self._validate_ticketing_vendor_configuration(
            production_environment=production_environment,
        )
        self._validate_remediation_configuration(
            production_environment=production_environment,
        )
        self._validate_notification_configuration(
            production_environment=production_environment,
        )
        if self.llm_report_enabled:
            if not self.rca_consumer_enabled:
                raise ValueError("LLM reports require the RCA consumer to be enabled")
            self._validate_llm_provider_fields()
            try:
                llm_provider_configs = self.llm_provider_configs
            except AppValidationError as exc:
                raise ValueError(exc.message) from exc
            if not llm_provider_configs:
                raise ValueError(
                    "LLM configuration is incomplete: no usable provider credentials"
                )
        return self

    @staticmethod
    def _contains_ascii_control(value: str) -> bool:
        """识别配置文本中的不可见ASCII控制字符。"""
        return any(ord(character) < 32 or ord(character) == 127 for character in value)

    @staticmethod
    def _validate_http_endpoint(field_name: str, value: str) -> None:
        """固定外部 HTTP(S) 端点不能携带凭据、查询或控制字符。"""
        parsed = urlparse(value)
        if (
            value != value.strip()
            or Settings._contains_ascii_control(value)
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(f"{field_name} is invalid")

    @staticmethod
    def _validate_https_endpoint(field_name: str, value: str) -> None:
        """固定安全端点必须使用 HTTPS 且不可携带动态拼接片段。"""
        parsed = urlparse(value)
        if (
            value != value.strip()
            or Settings._contains_ascii_control(value)
            or parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(f"{field_name} is invalid")

    @staticmethod
    def _validate_optional_secret(
        field_name: str,
        value: SecretStr | None,
    ) -> None:
        """可选外部 Bearer Token 一旦配置就必须保持头部安全。"""
        if value is None:
            return
        Settings._validate_secret_text(
            field_name,
            value.get_secret_value(),
            8192,
        )

    def _validate_ticketing_vendor_configuration(
        self,
        *,
        production_environment: bool,
    ) -> None:
        """校验 Jira 与 ServiceNow 直连适配器的启用边界。"""
        enabled = self.ticketing_jira_enabled or self.ticketing_servicenow_enabled
        if enabled and not self.ticket_submission_consumer_enabled:
            raise ValueError(
                "Vendor ticketing gateways require the ticket submission "
                "consumer to be enabled"
            )
        if self.ticketing_jira_enabled:
            self._require_non_empty_fields(
                {
                    "ticketing_jira_base_url": self.ticketing_jira_base_url,
                    "ticketing_jira_user_email": (self.ticketing_jira_user_email),
                    "ticketing_jira_project_key": (self.ticketing_jira_project_key),
                },
                "Jira ticketing configuration is incomplete",
            )
            if self.ticketing_jira_api_token is None:
                raise ValueError(
                    "Jira ticketing configuration is incomplete: "
                    "ticketing_jira_api_token"
                )
            self._validate_plain_text_identifier(
                "ticketing_jira_user_email",
                self.ticketing_jira_user_email or "",
                320,
            )
            self._validate_plain_text_identifier(
                "ticketing_jira_project_key",
                self.ticketing_jira_project_key or "",
                64,
            )
            self._validate_plain_text_identifier(
                "ticketing_jira_issue_type",
                self.ticketing_jira_issue_type,
                128,
            )
            self._validate_secret_text(
                "ticketing_jira_api_token",
                self.ticketing_jira_api_token.get_secret_value(),
                8192,
            )
            if production_environment:
                self._validate_https_endpoint(
                    "ticketing_jira_base_url",
                    self.ticketing_jira_base_url or "",
                )
        if self.ticketing_servicenow_enabled:
            self._require_non_empty_fields(
                {
                    "ticketing_servicenow_base_url": (
                        self.ticketing_servicenow_base_url
                    ),
                    "ticketing_servicenow_username": (
                        self.ticketing_servicenow_username
                    ),
                },
                "ServiceNow ticketing configuration is incomplete",
            )
            if self.ticketing_servicenow_password is None:
                raise ValueError(
                    "ServiceNow ticketing configuration is incomplete: "
                    "ticketing_servicenow_password"
                )
            self._validate_plain_text_identifier(
                "ticketing_servicenow_username",
                self.ticketing_servicenow_username or "",
                256,
            )
            self._validate_plain_text_identifier(
                "ticketing_servicenow_table",
                self.ticketing_servicenow_table,
                128,
            )
            self._validate_secret_text(
                "ticketing_servicenow_password",
                self.ticketing_servicenow_password.get_secret_value(),
                8192,
            )
            if production_environment:
                self._validate_https_endpoint(
                    "ticketing_servicenow_base_url",
                    self.ticketing_servicenow_base_url or "",
                )

    def _validate_remediation_configuration(
        self,
        *,
        production_environment: bool,
    ) -> None:
        """修复控制器必须固定地址、白名单完整且执行默认关闭。"""
        configured = self.remediation_controller_base_url is not None
        catalog_configured = self.remediation_action_catalog_path is not None
        if self.remediation_execution_enabled and not configured:
            raise ValueError("Remediation execution requires a configured controller")
        if configured != catalog_configured:
            raise ValueError(
                "Remediation controller and action catalog must be configured together"
            )
        if self.remediation_reclaim_worker_enabled and not configured:
            raise ValueError(
                "Remediation reclaim worker requires a configured controller"
            )
        if not configured:
            if (
                self.remediation_allowed_tenants
                or self.remediation_controller_bearer_token is not None
            ):
                raise ValueError("Remediation policy requires a configured controller")
            return
        if self.remediation_execution_enabled and not self.remediation_allowed_tenants:
            raise ValueError("Remediation execution requires allowed tenants")
        if (
            self.remediation_maintenance_start_hour_utc
            >= self.remediation_maintenance_end_hour_utc
        ):
            raise ValueError("Remediation maintenance window is invalid")
        # 请求超时必须短于租约，否则外部调用可能在租约仍有效时挂死且无法被 reclaim。
        if self.remediation_request_timeout_seconds >= self.remediation_lease_seconds:
            raise ValueError(
                "remediation_request_timeout_seconds must be shorter than "
                "remediation_lease_seconds"
            )
        for tenant_id in self.remediation_allowed_tenants:
            self._validate_demo_identity(
                "remediation_allowed_tenant",
                tenant_id,
            )
        self._validate_optional_secret(
            "remediation_controller_bearer_token",
            self.remediation_controller_bearer_token,
        )
        if self.remediation_controller_base_url is not None:
            parsed = urlparse(self.remediation_controller_base_url)
            if self.remediation_controller_base_url.endswith(
                "/"
            ) or parsed.path not in {"", "/"}:
                raise ValueError(
                    "remediation_controller_base_url must be an origin URL"
                )
        if production_environment:
            self._validate_https_endpoint(
                "remediation_controller_base_url",
                self.remediation_controller_base_url or "",
            )

    def _validate_notification_configuration(
        self,
        *,
        production_environment: bool,
    ) -> None:
        """校验通知 Webhook 和 PagerDuty 路由密钥。"""
        for field_name, secret in (
            (
                "notification_slack_webhook_url",
                self.notification_slack_webhook_url,
            ),
            (
                "notification_teams_webhook_url",
                self.notification_teams_webhook_url,
            ),
        ):
            if secret is None:
                continue
            value = secret.get_secret_value()
            self._validate_secret_text(field_name, value, 8192)
            parsed = urlparse(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
            ):
                raise ValueError(f"{field_name} is invalid")
            if production_environment:
                self._validate_https_endpoint(field_name, value)
        if self.notification_pagerduty_routing_key is not None:
            self._validate_secret_text(
                "notification_pagerduty_routing_key",
                self.notification_pagerduty_routing_key.get_secret_value(),
                8192,
            )

    def _validate_llm_provider_fields(self) -> None:
        """Validate configured LLM provider fields before building gateways."""
        for field_name, value in (
            ("llm_base_url", self.llm_base_url),
            ("llm_openai_base_url", self.llm_openai_base_url),
            ("llm_dashscope_base_url", self.llm_dashscope_base_url),
            (
                "llm_openai_compatible_base_url",
                self.llm_openai_compatible_base_url,
            ),
            ("llm_custom_base_url", self.llm_custom_base_url),
        ):
            if value is not None:
                self._validate_http_endpoint(field_name, value)
        for field_name, value in (
            ("llm_model", self.llm_model),
            ("llm_openai_model", self.llm_openai_model),
            ("llm_dashscope_model", self.llm_dashscope_model),
            ("llm_openai_compatible_model", self.llm_openai_compatible_model),
            ("llm_custom_model", self.llm_custom_model),
        ):
            if value is not None:
                self._validate_plain_text_identifier(field_name, value, 128)
        for field_name, value in (
            ("llm_api_key", self.llm_api_key),
            ("llm_openai_api_key", self.llm_openai_api_key),
            ("llm_dashscope_api_key", self.llm_dashscope_api_key),
            (
                "llm_openai_compatible_api_key",
                self.llm_openai_compatible_api_key,
            ),
            ("llm_custom_api_key", self.llm_custom_api_key),
        ):
            self._validate_optional_secret(field_name, value)

    @staticmethod
    def _require_non_empty_fields(
        fields: dict[str, str | None],
        message: str,
    ) -> None:
        """要求一组可选文本全部存在且去除首尾空白后非空。"""
        missing = [
            field_name
            for field_name, value in fields.items()
            if value is None or not value.strip()
        ]
        if missing:
            raise ValueError(f"{message}: {', '.join(missing)}")

    def _validate_kafka_sasl_configuration(self) -> None:
        """SASL协议与凭据必须成套配置，避免Kafka启动阶段才失败。"""
        sasl_enabled = self.kafka_security_protocol.startswith("SASL")
        configured_fields = {
            "kafka_sasl_mechanism": self.kafka_sasl_mechanism,
            "kafka_sasl_username": self.kafka_sasl_username,
            "kafka_sasl_password": (
                self.kafka_sasl_password.get_secret_value()
                if self.kafka_sasl_password is not None
                else None
            ),
        }
        if not sasl_enabled:
            configured = [
                field_name
                for field_name, value in configured_fields.items()
                if value is not None
            ]
            if configured:
                raise ValueError(
                    "Kafka SASL settings require a SASL security_protocol: "
                    f"{', '.join(configured)}"
                )
            return

        missing = [
            field_name
            for field_name, value in configured_fields.items()
            if value is None or not value.strip()
        ]
        if missing:
            raise ValueError(
                f"Kafka SASL configuration is incomplete: {', '.join(missing)}"
            )
        password = configured_fields["kafka_sasl_password"]
        if password is None:
            raise ValueError(
                "Kafka SASL configuration is incomplete: kafka_sasl_password"
            )
        self._validate_secret_text("Kafka sasl_password", password, 1024)

    @staticmethod
    def _validate_plain_text_identifier(
        field_name: str,
        value: str,
        max_length: int,
    ) -> None:
        """校验会进入日志和Broker侧元数据的短文本身份。"""
        if (
            not 1 <= len(value) <= max_length
            or value != value.strip()
            or Settings._contains_ascii_control(value)
        ):
            raise ValueError(f"{field_name} is invalid")

    @staticmethod
    def _validate_demo_identity(field_name: str, value: str) -> None:
        """演练固定身份会进入授权和审计字段，禁止空白与控制字符。"""
        if (
            not 1 <= len(value) <= 128
            or value != value.strip()
            or Settings._contains_ascii_control(value)
            or any(character.isspace() for character in value)
        ):
            raise ValueError(f"{field_name} is invalid")

    @staticmethod
    def _validate_secret_text(
        field_name: str,
        value: str,
        max_length: int,
    ) -> None:
        """校验密钥文本边界，调用方必须避免把真实值写入日志。"""
        if (
            not 1 <= len(value) <= max_length
            or value != value.strip()
            or Settings._contains_ascii_control(value)
        ):
            raise ValueError(f"{field_name} is invalid")

    def _require_dead_letter_topic_distinct(
        self,
        field_name: str,
        dead_letter_topic: str,
    ) -> None:
        """启用消费者时死信Topic不能回写到主业务事件流。"""
        if self.kafka_topic.strip() == dead_letter_topic.strip():
            raise ValueError(f"{field_name} must be different from kafka_topic")

    def _require_consumer_group_ids_distinct(self) -> None:
        """共享Topic上的不同业务消费者必须使用独立消费组。"""
        if (
            self.rca_consumer_group_id.strip()
            == self.ticket_submission_consumer_group_id.strip()
        ):
            raise ValueError(
                "rca_consumer_group_id must be different from "
                "ticket_submission_consumer_group_id"
            )

    def _require_dead_letter_topics_distinct(self) -> None:
        """不同业务消费者的死信流必须独立，避免重放和留存策略混淆。"""
        if (
            self.rca_dead_letter_topic.strip()
            == self.ticket_submission_dead_letter_topic.strip()
        ):
            raise ValueError(
                "rca_dead_letter_topic must be different from "
                "ticket_submission_dead_letter_topic"
            )

    @property
    def kafka_servers(self) -> tuple[str, ...]:
        """把逗号分隔的Broker配置转换为不可变地址集合。"""
        servers = tuple(
            server.strip()
            for server in self.kafka_bootstrap_servers.split(",")
            if server.strip()
        )
        if not servers:
            raise ValueError("kafka_bootstrap_servers must not be empty")
        return servers

    @property
    def llm_provider_configs(self) -> tuple[LLMProviderRuntimeConfig, ...]:
        return build_llm_provider_configs(
            provider_order=self.llm_provider_order,
            legacy_provider=self.llm_provider,
            legacy_api_style=self.llm_api_style,
            legacy_base_url=self.llm_base_url,
            legacy_api_key=self.llm_api_key,
            legacy_model=self.llm_model,
            openai_api_style=self.llm_openai_api_style,
            openai_base_url=self.llm_openai_base_url,
            openai_api_key=self.llm_openai_api_key,
            openai_model=self.llm_openai_model,
            dashscope_api_style=self.llm_dashscope_api_style,
            dashscope_base_url=self.llm_dashscope_base_url,
            dashscope_api_key=self.llm_dashscope_api_key,
            dashscope_model=self.llm_dashscope_model,
            openai_compatible_api_style=(self.llm_openai_compatible_api_style),
            openai_compatible_base_url=(self.llm_openai_compatible_base_url),
            openai_compatible_api_key=(self.llm_openai_compatible_api_key),
            openai_compatible_model=self.llm_openai_compatible_model,
            custom_api_style=self.llm_custom_api_style,
            custom_base_url=self.llm_custom_base_url,
            custom_api_key=self.llm_custom_api_key,
            custom_model=self.llm_custom_model,
        )

    @property
    def admin_oidc_allowed_algorithms(self) -> tuple[str, ...]:
        """把逗号分隔算法配置转换为去重且保序的元组。"""
        algorithms = tuple(
            algorithm.strip()
            for algorithm in self.admin_oidc_algorithms.split(",")
            if algorithm.strip()
        )
        return tuple(dict.fromkeys(algorithms))


def get_settings() -> Settings:
    """加载并校验运行时配置。"""
    return Settings()
