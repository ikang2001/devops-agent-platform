# DevOps Agent v0.7.0 最终优化任务书
## 黑盒 E2E 泛化评测 + Benchmark Integrity + 面试收口

> 仓库：`https://github.com/ikang2001/devops-agent-platform`
>
> 当前基线：`v0.6.0`
>
> 本文用途：作为 v0.7.0 最终开发任务书，可直接交给 Codex / Claude Code / Cursor 等编程 Agent 执行。
>
> 核心目标：**不再针对已知 12 个 MiniShop 场景继续刷分，而是证明当前 RCA 系统面对没见过的服务、资源、错误表达、噪声、缺失证据和拓扑变化时，仍然具备稳定的故障诊断能力。**

---

# 0. 项目演进主线

本项目的完整演进逻辑必须始终保持如下主线：

```text
短期实习
↓
接触真实 AIOps 故障诊断问题
↓
发现运维 Agent 不能只靠自由 ReAct
↓
回来独立复现生产导向 AIOps 架构
↓
补齐 Outbox / Kafka / Lease / Fence
解决异步 Agent 长任务可靠性
↓
补齐 Dynamic RCA / Topology / Knowledge
解决“查什么、怎么查”
↓
搭建云原生故障演练与评测环境
↓
接入真实千问 Benchmark
↓
发现 Evidence 已经够好
但 Root Cause 判断不准
↓
补 Root Cause Reasoning
Taxonomy / Candidate / Support-Contradiction / Causal Guard
↓
已知 12 个场景达到较高准确率
↓
进一步发现：
“已知场景 100% ≠ 真实泛化能力”
↓
v0.7.0
Black-box E2E + Hidden Holdout
↓
验证未知故障泛化能力
```

v0.7.0 的存在目的不是新增一个孤立模块。

它是整个项目自然演进出来的下一阶段：

> **从“把已知题做对”升级为“证明系统真的会排障”。**

---

# 1. v0.6.0 当前状态

当前 main 分支已进入：

```text
v0.6.0
```

主链：

```text
Alert
→ Alert Correlation
→ Incident
→ Transactional Outbox
→ Kafka / Redpanda
→ RCA Worker
→ Bounded Dynamic Investigation
→ Metrics / Change / Logs / Traces
→ Topology / Knowledge / Runbook
→ Evidence
→ Root Cause Reasoning
→ RCA Report
```

当前已有：

```text
Root Cause Taxonomy
Evidence Normalizer
Root Cause Candidate
Candidate Scoring
Support / Contradiction
Causal Chain
Blast Radius
Confidence Guard
```

工程能力已有：

```text
PostgreSQL
Kafka
OIDC/JWKS
Prometheus
Loki
Tempo
Transactional Outbox
Claim / Lease / Heartbeat / Fence
Checkpoint / Resume
Load / Chaos
真实千问 API
12 Scenario Benchmark
```

当前项目 README 记录：

```text
12 Scenario × 5 = 60
RCA Top-1 = 100%
Strict RCA = 100%
Evidence = 100%
Causal Chain = 100%
Blast Radius = 100%
Tool Selection = 100%
```

因此：

> 当前最大问题已经不是“Known Benchmark 准确率不够”。

而是：

> **“这 100% 是否依赖当前 12 个场景、固定服务名、固定资源类型、答案型 Evidence 和针对性规则？”**

---

# 2. v0.7.0 核心问题定义

本轮只回答一个问题：

# 当前 RCA 系统到底是真的会推理，还是只是很好地拟合了 MiniShop Benchmark？

必须重点验证是否依赖：

```text
MiniShop 固定服务名
固定 Topology
固定 Root Resource
固定错误关键词
Scenario expected_signals
Ground Truth 衍生 Runbook
已知 Taxonomy alias
已知 Resource hardcode
干净、完整、无噪声 Evidence
单 Incident 场景
```

---

# 3. 当前源码中的具体风险点

本轮优化必须明确针对以下源码事实，不允许泛泛而谈。

---

## 3.1 风险一：真实千问 Benchmark 仍使用 Scenario expected_signals 构造输入

当前：

```text
ops/simulation/run_simulation.py
```

调用：

```text
build_reference_suite(SCENARIO_ROOT, simulation=True)
```

而：

```text
build_reference_suite()
```

读取：

```text
Scenario Manifest
→ expected_signals[]
→ assertion
→ Evidence Summary
→ LLM
```

这说明当前 12×5 Benchmark 的模型输入并非全部来自：

