import pytest
from pydantic import SecretStr, ValidationError

from devops_agent_platform.infrastructure.config.settings import Settings

DEMO_ADMIN_TOKEN = "local-demo-administrator-token-123456"


def test_kafka_servers_are_trimmed_and_keep_declared_order() -> None:
    settings = Settings(
        _env_file=None,
        kafka_bootstrap_servers=" kafka-1:9092, kafka-2:9092 ",
    )

    assert settings.kafka_servers == ("kafka-1:9092", "kafka-2:9092")


def test_empty_kafka_servers_are_rejected_when_accessed() -> None:
    settings = Settings(_env_file=None, kafka_bootstrap_servers=" , ")

    with pytest.raises(ValueError, match="must not be empty"):
        _ = settings.kafka_servers


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("kafka_topic", ""),
        ("kafka_topic", "invalid topic"),
        ("rca_dead_letter_topic", "_invalid-start"),
        ("ticket_submission_dead_letter_topic", "t" * 250),
    ],
)
def test_invalid_kafka_topic_names_are_rejected(
    field_name: str,
    value: str,
) -> None:
    """Topic名决定消息路由，必须在Settings阶段提前校验。"""
    with pytest.raises(ValidationError, match="Kafka topic"):
        Settings(_env_file=None, **{field_name: value})


@pytest.mark.parametrize("protocol", ["UNKNOWN", " SASL_SSL"])
def test_invalid_kafka_security_protocol_is_rejected(protocol: str) -> None:
    """安全协议不支持时必须在Settings阶段失败。"""
    with pytest.raises(ValidationError, match="security_protocol"):
        Settings(_env_file=None, kafka_security_protocol=protocol)


@pytest.mark.parametrize(
    "missing_field",
    [
        "kafka_sasl_mechanism",
        "kafka_sasl_username",
        "kafka_sasl_password",
    ],
)
def test_kafka_sasl_requires_complete_credentials(
    missing_field: str,
) -> None:
    """SASL协议启用后机制、用户名和密码缺一不可。"""
    values = {
        "kafka_security_protocol": "SASL_SSL",
        "kafka_sasl_mechanism": "SCRAM-SHA-512",
        "kafka_sasl_username": "publisher",
        "kafka_sasl_password": SecretStr("private-kafka-password"),
    }
    values.pop(missing_field)

    with pytest.raises(ValidationError, match="SASL configuration"):
        Settings(_env_file=None, **values)


def test_non_sasl_protocol_rejects_sasl_credentials() -> None:
    """配置了SASL凭据但协议仍是PLAINTEXT时要直接拒绝。"""
    with pytest.raises(ValidationError, match="SASL settings require"):
        Settings(
            _env_file=None,
            kafka_security_protocol="PLAINTEXT",
            kafka_sasl_username="publisher",
        )


def test_kafka_sasl_valid_configuration_is_normalized_and_masked() -> None:
    """合法SASL配置会归一化枚举值，且密码不进入repr。"""
    password = "private-kafka-password"
    settings = Settings(
        _env_file=None,
        kafka_security_protocol="sasl_ssl",
        kafka_sasl_mechanism="scram-sha-512",
        kafka_sasl_username="publisher",
        kafka_sasl_password=SecretStr(password),
    )

    assert settings.kafka_security_protocol == "SASL_SSL"
    assert settings.kafka_sasl_mechanism == "SCRAM-SHA-512"
    assert password not in repr(settings)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("kafka_sasl_mechanism", "GSSAPI"),
        ("kafka_sasl_username", "publisher\nforged"),
    ],
)
def test_invalid_kafka_sasl_text_fields_are_rejected(
    field_name: str,
    value: str,
) -> None:
    """SASL机制和用户名也不能等到Kafka启动时才失败。"""
    with pytest.raises(ValidationError, match="sasl"):
        Settings(_env_file=None, **{field_name: value})


def test_invalid_kafka_sasl_password_is_rejected() -> None:
    """SASL密码不能为空白、超长或携带换行。"""
    with pytest.raises(ValidationError, match="sasl_password"):
        Settings(
            _env_file=None,
            kafka_security_protocol="SASL_SSL",
            kafka_sasl_mechanism="PLAIN",
            kafka_sasl_username="publisher",
            kafka_sasl_password=SecretStr("bad\nsecret"),
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("kafka_client_id", " client"),
        ("rca_consumer_client_id", "client\nforged"),
        ("ticket_submission_consumer_client_id", "c" * 129),
    ],
)
def test_invalid_kafka_client_ids_are_rejected(
    field_name: str,
    value: str,
) -> None:
    """client_id会进入Broker日志，不能超长或携带换行。"""
    with pytest.raises(ValidationError, match="client_id"):
        Settings(_env_file=None, **{field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("rca_consumer_group_id", ""),
        ("ticket_submission_consumer_group_id", "group\rforged"),
        ("rca_consumer_group_id", "g" * 256),
    ],
)
def test_invalid_kafka_consumer_group_ids_are_rejected(
    field_name: str,
    value: str,
) -> None:
    """group_id决定消费组归属，不能等到Consumer启动才失败。"""
    with pytest.raises(ValidationError, match="group_id"):
        Settings(_env_file=None, **{field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("readiness_database_timeout_seconds", 0),
        ("readiness_database_timeout_seconds", 11),
        ("readiness_cache_ttl_seconds", -1),
        ("readiness_cache_ttl_seconds", 31),
    ],
)
def test_invalid_readiness_settings_are_rejected(
    field_name: str,
    value: float,
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field_name: value})


