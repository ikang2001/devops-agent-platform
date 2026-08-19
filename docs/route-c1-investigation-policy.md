# 路线 C1：可配置固定调查策略

## 一句话

RCA Consumer 可以在启动时选择服务端发布的固定只读计划变体，或选择受后端预算约束的
`bounded_dynamic_v1`；LLM 不能自选工具、增加步骤或改变工具参数。

## 运行时配置

```text
DEVOPS_AGENT_RCA_INVESTIGATION_POLICY=fixed_default
```

| 策略 | 固定步骤 | 资源要求 |
| --- | --- | --- |
| `fixed_default` | metrics → logs → traces → runbooks | Prometheus、Loki、Tempo |
| `fixed_no_traces` | metrics → logs → runbooks | Prometheus、Loki |
| `fixed_metrics_logs_runbooks` | metrics → logs → runbooks | Prometheus、Loki |
| `bounded_dynamic_v1` | Planner 在七个只读工具中按后端规则选择下一步 | Prometheus、Loki、Tempo、数据库中的权限/拓扑/知识 |

后两个计划步骤同形，但保留独立 `plan_id`，用于审计部署方选择该计划的意图。
默认策略继续使用既有 `default.observability-rca` / `v2`，保持历史执行身份兼容。

## 已实现语义

1. `Settings` 只接受上述四个键；未知值和 `auto` 在启动前失败。
2. Runtime 通过 `build_plan_for_policy()` 构造不可变 `RCAWorkflowPlan`。
3. 只有计划包含 `traces.query` 时才要求、创建并关闭 Tempo 客户端和工具处理器。
4. 每个步骤仍经过工具版本、风险、权限、超时、输入输出和证据净化边界。
5. 策略由部署配置决定，单次 Incident 或 LLM 响应不能覆盖。
6. 动态策略的步骤、总时长、单工具调用、Evidence 和 LLM 预算由 Settings 注入，不能由模型自行声明。

## 明确边界

**可以说：**

- 平台支持三种可审计的静态调查计划和一套有界动态调查；
- 无 Tempo 的环境可使用无 Trace 固定计划；
- 未知策略失败关闭，不会悄悄回落到默认计划。

**不可以说：**

- 这是自适应多轮 Agent；
- 动态策略会在后端 allowlist 和预算内根据中间状态选择下一工具，但不是自由 ReAct；
- 动态策略或四种计划已在真实生产环境完成效果对比。

## 验证

- `tests/unit/agent/test_investigation_policy.py`：计划身份、步骤形状、非法策略。
- `tests/unit/bootstrap/test_settings.py`：默认值、严格配置校验、无 Trace 时 Tempo 可省略。
- `tests/unit/bootstrap/test_rca_runtime.py`：按计划创建工具和网络资源。

总计划见 [`remediation-roadmap-master-plan.md`](remediation-roadmap-master-plan.md)，
完成度边界见 [`../缺少内容.md`](../缺少内容.md)。