```text
Agent Runtime 实时调用 Prometheus / Loki / Tempo
```

而是来自：

```text
Scenario 作者预先写好的运行时信号摘要
```

---

## 3.2 改造前

```text
Manifest expected_signals.assertion
↓
Benchmark Evidence
↓
LLM
```

## 3.3 改造后

```text
Fault Injection
↓
Business Request
↓
Prometheus / Loki / Tempo
↓
ToolRegistry
↓
ToolInvocation
↓
Evidence Store
↓
LLM
```

## 3.4 验收

最终 Generalization Benchmark 中：

```text
build_reference_suite()
```

禁止参与真实 RCA 输入。

它只能保留在：

```text
Contract Test
Deterministic Scorer Test
```

---

# 4. 风险二：Evidence 中存在非常强的答案型提示

例如当前 Scenario 可能直接出现：

```text
DB_TIMEOUT
db_timeout
inventory-service database timeout
redis_latency
pool_exhaustion
```

这不等于 Ground Truth 直接泄漏。

但它属于：

> **Strong Scenario Hint**

真实线上错误更可能是：

```text
storage operation exceeded deadline
unable to acquire connection
upstream request expired
cache lookup exceeded threshold
```

---

# 5. 改造要求：Explicit / Opaque 两类信号

每个重要 Root Cause 至少提供：

```text
Explicit Signal
Opaque Signal
```

例如：

## Timeout Explicit

```text
DB_TIMEOUT
```

## Timeout Opaque

```text
storage operation exceeded deadline
```

---

# 6. 验收

新增指标：

```text
Error Paraphrase Accuracy
Opaque Signal Accuracy
```

如果：

```text
Explicit = 95%
Opaque = 50%
```

说明系统仍然依赖答案型关键词。

---

# 7. 风险三：Normalizer 存在 MiniShop 专属 Resource Hardcode

当前存在类似：

```text
DEPENDENCY_TIMEOUT
→ provider ? payment-provider : postgres

DEPENDENCY_LATENCY
→ redis

RESOURCE_EXHAUSTION
→ <service>-db-pool
```

这对当前 Benchmark 非常友好。

但对于：

```text
mysql
mongodb
rabbitmq
elasticsearch
memcached
s3
dns
service mesh
```

没有泛化证明。

---

# 8. 改造前

```text
RootCauseType
↓
Hardcoded Resource
```

例如：

```text
timeout
→ postgres
```

---

# 9. 改造后

新增：

```text
EvidenceEntityExtractor
```

优先从结构化属性提取：

```text
service.name
db.system
server.address
peer.service
rpc.service
messaging.system
deployment.version
cloud.availability_zone
error.type
```

例如：

```json
{
  "service.name": "order-api",
  "db.system": "mysql",
  "server.address": "mysql-primary",
  "error.type": "deadline_exceeded"
}
```

推导：

```text
root_service = order-api
root_resource = mysql-primary
root_type = dependency_timeout
```

文本关键词只作为 fallback。

---

# 10. 风险四：当前 E2E Runbook 存在 Ground Truth 衍生

当前 E2E 会使用：

```text
scenario.ground_truth.root_cause.summary
scenario.ground_truth.root_cause.causal_chain
```

动态发布 Runbook。

这适合：

```text
验证 Runbook API
```

但不适合证明：

```text
RCA 泛化
```

因为等于：

```text
正确答案
↓
生成 Runbook
↓
Agent 检索
↓
判断正确答案
```

---

# 11. 改造要求：Knowledge / Runbook 与 Benchmark 解耦

建立独立：

```text
runbooks/base/
knowledge/base/
```

生命周期独立于：

```text
Scenario Ground Truth
```

每次正式 Benchmark 固定：

```text
knowledge_version
knowledge_hash
runbook_version
runbook_hash
```

---

# 12. Phase 1：建立 Benchmark 三层体系

最终 Benchmark 必须拆成三层。

---

## Level 1：Contract Benchmark

用途：

```text
Schema
Scorer
Prompt Contract
Runtime Adapter
```

允许：

```text
build_reference_suite()
```

特点：

```text
快
可重复
不代表模型泛化
```

---

## Level 2：Known Black-box E2E

当前 12 个已知 Scenario：

```text
真实故障注入
+
真实业务请求
+
真实 Alert
+
真实 Incident
+
真实 RCA Worker
+
真实 Prometheus / Loki / Tempo
+
真实 ToolInvocation
+
真实千问
```

