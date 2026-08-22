# 独立 Runbook 基线

这里的 Runbook 生命周期独立于 Scenario Private Ground Truth。它们只描述调查
意图、证据优先级和安全边界，不包含某次 Benchmark 的根因、答案字段、必然因果链
或场景专属 Evidence ID。

正式 Benchmark 固定记录 `runbook_version` 与 `runbook_hash`；Runtime 只能读取已
发布的基线 Runbook，不能根据当前 Scenario 动态生成答案。
