import json
import re
from pathlib import Path
from typing import Any

import yaml

from devops_agent_platform.infrastructure.metrics import ApplicationMetrics

ROOT = Path(__file__).resolve().parents[3]
PROMETHEUS_DIR = ROOT / "ops" / "prometheus"
GRAFANA_DIR = ROOT / "ops" / "grafana"
ALERTMANAGER_DIR = ROOT / "ops" / "alertmanager"
RUNBOOK_DIR = ROOT / "ops" / "runbooks"
RECORDING_RULES = PROMETHEUS_DIR / "rules" / "devops-agent-recording.yml"
ALERT_RULES = PROMETHEUS_DIR / "rules" / "devops-agent-alerts.yml"
DASHBOARD = GRAFANA_DIR / "dashboards" / "devops-agent-operations.json"
CUSTOM_METRIC_PATTERN = re.compile(r"\bdevops_agent[_:][a-zA-Z0-9_:]+\b")
FORBIDDEN_LABELS = {
    "trace_id",
    "tenant_id",
    "incident_id",
    "event_id",
    "target_system",
}


def load_yaml(path: Path) -> dict[str, Any]:
    """读取YAML并要求根节点为对象。"""
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict), path
    return value


def all_rules(document: dict[str, Any]) -> list[dict[str, Any]]:
    """展开所有规则组，便于执行统一契约检查。"""
    return [
        rule
        for group in document["groups"]
        for rule in group["rules"]
    ]


def dashboard_expressions(dashboard: dict[str, Any]) -> list[str]:
    """提取Dashboard全部PromQL表达式。"""
    return [
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
        if "expr" in target
    ]


def exported_metric_names() -> set[str]:
    """从真实Registry读取应用能够暴露的sample名称。"""
    metrics = ApplicationMetrics()
    method = metrics.start_http_request("GET")
    metrics.finish_http_request(
        method=method,
        route="/healthz",
        status_code=200,
        duration_seconds=0.01,
    )
    return {
        sample.name
        for metric in metrics.registry.collect()
        for sample in metric.samples
    }


def test_prometheus_rule_files_have_stable_contracts() -> None:
    recording_document = load_yaml(RECORDING_RULES)
    alert_document = load_yaml(ALERT_RULES)
    recording_rules = all_rules(recording_document)
    alert_rules = all_rules(alert_document)

    recording_names = [rule["record"] for rule in recording_rules]
    alert_names = [rule["alert"] for rule in alert_rules]
    assert len(recording_names) == len(set(recording_names))
    assert len(alert_names) == len(set(alert_names))
    assert all(rule.get("for") for rule in alert_rules)
    assert all(
        rule["labels"]["severity"] in {"warning", "critical"}
        for rule in alert_rules
    )
    assert all(rule["labels"]["team"] == "platform" for rule in alert_rules)
    assert all(
        rule["labels"]["service"] == "devops-agent-platform"
        for rule in alert_rules
    )
    assert all(
        rule.get("annotations", {}).get("summary")
        and rule.get("annotations", {}).get("description")
        for rule in alert_rules
    )


def test_promql_only_references_exported_or_recorded_custom_metrics() -> None:
    recording_rules = all_rules(load_yaml(RECORDING_RULES))
    alert_rules = all_rules(load_yaml(ALERT_RULES))
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    recording_names = {rule["record"] for rule in recording_rules}
    expressions = [
        *(str(rule["expr"]) for rule in recording_rules),
        *(str(rule["expr"]) for rule in alert_rules),
        *dashboard_expressions(dashboard),
    ]
    referenced_names = {
        metric_name
        for expression in expressions
        for metric_name in CUSTOM_METRIC_PATTERN.findall(expression)
    }

    assert referenced_names <= exported_metric_names() | recording_names
    assert not any(
        forbidden in expression
        for expression in expressions
        for forbidden in FORBIDDEN_LABELS
    )


def test_dashboard_has_unique_ids_and_provisioned_datasource() -> None:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    datasource = load_yaml(
        GRAFANA_DIR / "provisioning" / "datasources" / "prometheus.yml"
    )
    provider = load_yaml(
        GRAFANA_DIR / "provisioning" / "dashboards" / "devops-agent.yml"
    )

    assert dashboard["uid"] == "devops-agent-operations"
    assert dashboard["title"] == "DevOps Agent Operations"
    assert dashboard["refresh"] == "30s"
    panel_ids = [panel["id"] for panel in dashboard["panels"]]
    assert len(panel_ids) == len(set(panel_ids))
    assert len(panel_ids) >= 10
    assert all(
        panel["datasource"]["uid"] == "prometheus"
        for panel in dashboard["panels"]
    )
    assert datasource["datasources"][0]["uid"] == "prometheus"
    assert datasource["datasources"][0]["editable"] is False
    assert provider["providers"][0]["allowUiUpdates"] is False
    assert provider["providers"][0]["updateIntervalSeconds"] > 10


