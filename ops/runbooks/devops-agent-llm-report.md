# DevOps Agent LLM Report Runbook

## Fallback Ratio High

**信号**：`DevOpsAgentLLMReportFallbackRatioHigh`

1. 按固定 outcome 对比超时、无效响应、供应商错误和熔断跳过的速率。
2. 检查模型服务状态、网络出口、配额、限流和认证配置。
3. 若主要是无效响应，核对模型是否支持当前严格 JSON Schema 和 Prompt 版本。
4. 若主要是超时，先确认供应商延迟，再评估超时阈值；不要直接无限放大超时。
5. 确认确定性报告仍正常生成，禁止为了降低告警而关闭响应校验。

## Circuit Open

**信号**：`DevOpsAgentLLMReportCircuitOpen`

1. 检查熔断前的最后一类失败及其发生时间，不要读取或记录模型响应正文。
2. 确认实例是否都在熔断；当前熔断状态是进程级，不代表整个集群状态。
3. 检查供应商恢复后半开探针是否成功，成功后熔断器应自动闭合。
4. 持续失败时保持确定性降级，不要通过重启风暴绕过恢复窗口。
5. 恢复后确认 `SUCCESS` 增长、降级比例回落且没有新的熔断跳过。
