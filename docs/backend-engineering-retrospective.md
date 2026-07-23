# 后端工程化开发复盘汇总

> 随 MiniShop 端到端 RCA 闭环开发持续更新，只记录真实发生或有证据支持的事件。

## 1. 基本信息

| 项目 | 内容 |
|---|---|
| 项目/需求 | MiniShop 到 DevOps Agent 的端到端 RCA、场景 Manifest 与代码导读 |
| 技术栈与环境 | Python 3.12、FastAPI、PostgreSQL、Kafka/Redpanda、Prometheus、Loki、Tempo、Docker Compose |
| 开始时间 | 2026-07-18 |
| 完成时间 | 2026-07-23 |
| 当前结论 | 三场景真实 Docker RCA 闭环已通过，代码、数据、导读和验收证据齐全 |
| 相关版本/提交 | 独立 Git 仓库 `main` 基线，提交信息见本仓库 `git log` |

## 2. 结果摘要

- 交付内容：三份机器可读 Manifest/Ground Truth、MiniShop 可观测与告警接入、
  真实 Compose RCA 验收、演练管理员认证、逐模块代码导读。
- 已完成验证：平台全量 `1531 passed, 9 skipped`；MiniShop 全量
  `37 passed`；E2E 静态资产 `3 passed`；Compose 配置展开通过；三场景真实
  Docker E2E 全部通过。
- 端到端证据：`checkout-latency`、`inventory-db-timeout`、
  `payment-error` 均为 `SUCCEEDED`，且具备 METRIC、LOG、TRACE、RUNBOOK
  四类 Evidence。
- 遗留风险：当前仅保留 Starlette TestClient 的上游弃用告警，尚不影响测试结果。
- 工程基线：根平台与 MiniShop 分别提交 `uv.lock`，CI 和镜像均使用
  `uv 0.11.31` 的 locked 模式，Ruff 固定为 `0.15.22`；项目已建立独立
  Git `main` 基线。

## 3. 阶段摘要

| 阶段 | 完成内容 | 验证证据 | 新增事件数 | 遗留事项 |
|---|---|---|---:|---|
| Step 1 需求分析 | 定义闭环、Manifest、教程三项交付及成功标准 | 现有源码、配置和两套测试基线 | 1 | 无 |
| Step 2 架构设计 | 选择原生 Alertmanager Relay、兼容 Telemetry、演练专用认证和 Compose 验收 | 现有端口/适配器边界 | 1 | 无 |
| Step 3 代码骨架 | 新增 Alertmanager Mapper、Relay、Agent Alert Client 与路由 | Relay 定向测试 6 项通过 | 1 | 无 |
| Step 4 增量实现 | Alertmanager Relay、兼容指标/日志、分服务 Trace、Tempo Ground Truth、演练认证和代码导读 | 定向认证测试 `173 passed`；MiniShop `37 passed` | 1 | 无 |
| Step 5 测试排错 | 修复 FastAPI 生命周期、Buildx 路径、tmpfs 权限、Tempo Trace ID 和锁定环境打包问题 | 平台 `1531 passed, 9 skipped`；MiniShop `37 passed` | 6 | 无 |
| Step 6 整合运维 | 真实 Compose 三场景验收、锁定镜像验证、独立 Git 基线和结果落盘 | `artifacts/results.json` 中 `passed: true` | 1 | 无 |

## 4. 事件索引