用途：

```text
回归测试
```

---

## Level 3：Hidden Generalization Benchmark

新增：

```text
8~12 个未知场景
```

特点：

```text
不参与开发调参
Public / Private 分离
Black-box E2E
Noise
Missing Evidence
Topology Shift
Unknown Resource
```

最终简历优先引用这一层结果。

---

# 13. Phase 2：Scenario Public / Private 拆分

当前一个 Scenario 文件包含：

```text
injection
expected_signals
ground_truth
```

必须拆分。

---

## Public Scenario

目录：

```text
scenarios/public/
```

只包含：

```text
scenario_id
injection
trigger
cleanup
alert configuration
environment
```

禁止：

```text
root_cause
expected_root_service
expected_root_type
required_evidence
causal_chain
forbidden_claims
```

---

## Private Ground Truth

目录：

```text
scenarios/private/
```

包含：

```text
root_service
root_type
root_resource
causal_chain
affected_services
required_evidence
forbidden_claims
```

---

# 14. Runtime 隔离

Agent Runtime 容器禁止挂载：

```text
scenarios/private/
```

增加架构测试：

```text
Runtime code
```

禁止直接读取 Private GT 路径。

---

# 15. Phase 3：Black-box E2E Runner

新增：

```text
ops/evaluation/run_blackbox_e2e.py
```

完整流程：

```text
1. 读取 Public Scenario
2. cleanup
3. 注入 fault
4. 发业务请求
5. 等 Alert
6. 等 Incident
7. 触发 RCA
8. 等 Workflow terminal
9. 获取 RCA Report
10. 获取 Evidence
11. 获取 ToolInvocation
12. cleanup
13. 最后加载 Private Ground Truth
14. Scorer
15. 输出报告
```

---

# 16. 核心边界

在：

```text
Workflow terminal
```

之前：

```text
Agent
LLM
Tool
Knowledge
Runbook
```

均不能读取：

```text
Private Ground Truth
```

---

# 17. Phase 4：Hidden Holdout Dataset

当前 12 Scenario 定义为：

```text
Dev / In-Distribution
```

新增至少：

```text
8 个 Holdout
```

推荐：

```text
mysql-lock-contention
mongodb-connection-timeout
rabbitmq-consumer-backlog
dns-resolution-failure
service-mesh-retry-storm
object-storage-throttling
third-party-sms-timeout
memory-leak-gc-pause
```

可选：

```text
elasticsearch-query-latency
certificate-expiry
cross-zone-network-loss
config-center-stale-value
```

---

# 18. Holdout 设计原则

至少覆盖：

```text
4 种当前 Reasoner 从未硬编码过的 Resource
4 个新 Service Name
3 种新错误表达
2 种新 Topology
1 个 Composite Fault
1 个 No Actionable Root Cause
```

---

# 19. Phase 5：Service Name Randomization

新增：

```text
ServiceAliasMutator
```

例如：

```text
inventory-service
→ stock-api
→ warehouse-service
→ sku-reserver
```

Ground Truth 同步映射。

Runtime Reasoner 不知道原始服务名。

---

# 20. 验收

新增：

```text
Service Rename Accuracy
```

例如：

```text
Original Name = 90%
Renamed = 87%
```

说明泛化较好。

如果：

```text
Renamed = 40%
```

说明服务名依赖严重。

---

# 21. Phase 6：Error Paraphrase

同一故障维护：

```text
3~5 种表达
```

Timeout 示例：

```text
operation timed out
deadline exceeded
dependency did not respond in time
storage operation exceeded deadline
request expired waiting for upstream
```

---

# 22. Phase 7：Noise Injection

新增：

```text
NoiseInjector
```

注入：

```text
无关 warning logs
健康服务 logs
旧 error
无关 deployment change
低优先级 alert
健康 trace
旧 Incident Knowledge
```

Noise Level：

```text
0%
10%
30%
50%
```

---

# 23. 新指标

```text
Noise Robustness Curve
```

例如：

| Noise | RCA Top-1 |
|---|---:|
| 0% | 80% |
| 10% | 79% |
| 30% | 72% |
| 50% | 62% |

---

# 24. Phase 8：Missing Evidence

每个 Holdout 至少测试：

```text
Full Evidence
No Logs
No Traces
No Knowledge
Observability Timeout
```

目标：

```text
证据不足
→ confidence 下降
→ 必要时 UNDETERMINED
```

不是：

```text
硬猜
```

---

# 25. 新指标