def test_log_level_is_normalized() -> None:
    settings = Settings(_env_file=None, log_level="warning")

    assert settings.log_level == "WARNING"


def test_bounded_dynamic_policy_and_budget_defaults_are_available() -> None:
    settings = Settings(_env_file=None, rca_investigation_policy="bounded_dynamic_v1")

    assert settings.rca_investigation_policy == "bounded_dynamic_v1"
    assert settings.rca_dynamic_max_steps == 7
    assert settings.rca_dynamic_max_total_duration_ms == 30_000
    assert settings.rca_dynamic_max_tool_calls_per_type == 2
    assert settings.rca_dynamic_max_evidence_count == 100
    assert settings.rca_dynamic_max_llm_calls == 3


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("rca_dynamic_max_steps", 0),
        ("rca_dynamic_max_total_duration_ms", 0),
        ("rca_dynamic_max_tool_calls_per_type", 21),
        ("rca_dynamic_max_evidence_count", 1001),
        ("rca_dynamic_max_llm_calls", 0),
    ],
)
def test_invalid_bounded_dynamic_budgets_are_rejected(
    field_name: str,
    value: int,
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field_name: value})


def test_production_requires_authenticated_alert_webhook() -> None:
    """生产环境不能因遗漏开关而暴露匿名告警入口。"""
    with pytest.raises(ValidationError, match="must be enabled"):
        Settings(_env_file=None, app_env="production")


def test_enabled_alert_webhook_requires_strong_masked_secret() -> None:
    """启用Webhook认证时共享密钥必须足够强且不会出现在repr。"""
    with pytest.raises(ValidationError, match="incomplete"):
        Settings(
            _env_file=None,
            alert_webhook_auth_enabled=True,
        )
    with pytest.raises(ValidationError, match="secret"):
        Settings(
            _env_file=None,
            alert_webhook_auth_enabled=True,
            alert_webhook_secret=SecretStr("too-short"),
        )
    with pytest.raises(ValidationError, match="secret"):
        Settings(
            _env_file=None,
            alert_webhook_auth_enabled=True,
            alert_webhook_secret=SecretStr("x" * 32 + "\t"),
        )
    with pytest.raises(ValidationError, match="secret"):
        Settings(
            _env_file=None,
            alert_webhook_auth_enabled=True,
            alert_webhook_secret=SecretStr("x" * 32 + "\x7f"),
        )

    secret = "production-alert-webhook-secret-32-bytes"
    settings = Settings(
        _env_file=None,
        app_env="prod",
        alert_webhook_auth_enabled=True,
        alert_webhook_secret=SecretStr(secret),
    )
    assert secret not in repr(settings)


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, log_level="verbose")


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("metrics_outbox_query_timeout_seconds", 0),
        ("metrics_outbox_query_timeout_seconds", 11),
        ("metrics_outbox_cache_ttl_seconds", -1),
        ("metrics_outbox_cache_ttl_seconds", 61),
    ],
)
def test_invalid_outbox_metrics_settings_are_rejected(
    field_name: str,
    value: float,
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field_name: value})


def test_oidc_disabled_does_not_require_external_configuration() -> None:
    """默认关闭OIDC时本地开发不需要身份服务地址。"""
    settings = Settings(_env_file=None)

    assert settings.admin_oidc_enabled is False
    assert settings.admin_demo_enabled is False
    assert settings.admin_oidc_allowed_algorithms == ("RS256",)


def test_demo_administrator_authentication_requires_a_token() -> None:
    with pytest.raises(ValidationError, match="admin_demo_token"):
        Settings(_env_file=None, admin_demo_enabled=True)


@pytest.mark.parametrize(
    "token",
    [
        "",
        "x" * 31,
        f"{'x' * 32} ",
        f"{'x' * 32}\n",
        "令牌" * 16,
    ],
)
def test_demo_administrator_authentication_rejects_unsafe_tokens(
    token: str,
) -> None:
    with pytest.raises(ValidationError, match="admin_demo_token"):
        Settings(
            _env_file=None,
            admin_demo_enabled=True,
            admin_demo_token=SecretStr(token),
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("admin_demo_tenant_id", ""),
        ("admin_demo_tenant_id", "tenant forged"),
        ("admin_demo_admin_id", "\tadmin"),
        ("admin_demo_admin_id", "admin\x7f"),
    ],
)
def test_demo_administrator_authentication_rejects_invalid_identity(
    field_name: str,
    value: str,
) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            admin_demo_enabled=True,
            admin_demo_token=SecretStr(DEMO_ADMIN_TOKEN),
            **{field_name: value},
        )


