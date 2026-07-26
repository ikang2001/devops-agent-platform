# 最小改造路线图总计划（A → B → C0/C1）

> 单一执行计划。任务点完成后在本文件勾选，并在 `docs/` 下各写一份中文说明。
> 完成度真相源仍是 [`缺少内容.md`](../缺少内容.md)；本文件管**怎么做**，不替代完成度清单。

## 0. 目标与原则

| 原则 | 说明 |
| --- | --- |
| 诚实边界 | 叙事 ≤ 代码；禁止“自适应 Agent / 通用自动修复 / Prompt·Flag·RAG 已上线” |
| 最小可合并 | 每条路线可单独验收；默认行为兼容（C 的开关默认关） |
| 中文交付 | 代码备注中文；每完成一个任务点写一份 `docs/*.md` |
| 不扩 scope | 不做自由 Agent 循环、不做自动重试外部写、不做假生产验收 |

选定路径（用户确认）：

1. **先 A**（半天）：只改叙事与边界测试
2. **立刻 B**（3～4 天）：修复 remediation 崩溃卡死
3. **再 C0+C1**（约 1 周）：部分失败降级 + 可配置固定计划变体

---

## 1. 路线总览

```text
A 诚实叙事 ──► B 执行租约 ──► C0 部分失败降级 ──► C1 调查策略
   (文档/测试)     (状态机真缺陷)     (RCA 可用性)         (固定计划变体)
```

| 路线 | 价值 | 不做 |
| --- | --- | --- |
| A | 面试/README 不穿帮 | 不借机“补实现”蓝图功能 |
| B | 生产缺陷：`EXECUTING` 永久卡住 | 无自动重试、无独立 reclaim worker（MVP） |
| C0 | 单步工具挂了仍能出部分报告 | 不把部分证据标 CONFIRMED |
| C1 | 无 Tempo 等环境可选更短固定计划 | 不做运行时多轮自适应推理 |

---

## 2. 当前进度快照（2026-07-26）

### 2.1 路线 A — 已完成

| 项 | 状态 | 文档 |
| --- | --- | --- |
| README / STEP6 / 缺少内容 / 面试文档 / ops/product 边界 | ✅ | [`route-a-honest-narrative.md`](route-a-honest-narrative.md) |
| 资产测试改为断言边界短语 | ✅ | 同上 |
| 禁止话术列表 | ✅ | `缺少内容.md` + 面试文档 |

### 2.2 路线 B — 已完成（MVP）

| 项 | 状态 | 文档 |
| --- | --- | --- |
| 领域 lease/attempt/owner + `fail_stale_*` | ✅ | [`remediation-execution-lease.md`](remediation-execution-lease.md) |
| 服务 claim → timeout → fence finish + `reclaim_stale` | ✅ | [`route-b-remediation-lease.md`](route-b-remediation-lease.md) |
| 迁移 0028 / ORM / mapper / repo stale 列表 | ✅ | 同上 |
| settings：`timeout < lease` | ✅ | 同上 |
| 单测 service / repository | ✅ | 同上 |
| 独立后台 reclaim worker / HTTP reclaim API | ❌ 刻意不做（仍需项） | — |

### 2.3 路线 C0 — 已完成

| 项 | 状态 | 说明 |
| --- | --- | --- |
| `ControlledAgentWorkflowConfig.continue_on_step_failure` | ✅ 已有 | 默认 `False`，兼容 fail-fast |
| 失败步骤记 invocation 后 `continue` | ✅ 已有 | 零证据仍 `AgentWorkflowExecutionFailure` |
| 报告摘要追加 `Partial collection: failed steps=...` | ✅ 已有 | 不改 LLM 协议 |
| settings 开关 + bootstrap 注入 | ✅ | 默认关闭，保持 fail-fast |
| 诚实置信度（覆盖率感知，仍禁止 CONFIRMED） | ✅ | 只下调生成器分数；部分报告硬封顶 `0.4` |
| 单元测试 | ✅ | 部分成功、零证据、置信度与结论护栏 |
| 中文文档 | ✅ | [`route-c0-partial-degrade.md`](route-c0-partial-degrade.md) |

### 2.4 路线 C1 — 已完成