| ID | 阶段 | 类型 | 标题 | 状态 | 结论 |
|---|---|---|---|---|---|
| DEV-001 | Step 1 | 踩坑 | 当前目录没有独立 Git 历史 | 已确认 | 不执行教程技能要求的自动 commit，避免纳入上级无关文件 |
| DEV-002 | Step 2 | 难点 | 演练认证不能削弱生产 OIDC 边界 | 已解决 | 采用默认关闭且生产环境强制拒绝的演练认证适配器 |
| DEV-003 | Step 3 | 犯错 | 首个组合补丁上下文匹配失败 | 已解决 | 拆分为新增文件和小范围更新补丁，验证没有部分写入 |
| DEV-004 | Step 4 | 踩坑 | 多文件补丁不会自动创建缺失父目录 | 已解决 | 先创建 E2E 目录，再继续小批次补丁 |
| DEV-005 | Step 5 | 难点 | FastAPI 移除 add_event_handler 导致测试收集失败 | 已解决 | 改用 lifespan 关闭 Trace Provider，MiniShop 37 项全绿 |
| DEV-006 | Step 5 | 踩坑 | 默认 Python 环境未安装项目开发依赖 | 已解决 | 按两个项目各自的 `.[dev]` 声明安装并完成全量回归 |
| DEV-007 | Step 5 | 难点 | Buildx Bake 无法处理非 ASCII 工作区会话头 | 已解决 | E2E 脚本固定使用经典构建器，保留中文工作区 |
| DEV-008 | Step 5 | 难点 | 非 root 容器无法写入默认 tmpfs | 已解决 | 为各镜像按实际 UID/GID 声明 tmpfs 所有权 |
| DEV-009 | Step 5 | 难点 | Tempo 搜索返回省略前导零的 Trace ID | 已解决 | 有界接受并规范化 16/128 位 Trace ID，补回归测试 |
| DEV-010 | Step 6 | 踩坑 | Git 中文路径默认转义破坏 PowerShell 审计 | 已解决 | 独立仓库关闭 `core.quotepath` 后重新执行候选文件审计 |
| DEV-011 | Step 5 | 难点 | 锁定环境暴露根项目缺少显式打包元数据 | 已解决 | 补 `build-system` 与 `src` 包发现，升级资产测试为 locked 门禁 |

## 5. 事件详情

### DEV-001：当前目录没有独立 Git 历史

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-18 / Step 1 |
| 模块 | 工作区与文档流程 |
| 分类 | 踩坑 |
| 状态 | 已确认 |
| 背景与目标 | 逐模块教程技能要求 spec、plan、正文逐阶段提交 |
| 现象与证据 | `git status --short --branch` 显示上级仓库 `No commits yet on main`，并包含多个项目级未跟踪目录 |
| 影响 | 自动提交可能把用户无关文件纳入历史，且无法取得可靠 HEAD 锚点 |
| 排查过程 | 核对工作目录、Git 状态和教程技能硬门禁 |
| 根因 | 当前项目目录不是具有独立 HEAD 的 Git 工作树 |
| 解决方案 | 不创建提交；使用单文件代码导读作为安全降级，并在最终结果中明确说明 |
| 涉及位置 | `docs/`、上级 Git 工作树 |
| 验证证据 | 未执行任何 `git add` 或 `git commit` |
| 残余风险 | 教程源码链接不能锚定不可变 commit |
| 预防措施 | 后续为本项目初始化独立仓库后，再启用 commit 锚定的教程维护流程 |

### DEV-002：演练认证不能削弱生产 OIDC 边界

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-18 / Step 2 |
| 模块 | 管理员认证与 RCA 启动 |
| 分类 | 难点 |
| 状态 | 已解决 |
| 背景与目标 | 自包含 E2E 需要管理员配置工具权限、发布 Runbook 并启动 RCA |
| 现象与证据 | 现有管理 API 默认 fail-closed，完整 OIDC 又需要额外身份服务 |
| 影响 | 直接绕过认证会破坏已有安全边界；引入完整 IdP 会显著增加演练复杂度 |
| 排查过程 | 检查 `AdministratorAuthenticatorPort`、OIDC 装配和生产配置校验 |
| 根因 | 演练需要受控管理员身份，但当前只有生产 OIDC 适配器 |
| 解决方案 | 新增显式开关的演练认证器：默认关闭、固定租户、常量时间 Token 比较、生产环境禁止、与 OIDC 互斥 |
| 涉及位置 | `infrastructure/auth`、`Settings`、`bootstrap/runtime.py` |
| 验证证据 | 演练认证器、Settings 与 Runtime 定向测试 `173 passed`；Ruff 静态检查通过 |
| 残余风险 | 只允许用于本地演练，不能进入生产配置 |
| 预防措施 | 启动期组合校验、弱密钥拒绝、生产环境硬拒绝和 README 醒目标识 |

### DEV-003：首个组合补丁上下文匹配失败

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-18 / Step 3 |
| 模块 | MiniShop Alertmanager Relay |
| 分类 | 犯错 |
| 状态 | 已解决 |
| 背景与目标 | 一次补丁同时新增 Relay 文件并修改配置、入口、依赖和脚本 |
| 现象与证据 | `apply_patch verification failed`，未匹配 `pyproject.toml` 依赖段 |
| 影响 | 第一轮补丁未落地，未造成代码污染 |
| 排查过程 | 使用 `Test-Path` 核实新文件均不存在，确认补丁整体回滚 |
| 根因 | 把多个独立文件编辑绑定在一个大补丁中，任一上下文差异都会使整体失败 |
| 解决方案 | 先新增独立文件，再按配置、入口、依赖分别应用小补丁 |
| 涉及位置 | MiniShop `app/`、`pyproject.toml`、`scripts/send_agent_alert.py` |
| 验证证据 | Relay 定向测试 `6 passed` |
| 残余风险 | 无代码残留；后续仍需全量测试 |
| 预防措施 | 跨多个既有文件时优先使用小补丁，每组修改后立即运行定向验证 |

