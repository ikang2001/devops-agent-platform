# 路线 C0：只读采集部分失败降级

## 一句话

RCA 固定计划默认仍 fail-fast；显式开启后，单个只读工具失败会留下 `FAILED`
调用审计并继续后续步骤。最终有证据才生成带部分采集标记的低置信度报告，零证据仍整体失败。

## 运行时配置

```text
DEVOPS_AGENT_RCA_CONTINUE_ON_STEP_FAILURE=false
```

默认值是 `false`，因此升级后不改变原有失败语义。开关在 `Settings` 中读取，并由
`bootstrap/rca_runtime.py` 注入 `ControlledAgentWorkflowConfig`。

## 已实现语义

| 场景 | 结果 |
| --- | --- |
| 开关关闭，任一步失败 | 立即抛 `AgentWorkflowExecutionFailure` |
| 开关开启，一步失败、后续有证据 | 返回报告；摘要列出失败步骤 |
| 开关开启，全部失败或零证据 | 仍抛失败，不生成空报告 |
| 外层取消 | 原样传播 `CancelledError`，不包装成普通工具失败 |

## 置信度护栏

工作流只会下调报告生成器给出的分数：

```text
最终 confidence = min(生成器 confidence, 证据类型覆盖率, 常规上限)
部分采集时再 min(部分报告上限 0.4)
```

- 常规上限默认 `1.0`，部分报告上限默认 `0.4`；二者属于服务端工作流配置。
- 确定性生成器不推断根因，因此即使完整采集仍保持 `confidence=0.0`。
- `CONFIRMED` 会在工作流边界降为 `UNDETERMINED`；只有人工复核链路可以确认根因。
- 摘要接近 4096 字符上限时优先截断原摘要，保留 `Partial collection` 失败步骤标记。

## 明确边界

**可以说：**

- 只读采集支持默认关闭的部分失败降级；
- 失败步骤、成功证据和最终报告可以一起审计；
- 缺证据会压低候选置信度，零证据不会生成报告。

**不可以说：**

- 平台会自动判断缺失证据不重要；
- 部分报告等于已确认根因；
- 工具失败会自动重试或切换数据源。

## 验证

`tests/unit/agent/test_controlled_workflow.py` 覆盖默认 fail-fast、部分成功、全失败、
置信度上限和 `CONFIRMED` 降级；Runtime 与 Settings 接线由对应 bootstrap 测试覆盖。

总计划见 [`remediation-roadmap-master-plan.md`](remediation-roadmap-master-plan.md)，
完成度边界见 [`../缺少内容.md`](../缺少内容.md)。