def test_demo_administrator_authentication_is_forbidden_in_production() -> None:
    with pytest.raises(ValidationError, match="not allowed in production"):
        Settings(
            _env_file=None,
            app_env="production",
            alert_webhook_auth_enabled=True,
            alert_webhook_secret=SecretStr("x" * 32),
            admin_demo_enabled=True,
            admin_demo_token=SecretStr(DEMO_ADMIN_TOKEN),
        )


def test_demo_and_oidc_administrator_authentication_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError, match="mutually exclusive"):
        Settings(
            _env_file=None,
            admin_demo_enabled=True,
            admin_demo_token=SecretStr(DEMO_ADMIN_TOKEN),
            admin_oidc_enabled=True,
            admin_oidc_issuer="https://identity.example.com/",
            admin_oidc_audience="devops-agent-api",
            admin_oidc_jwks_url=("https://identity.example.com/.well-known/jwks.json"),
        )


def test_demo_administrator_authentication_accepts_local_configuration() -> None:
    settings = Settings(
        _env_file=None,
        admin_demo_enabled=True,
        admin_demo_token=SecretStr(DEMO_ADMIN_TOKEN),
        admin_demo_tenant_id="tenant-demo",
        admin_demo_admin_id="admin-demo",
    )

    assert settings.admin_demo_enabled is True
    assert settings.admin_demo_tenant_id == "tenant-demo"
    assert settings.admin_demo_admin_id == "admin-demo"


def test_audit_retention_worker_is_disabled_by_default() -> None:
    """默认配置不能主动删除审计数据。"""
    settings = Settings(_env_file=None)

    assert settings.audit_retention_worker_enabled is False
    assert settings.audit_retention_days == 30
    assert settings.audit_retention_batch_size == 100


def test_rca_consumer_and_llm_are_disabled_by_default() -> None:
    """本地默认配置不能启动外部观测查询或产生模型费用。"""
    settings = Settings(_env_file=None)

    assert settings.rca_consumer_enabled is False
    assert settings.rca_continue_on_step_failure is False
    assert settings.rca_investigation_policy == "fixed_default"
    assert settings.ticket_submission_consumer_enabled is False
    assert settings.ticketing_http_json_enabled is False
    assert settings.llm_report_enabled is False


def test_default_metrics_query_contract_remains_backward_compatible() -> None:
    settings = Settings(_env_file=None)

    assert settings.metrics_query_tenant_label == "tenant_id"
    assert settings.metrics_query_service_label == "service"
    assert settings.metrics_query_status_label == "status"
    assert settings.metrics_query_requests_metric == "http_requests_total"
    assert (
        settings.metrics_query_latency_bucket_metric
        == "http_request_duration_seconds_bucket"
    )
    assert settings.metrics_query_availability_metric == "up"


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("metrics_query_tenant_label", "tenant-id"),
        ("metrics_query_service_label", "service name"),
        ("metrics_query_status_label", "status\nforged"),
        ("metrics_query_requests_metric", "http requests"),
        ("metrics_query_latency_bucket_metric", "metric{bad}"),
        ("metrics_query_availability_metric", "up;drop"),
    ],
)
def test_metrics_query_contract_rejects_unsafe_identifiers(
    field_name: str,
    value: str,
) -> None:
    with pytest.raises(ValidationError, match="Metrics query"):
        Settings(_env_file=None, **{field_name: value})


@pytest.mark.parametrize(
    "missing_field",
    [
        "prometheus_base_url",
        "loki_base_url",
        "tempo_base_url",
    ],
)
def test_enabled_rca_consumer_requires_all_observability_urls(
    missing_field: str,
) -> None:
    """三步固定计划的数据源地址缺一不可。"""
    values = {
        "rca_consumer_enabled": True,
        "prometheus_base_url": "https://prometheus.example.com",
        "loki_base_url": "https://loki.example.com",
        "tempo_base_url": "https://tempo.example.com",
    }
    values[missing_field] = None

    with pytest.raises(ValidationError, match="incomplete"):
        Settings(_env_file=None, **values)


def test_trace_free_policy_does_not_require_tempo() -> None:
    settings = Settings(
        _env_file=None,
        rca_consumer_enabled=True,
        rca_investigation_policy="fixed_no_traces",
        prometheus_base_url="https://prometheus.example.com",
        loki_base_url="https://loki.example.com",
    )

    assert settings.tempo_base_url is None