| 项 | 状态 | 说明 |
| --- | --- | --- |
| `investigation_policy.py` 固定计划变体 | ✅ 已有 | `fixed_default` / `fixed_no_traces` / `fixed_metrics_logs_runbooks` |
| settings `rca_investigation_policy` | ✅ | 未知值与 `auto` 启动前失败 |
| `rca_runtime` 按策略建 plan | ✅ | 无 Trace 计划不创建或要求 Tempo 资源 |
| 单元测试 | ✅ | 三策略形状、非法键、Settings 与 Runtime 接线 |
| 中文文档 | ✅ | [`route-c1-investigation-policy.md`](route-c1-investigation-policy.md) |
| 自适应多轮 / auto 策略 | ❌ 明确不做 | 禁止话术 |

---

## 3. 执行记录（按任务点）

每个任务点定义：**改什么 → 验收 → 文档**。

### T1. 总计划文档（本文件）

- [x] 写出 A/B/C 状态、剩余项、验收与禁止项
- 文档：`docs/remediation-roadmap-master-plan.md`

### T2. 路线 A 文档对齐（若已有则只校验）

- [x] `docs/route-a-honest-narrative.md` 已存在
- 验收：与 `缺少内容.md` 禁止话术一致

### T3. 路线 B 文档对齐（若已有则只校验）

- [x] `docs/remediation-execution-lease.md` + `docs/route-b-remediation-lease.md`
- 验收：明确“无后台 worker / 无自动重试”

### T4. C0 接线与诚实置信度 — [x]

**改动清单（预计）：**

| 文件 | 改动 |
| --- | --- |
| `infrastructure/config/settings.py` | 增加 `rca_continue_on_step_failure: bool = False` |
| `bootstrap/rca_runtime.py` | 构造 `ControlledAgentWorkflowConfig(continue_on_step_failure=...)` |
| `agent/controlled_workflow.py` | 按证据类型覆盖率和服务端上限下调 confidence；部分报告封顶 `0.4`，保持禁止 CONFIRMED |
| `tests/unit/agent/test_controlled_workflow.py` | 单步失败 continue / 零证据仍失败 / 摘要标记 |
| `tests/unit/agent/test_controlled_workflow.py` | 覆盖率与 confidence 上限 |
| `tests/unit/bootstrap/test_settings.py` | 默认 False |

**规则（C0）：**

1. 默认 `continue_on_step_failure=False` → 行为与历史一致。
2. 开启后：失败步骤写 `FAILED` invocation，继续后续只读步骤。
3. 若最终 `evidence` 为空 → 仍抛 `AgentWorkflowExecutionFailure`。
4. 有证据 → 生成报告，`conclusion_status` 仍不得为 `CONFIRMED`。
5. confidence：`min(类型覆盖率, 配置上限)`，部分失败时再乘折扣或硬封顶（如 0.4）。
6. 摘要必须能看出失败步骤（已有 marker 可保留）。

**验收：**

```bash
uv run pytest -q tests/unit/agent/test_controlled_workflow.py tests/unit/agent/test_report_generator.py tests/unit/bootstrap/test_settings.py
```

**文档：** `docs/route-c0-partial-degrade.md`

### T5. C1 调查策略接线 — [x]

**改动清单（预计）：**

| 文件 | 改动 |
| --- | --- |
| `infrastructure/config/settings.py` | `rca_investigation_policy: str = "fixed_default"` + 校验 |
| `bootstrap/rca_runtime.py` | `build_plan_for_policy(settings.rca_investigation_policy)` |
| `agent/__init__.py` | 按需导出 policy API |
| `tests/unit/agent/test_investigation_policy.py` | 新建：三策略计划形状、非法键失败 |
| `tests/unit/bootstrap/test_settings.py` | 默认与非法值 |
| `.env.example` / `ops/deploy/env.production.example` | 文档化配置项（可选但建议） |

**规则（C1）：**

1. 全部策略是**静态** `RCAWorkflowPlan`，不是运行时推理。
2. 默认 `fixed_default` = 现有四步。
3. 未知策略 → 启动/构造失败，禁止静默回落。
4. 不引入 `auto` / 多轮 tool 选择。

**验收：**

```bash
uv run pytest -q tests/unit/agent/test_investigation_policy.py tests/unit/bootstrap/test_settings.py
```

**文档：** `docs/route-c1-investigation-policy.md`

### T6. 收口：更新完成度与交叉链接 — [x]