### DEV-004：多文件补丁不会自动创建缺失父目录

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-19 / Step 4 |
| 模块 | `ops/minishop-e2e` 运维资产 |
| 分类 | 踩坑 |
| 状态 | 已解决 |
| 现象与证据 | 首批 `apply_patch` 已创建 MiniShop Dockerfile，但因 `ops/minishop-e2e` 不存在而在后续文件处失败 |
| 影响 | 补丁发生部分落盘，若直接重放会与已创建 Dockerfile 冲突 |
| 排查过程 | 分别用 `Test-Path` 检查 Dockerfile 和目标目录，确认前者存在、后者不存在 |
| 根因 | 补丁工具能新增文件，但不会为后续文件自动创建缺失父目录 |
| 解决方案 | 先创建 `ops/minishop-e2e/artifacts` 父目录，再重放不含 Dockerfile 的剩余补丁 |
| 验证证据 | `docker compose ... config --quiet` 返回 0 |
| 预防措施 | 新增多层目录时先显式建立目录，并在补丁失败后检查每个目标是否部分落盘 |

### DEV-005：FastAPI 生命周期 API 兼容回归

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-19 / Step 5 |
| 模块 | MiniShop OpenTelemetry 生命周期 |
| 分类 | 难点 |
| 状态 | 已解决 |
| 现象与证据 | MiniShop pytest 在收集 3 个模块时均报 `FastAPI` 不存在 `add_event_handler` |
| 影响 | 新增 Trace 关闭逻辑使整个 MiniShop 测试集无法收集 |
| 排查过程 | 错误稳定指向 `app/main.py:18`，确认当前 FastAPI 版本已要求 lifespan 模式 |
| 根因 | Trace 初版沿用了已移除的事件注册 API |
| 解决方案 | 使用 `asynccontextmanager` lifespan，在应用退出的 `finally` 中幂等关闭四个 Tracer Provider |
| 涉及位置 | `MiniShop 电商下单故障演练靶场/app/main.py` |
| 验证证据 | MiniShop 全量 `37 passed`，仅保留既有 TestClient 弃用告警 |
| 残余风险 | 后续升级到 httpx2 时需迁移当前 TestClient 用法 |
| 预防措施 | 生命周期能力优先使用 FastAPI lifespan，并把应用导入测试纳入最小回归 |

### DEV-006：默认 Python 环境未安装项目开发依赖

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-23 / Step 5 |
| 模块 | 演练管理员认证测试 |
| 分类 | 踩坑 |
| 状态 | 已解决 |
| 现象与证据 | 首次定向 pytest 在收集 `test_runtime.py` 时报告 `ModuleNotFoundError: aiokafka`，并提示未知 `asyncio_mode` |
| 影响 | 测试未进入认证功能断言，无法形成红绿验证 |
| 排查过程 | 检查当前解释器、虚拟环境和已安装包，确认工作区没有独立 venv，Anaconda 环境缺少 `aiokafka` 与 `pytest-asyncio` |
| 根因 | 当前机器环境此前未按根项目 `pyproject.toml` 安装完整开发依赖 |
| 解决方案 | 执行 `python -m pip install -e ".[dev]"`，按项目声明安装运行与测试依赖 |
| 验证证据 | 依赖安装后同一组测试进入断言，先得到 6 个预期红测，再实现并达到 `173 passed` |
| 残余风险 | 开发依赖未锁定精确版本，新版 Ruff 的格式结果与既有文件风格存在差异 |
| 预防措施 | 后续增加锁文件或受控工具版本，并优先使用项目独立虚拟环境 |