def test_dashboard_rca_ticketing_panels_are_present_and_do_not_overlap() -> None:
    """RCA、工单和LLM面板必须完整，并保持稳定24列网格布局。"""
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    panels = dashboard["panels"]
    titles = {panel["title"] for panel in panels}
    assert {
        "审计清理Worker启用",
        "审计清理运行态",
        "审计清理连续失败",
        "审计清理最近成功距今",
        "审计清理累计批次",
        "审计清理累计工作流",
        "审计清理周期趋势",
        "审计清理记录趋势",
        "RCA Consumer启用",
        "RCA Consumer运行态",
        "RCA连续失败",
        "RCA最近成功距今",
        "RCA Consumer处理速率",
        "RCA Consumer状态趋势",
        "RCA Consumer Lag趋势",
        "RCA Lag采集覆盖",
        "RCA死信增长",
        "RCA重试速率",
        "工单提交Consumer启用",
        "工单提交运行态",
        "工单提交连续失败",
        "工单提交最近成功距今",
        "工单提交死信增长",
        "工单提交重试速率",
        "工单提交处理速率",
        "工单提交状态趋势",
        "工单提交Consumer Lag趋势",
        "工单提交Lag采集覆盖",
        "工单网关错误比例",
        "工单网关业务失败比例",
        "工单网关P95耗时",
        "工单网关调用速率",
        "工单网关结果速率",
        "工单网关耗时分位数",
        "LLM报告降级比例",
        "LLM报告P95耗时",
        "LLM报告结果速率",
        "LLM报告耗时分位数",
    } <= titles
    assert dashboard["version"] >= 7
    assert {"rca", "ticketing", "llm", "retention"} <= set(
        dashboard["tags"]
    )

    rectangles = []
    for panel in panels:
        position = panel["gridPos"]
        assert position["w"] > 0
        assert position["h"] > 0
        assert 0 <= position["x"] < 24
        assert position["x"] + position["w"] <= 24
        assert position["y"] >= 0
        rectangles.append(
            (
                panel["id"],
                position["x"],
                position["y"],
                position["x"] + position["w"],
                position["y"] + position["h"],
            )
        )

    for index, first in enumerate(rectangles):
        for second in rectangles[index + 1 :]:
            separated = (
                first[3] <= second[1]
                or second[3] <= first[1]
                or first[4] <= second[2]
                or second[4] <= first[2]
            )
            assert separated, (
                f"dashboard panels overlap: {first[0]} and {second[0]}"
            )


def test_prometheus_example_loads_all_versioned_rule_files() -> None:
    config = load_yaml(PROMETHEUS_DIR / "prometheus.example.yml")

    assert config["rule_files"] == [
        "/etc/prometheus/rules/devops-agent-recording.yml",
        "/etc/prometheus/rules/devops-agent-alerts.yml",
    ]
    scrape_config = config["scrape_configs"][0]
    assert scrape_config["metrics_path"] == "/metrics"
    assert scrape_config["job_name"] == "devops-agent-platform"
    assert scrape_config["static_configs"][0]["targets"]
    assert config["alerting"]["alertmanagers"][0]["static_configs"][0][
        "targets"
    ] == ["alertmanager:9093"]


def test_alertmanager_routes_reference_hardened_receivers() -> None:
    config = load_yaml(ALERTMANAGER_DIR / "alertmanager.example.yml")
    receivers = config["receivers"]
    receiver_names = [receiver["name"] for receiver in receivers]
    child_routes = config["route"]["routes"]

    assert len(receiver_names) == len(set(receiver_names))
    assert config["route"]["receiver"] in receiver_names
    assert config["route"]["group_by"] == ["alertname", "service", "job"]
    assert all(route["receiver"] in receiver_names for route in child_routes)
    assert {
        matcher
        for route in child_routes
        for matcher in route["matchers"]
        if matcher.startswith("severity=")
    } == {'severity="critical"', 'severity="warning"'}

    for receiver in receivers:
        slack_config = receiver["slack_configs"][0]
        assert "api_url" not in slack_config
        assert slack_config["api_url_file"].startswith("/run/secrets/")
        assert slack_config["send_resolved"] is True
        assert slack_config["timeout"] == "10s"
        assert slack_config["title"] == '{{ template "devops.title" . }}'
        assert slack_config["text"] == '{{ template "devops.text" . }}'