| 文件 | 动作 |
| --- | --- |
| `缺少内容.md` | C0/C1 写入「已实现」；未做项留在「仍需」 |
| `项目面试文档.md` | 补 1～2 句：部分失败降级开关、固定策略变体（诚实） |
| `README.md` | 可选一行配置说明 |
| 本总计划 | 勾选 T4–T6 |

---

## 4. 文件责任矩阵

| 路径 | A | B | C0 | C1 |
| --- | --- | --- | --- | --- |
| `README.md` / `STEP6_*` / `缺少内容.md` / `项目面试文档.md` | 主 | 交叉 | 交叉 | 交叉 |
| `ops/product/*` | 主 | 边界 | — | — |
| `domain/models/remediation.py` | — | 主 | — | — |
| `application/services/remediation_service.py` | — | 主 | — | — |
| `infrastructure/**/remediation*` + 迁移 0028 | — | 主 | — | — |
| `agent/controlled_workflow.py` | — | — | 主 | 辅 |
| `agent/report_generator.py` | — | — | 主 | — |
| `agent/investigation_policy.py` | — | — | — | 主 |
| `bootstrap/rca_runtime.py` | — | — | 接线 | 接线 |
| `infrastructure/config/settings.py` | — | lease | 开关 | 策略键 |
| `tests/unit/agent/*` | — | — | 主 | 主 |
| `tests/unit/application/test_remediation_service.py` | — | 主 | — | — |
| `docs/route-*.md` | ✅ | ✅ | ✅ | ✅ |

---

## 5. 验收总清单

### 5.1 必须绿

```bash
uv run pytest -q tests/unit/ops/test_step5_step6_assets.py
uv run pytest -q tests/unit/application/test_remediation_service.py tests/unit/infrastructure/test_remediation_repository.py
uv run pytest -q tests/unit/agent/test_controlled_workflow.py
uv run pytest -q tests/unit/agent/test_investigation_policy.py   # T5 后
uv run pytest -q tests/unit/bootstrap/test_settings.py
```

### 5.2 行为验收

| 场景 | 期望 |
| --- | --- |
| B：claim 后崩溃模拟 | 租约过期 → `reclaim_stale` → `FAILED`；旧 finish 被 fence 拒绝 |
| B：timeout ≥ lease | settings 校验失败 |
| C0：关开关 + 一步失败 | 立即 `AgentWorkflowExecutionFailure`（旧行为） |
| C0：开开关 + 一步失败 + 后续成功 | 有报告 + summary 含 Partial + 非 CONFIRMED + 低 confidence |
| C0：开开关 + 全失败 | 仍失败，不造假报告 |
| C1：默认策略 | 四步 plan_id 与现网一致 |
| C1：`fixed_no_traces` | 无 traces 步骤 |
| C1：非法策略 | 校验错误 |

### 5.3 文档验收

每完成任务点，`docs/` 下有对应中文 md，且：

- 写清「已实现 / 未实现」
- 写清面试可说 / 不可说
- 链回本总计划与 `缺少内容.md`

---

## 6. 明确不做（防 scope 膨胀）

1. 自由多轮 Agent / ReAct / 工具自选循环
2. LLM 输出 `CONFIRMED` 根因
3. Remediation 自动重试外部写、自动接管重跑
4. 独立生产级 reclaim 后台 worker（可后续加，不在本路线）
5. Prompt Registry / Feature Flags / RAG 运行时接线
6. 把本地 E2E 包装成 staging 签字

---

## 7. 面试一句话（改造后）

> 这是生产导向的 Incident/RCA **作业编排**后端：固定只读调查计划、可选部分失败降级、可配置固定计划变体；修复走审批 + claim/lease/fence，不是自适应智能 Agent，也不是通用自动修复引擎。

---

## 8. 收口状态与下一边界

1. A/B/C0/C1 代码、配置、测试和中文说明均已完成。
2. C0/C1 定向回归：`190 passed`，相关 Ruff 检查通过。
3. 根平台全量回归：`1625 passed, 9 skipped`，锁文件一致。
4. 下一边界不是继续扩成自由 Agent，而是在真实 staging/sandbox 验证观测数据源、
   LLM、OIDC、Kafka 和外部修复控制器；未取得证据前不得宣称生产验收完成。
5. Remediation 后台 reclaim worker、外部写自动重试、自适应多轮调查、
   Prompt/Flag/RAG 运行时接线仍属于后续独立工程。