### DEV-007：Buildx Bake 无法处理非 ASCII 工作区会话头

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-23 / Step 5 |
| 模块 | `ops/minishop-e2e/run-e2e.ps1` |
| 分类 | 难点 |
| 状态 | 已解决 |
| 现象与证据 | Docker Desktop 构建报 `x-docker-expose-session-sharedkey` 含 non-printable ASCII，错误只在带中文的项目绝对路径出现 |
| 影响 | Compose 配置正确，但 Agent 和 MiniShop 镜像无法进入构建阶段 |
| 排查过程 | 分别关闭 Compose Bake 与 BuildKit，确认 `COMPOSE_BAKE=false` 不足以覆盖当前 Docker Desktop 默认行为，而 `DOCKER_BUILDKIT=0` 可稳定构建 |
| 根因 | Buildx 会话元数据把非 ASCII 工作区路径写入仅允许 ASCII 的 HTTP 头 |
| 解决方案 | E2E PowerShell 入口同时设置 `COMPOSE_BAKE=false` 与 `DOCKER_BUILDKIT=0`，使用经典构建器 |
| 验证证据 | 两个镜像均成功构建，随后完整三场景 E2E 通过 |
| 残余风险 | 经典构建器速度较慢，后续 Docker 修复该路径编码问题后可恢复 BuildKit |
| 预防措施 | 自动化脚本必须在真实工作区路径运行一次，不能只在纯 ASCII 临时目录验证 |

### DEV-008：非 root 容器无法写入默认 tmpfs

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-23 / Step 5 |
| 模块 | `ops/minishop-e2e/docker-compose.yml` |
| 分类 | 难点 |
| 状态 | 已解决 |
| 现象与证据 | Redpanda 无法创建 `pid.lock`，Prometheus 无法创建 `queries.active`，Tempo 无法创建 blocks 目录，均报告 `Permission denied` |
| 影响 | 基础设施容器提前退出，真实告警和 RCA 链路无法启动 |
| 排查过程 | 对照各容器运行 UID/GID 与 tmpfs 默认 root 所有权，逐个验证写入目录 |
| 根因 | Compose 的 tmpfs 挂载覆盖镜像目录后恢复成 root 所有权，而镜像按非 root 用户运行 |
| 解决方案 | 在各 tmpfs 声明中设置匹配镜像运行用户的 `uid`、`gid` 和 `mode` |
| 验证证据 | Redpanda、Prometheus、Alertmanager、Loki、Tempo 全部稳定启动，迁移容器退出码为 0 |
| 残余风险 | 升级镜像若改变运行 UID/GID，需要同步复核 Compose |
| 预防措施 | 非 root 镜像新增 tmpfs 时同时验证目录所有权和写入健康检查 |

### DEV-009：Tempo 搜索返回省略前导零的 Trace ID

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-23 / Step 5 |
| 模块 | `infrastructure/observability/tempo.py` |
| 分类 | 难点 |
| 状态 | 已解决 |
| 现象与证据 | 首个真实 Workflow 中 metrics、logs 成功而 traces 失败；Tempo `/api/search` 返回一条 31 位十六进制 `traceID` |
| 影响 | 单条摘要校验失败使整个 traces 工具失败，Workflow 无法生成四类 Evidence |
| 排查过程 | 直接执行两条 TraceQL，逐字段检查每条摘要，确认 31 位 ID 补一个前导零后用 31 位和 32 位形式均可从 Tempo 取回同一 Trace |
| 根因 | Tempo 搜索 JSON 对以零开头的 Trace ID 省略了前导零，而客户端只接受精确 16/32 位 |
| 解决方案 | 仍只接受最多 32 位十六进制字符，并按 64/128 位宽恢复前导零后输出规范化小写 ID |
| 验证证据 | Tempo 客户端 `20 passed`；真实三场景 Workflow 全部 `SUCCEEDED`，TRACE Evidence 齐全 |
| 残余风险 | Tempo API 版本升级后需继续用真实响应做契约测试 |
| 预防措施 | 外部观测 API 除 Mock 契约测试外，至少保留一条真实版本兼容验收 |