@pytest.mark.parametrize(
    "policy",
    ["auto", "fixed_default ", "", "unknown"],
)
def test_rca_investigation_policy_rejects_unknown_values(policy: str) -> None:
    with pytest.raises(ValidationError, match="rca_investigation_policy"):
        Settings(_env_file=None, rca_investigation_policy=policy)


@pytest.mark.parametrize(
    "changes",
    [
        {"prometheus_base_url": "https://prometheus.example.com\nforged"},
        {"loki_base_url": "https://loki.example.com?debug=true"},
        {"tempo_base_url": "https://user:pass@tempo.example.com"},
        {"prometheus_bearer_token": SecretStr("metrics\ttoken")},
        {"loki_bearer_token": SecretStr("logs\ttoken")},
        {"tempo_bearer_token": SecretStr("traces\ttoken")},
    ],
)
def test_enabled_rca_consumer_rejects_dirty_observability_configuration(
    changes: dict[str, object],
) -> None:
    """观测端点和 Token 会进入 HTTP 请求，必须启动阶段收紧。"""
    values = {
        "rca_consumer_enabled": True,
        "prometheus_base_url": "https://prometheus.example.com",
        "loki_base_url": "https://loki.example.com",
        "tempo_base_url": "https://tempo.example.com",
    }
    values.update(changes)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_enabled_rca_consumer_rejects_dead_letter_topic_on_source_topic() -> None:
    """RCA死信不能回写到共享业务事件Topic。"""
    with pytest.raises(ValidationError, match="rca_dead_letter_topic"):
        Settings(
            _env_file=None,
            rca_consumer_enabled=True,
            prometheus_base_url="https://prometheus.example.com",
            loki_base_url="https://loki.example.com",
            tempo_base_url="https://tempo.example.com",
            kafka_topic="devops-agent.events.v1",
            rca_dead_letter_topic="devops-agent.events.v1",
        )


def test_enabled_shared_topic_consumers_require_distinct_group_ids() -> None:
    """共享Topic上的不同业务消费者不能加入同一个消费组。"""
    with pytest.raises(ValidationError, match="rca_consumer_group_id"):
        Settings(
            _env_file=None,
            rca_consumer_enabled=True,
            ticket_submission_consumer_enabled=True,
            prometheus_base_url="https://prometheus.example.com",
            loki_base_url="https://loki.example.com",
            tempo_base_url="https://tempo.example.com",
            rca_consumer_group_id="devops-agent-consumers",
            ticket_submission_consumer_group_id="devops-agent-consumers",
        )


def test_enabled_consumers_require_distinct_dead_letter_topics() -> None:
    """不同业务死信流不能混到同一个Topic里。"""
    with pytest.raises(ValidationError, match="rca_dead_letter_topic"):
        Settings(
            _env_file=None,
            rca_consumer_enabled=True,
            ticket_submission_consumer_enabled=True,
            prometheus_base_url="https://prometheus.example.com",
            loki_base_url="https://loki.example.com",
            tempo_base_url="https://tempo.example.com",
            rca_consumer_group_id="devops-agent-rca-v1",
            ticket_submission_consumer_group_id=("devops-agent-ticket-submission-v1"),
            rca_dead_letter_topic="devops-agent.dead-letter.v1",
            ticket_submission_dead_letter_topic=("devops-agent.dead-letter.v1"),
        )


def test_enabled_llm_requires_consumer_and_complete_credentials() -> None:
    """LLM 不能脱离实际消费链路单独假启用。"""
    with pytest.raises(ValidationError, match="consumer"):
        Settings(_env_file=None, llm_report_enabled=True)

    base = {
        "rca_consumer_enabled": True,
        "prometheus_base_url": "https://prometheus.example.com",
        "loki_base_url": "https://loki.example.com",
        "tempo_base_url": "https://tempo.example.com",
        "llm_report_enabled": True,
        "llm_base_url": "https://api.example.com",
        "llm_model": "rca-model-v1",
    }
    with pytest.raises(ValidationError, match="no usable provider"):
        Settings(_env_file=None, **base)

    settings = Settings(
        _env_file=None,
        **base,
        llm_api_key=SecretStr("private-key"),
    )
    assert settings.llm_api_key is not None
    assert len(settings.llm_provider_configs) == 1
    assert settings.llm_provider_configs[0].provider_name == "openai_compatible"
    assert "private-key" not in repr(settings)

    dashscope_settings = Settings(
        _env_file=None,
        **{
            **base,
            "llm_provider": "dashscope",
            "llm_api_style": "chat-completions",
            "llm_base_url": None,
        },
        llm_api_key=SecretStr("private-key"),
    )
    assert dashscope_settings.llm_provider == "dashscope"
    assert dashscope_settings.llm_api_style == "chat_completions"
    assert dashscope_settings.llm_provider_configs[0].provider_name == "dashscope"


