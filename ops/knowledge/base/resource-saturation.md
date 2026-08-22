# Resource saturation investigation

资源耗尽、队列积压、连接池压力和 GC 停顿必须由当前窗口的 Metric 与 Log 支持，
再结合 Trace 或 Topology 确认受影响服务。未知资源系统按运行时结构化属性记录，
不能把资源类型硬编码为 PostgreSQL、Redis 或某个固定服务名。

证据缺失时降低置信度；不要把单个健康服务告警、旧错误或低优先级 Change 当作根因。