```text
Missing Evidence Accuracy
Undetermined Precision
Undetermined Recall
False Confirmation Rate
```

---

# 26. Phase 9：Topology Shift

同一场景变更 Runtime Topology。

例如：

```text
gateway
→ order
→ inventory
```

改成：

```text
gateway
→ order
→ inventory-proxy
→ inventory
```

或者：

```text
order
├── pricing
└── inventory
```

---

# 27. 目标

Reasoner 必须使用：

```text
Runtime Topology Evidence
```

不能依赖：

```text
固定 checkout → inventory → postgres
```

---

# 28. Phase 10：Composite Fault / Distractor

新增：

```text
真实根因
+
多个干扰信号
```

例如：

```text
Root：
MySQL lock contention

同时：
payment 旧 warning
昨日 deployment
Redis latency 稍高
旧 Knowledge 命中
```

验证：

```text
Change
Knowledge
Noise
```

是否带偏最终 Root Cause。

---

# 29. Phase 11：多 Incident 并发

同时注入：

```text
Incident A
inventory timeout

Incident B
payment provider error
```

验证：

```text
Tenant
Incident
Trace
Workflow
Evidence
```

隔离。

---

# 30. 新指标

```text
Cross-Incident Evidence Leak Rate
```

目标：

```text
0
```

---

# 31. Phase 12：Benchmark Leakage Guard

新增：

```text
BenchmarkLeakageGuard
```

扫描：

```text
Prompt
Evidence Summary
Knowledge
Runbook
Runtime Input
```

禁止出现：

```text
ground_truth
expected_root_cause
expected_root_service
expected_root_resource
required_evidence
forbidden_claims
scenario_answer
```

---

# 32. 注意

真实日志可能合理出现：

```text
timeout
mysql
redis
```

不能简单把故障词都算泄漏。

Leakage Guard 检测的是：

```text
Benchmark 专属答案字段
Ground Truth 结构
异常直接答案拼接
```

---

# 33. Phase 13：正式 Run Provenance

每次正式评测固定并记录：

```text
run_id
git_commit
public_scenario_hash
private_ground_truth_hash
prompt_hash
taxonomy_hash
reasoning_pipeline_hash
knowledge_hash
runbook_hash
model
provider
temperature
runs_per_scenario
timestamp
```

---

# 34. 禁止挑正确结果

允许：

```text
Transport Error
Connection Reset
Provider Timeout
```

按有限策略重试。

禁止：

```text
模型答错
↓
重新跑
↓
只留正确结果
```

---

# 35. Merge 规则

Shard Merge 只允许：

```text
恢复技术失败批次
```

任何：

```text
有效 Prediction
```

即使答错：

> 也必须保留。

---

# 36. Phase 14：Generalization Metrics

最终必须输出：

```text
In-Domain Accuracy
Hidden Holdout Accuracy
Generalization Gap

Root Service Accuracy
Root Type Accuracy
Root Resource OOD Accuracy

Candidate Recall@3
MRR

Service Rename Accuracy
Error Paraphrase Accuracy
Topology Shift Accuracy

Noise Robustness
Missing Evidence Accuracy

Unsupported Claim
Forbidden Claim
False Confirmation

Undetermined Precision
Undetermined Recall

Calibration ECE
Brier Score

Cross-Incident Evidence Leak Rate
Benchmark Leakage Violation Count
```

---

# 37. 核心指标：Generalization Gap

定义：

```text
Generalization Gap
=
Known Accuracy
-
Holdout Accuracy
```

例如：

```text
Known = 96%
Holdout = 70%

Gap = 26pp
```

本轮优化目标：

> 缩小 Gap。

不是：

> 把 Known 96% 再调到 100%。

---

# 38. 核心指标：Robustness Drop

例如：

```text
Clean = 78%
30% Noise = 68%

Drop = 10pp
```

建议：

```text
30% Noise Drop <= 15pp
```

---

# 39. 最终实验矩阵

v0.7.0 必须至少跑：

| Test Set | Purpose |
|---|---|
| Known Clean | 已知场景回归 |
| Hidden Holdout | 未知故障泛化 |
| Service Rename | 服务名依赖 |
| Resource OOD | 未知资源 |
| Error Paraphrase | 关键词依赖 |
| Noise 10% | 轻度干扰 |
| Noise 30% | 中度干扰 |
| Noise 50% | 强干扰 |
| Missing Logs | 日志不可用 |
| Missing Traces | Trace 不可用 |
| Missing Knowledge | 历史知识不可用 |
| Topology Shift | 拓扑变化 |
| Composite Fault | 多信号竞争 |
| Multi Incident | Evidence 隔离 |