def test_enabled_llm_accepts_ordered_provider_chain() -> None:
    settings = Settings(
        _env_file=None,
        rca_consumer_enabled=True,
        prometheus_base_url="https://prometheus.example.com",
        loki_base_url="https://loki.example.com",
        tempo_base_url="https://tempo.example.com",
        llm_report_enabled=True,
        llm_provider_order=" openai, dashscope, openai ",
        llm_openai_api_key=SecretStr("private-openai-key"),
        llm_openai_model="gpt-approved",
        llm_dashscope_api_key=SecretStr("private-dashscope-key"),
        llm_dashscope_model="qwen3.7-plus",
    )

    assert settings.llm_provider_order == "openai,dashscope"
    assert tuple(config.provider_name for config in settings.llm_provider_configs) == (
        "openai",
        "dashscope",
    )
    assert settings.llm_provider_configs[0].api_style == "responses"
    assert settings.llm_provider_configs[0].base_url == "https://api.openai.com"
    assert settings.llm_provider_configs[1].api_style == "chat_completions"
    assert settings.llm_provider_configs[1].base_url == (
        "https://dashscope.aliyuncs.com/compatible-mode"
    )


def test_enabled_llm_rejects_empty_provider_chain() -> None:
    with pytest.raises(ValidationError, match="no usable provider"):
        Settings(
            _env_file=None,
            rca_consumer_enabled=True,
            prometheus_base_url="https://prometheus.example.com",
            loki_base_url="https://loki.example.com",
            tempo_base_url="https://tempo.example.com",
            llm_report_enabled=True,
            llm_provider_order="openai,dashscope",
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"llm_base_url": "https://api.example.com\nforged"},
        {"llm_base_url": "https://api.example.com?debug=true"},
        {"llm_base_url": "https://user:pass@api.example.com"},
        {"llm_provider": "unsupported"},
        {"llm_api_style": "legacy"},
        {"llm_model": "rca-model-v1\tforged"},
        {"llm_api_key": SecretStr("private\tkey")},
        {"llm_provider_order": "unsupported"},
        {"llm_openai_api_style": "legacy"},
        {"llm_openai_base_url": "https://openai.example.com?debug=true"},
        {"llm_dashscope_model": "qwen\nforged"},
        {"llm_dashscope_api_key": SecretStr("private\tdashscope")},
    ],
)
def test_enabled_llm_rejects_contaminated_external_configuration(
    changes: dict[str, object],
) -> None:
    """LLM 外部端点、模型名和 API Key 必须在启动阶段被收紧。"""
    values = {
        "rca_consumer_enabled": True,
        "prometheus_base_url": "https://prometheus.example.com",
        "loki_base_url": "https://loki.example.com",
        "tempo_base_url": "https://tempo.example.com",
        "llm_report_enabled": True,
        "llm_base_url": "https://api.example.com",
        "llm_model": "rca-model-v1",
        "llm_api_key": SecretStr("private-key"),
    }
    values.update(changes)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    "values",
    [
        {
            "rca_consumer_heartbeat_seconds": 30,
            "rca_consumer_lease_seconds": 30,
        },
        {
            "rca_consumer_heartbeat_seconds": 30,
            "rca_consumer_execution_timeout_seconds": 30,
        },
    ],
)
def test_rca_heartbeat_must_fit_lease_and_execution_windows(
    values: dict[str, int],
) -> None:
    """心跳必须早于租约过期和执行总超时。"""
    with pytest.raises(ValidationError, match="heartbeat"):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    "worker_id",
    [" ", "worker\nforged", "w" * 129],
)
def test_explicit_worker_id_is_not_silently_cleaned(
    worker_id: str,
) -> None:
    """显式 Worker ID 污染日志或超长时必须拒绝。"""
    for field_name in (
        "outbox_worker_id",
        "rca_consumer_worker_id",
        "ticket_submission_consumer_worker_id",
        "audit_retention_worker_id",
        "remediation_reclaim_worker_id",
    ):
        with pytest.raises(ValidationError, match="worker_id"):
            Settings(_env_file=None, **{field_name: worker_id})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("ticket_submission_consumer_poll_timeout_ms", 0),
        ("ticket_submission_consumer_poll_timeout_ms", 60_001),
        ("ticket_submission_consumer_shutdown_timeout_seconds", 0),
        ("ticket_submission_consumer_shutdown_timeout_seconds", 3601),
    ],
)
def test_invalid_ticket_submission_consumer_settings_are_rejected(
    field_name: str,
    value: int,
) -> None:
    """工单提交消费超时和生命周期配置必须有明确边界。"""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field_name: value})


