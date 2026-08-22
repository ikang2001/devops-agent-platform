# Dependency timeout investigation

先用时间窗口内的 Metric、Log、Trace 和 Topology 交叉确认依赖是否超时，再判断
调用方是否只是下游受影响服务。没有结构化资源或直接依赖证据时，保持候选或
`UNDETERMINED`，不要凭固定数据库名称补全资源。

只读调查顺序：确认请求失败范围、定位超时跨度、对齐上游 Span、核对依赖地址，
最后检查近期 Change 是否只是相关性。禁止把历史知识单独升级为当前根因。