---

# 40. 每组统一输出

统一比较：

```text
RCA Top-1
Strict RCA
Root Service
Root Type
Root Resource
Candidate Recall@3
Evidence Recall
Unsupported Claim
Tool Calls
Token
P50
P95
```

---

# 41. v0.7.0 现实验收目标

不追求：

```text
Holdout = 100%
```

建议：

```text
Known RCA Top-1 >= 90%

Hidden Holdout RCA Top-1 >= 60%
Hidden Root Service >= 75%
Candidate Recall@3 >= 85%

Unsupported Claim = 0
Forbidden Claim = 0

30% Noise Drop <= 15pp

Missing Evidence:
Unsupported Claim = 0

Cross-Incident Evidence Leak = 0
Benchmark Leakage Violation = 0
```

---

# 42. Holdout 使用纪律

正式 Holdout v1 跑完以后：

禁止：

```text
看答案
↓
加 if scenario == xxx
↓
重新跑同一 Holdout
```

正确方式：

```text
Holdout v1 暴露问题
↓
归纳成错误类别
↓
新增 Dev Scenario
↓
修复通用逻辑
↓
创建 Holdout v2
↓
再次正式验收
```

---

# 43. Phase 15：Generalization Bad Case Taxonomy

新增：

```text
GENERALIZATION_SERVICE_RENAME
GENERALIZATION_RESOURCE_OOD
GENERALIZATION_PARAPHRASE
GENERALIZATION_TOPOLOGY_SHIFT

NOISE_DISTRACTION
CHANGE_DISTRACTION
KNOWLEDGE_DISTRACTION

MISSING_METRIC
MISSING_LOG
MISSING_TRACE

CROSS_INCIDENT_CONTAMINATION

BENCHMARK_LEAKAGE

OVERCONFIDENT_WRONG_RCA
UNDETERMINED_WHEN_EVIDENCE_SUFFICIENT
```

---

# 44. 最终报告

必须生成：

```text
generalization-report.md
generalization-bad-cases.jsonl
benchmark-provenance.json
resume-metrics.json
```

---

# 45. generalization-report.md 必须回答

```text
Known 准确率多少？
Holdout 准确率多少？

Generalization Gap 多大？

哪些 Root Type 最差？
哪些 Resource 最差？

服务改名下降多少？
错误改写下降多少？

30% Noise 下降多少？

Logs 不可用时是否乱猜？
Trace 不可用时是否过度自信？

Knowledge 是否带偏？
Change 是否带偏？

两个 Incident 是否发生 Evidence 串线？
```

---

# 46. resume-metrics.json

只保存最终可用于简历的真实指标：

```json
{
  "known_rca_top1": 0.0,
  "holdout_rca_top1": 0.0,
  "generalization_gap_pp": 0.0,
  "noise_30_drop_pp": 0.0,
  "unsupported_claim_rate": 0.0,
  "cross_incident_leak_rate": 0.0
}
```

所有值必须自动从正式 Benchmark 产物计算。

禁止人工填写。

---

# 47. 推荐代码结构

```text
ops/evaluation/
├── contract/
├── blackbox/
│   ├── runner.py
│   ├── fault_executor.py
│   ├── workflow_waiter.py
│   └── artifact_collector.py
├── integrity/
│   ├── leakage_guard.py
│   └── provenance.py
└── generalization/
    ├── noise.py
    ├── service_alias.py
    ├── paraphrase.py
    ├── topology_mutator.py
    └── reporter.py
```

Reasoning：

```text
src/devops_agent_platform/rca_reasoning/
├── entity_extractor.py
├── normalizer.py
├── pipeline.py
└── taxonomy.py
```

---

# 48. 推荐实施顺序

严格建议：

```text
Phase 1
Public / Private Scenario Split

Phase 2
Benchmark Leakage Guard

Phase 3
Black-box E2E Runner

Phase 4
当前 12 Known Scenario 全 E2E + Real Qwen

Phase 5
Runbook / Knowledge 独立化

Phase 6
8~12 Hidden Holdout

Phase 7
Evidence Entity Extraction

Phase 8
去掉 MiniShop Resource Hardcode

Phase 9
Service Rename

Phase 10
Error Paraphrase

Phase 11
Noise / Missing Evidence

Phase 12
Topology Shift / Composite Fault

Phase 13
Multi-Incident Isolation

Phase 14
Generalization Report

Phase 15
Resume / Interview Material
```