def test_ticketing_http_json_requires_consumer_and_endpoint() -> None:
    """HTTP 工单网关只能随提交 Consumer 一起显式启用。"""
    with pytest.raises(ValidationError, match="consumer"):
        Settings(
            _env_file=None,
            ticketing_http_json_enabled=True,
            ticketing_http_json_endpoint_url=(
                "https://ticketing.example.com/api/tickets"
            ),
        )

    with pytest.raises(ValidationError, match="incomplete"):
        Settings(
            _env_file=None,
            ticket_submission_consumer_enabled=True,
            ticketing_http_json_enabled=True,
        )


def test_enabled_ticket_submission_rejects_dlq_on_source_topic() -> None:
    """工单提交死信也必须进入独立Topic，避免被共享消费者再次读取。"""
    with pytest.raises(
        ValidationError,
        match="ticket_submission_dead_letter_topic",
    ):
        Settings(
            _env_file=None,
            ticket_submission_consumer_enabled=True,
            kafka_topic="devops-agent.events.v1",
            ticket_submission_dead_letter_topic="devops-agent.events.v1",
        )


def test_ticketing_http_json_valid_configuration_masks_token() -> None:
    """启用 HTTP 工单网关时令牌不能出现在配置调试输出中。"""
    settings = Settings(
        _env_file=None,
        ticket_submission_consumer_enabled=True,
        ticketing_http_json_enabled=True,
        ticketing_http_json_endpoint_url=("https://ticketing.example.com/api/tickets"),
        ticketing_http_json_bearer_token=SecretStr("private-ticket-token"),
    )

    assert settings.ticketing_http_json_endpoint_url == (
        "https://ticketing.example.com/api/tickets"
    )
    assert "private-ticket-token" not in repr(settings)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        (
            "ticketing_http_json_endpoint_url",
            "https://user:pass@ticketing.example.com/api",
        ),
        (
            "ticketing_http_json_endpoint_url",
            "https://ticketing.example.com/api?debug=true",
        ),
        (
            "ticketing_http_json_endpoint_url",
            "https://ticketing.example.com/api\nforged",
        ),
        ("ticketing_http_json_endpoint_url", "file:///tmp/ticketing"),
        ("ticketing_http_json_request_timeout_seconds", 0),
        ("ticketing_http_json_request_timeout_seconds", 61),
        ("ticketing_http_json_max_response_bytes", 0),
        ("ticketing_http_json_max_response_bytes", 1024 * 1024 + 1),
    ],
)
def test_invalid_ticketing_http_json_settings_are_rejected(
    field_name: str,
    value: object,
) -> None:
    """外部工单网关地址、超时和响应容量必须有明确边界。"""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field_name: value})


def test_enabled_ticketing_http_json_rejects_invalid_token() -> None:
    """启用时拒绝会污染请求头或日志的 Bearer Token。"""
    for token in ("bad\ntoken", "bad\ttoken"):
        with pytest.raises(ValidationError, match="bearer token"):
            Settings(
                _env_file=None,
                ticket_submission_consumer_enabled=True,
                ticketing_http_json_enabled=True,
                ticketing_http_json_endpoint_url=(
                    "https://ticketing.example.com/api/tickets"
                ),
                ticketing_http_json_bearer_token=SecretStr(token),
            )


def test_vendor_ticketing_defaults_are_disabled() -> None:
    settings = Settings(_env_file=None)

    assert settings.ticketing_jira_enabled is False
    assert settings.ticketing_servicenow_enabled is False
    assert settings.ticketing_jira_issue_type == "Task"
    assert settings.ticketing_servicenow_table == "incident"


def test_vendor_ticketing_valid_configuration_masks_credentials() -> None:
    settings = Settings(
        _env_file=None,
        ticket_submission_consumer_enabled=True,
        ticketing_jira_enabled=True,
        ticketing_jira_base_url="https://example.atlassian.net",
        ticketing_jira_user_email="ops@example.com",
        ticketing_jira_api_token=SecretStr("jira-secret"),
        ticketing_jira_project_key="OPS",
        ticketing_jira_issue_type="Service Request",
        ticketing_servicenow_enabled=True,
        ticketing_servicenow_base_url=("https://example.service-now.com"),
        ticketing_servicenow_username="devops.integration",
        ticketing_servicenow_password=SecretStr("snow-secret"),
    )

    assert settings.ticketing_jira_project_key == "OPS"
    assert settings.ticketing_servicenow_table == "incident"
    assert "jira-secret" not in repr(settings)
    assert "snow-secret" not in repr(settings)


