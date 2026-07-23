# Step 6：整合、运维适配与后续演进

## 1. 本地演示流程

1. 启动 MiniShop。
2. 正常调用 `/checkout`，确认链路健康。
3. 开启一个故障，例如 `/faults/inventory-db-timeout`。
4. 持续请求 `/checkout`。
5. 查看 `/metrics` 中延迟和错误指标。
6. 使用 `scripts/send_agent_alert.py` 发送 Agent 告警样例。
7. 在 DevOps Agent 平台中观察 Incident 和 RCA 流程。

一条命令联立 MiniShop 和 DevOps Agent：

```powershell
python .\scripts\demo_minishop_to_agent.py --agent-url "http://127.0.0.1:8000/api/v1/alerts"
```

这个脚本会先给 MiniShop 注入 `inventory_db_timeout`，再请求 `/checkout` 复现失败，最后把兼容 DevOps Agent 的告警 payload 发送到 `POST /api/v1/alerts`。

## 2. 运维观测入口

| 入口 | 用途 |
|---|---|
| `/healthz` | 存活检查 |
| `/metrics` | Prometheus 指标采集 |
| JSON 日志 | 故障排查和 trace_id 串联 |
| `runbooks/` | Agent 知识检索 |
| `prometheus/alert_rules.yml` | 告警规则 |

## 3. 当前方案优点

1. 不依赖容器和数据库，启动成本低。
2. 故障可动态打开和关闭。
3. 指标、日志、Runbook 与 Agent 接入字段完整。
4. 后续可以自然拆分成多服务。

## 4. 当前不足

1. 故障状态是内存态，服务重启后会丢失。
2. v1 没有真实 Loki / Tempo。
3. v1 没有真实 Alertmanager webhook 转发验收。
4. v1 没有真实数据库慢查询，只是模拟数据库超时。

## 5. 后续演进

v1.1 接 Prometheus + Alertmanager；v1.2 接 Loki / Tempo / Grafana；v2 拆成多服务；v3 让 Agent 基于真实观测系统完成 RCA。
