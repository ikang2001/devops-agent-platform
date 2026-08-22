# v0.7 独立 Knowledge 基线

这些知识条目与 Scenario Ground Truth 独立维护。它们描述调查方法、证据优先级
和停止条件，不包含某个场景的根因、答案型 Evidence ID 或禁止声明清单。

正式 Benchmark 运行时应记录本目录的 SHA-256 作为 `knowledge_hash`；修改知识后，
应创建新的知识版本并重新运行 Known/Hidden 评测，不能覆盖旧结果。