@pytest.mark.parametrize(
    "changes",
    [
        {
            "ticketing_jira_enabled": True,
            "ticketing_jira_base_url": "https://example.atlassian.net",
            "ticketing_jira_user_email": "ops@example.com",
            "ticketing_jira_api_token": SecretStr("jira-secret"),
            "ticketing_jira_project_key": "OPS",
        },
        {
            "ticket_submission_consumer_enabled": True,
            "ticketing_jira_enabled": True,
            "ticketing_jira_base_url": "https://example.atlassian.net",
            "ticketing_jira_user_email": "ops@example.com",
            "ticketing_jira_project_key": "OPS",
        },
        {
            "ticket_submission_consumer_enabled": True,
            "ticketing_servicenow_enabled": True,
            "ticketing_servicenow_base_url": ("https://example.service-now.com"),
            "ticketing_servicenow_username": "devops.integration",
        },
        {
            "ticket_submission_consumer_enabled": True,
            "ticketing_jira_enabled": True,
            "ticketing_jira_base_url": "https://example.atlassian.net/",
            "ticketing_jira_user_email": "ops@example.com",
            "ticketing_jira_api_token": SecretStr("jira-secret"),
            "ticketing_jira_project_key": "OPS",
        },
    ],
)
def test_incomplete_vendor_ticketing_configuration_is_rejected(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, **changes)


def test_production_vendor_ticketing_requires_https() -> None:
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            app_env="production",
            alert_webhook_auth_enabled=True,
            alert_webhook_secret=SecretStr("a" * 32),
            ticket_submission_consumer_enabled=True,
            ticketing_jira_enabled=True,
            ticketing_jira_base_url="http://jira.internal",
            ticketing_jira_user_email="ops@example.com",
            ticketing_jira_api_token=SecretStr("jira-secret"),
            ticketing_jira_project_key="OPS",
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("audit_retention_days", 0),
        ("audit_retention_days", 3651),
        ("audit_retention_batch_size", 0),
        ("audit_retention_batch_size", 1001),
        ("audit_retention_active_interval_seconds", 0),
        ("audit_retention_idle_interval_seconds", 0),
        ("audit_retention_shutdown_timeout_seconds", 0),
    ],
)
def test_invalid_audit_retention_settings_are_rejected(
    field_name: str,
    value: float,
) -> None:
    """删除任务的留存、容量和生命周期参数必须有上界。"""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("rca_consumer_lag_timeout_ms", 9),
        ("rca_consumer_lag_timeout_ms", 5001),
        ("rca_consumer_max_lag_partitions", 0),
        ("rca_consumer_max_lag_partitions", 10_001),
        ("rca_consumer_lag_sample_interval_seconds", 0),
        ("rca_consumer_lag_sample_interval_seconds", 301),
    ],
)
def test_invalid_rca_lag_bounds_are_rejected(
    field_name: str,
    value: int,
) -> None:
    """Lag采集超时和分区容量必须保持有限。"""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field_name: value})


def test_audit_retention_error_backoff_order_is_validated() -> None:
    """异常初始退避不能大于最大退避。"""
    with pytest.raises(ValidationError, match="initial must not exceed"):
        Settings(
            _env_file=None,
            audit_retention_error_backoff_initial_seconds=10,
            audit_retention_error_backoff_max_seconds=5,
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("remediation_reclaim_batch_size", 0),
        ("remediation_reclaim_batch_size", 1001),
        ("remediation_reclaim_interval_seconds", 0),
        ("remediation_reclaim_error_backoff_initial_seconds", 0),
        ("remediation_reclaim_error_backoff_max_seconds", 86401),
        ("remediation_reclaim_shutdown_timeout_seconds", 0),
    ],
)
def test_invalid_remediation_reclaim_settings_are_rejected(
    field_name: str,
    value: float,
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field_name: value})


def test_remediation_reclaim_error_backoff_order_is_validated() -> None:
    with pytest.raises(ValidationError, match="initial must not exceed"):
        Settings(
            _env_file=None,
            remediation_reclaim_error_backoff_initial_seconds=10,
            remediation_reclaim_error_backoff_max_seconds=5,
        )


@pytest.mark.parametrize(
    "missing_field",
    [
        "admin_oidc_issuer",
        "admin_oidc_audience",
        "admin_oidc_jwks_url",
    ],
)
def test_enabled_oidc_requires_complete_trust_configuration(
    missing_field: str,
) -> None:
    """启用OIDC时issuer、audience和JWKS地址缺一不可。"""
    values = {
        "admin_oidc_enabled": True,
        "admin_oidc_issuer": "https://identity.example.com/",
        "admin_oidc_audience": "devops-agent-api",
        "admin_oidc_jwks_url": ("https://identity.example.com/.well-known/jwks.json"),
    }
    values[missing_field] = None

    with pytest.raises(ValidationError, match="incomplete"):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    "changes",
    [
        {"admin_oidc_issuer": "https://identity.example.com/\nforged"},
        {"admin_oidc_issuer": "https://identity.example.com/\x7fforged"},
        {"admin_oidc_issuer": "https://identity.example.com/?debug=true"},
        {"admin_oidc_audience": "devops-agent-api\tforged"},
        {"admin_oidc_audience": "devops-agent-api\x7fforged"},
        {
            "admin_oidc_jwks_url": (
                "https://identity.example.com/.well-known/jwks.json?debug=true"
            )
        },
        {"admin_oidc_subject_claim": "sub\tforged"},
        {"admin_oidc_subject_claim": "sub\x7f"},
        {"admin_oidc_scopes_claim": "scope\x00"},
    ],
)
def test_enabled_oidc_rejects_dirty_trust_configuration(
    changes: dict[str, object],
) -> None:
    """OIDC 信任端点、受众和 Claim 名必须在启动阶段收紧。"""
    values = {
        "admin_oidc_enabled": True,
        "admin_oidc_issuer": "https://identity.example.com/",
        "admin_oidc_audience": "devops-agent-api",
        "admin_oidc_jwks_url": ("https://identity.example.com/.well-known/jwks.json"),
    }
    values.update(changes)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_oidc_algorithm_list_is_trimmed_deduplicated_and_ordered() -> None:
    """逗号配置转换为稳定算法元组。"""
    settings = Settings(
        _env_file=None,
        admin_oidc_algorithms=" RS256, ES256,RS256 ",
    )

    assert settings.admin_oidc_allowed_algorithms == (
        "RS256",
        "ES256",
    )