### DEV-010：Git 中文路径默认转义破坏 PowerShell 审计

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-23 / Step 6 |
| 模块 | 独立 Git 仓库初始化与提交前审计 |
| 分类 | 踩坑 |
| 状态 | 已解决 |
| 现象与证据 | `git ls-files --others` 把中文路径输出成带引号的八进制转义，PowerShell `Test-Path` 连续报告 `Illegal characters in path` |
| 影响 | 首轮候选文件大小与敏感文件统计漏掉中文目录，不能作为可靠提交依据 |
| 排查过程 | 对比 Git 原始输出与磁盘真实路径，确认文件未损坏，问题只发生在命令行显示编码层 |
| 根因 | Git 默认 `core.quotepath=true` 会转义非 ASCII 路径，返回值不是 PowerShell 可直接访问的路径 |
| 解决方案 | 在独立仓库设置 `core.quotepath=false`，重新统计 571 个候选条目并复核敏感文件、归档和数据库文件 |
| 验证证据 | 重新审计得到 570 个实际文件、约 5.67 MiB；真实 `.env` 与临时目录均被忽略，未发现私钥或云访问密钥形态 |
| 残余风险 | 其他终端若覆盖本地 Git 配置，中文路径输出可能再次被转义 |
| 预防措施 | 中文仓库的自动审计脚本显式使用 `git -c core.quotepath=false` 或设置仓库级配置 |

### DEV-011：锁定环境暴露根项目缺少显式打包元数据

| 字段 | 内容 |
|---|---|
| 日期/阶段 | 2026-07-23 / Step 5 |
| 模块 | `pyproject.toml`、CI、Docker 与运维资产测试 |
| 分类 | 难点 |
| 状态 | 已解决 |
| 现象与证据 | 首次 `uv run pytest -q` 有 3 个失败：Alembic 子进程无法导入 `devops_agent_platform`，另两个测试仍写死旧 pip/python 命令 |
| 影响 | 锁文件能解析依赖，但根项目本体没有进入隔离环境；资产测试也会错误阻止 locked 工具链迁移 |
| 排查过程 | 对比 MiniShop 与根 `pyproject.toml`，确认根项目缺少 `build-system` 和 `src` 包发现；检查失败断言后确认其验证的是命令文本而非门禁语义 |
| 根因 | 旧环境依赖 pytest `pythonpath` 与 editable pip 的隐式行为，打包边界未被独立验证；资产测试过度耦合实现字符串 |
| 解决方案 | 增加 setuptools 构建元数据与 `where = ["src"]`，CI 改用 `uv sync --locked`，测试改为验证锁定、Ruff、pytest、Alembic 和镜像安装语义 |
| 验证证据 | 定向资产测试 `10 passed`，锁定环境平台全量 `1531 passed, 9 skipped`，MiniShop `37 passed`，锁定版三场景 Docker E2E 通过 |
| 残余风险 | TestClient 仍有一条上游弃用告警，与依赖锁定无关 |
| 预防措施 | 新 Python 项目从第一天同时验证 `uv sync --locked`、子进程导入、wheel 构建和容器安装 |

## 6. 分类汇总

### 踩坑

- DEV-001：无独立 Git HEAD 时不能安全执行自动文档提交。
- DEV-004：新增多层目录前先建父目录，补丁失败后检查部分落盘。
- DEV-006：运行测试前确认项目开发依赖已安装到当前解释器。
- DEV-010：Git 中文路径审计前关闭 `core.quotepath` 转义或显式按原样输出。

### 犯过的错误

- DEV-003：首个组合补丁粒度过大，因单个上下文不匹配而整体失败。

### 主要难点

- DEV-002：在自包含演练和生产安全边界之间建立明确隔离。
- DEV-005：用当前 FastAPI lifespan 管理 OpenTelemetry Provider 关闭。
- DEV-007：非 ASCII 工作区需要验证 Docker 构建会话元数据兼容性。
- DEV-008：tmpfs 挂载必须匹配非 root 容器的实际 UID/GID。
- DEV-009：外部 Trace API 的 ID 序列化需要规范化兼容层。
- DEV-011：锁定环境必须同时验证依赖和项目本体的标准打包元数据。

## 7. 可复用解决经验

