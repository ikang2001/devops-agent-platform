# Topology 与 Blast Radius

平台把静态声明和 Trace 派生关系统一为 `ServiceNode`、`ResourceNode` 与 `DependencyEdge`。每个节点/边都携带 tenant、来源、置信度、观察时间和 TTL；查询时先做租户过滤，再按最大深度遍历。

`TopologyService.register_dependency` 会拒绝未知节点和环；同一租户内以 `(source, target, relation)` 去重。`BlastRadiusService` 使用确定性 BFS 生成直接/间接影响服务、因果边和置信度分数，因此相同输入不会依赖 LLM 产生不同影响面。

当前已实现：领域模型、内存适配器、带租户/TTL 过滤的 SQLAlchemy 适配器、Alembic migration、
`topology.query@v1`、RCA context assembler、RCA runtime 工具注册和仓储测试。真实
CMDB/Tempo 连接仍需外部环境凭据。