def test_notification_credentials_are_optional_and_remain_secret() -> None:
    settings = Settings(
        _env_file=None,
        notification_slack_webhook_url=("https://hooks.slack.test/services/secret"),
        notification_teams_webhook_url=("https://teams.test/webhook/secret"),
        notification_pagerduty_routing_key="pager-secret-key",
    )

    assert isinstance(settings.notification_slack_webhook_url, SecretStr)
    assert isinstance(settings.notification_teams_webhook_url, SecretStr)
    assert isinstance(
        settings.notification_pagerduty_routing_key,
        SecretStr,
    )
    assert "pager-secret-key" not in repr(settings)


@pytest.mark.parametrize(
    "changes",
    [
        {"notification_slack_webhook_url": ("ftp://hooks.slack.test/services/secret")},
        {
            "notification_teams_webhook_url": (
                "https://user:password@teams.test/webhook"
            )
        },
        {
            "notification_pagerduty_routing_key": " dirty-key ",
        },
    ],
)
def test_notification_configuration_rejects_dirty_credentials(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **changes)


def test_production_notification_webhooks_require_https() -> None:
    with pytest.raises(
        ValidationError,
        match="notification_slack_webhook_url",
    ):
        Settings(
            _env_file=None,
            app_env="production",
            alert_webhook_auth_enabled=True,
            alert_webhook_secret=SecretStr("a" * 32),
            admin_oidc_enabled=True,
            admin_oidc_issuer="https://identity.example.com/",
            admin_oidc_audience="devops-agent-api",
            admin_oidc_jwks_url=("https://identity.example.com/.well-known/jwks.json"),
            notification_slack_webhook_url=("http://hooks.slack.test/services/secret"),
        )


def test_remediation_defaults_to_disabled_and_requires_action_catalog() -> None:
    defaults = Settings(_env_file=None)
    assert defaults.remediation_execution_enabled is False
    assert defaults.remediation_controller_base_url is None
    assert defaults.remediation_action_catalog_path is None
    assert defaults.remediation_lease_seconds == 60
    assert defaults.remediation_request_timeout_seconds == 10
    assert defaults.remediation_reclaim_worker_enabled is False
    assert defaults.remediation_reclaim_batch_size == 50
    assert defaults.remediation_reclaim_interval_seconds == 30

    with pytest.raises(ValidationError, match="configured together"):
        Settings(
            _env_file=None,
            remediation_controller_base_url="https://automation.example",
        )

    with pytest.raises(ValidationError, match="reclaim worker requires"):
        Settings(
            _env_file=None,
            remediation_reclaim_worker_enabled=True,
        )


def test_remediation_request_timeout_must_be_shorter_than_lease() -> None:
    with pytest.raises(ValidationError, match="shorter than"):
        Settings(
            _env_file=None,
            remediation_controller_base_url="https://automation.example",
            remediation_action_catalog_path="ops/remediation/actions.example.json",
            remediation_request_timeout_seconds=60,
            remediation_lease_seconds=30,
        )


def test_remediation_execution_requires_tenant_allowlist() -> None:
    with pytest.raises(ValidationError, match="allowed tenants"):
        Settings(
            _env_file=None,
            remediation_controller_base_url="https://automation.example",
            remediation_action_catalog_path="ops/remediation/actions.example.json",
            remediation_execution_enabled=True,
        )


@pytest.mark.parametrize(
    "base_url",
    [
        "https://automation.example/path",
        "https://automation.example/",
        "https://user:secret@automation.example",
    ],
)
def test_remediation_controller_rejects_dynamic_or_credentialed_urls(
    base_url: str,
) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            remediation_controller_base_url=base_url,
            remediation_action_catalog_path="ops/remediation/actions.example.json",
        )