| 关联事件 | 问题模式 | 推荐解法 | 适用前提 | 不适用条件 | 验证方法 |
|---|---|---|---|---|---|
| DEV-001 | 自动化流程依赖 Git HEAD，但工作区没有可靠历史 | 停止自动 commit，保留未提交产物并说明限制 | 用户文件安全优先 | 已有独立、干净仓库 | `git status` 与提交历史检查 |
| DEV-002 | 本地 E2E 需要管理身份 | 显式演练认证开关 + 生产硬拒绝 | 仅本地、固定租户演练 | 生产或共享环境 | 配置负向测试 + 认证单测 |
| DEV-007 | Docker 构建上下文路径含非 ASCII | 在入口脚本显式选择已验证的构建后端 | 本地隔离演练 | 依赖 BuildKit 专属特性 | 在原始绝对路径执行真实构建 |
| DEV-008 | 非 root 镜像挂载 tmpfs 后失去写权限 | 按镜像 UID/GID 声明 tmpfs 所有权 | UID/GID 固定的镜像版本 | 动态或随机 UID | 容器启动日志 + 写目录健康检查 |
| DEV-009 | 外部系统省略标识符前导零 | 有界校验后恢复协议规定宽度 | 仍可确定 64/128 位语义 | 无法确定原始位宽 | Mock 边界测试 + 真实 API 查询 |
| DEV-010 | Git 输出含中文路径 | 关闭 quotePath 后再把输出交给文件系统工具 | 仓库允许本地 Git 配置 | 必须保持转义日志格式 | 候选数、文件数与磁盘统计交叉核对 |
| DEV-011 | 依赖锁存在但项目本体未安装 | 补标准构建元数据并在空 venv 验证子进程导入 | Python 包采用 src layout | 纯脚本且不作为包安装 | `uv sync --locked` + Alembic/CLI 子进程测试 |

## 8. 关键技术决策

| 决策 | 候选方案 | 最终选择 | 选择依据 | 代价/风险 |
|---|---|---|---|---|
| Alertmanager 接入 | 修改平台 canonical DTO / Alertmanager Relay | MiniShop 内置 Relay | 保留平台现有 HMAC 与扁平告警契约 | Relay 需要独立测试和超时处理 |
| 管理员认证 | 完整 IdP / 绕过认证 / 演练认证器 | 受控演练认证器 | 自包含且不破坏生产默认 | 必须严格防止生产启用 |
| 场景数据 | Markdown / JSON Manifest | 版本化 JSON Manifest + Pydantic 校验 | 无新增 YAML 解析依赖，便于测试和工具消费 | 人工编辑略显冗长 |

## 9. 技术债清偿状态

| 编号 | 原问题 | 状态 | 清偿方案 | 验证 |
|---|---|---|---|---|
| DEBT-001 | 项目没有独立 Git HEAD | 已解决 | 在项目根目录初始化独立 `main` 仓库，提交前排除 `.env`、虚拟环境、缓存和临时输出 | `git rev-parse --show-toplevel` 指向当前项目；基线提交后工作区干净 |
| DEBT-002 | 开发依赖和 Ruff 未精确锁定 | 已解决 | 根平台与 MiniShop 各自提交 `uv.lock`；CI/Docker 使用 `uv 0.11.31 --locked`；Ruff 固定 `0.15.22` | 两个 lock check、隔离环境全量测试、Ruff、锁定版 Docker E2E 均通过 |

## 10. 最终复盘

### 做得好的地方

- 从 Manifest 标准答案倒推指标、日志、Trace、告警和报告断言，避免只验证 Workflow 状态。
- 保留平台 HMAC、权限、租户、Outbox、Kafka 和租约边界，没有为演练绕过生产核心契约。
- 每个真实容器故障都先保留原始证据，再做最小修改并重跑完整链路。

### 可以改进的地方

- 应更早在包含中文的真实绝对路径执行 Docker 构建，静态 Compose 展开无法发现 Buildx 会话头问题。
- 应在项目开始时创建隔离虚拟环境并锁定工具版本，避免全局 Anaconda 包冲突和 Ruff 漂移。
- Tempo Mock 响应应尽早加入前导零被省略的真实样本。

### 下次直接复用的经验

- 端到端 RCA 验收至少同时检查：Incident、Workflow、Invocation、四类 Evidence、报告引用和 Ground Truth。
- 本地固定 Token 必须默认关闭、与 OIDC 互斥、生产硬拒绝，不能用“仅演示”代替安全边界。
- 非 root 可观测栈使用 tmpfs 时，把 UID/GID 验证纳入 Compose 资产测试和启动验收。

### 验证与已知限制

- 已完成验证：平台 `1531 passed, 9 skipped`，MiniShop `37 passed`，
  E2E 资产 `3 passed`，Ruff check 和 Compose config 通过。
- 已完成真实链路：三份 Manifest 均在重建后的 Docker 环境得到
  `SUCCEEDED` Workflow 与四类 Evidence，结果文件 `passed: true`。
- 已完成工程基线：独立 Git `main` 仓库、两个 `uv.lock`、锁定 CI 与锁定
  Docker 安装路径均已验证。
- 已知限制：TestClient 保留一条上游弃用告警，后续升级到 httpx2 时再迁移。