def test_multichannel_alertmanager_fans_out_critical_with_file_secrets() -> None:
    config = load_yaml(
        ALERTMANAGER_DIR / "alertmanager.multichannel.example.yml"
    )
    routes = config["route"]["routes"]
    critical_routes = [
        route
        for route in routes
        if 'severity="critical"' in route["matchers"]
    ]
    receivers = {
        receiver["name"]: receiver
        for receiver in config["receivers"]
    }

    assert [route["receiver"] for route in critical_routes] == [
        "platform-critical-slack",
        "platform-critical-teams",
        "platform-critical-pagerduty",
    ]
    assert [route.get("continue", False) for route in critical_routes] == [
        True,
        True,
        False,
    ]

    teams = receivers["platform-critical-teams"]["msteamsv2_configs"][0]
    assert teams["webhook_url_file"] == (
        "/run/secrets/alertmanager-teams-critical-url"
    )
    assert "webhook_url" not in teams
    assert teams["send_resolved"] is True

    pagerduty = receivers["platform-critical-pagerduty"][
        "pagerduty_configs"
    ][0]
    assert pagerduty["routing_key_file"] == (
        "/run/secrets/alertmanager-pagerduty-routing-key"
    )
    assert "routing_key" not in pagerduty
    assert pagerduty["send_resolved"] is True
    assert set(pagerduty["details"]) == {
        "service",
        "job",
        "firing",
        "resolved",
    }


def test_inhibition_rules_are_explicit_and_scoped() -> None:
    config = load_yaml(ALERTMANAGER_DIR / "alertmanager.example.yml")

    for rule in config["inhibit_rules"]:
        assert rule["equal"] == ["job", "service"]
        assert any(
            matcher.startswith("alertname=")
            for matcher in rule["source_matchers"]
        )
        assert 'severity="critical"' not in rule["source_matchers"]
        assert rule["target_matchers"]


def test_every_alert_references_an_existing_runbook_anchor() -> None:
    alert_rules = all_rules(load_yaml(ALERT_RULES))

    for rule in alert_rules:
        runbook_reference = rule["annotations"]["runbook"]
        relative_path, anchor = runbook_reference.split("#", maxsplit=1)
        runbook_path = ROOT / relative_path
        assert runbook_path.is_file(), rule["alert"]
        heading = anchor.replace("-", " ").casefold()
        headings = {
            line.removeprefix("## ").strip().casefold()
            for line in runbook_path.read_text(encoding="utf-8").splitlines()
            if line.startswith("## ")
        }
        assert heading in headings, rule["alert"]


def test_ticket_submission_dead_letter_runbook_documents_reason_codes() -> None:
    """工单提交死信 Runbook 必须覆盖不可自愈坏消息的处置入口。"""
    text = (
        RUNBOOK_DIR / "devops-agent-ticket-submission-consumer.md"
    ).read_text(encoding="utf-8")

    assert {
        "INVALID_MESSAGE_SIZE",
        "INVALID_JSON",
        "MESSAGE_CONTRACT_INVALID",
        "TICKET_SUBMISSION_NOT_FOUND",
    } <= set(re.findall(r"`([A-Z_]+)`", text))
    assert "source_topic/source_partition/source_offset" in text
    assert "dead_letter_id" in text
    assert "回放前检查清单" in text


def test_rca_dead_letter_runbook_documents_reason_codes() -> None:
    """RCA死信 Runbook 必须覆盖契约坏消息和缺失工作流处置入口。"""
    text = (RUNBOOK_DIR / "devops-agent-rca-consumer.md").read_text(
        encoding="utf-8"
    )

    assert {
        "INVALID_VALUE_TYPE",
        "INVALID_MESSAGE_SIZE",
        "INVALID_JSON",
        "MESSAGE_CONTRACT_INVALID",
        "WORKFLOW_NOT_FOUND",
    } <= set(re.findall(r"`([A-Z_]+)`", text))
    assert "source_topic/source_partition/source_offset" in text
    assert "dead_letter_id" in text
    assert "回放前检查清单" in text
    assert "CANCELED" in text


def test_notification_template_does_not_dump_arbitrary_labels() -> None:
    template = (
        ALERTMANAGER_DIR / "templates" / "devops-agent.tmpl"
    ).read_text(encoding="utf-8")

    assert 'define "devops.title"' in template
    assert 'define "devops.text"' in template
    assert 'define "devops.color"' in template
    assert ".CommonLabels.SortedPairs" not in template
    assert "range .CommonLabels" not in template
    assert ".Annotations.runbook" in template


def test_alertmanager_secret_directory_is_ignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "ops/alertmanager/secrets/" in gitignore