---

# 49. 每个 Phase 的 Definition of Done

- [ ] 有源码；
- [ ] 有单元测试；
- [ ] 有 E2E；
- [ ] 不读取 Private GT；
- [ ] 无 Ground Truth 注入 Prompt；
- [ ] 不改变 Tool 权限边界；
- [ ] 不允许自由 Shell；
- [ ] 输出 Provenance；
- [ ] 输出 Bad Case；
- [ ] 更新 README；
- [ ] 更新 `缺少内容.md`；
- [ ] 指标与源码一致。

---

# 50. v0.7.0 最终 Definition of Done

必须满足：

- [ ] 最终真实 Benchmark 不使用 `build_reference_suite()` 直接构造模型 Evidence；
- [ ] Runtime 无法读取 Private Ground Truth；
- [ ] Runbook 不根据当前 Scenario Ground Truth 动态生成；
- [ ] Knowledge 与 Scenario 独立版本化；
- [ ] 当前 12 Scenario 真实 Fault Injection + Real Tools + Real Qwen；
- [ ] 至少 8 个 Hidden Holdout；
- [ ] 至少 4 种未知 Resource；
- [ ] Service Rename；
- [ ] Error Paraphrase；
- [ ] 30% Noise；
- [ ] Missing Evidence；
- [ ] Topology Shift；
- [ ] Composite Fault；
- [ ] Multi Incident；
- [ ] Cross Incident Evidence Leak = 0；
- [ ] Leakage Violation = 0；
- [ ] 输出 Generalization Report；
- [ ] 输出 resume-metrics.json；
- [ ] 所有正式结果带 Provenance。

---

# 51. 本阶段禁止继续做的功能

禁止扩：

```text
Multi-Agent
新 Agent Framework
新 MQ
新数据库
新 MCP 能力
新 Workspace 能力
自动 Remediation
任意 Shell
```

原因：

> 它们已经不是当前项目最大的可信度瓶颈。

---

# 52. 最终简历价值

v0.7 做完后，简历可以新增：

> **为避免已知故障场景过拟合，设计 Black-box E2E + Hidden Holdout Benchmark，将故障注入、Alert/Incident、RCA Worker、Prometheus/Loki/Tempo 实时 Tool 调用与真实 LLM 串成统一评测链路，并通过未知 Resource、Service Rename、Noise、Missing Evidence 和 Topology Shift 验证 RCA 泛化能力。**

如果真实数据达到要求：

> **已知场景 RCA Top-1 XX%，未知 Holdout XX%，30% Noise 下仅下降 Xpp，Unsupported Claim 与跨 Incident Evidence 泄漏均保持 0。**

所有数字必须来自：

```text
resume-metrics.json
```

---

# 53. 面试主线

以后不要只讲：

> “我做了一个 AIOps Agent。”

应该讲：

```text
第一阶段
解决 Agent 长任务可靠性
→ Outbox / Kafka / Lease / Fence

第二阶段
解决生产运维 Tool 不可自由调用
→ Bounded Dynamic Investigation

第三阶段
真实 Benchmark 发现 Evidence 已经够好
但 Root Cause 不准
→ Root Cause Reasoning

第四阶段
Known Benchmark 做到很高后
主动质疑 Benchmark 是否过拟合
→ Hidden Holdout + Black-box E2E
```

---

# 54. 面试中的关键一句

推荐：

> “我们做到已知 12 个场景接近满分之后，我反而认为继续刷这个数字没有意义，因为场景、Evidence 和 Reasoning 都是自己设计的，存在过拟合风险。所以后面我把评测改成 Public/Private Ground Truth 隔离的 Black-box E2E，并加入未知 Resource、服务改名、错误改写、Noise 和 Missing Evidence，专门测泛化。”

这句话比：

> “准确率 100%。”

更有技术含量。

---

# 55. 最终项目定位

v0.7.0 完成后，项目真正形成：

```text
AIOps Domain
+
Agent Workflow
+
Backend Reliability
+
Distributed Systems
+
Observability
+
LLM Reasoning
+
Evaluation
+
Benchmark Integrity
+
Generalization
+
Chaos / Load
```

---

# 56. 最重要的一句话

> **v0.6.0 证明系统会做已知题；v0.7.0 要证明系统不是背题，而是真的会排障。**

这就是下一阶段唯一主线。
