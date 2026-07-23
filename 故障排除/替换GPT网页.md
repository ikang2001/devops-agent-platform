# 替换为 GPT

> C:\Users\MSN\Desktop\llm\AgentChat-master  这个项目你看一下，我能换成gpt吗

<details><summary>4 previous messages</summary>

> 我先把项目的模型调用链和配置入口摸清楚，重点看它现在是否绑定 Claude、是否已有 OpenAI 兼容层，以及改成 GPT 需要动哪些文件。只做检查，先不给项目写入改动。
>
> 先说结论方向：能换，但这里的“换”有两层——把外部规划 Agent 换成 GPT/Codex，几乎不用改；把浏览器里被操控的 Gemini Web 换成 ChatGPT Web，则不是改一个 `MODEL=gpt` 就行，核心脚本明显写死了 Gemini 的页面结构。我再定位具体耦合点，给你准确改造量。
>
> 如果你的目标是“让这个项目最终调用 GPT”，我更建议接 OpenAI API，而不是继续抓 ChatGPT 网页 DOM：API 稳定得多，也更容易做模型切换。我会按 OpenAI 官方接入规范核一下最合适的接口形态，再给你一个不绕弯的改造结论。
>
> <details><summary>Searched the web</summary>
>
> <details><summary>Searched the web</summary>
>
> - Searched the web for `site:developers.openai.com/api/docs Responses API JavaScript official OpenAI`
>
> </details>
>
> </details>
> </details>

可以换成 GPT，但分两种：

1. **让 GPT/Codex 当 Planner**：基本不用改。项目只是接收命令行 Prompt、输出结果，并未绑定 Claude SDK。[调用入口](./AgentChat-master/skills/gemini-web-extended-thinking/SKILL.md:215)

2. **把 Gemini Web 替换成 GPT**：可以，但不是改模型名。当前代码写死了 Gemini URL、输入框、模型选择器和回复 DOM。[核心代码](./AgentChat-master/skills/gemini-web-extended-thinking/index.js:32)

我建议改成 **OpenAI API + Responses API**：

- 新增 `OPENAI_API_KEY`、`OPENAI_MODEL`
- 保留现有命令行输入/标准输出协议
- 移除 Chrome、CDP、网页选择器和 Gemini 登录依赖
- 比自动操作 ChatGPT 网页稳定得多
  [OpenAI 官方模型/API说明](https://developers.openai.com/api/docs/models)

如果坚持使用 ChatGPT 网页免费版，也能做，但需要重写网页交互层，页面更新后容易失效。

另外发现现有项目有个问题：虽然定义了 `CHROME_PROFILE`，启动时却用了普通 `chromium.launch()`，登录状态实际上没有接入持久 Profile。[启动代码](./AgentChat-master/scripts/start-chrome-debug.py:79)

目前我没有修改文件。你要的话，我下一步可以直接给它改成 **GPT API 版**。

> 使用GPT API 版不需要token吗？为什么不调用网页版。。 你仔细看看项目定义
>
> User context
>
> Attachments:
>
> - AgentChat — Gemini Web Automation Chrome CDP + Playwright 驱动的 Gemini Web 自动化交互系…: `~/.codex/attachments/2ea4c13d-4933-47a9-8bd4-53b703502e91/pasted-text.txt`

<details><summary>2 previous messages</summary>

> 你说得对，我上一条把“更稳定”放在了项目目标前面，偏题了。这个项目的核心恰恰是绕开模型 API 计费，用已有 Web 账号做“免费大脑”；我现在按附件和源码重新核对它的定义，以及换成 ChatGPT Web 到底要保留哪些机制。
>
> 重新核对后，项目定位很明确：它不是“统一模型 API 客户端”，而是“低成本 Planner + 免费 Web 端推理”的 CDP 桥。换成 GPT 时，正确替换对象应当是 **Gemini Web → ChatGPT Web**，而不是改成 OpenAI API。源码里还有两处实现与文档目标不一致，我一并说明。
> </details>

你说得对，我上一条判断偏离了项目核心。

**GPT API 必须有 API Key，并按输入/输出 Token 计费。**
这个项目追求的是“免费 Web 大脑”，所以正确方案应是：

> Planner → CDP/Playwright → ChatGPT 网页版 → 读取回答

这样不需要 `OPENAI_API_KEY`，也不产生 API Token 费用；只需要登录 ChatGPT 网页账号，但仍受免费版或订阅账号的使用额度限制。

项目本身也明确写了：

- [网页版免费、节省 API Token](./AgentChat-master/README.md:26)
- [接入 ChatGPT Web](./AgentChat-master/README.md:41)
- [复用 CDP，只替换页面适配层](./AgentChat-master/README.md:48)

正确改造应该是新增 `chatgpt-web` Skill，保留现有 CDP、Playwright、代理和命令行协议，只替换：

- 地址为 `https://chatgpt.com/`
- 登录状态检测
- 模型选择器
- 输入框与发送按钮
- 停止生成与完成检测
- ChatGPT 回复内容提取
- 网页额度限制检测

不过当前源码有两处需要顺手修复：

1. 定义了 `CHROME_PROFILE`，却没有传给持久浏览器上下文，登录可能无法保存。[启动代码](./AgentChat-master/scripts/start-chrome-debug.py:79)

2. 文档要求复用对话，但代码每次都 `newPage()`，实际会丢失上下文。[页面创建](./AgentChat-master/skills/gemini-web-extended-thinking/index.js:112)

所以结论是：**应当调用 ChatGPT 网页版，不应改成 GPT API。**我之前推荐 API 版不符合这个项目的“近乎零成本”定义。

> 我想要ChatGPT 网页版和gemini网页版都保留，甚至加入豆包。到时候根据使用者需求，转化选择

<details><summary>16 previous messages</summary>

> 明白：保留三个网页端，做成一个统一入口，既允许使用者明确指定 `gemini/chatgpt/doubao`，也支持 `auto` 根据任务意图和可用状态选择，并在失败时按配置降级。我会先把现有 Gemini 的可靠性逻辑原样保护住，再新增适配器和路由层。
>
> 这个改造涉及真实网页 DOM 和登录态，我会使用浏览器控制技能核对页面结构；如果当前环境没有相应登录态，就用稳健的多选择器和诊断模式实现，并明确哪些测试只能做到静态验证。
>
> <details><summary>Called 3 tools</summary>
>
> MCP tool call
>
> node_repl.js
>
> ```json
> {
> "code": "if (globalThis.agent?.browsers == null) {\n  const { setupBrowserRuntime } = await import(\"C:/Users/MSN/.codex/plugins/cache/openai-bundled/browser/26.623.42026/scripts/browser-client.mjs\");\n  await setupBrowserRuntime({ globals: globalThis });\n}\nglobalThis.browser = await agent.browsers.get(\"iab\");\nnodeRepl.write(await browser.documentation());",
> "timeout_ms": 30000,
> "title": "连接并读取浏览器能力"
> }
> ```
>
> ```text
> Browser is not available: iab
> ```
>
> MCP tool call
>
> node_repl.js
>
> ```json
> {
> "code": "nodeRepl.write(await agent.documentation.get(\"bootstrap-troubleshooting\"));",
> "timeout_ms": 30000,
> "title": "检查浏览器连接状态"
> }
> ```
>
> ```text
> # Browser Runtime Troubleshooting
> 
> - If browser setup completed but discovery or selection fails, reuse the existing `agent`; do not reset the JavaScript session or import another browser runtime.
> - Inspect `await agent.browsers.list()` once to see which browser types are available. Do not assume that a missing requested browser can be replaced with another backend when the user explicitly named it.
> - If a requested backend has specific troubleshooting documentation in the skill's setup catalog, read it before retrying.
> - If the requested browser remains unavailable, report that plainly instead of controlling it through an unrelated browser tool or source-code workaround.
> ```
>
> MCP tool call
>
> node_repl.js
>
> ```json
> {
>   "code": "nodeRepl.write(JSON.stringify(await agent.browsers.list(), null, 2));",
>   "timeout_ms": 30000,
>   "title": "查看可用浏览器"
> }
> ```
>
> ```text
> []
> ```
>
> </details>
>
> 当前会话没有可连接的内置浏览器，所以无法现场读取三家登录后的 DOM；我不会假装做过网页实测。实现会采用“多候选选择器 + 环境变量覆盖 + `--smoke/--doctor` 诊断”的方式，并用本机 CDP（若已启动）做可达性验证。这样页面改版时不必重写整个路由层。
>
> <details><summary>Searched the web</summary>
>
> <details><summary>Searched the web</summary>
>
> - Searched the web for `豆包 官方 网页版 对话 doubao.com/chat ...`
> - Searched the web for `https://chatgpt.com/`
>
> </details>
>
> </details>
>
> 架构我定成“保守扩展”：现有 Gemini 执行器不重写，避免破坏它已经调过的 Pro Extended 流程；新增统一路由器、ChatGPT/豆包适配器和公共网页桥。`auto` 会先识别显式指令，再按任务特征评分，最后按可配置优先级选择；只有可恢复故障才自动换下一家，安全拒绝不会偷偷绕到别家。
>
> <details><summary>Created 15 files, edited a file</summary>
>
> - Wrote `./AgentChat-master/index.js` (+228 -0)
> - Wrote `./AgentChat-master/lib/env.js` (+29 -0)
> - Wrote `./AgentChat-master/lib/providers.js` (+119 -0)
> - Wrote `./AgentChat-master/lib/router.js` (+125 -0)
> - Wrote `./AgentChat-master/lib/web-provider-bridge.js` (+382 -0)
> - Wrote `./AgentChat-master/package.json` (+22 -0)
> - Wrote `./AgentChat-master/scripts/connect-provider.py` (+81 -0)
> - Wrote `./AgentChat-master/skills/chatgpt-web/SKILL.md` (+27 -0)
> - Wrote `./AgentChat-master/skills/chatgpt-web/index.js` (+4 -0)
> - Wrote `./AgentChat-master/skills/chatgpt-web/package.json` (+10 -0)
> - Wrote `./AgentChat-master/skills/doubao-web/SKILL.md` (+17 -0)
> - Wrote `./AgentChat-master/skills/doubao-web/index.js` (+4 -0)
> - Wrote `./AgentChat-master/skills/doubao-web/package.json` (+10 -0)
> - Wrote `./AgentChat-master/skills/web-llm-router/SKILL.md` (+21 -0)
> - Wrote `./AgentChat-master/tests/router.test.js` (+29 -0)
> - Wrote `./AgentChat-master/lib/web-provider-bridge.js` (+30 -4)
>
> </details>
>
> 路由单元测试已经通过，三个入口和中文前缀都能正确选择。现在我在处理共享登录态：三个网页必须共用一个持久 Chrome Profile，否则 ChatGPT/豆包每次重启都要重新登录；同时把启动脚本从“只启动 Gemini”改成可配置启动多个网页，但默认仍只开 Gemini，避免无谓占资源。
>
> <details><summary>Created 2 files, edited 4 files</summary>
>
> - Wrote `./AgentChat-master/.env.example` (+19 -1)
> - Wrote `./AgentChat-master/scripts/connect-chatgpt.sh` (+4 -0)
> - Wrote `./AgentChat-master/scripts/connect-doubao.sh` (+4 -0)
> - Wrote `./AgentChat-master/scripts/start-chrome-debug.py` (+82 -24)
> - Wrote `./AgentChat-master/scripts/start-chrome-debug.sh` (+6 -5)
> - Wrote `./AgentChat-master/skills/gemini-web-extended-thinking/index.js` (+14 -5)
>
> </details>
>
> 静态检查、路由测试和 CDP 诊断都通过；当前共享浏览器里只有 Gemini 标签，没有 ChatGPT/豆包。我接下来会分别跑三家的 `--smoke`：只检查页面能否打开和输入框能否识别，不发送任何消息，也不会消耗网页额度。
>
> <details><summary>Edited 5 files</summary>
>
> - Wrote `./AgentChat-master/README.md` (+69 -2)
> - Wrote `./AgentChat-master/lib/providers.js` (+7 -0)
> - Wrote `./AgentChat-master/lib/web-provider-bridge.js` (+8 -0)
> - Wrote `./AgentChat-master/scripts/setup.sh` (+12 -3)
> - Wrote `./AgentChat-master/scripts/start-chrome-debug.py` (+0 -1)
> - Wrote `./AgentChat-master/scripts/start-chrome-debug.py` (+21 -1)
>
> </details>
>
> 持久 Profile 启动测试暴露了一个本机环境差异：系统默认 `python` 没装 Playwright，而当前正在运行的 Gemini 浏览器来自另一个 Conda Python。代码本身已通过编译；我会用项目实际在用的 Python 再验一次，并把启动器的依赖错误提示做得更明确。
>
> <details><summary>Edited 8 files</summary>
>
> - Wrote `./AgentChat-master/README.md` (+33 -15)
> - Wrote `./AgentChat-master/scripts/setup.sh` (+1 -1)
> - Wrote `./AgentChat-master/scripts/start-chrome-debug.py` (+8 -1)
> - Wrote `./AgentChat-master/index.js` (+2 -1)
> - Wrote `./AgentChat-master/lib/router.js` (+3 -2)
> - Wrote `./AgentChat-master/lib/web-provider-bridge.js` (+13 -3)
> - Wrote `./AgentChat-master/scripts/start-chrome-debug.py` (+13 -6)
> - Wrote `./AgentChat-master/tests/router.test.js` (+4 -0)
> - Wrote `./AgentChat-master/.gitignore` (+3 -0)
>
> </details>
>
> 最后一轮验证会覆盖：所有 JS/Python 语法、路由规则、CDP 状态、Gemini 冒烟、故障降级，以及持久 Profile 独立端口启动。不会发送真实 Prompt。已知的外部状态是：ChatGPT 正停在 Cloudflare 真人验证页，豆包页面要求登录；完成一次可见浏览器登录后才能做发送/回读实测。
>
> <details><summary>Edited 2 files</summary>
>
> - Wrote `./AgentChat-master/index.js` (+4 -1)
> - Wrote `./AgentChat-master/skills/gemini-web-extended-thinking/index.js` (+13 -10)
>
> </details>
>
> 我补了一道数据边界：自动“选择”与失败后“把同一 Prompt 再发给另一家”不是一回事。默认只自动选一家，不会悄悄跨平台重发；需要故障转移时显式加 `--fallback`，或配置 `AUTO_FALLBACK=1`。这能避免超时后同一份敏感内容被投递到多个网页端。
>
> <details><summary>Edited 4 files</summary>
>
> - Wrote `./AgentChat-master/.env.example` (+2 -0)
> - Wrote `./AgentChat-master/README.md` (+7 -2)
> - Wrote `./AgentChat-master/index.js` (+5 -1)
> - Wrote `./AgentChat-master/skills/web-llm-router/SKILL.md` (+3 -2)
>
> </details>
> </details>

已改成三网页端并存，不调用模型 API，因此没有 API Token 费用，只受各网页账号额度限制。

核心功能：

- `auto/gemini/chatgpt/doubao` 路由：[index.js](./AgentChat-master/index.js)
- ChatGPT、豆包适配器：[providers.js](./AgentChat-master/lib/providers.js)
- 三家共享持久登录 Profile：[start-chrome-debug.py](./AgentChat-master/scripts/start-chrome-debug.py)
- 完整使用说明：[README.md](./AgentChat-master/README.md)

支持：

```powershell
node index.js --provider=auto "分析这篇论文"
node index.js --provider=chatgpt "检查代码"
node index.js --provider=doubao "写中文文案"
node index.js --route-only "问题"       # 只看路由结果
node index.js --provider=auto --fallback "问题"
```

也支持 `/gemini`、`/chatgpt`、`/豆包` 前缀。默认不会跨平台重复发送 Prompt，`--fallback` 才允许故障转移。

验证结果：

- Gemini：冒烟测试通过
- ChatGPT：已打开[官方网页版](https://chatgpt.com/)，等待手动完成人机验证
- 豆包：已打开[官方网页版](https://www.doubao.com/chat/)，等待登录
- 路由测试、语法检查、持久 Profile 启动、依赖审计全部通过

当前默认 Python 没有 Playwright，需要用已有的 `yolo` 环境重新启动并登录：

```powershell
Stop-Process -Id (Get-Content "$HOME\.local\state\agentchat\chrome-debug.pid")

$env:HEADLESS="0"
$env:STARTUP_PROVIDERS="gemini,chatgpt,doubao"

& "$HOME\.conda\envs\yolo\python.exe" scripts\start-chrome-debug.py
```

在打开的浏览器中完成 ChatGPT 真人验证和豆包登录后，三个网页端即可正式切换使用。

> 所以这个项目该如何使用

这个项目的使用逻辑是：

> 先保持专用 Chrome 运行 → 再通过命令把问题发送给 Gemini、ChatGPT 或豆包网页。

## 第一次使用：登录三个网页

在 PowerShell 中执行：

```powershell
cd C:\Users\MSN\Desktop\llm\AgentChat-master

# 停止当前旧浏览器进程
$pidFile = "$HOME\.local\state\agentchat\chrome-debug.pid"
if (Test-Path $pidFile) {
    Stop-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue
}

# 打开可见浏览器
$env:HEADLESS="0"
$env:STARTUP_PROVIDERS="gemini,chatgpt,doubao"

& "$HOME\.conda\envs\yolo\python.exe" scripts\start-chrome-debug.py
```

浏览器打开后：

1. 登录 Gemini。
2. 完成 ChatGPT 真人验证并登录。
3. 登录豆包。
4. 保持这个 PowerShell 和浏览器运行。

然后打开第二个 PowerShell：

```powershell
cd C:\Users\MSN\Desktop\llm\AgentChat-master
node index.js --doctor
```

测试三家输入框：

```powershell
node index.js --provider=gemini --smoke
node index.js --provider=chatgpt --smoke
node index.js --provider=doubao --smoke
```

## 正式使用

自动选择：

```powershell
node index.js --provider=auto "分析这篇论文中的关键观点"
```

明确指定：

```powershell
node index.js --provider=gemini "分析这份长文档"
node index.js --provider=chatgpt "帮我检查这段代码"
node index.js --provider=doubao "写一段小红书中文文案"
```

也可以使用提示词前缀：

```powershell
node index.js "/gemini 分析这个科研问题"
node index.js "/chatgpt 设计一个后端架构"
node index.js "/豆包 写一段短视频口播"
```

只查看会选择谁，不真正发送：

```powershell
node index.js --route-only "帮我调试 Python 代码"
```

允许失败后切换其他网页端：

```powershell
node index.js --provider=auto --fallback "你的问题"
```

开始新对话：

```powershell
node index.js --provider=chatgpt --new-chat "新任务"
node index.js --provider=doubao --new-chat "新任务"
```

## 日常无界面运行

完成首次登录后，可以按 `Ctrl+C` 停止可见浏览器，再后台启动：

```powershell
$env:HEADLESS="1"
$env:STARTUP_PROVIDERS="gemini,chatgpt,doubao"

Start-Process `
  -FilePath "$HOME\.conda\envs\yolo\python.exe" `
  -ArgumentList "scripts\start-chrome-debug.py" `
  -WorkingDirectory "C:\Users\MSN\Desktop\llm\AgentChat-master" `
  -WindowStyle Hidden
```

之后直接运行 `node index.js ...` 即可，不需要 API Key。完整说明在 [README.md](./AgentChat-master/README.md)。

> (yolo) PS C:\Users\MSN\Desktop\llm\AgentChat-master> & "$HOME\.conda\envs\yolo\python.exe" scripts\start-chrome-debug.py
> 13:02:33 [daemon] gemini loaded: status=200 title=‎Google Gemini
> 13:02:35 [daemon] chatgpt loaded: status=200 title=ChatGPT
> 13:02:36 [daemon] doubao loaded: status=200 title=豆包 - 字节跳动旗下 AI 智能助手
> 13:02:36 [daemon] Daemon ready — CDP http://127.0.0.1:9222 providers=gemini,chatgpt,doubao PID=39524
> 13:03:36 [daemon] Heartbeat check failed: Page.evaluate: Execution context was destroyed, most likely because of a navigation
> Traceback (most recent call last):
> File "C:\Users\MSN\Desktop\llm\AgentChat-master\scripts\start-chrome-debug.py", line 335, in <module>
>  main()
>  ~~~~^^
> File "C:\Users\MSN\Desktop\llm\AgentChat-master\scripts\start-chrome-debug.py", line 313, in main
>  crash_count += 1
>  ^^^^^^^^^^^
> UnboundLocalError: cannot access local variable 'crash_count' where it is not associated with a value   这个是什么情况
>  ~~~~

<details><summary>3 previous messages</summary>

> 这是两个问题叠在一起：
>
> 1. Gemini/ChatGPT 登录跳转时，页面正在导航，旧的 JS 执行上下文被销毁——这是正常瞬时状态，不是浏览器崩溃。
> 2. 守护进程把它误判成崩溃后执行 `crash_count += 1`，但 `main()` 漏了 `global crash_count`，于是 Python 抛出 `UnboundLocalError` 并退出。
>
> 我直接修复：心跳只检查浏览器连接和页面存活，不再在导航期间执行页面 JS；同时修正计数器作用域并补回健康后的计数清零。
>
> <details><summary>Edited a file</summary>
>
> - Wrote `./AgentChat-master/scripts/start-chrome-debug.py` (+38 -15)
> - Wrote `./AgentChat-master/scripts/start-chrome-debug.py` (+5 -1)
>
> </details>
>
> 修复已经写入。我会用独立的 `9333` 端口和临时 Profile 跑一个加速心跳测试，不碰你正常的 `9222` 浏览器；连续通过多个心跳后再给你重启命令。
> </details>

这是心跳检测误判加 Python 作用域 Bug，不是账号或网页故障。

已修复：

- 导航时不再执行 `page.evaluate()`
- 修复 `crash_count` 作用域
- 页面导航不再被误判为崩溃
- 独立端口连续心跳测试已通过

当前 `9222` 已关闭，直接重新运行：

```powershell
& "$HOME\.conda\envs\yolo\python.exe" scripts\start-chrome-debug.py
```

保持窗口运行，在另一个 PowerShell 验证：

```powershell
node index.js --doctor
node index.js --provider=chatgpt --smoke
node index.js --provider=doubao --smoke
```

修复位于 [start-chrome-debug.py](./AgentChat-master/scripts/start-chrome-debug.py)。

> 为什么gpt网页卡在这，gemini网页
>
> User context
>
> Images:
> - `data:image/png;base64,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AAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6QkcAAAAQHoCBwAAAJCewAEAAACkJ3AAAAAA6fV7vVV9DQDANrBixapYtuy1WL78tep62LAhMXLk8Nhzz91iyJDB9WcBANuSwAEAsA20tKyPBx54NO677+FYuXJ1vdrThAl7xmGHHRgHHbRvvQIAbAsCBwDAVnr22cVxww23x2uvraxXNm/PPXePqVNPiF13HVOvAABbQ+AAANgKDz30RNx00531XVflsZRRo4ZH+dfWSy+9Es3NLfUrbQYObIrp098b48fvUa8AAG+UwAEA8Abdf/8jMWvW7+q7NpMnHxj7779PTJgwLpqaBtSrbZ588pnqEZbnnnuxXonqc972tgPi5ZeXR/lnWZnRMWLEsNh997Gt7/MWMzsAoI8EDgCAN2Dhwj/Er3/92/ouqkdNzjjjlBg9ekS90rsSOW699d76btP22Wd8nHTSsbHLLqPrFQCgEYEDAOANuPjiK+KVV5ZX1+VRlLPP/kAMHTqkuu9NGUT6+9/PjbvvfqBe6bvjj397vOMdh9d3AEB3AgcAwBZ68smn4+qrZ9V3ER/72GnVEbCbUr5m1qzf9xhEOnz40OoRlVGjRlShZO3adfHCC0ti0aIXYvHiJfVntSmzOj784VNj0KCB9QoA0E7gAADYAuvWNceVV95cBYiizMk4/fSTq+tGyi6PmTPviWeeeb5eaTNs2JA4/vgjqiNje1MCRzmdpX2nSFFCSgkqAEBXAgcAQB888cTTMXfu4/H008/VK216271RQkh5FKXM2+juyCPfWj1y0tedGL/5za3x2GML6ruId73rqDj66LfVdwBAIXAAAGzC0qWvxm9/e1csWrS4XulQTjj53Oc+Wt91eOSR+dUQ0VWr1tQrbcrA0Pe857gYM2ZUvdJ3ZaBpGWxalJNXPv3pMzc78wMAdib96/8CANDNiy++HJdddm3DuFGMGTOyvmpTdm38z/9cF9dff3uXuDF69Mg444z3xPTp720YN0pEWb78tfqusfe//8RqXkdRhpU+8MBj1TUA0EbgAABo4KWXXolf/OL6auhnu8mTD+xykkkZDNpZCSHPPfdifde206I8TnLeedNj0qS969UOq1evqXaHXHTRr+PHP/5V/O///r6KJI0MGTIopkw5qr6LuP/+R+orAKAQOAAAumlpaYmrr54Zzc0t1f3YsaPjnHM+GKeeevzGXRRFv3796qs2GzZsqK8iDj10/zj//DN7nZVx770PxYUXzqjmerSbPXte/PjHM2LevPn1SlcHHLBPfRWxZs3aWLJkaX0HAAgcAADdlONcly9fUV2Xx0s++tH3x267ja3uO+vWN1q1LZTPnTr1ndVJKd09+eQzVdi47bb7qoBSdmbsvfef1K+WXR1rq5NTfv7z31S7SDobOLApxo3btb6LeO65l+orAEDgAADoZMWKVV12VZx00jHVMNGt9fLLy+KXv7yh2hny6qtt8eTwww+Oc8+dHmeeObU6anbkyOHVevH88y/Fz352ddx8893Vbo12u+46pr4qMz86Hp8BgJ2dwAEA0EnnuFF2SzSanbElyrDRMmfjpz+9cuOw0nKayic+cUZ1okp7PNl//7dUJ6OccMIR1eyOdg8++Fj85Ce/ijlzHq3uN2zofABejy0kALDTEjgAADopj5C0621+Rl/97ncPxoUXXt4lmhTllP7u8zvaHXfcYdWujoMP3rdeKfM21sXMmffEJZdcVZ3s0q483gIAtBE4AABqZSZG58Gdb3lLx2yMLbFy5apqzsYdd9xfHek6ePCgeNe7jo7DDjuoev2ZZ56Piy++Im65pevjJ+1GjBhWHQt71lnvj112GV2vRvW9vfLK8vqu7DDZrb4CAAQOAIBa+2DRYujQIVs8e6PszCjKoND2ORtHHXVonHfeR+Loow+NU055R7U7ozyOUjzwwGPV8bC9Hfm61157xCc/+aHq9JZG38vuu/ccfAoAOyuBAwCg1vmY13JiydY45JD94s///E/jxBOP7vIoyZgxI6uBoh/72GkxfvwesXbtupg163fVnI0FCxbVn9XV5MkHxqc+9eEeO0ruumtOfQUACBwAALVBgwbWV2UXxpr6asuVY2KnTZtSPWrSmz333C2mT39vfRexbNlrccUVN8eMGTd2eQyl3dChg+MjH5laxY52d9/9QCxevKS+A4Cdm8ABAFAruyvaTzBpm8exrLru3bY7xaQ8ylJ+7c3N5yiPq5RHV9rNnPm7+goAdm4CBwBAJ+WxkXbz5j1ZX3UYOLBjl8eSJa/EypWr67utc8wxk6tZHeXRlqJ9PsdDDz1R3Xc2bdq76quIxYtfihdffKW+A4Cdl8ABANDJQQd1HM/64IOP9QgYhxwyKY444pDquuzw+PGPZ8Q99zxYnZaytYYPH1o92vJnf/aBaGpqquZz3HTTnbFixar6M9qMGjUi9ttv7/ou4oknFtZXALDzEjgAADopgaP9xJLymMott9xTXXd20knHVqeblKGfJWzceef9mxwS2jdtJ7AU48bt2mUw6bp1zfVVhwMPnFhfRbzwwsv1FQDsvAQOAIBOyukpxxzztvou4sknn46ZM3tGjl12GV0N/fzgB99Tze4ouywefrjnIy3by2677VJfRbz88uZmhQDA/38CBwBAN2Xg55/8ye71XcScOY/GddfdGuvXdxwj2648KvLJT3443v3uY2Lw4LZdF0uWLI0bbri9x6Mlnb3wwpL41a9urO8iXu/YwFF5vdNC5+t2gwd3zAJptMMDAHY2AgcAQDf9+vWLd77ziPquzaOPLoif/eyq6pST7vr37xdHHvnWOO+86XH44QdXa/PmzY8f/eiXcdtt93YJEOU42KuvnhWXXnptPPfcS/Vq+TXri1r5Htp1vm7XObb07++fdADg/4YAAA00OiL2lVeWx4wZN8a11/5vvPrqinq1Q5nd8Z73HLdxPkdx770Px3/91+Wt/30obr757mpWR3nspei8S2RLLVv2an1Vho4Or68AYOclcAAANLB48ZL6KmKffcbHsGFD6ruIxx9fGBdeOKMaLtrS0lKvdmifz3HGGW3zOcppKLfddl91KktRTkH5wAdO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AKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkJ7AAQAAAKQncAAAAADpCRwAAABAegIHAAAAkFzE/wFpHyrY9qb3OgAAAABJRU5ErkJggg==`
> - `data:image/png;base64,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iAMAAAAAAOAChDgAAAAAAAAuQIgDAAAAAADgAoQ4AAAAAAAALkCIAwAAAAAA4AKEOAAAAAAAAC5AiAMAAAAAAOAChDgAAAAAAAAuQIgDAAAAAADgAoQ4AAAAAAAALkCIAwAAAAAA4AKEOAAAAAAAAC5AiAMAAAAAAOAChDgAAAAAAAAuQIgDAAAAAADgAoQ4AAAAAAAALkCIAwAAAAAA4AKEOAAAAAAAAC5AiAMAAAAAAOAChDgAAAAAAAAuQIgDAAAAAADgAoQ4AAAAAAAALkCIAwAAAAAA4AKEOAAAAAAAAC5AiAMAAAAAAOAChDgAAAAAAAAuQIgDAAAAAADgAoQ4AAAAAAAALkCIAwAAAAAA4AKEOAAAAAAAAC5AiAMAAAAAAOAChDgAAAAAAAAuQIgDAAAAAADgAoQ4AAAAAAAALkCIAwAAAAAA4AKEOAAAAAAAAC5AiAMAAAAAAOAChDgAAAAAAAAuQIgDAAAAAADgAoQ4AAAAAAAALkCIAwAAAAAA4AKEOAAAAAAAAC5AiAMAAAAAAOAChDgAAAAAAAAuQIgDAAAAAADgAoQ4AAAAAAAALkCIAwAAAAAA4AKEOAAAAAAAAC5AiAMAAAAAAOAChDgAAAAAAAAuQIgDAAAAAADgAoQ4AAAAAAAALkCIAwAAAAAA4AKEOAAAAAAAAC5AiAMAAAAAAOAChDgAAAAAAAAuQIgDAAAAAADgAoQ4AAAAAAAALkCIAwAAAAAA4AKEOAAAAAAAAC5AiAMAAAAAAOAChDgAAAAAAAAuQIgDAAAAAADgAoQ4AAAAAAAALkCIAwAAAAAA4AKEOAAAAAAAAC5AiAMAAAAAAOAChDgAAAAAAAAuQIgDAAAAAADgAoQ4AAAAAAAALkCIAwAAAAAA4AKEOAAAAAAAAC5AiAMAAAAAAOAChDgAAAAAAAAuQIgDAAAAAADgAoQ4AAAAAAAALkCIAwAAAAAA4AKEOAAAAAAAAC5AiAMAAAAAAOAChDgAAAAAAAAuQIgDAAAAAADgAoQ4AAAAAAAALkCIAwAAAAAA4AKEOAAAAAAAAC5AiAMAAAAAAOAChDgAAAAAAAAuQIgDAAAAAADgAoQ4AAAAAAAALkCIAwAAAAAA4AKEOAAAAAAAAC5AiAMAAAAAAOAChDgAAAAAAAAuQIgDAAAAAADgAoQ4AAAAAAAALkCIAwAAAAAA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Rm7+iv5zelovv7LS3tf4+tfq17/eYh9vesbEvnebXlJ1pdauXWvvW041r3yVdu/OKfXb3zh7AAAAgMOR0c++0acvfM5avvErPTg1lF+/4z49MnWPnvbmTr33/S9TbjqjCy/25u/y8F365A0/ddZvtdZ/lV/Xw7p/roVfMnL3jWlHlfU6Xu/Vj6az8mbv1PXdv9KG7Nf0yQ+/R697/tWK3OccrJx+9PGX65pgj0LBD+r+qs/rqzddq4vsm+7VHZPWC/TYBy5t203WY16qyw9Yukor8Mj+RJ944/t099r79IU3vERXvcwsf2Gd53t0e/H31D/tcW57n753f5/eZq+/U7fcd6+m175QG5zD4A4rtu/M7XfWgVPSA1u36p96e/Shj3xM99+X0k8SP9bVr36N/u3Gf1Xo45/UL/97s344OanrP9St1atX2/f53QNb9dl/+rSuekWtfp78L3vfE48/rp//PKkXVVwxe9xVNa/QlS/7G0VusH5Zfr7P3reUM844Q//2lX+X7/zz9bZrr9Ef/vCgc8vSzHPdeNPNzhYAAABwINPjZsRkMg/+1Gq8P09XX3aOLq7r1MZvvU33v+/buviLV+uWmm+rv8YJcUxI8flL9O2+lznrPvVfs1b37/ipIn//c2365za9yBx30Qt11WU++y4n07avXK0vXHK72n/6t7r5im55Pt2jtaEb9dZLpIe/9U51PdytG991idae45O3KKD5UefV1uu/XW+1ExzLPSFVfPVKTfe90tmRV7h+D06P6cGLGvSiqtfo6uyndEfN7fro5c5BuVvVfm1GH/nmO/KB0EmWvecmvS84qqddfo4evLhbX3r/C+XNpRS59m269/236zNVzs/allP24ax266f65Dt+pdabWnThWq/W39+jireMWvvnvOrLv7Due6iUCycTIQ5OeWaI0u9/9zvd9G83qO26dm3bllb20bloeu++vfaQqQ0bLtamy19oHx/+x4/qQeu4T//T5+xjTD2dT348pKm77lTTtW/Vu657t73/rLPWad26dXaIc4n/Ofqbl19l7zfDqYb+7xfV8+k++3YzrKr7g51qe/ffqfz5f2EfY7q5msfs+vBH9YxnXGjvM26b+K5+/l//pQ92f1QrVqxw9gIAAACLM8OpdpiW+NQH9c77grrx7RfZgcYvup0Q45w79Ynrvqmrvtyvl5q2vQluage042mmsZ7VI1eE9aXLfqLb79umn92W1cZXP0/n7PiVbvd0atoEPSdZPsT5oi68YUxX39Sp9VN9+sJEYQhY3o57b9Xa9/+3PlOV0Y9u6LNeiwllbtWOy16jjedcoqvff50u/NbV6lp7g77x9jLnXnkP3/t9bd52n275+89qx9u+qNarLtGLdg/obfcG9e13+fMH/TSk101dq2+//3n57ZMpl9Itn79LG9/zDm3yZrX5K+9RxzeyOufh3Xpp3zd0/bwAx/LwnYp87lbd72zaLnmNXrrtY5p+9e26/rKcTPr1w7Z3Kvepm/W69c4xKEkMp8Ipb82aNcrlntCLX/JSjXx12B4qVWzVylV6OJPRt7/1TXt78gcx/ebXW3TueefZIY3Pd7527typlStXqvLFL9WfMg/Z4YrZbwKao7V+vU+bLg/o1vHvzJ6TCXtuHf+26l5bf9wDnG98/d/V+Mb6RZePfqRLjz/+uHMkAAAA3OPnGrnOasQH36N3fvouPXJHj73+no/fqtl+396X6a019+oL30o7Oyyv/rzuuPNu3fG1Tm3Qer30Xb36xKda9IKL3qB/+JS1/q4XOgeWCutv5/U/18/us/6Ovuw1ennNK+YtL72kEFz49KJr8kPInvbgbuniN1jr1+pF6zPaPJXVS66YH+AY6y97pa66eIe2Wuu/mLxL51zml7filbpw8qcqlJqZvu0uvaimBAIcw+PXmz54rTY8/BPd8rn36ZNfuk8XVr1ML70sp9ut7f6v/0T3P5ydqxe0/nl6yYLr9fKLf6pbbrtSV1+R0S1vCeln+onuePgV1nVy7oOSRYiD08K3vvkfevazN+itb3+H4j+8Q3ffPTW7PPNZz1Lli1+i1avyQ6ResOlyfezjn9RZZ55lb5uA5Xu33aq61/6tfD6fLr88oB9O/sC+7Vi94Y1vUvK/fqb//sVme9sEOBdeWKbn/8UL7O3j6Zo3v0XXXHOtszXnec8r14e7/1FnnnmmswcAAADu8UK1fu1m9b7Zpx0Xflhf/+4tuvlr1vKp18j09d493aPXvewl6ph6hf7h9UUBxra78kWNp36uHc4u3Xeftlx2SX640I6sLrysFAYOFXh11fteoe986U7pnIvsHvTFy8YL53qfeLw+aepW3b32Ej3t3j595l5rX+6nuv2eV+iqy7K6ve1q9c/W0Mmb/qr1uNYFe9WrvfrkO27S/Z4rdLVnSDeb47K3KjL9Gr21MLTqJNv29TepqvJ1et+X7pKqPqwb4nfrvfqm9J4f6o6vfVgv1V0a7LxGr37ZW3WLXePIqwsXXK9NFW3q/W6nKmbrIP21rh96Q0kMFcPBEeLgtPLoo49q3779dkhjlmc+81n64x//6Nyad9FFZVrvmxv7m/jxj/SHBx+0P+yMV77qajv8MXVzin3kw9frVTXV9vKe696l+P/7oV7/2qvtbfPfpFNbp9h5T3mKrmv///TpT31Ct992q2797nfU0vrO2Zo7x9vCIIcABwAAwO1y2vaVd+pdE1eqf+g18piZqe7p0+te26cdr27TC6zG+o3f/LZu/OegKhaMslkod+9PpU2XOFuWtSVWG+WiS7Th3vt0y+ev1jVveN28peOrRalM9lZ99CtX6N010ks/+Glt+vqAPRvX3TWv0CZ5dfW7rtD3vvoT52CLCWnuv1atJqS5olP9b5cezPqs4y7X9770ff3o8z3Se9p0cf7ok+6iN9+iqe9+Xs2vvlxP23Gffjb1ff3wnoe1+Q4Tyt2nHesv19Vv+7y+cefNetNFJvR5j16/4HpdY7VPIvdZb4jcNj24+17dv82j9esP8QZBSSDEwWnnB9//3mzYYurWHMyDD25T5IZ/1XXtfzc7S9V55z1F17z5Wn3m0z2z05OfffbZ6vvcF/S9ybi9fOnLN6j6b16ub333dnv727d+T1fVvFKr16yxjy9mev7UvKJWH7r+A/rf17XbIdJyKgQ5BDgAAACnAo8uevun1fygMyuVWW64Uw9uu1O3T/5E3/mitf3xd+rV4aLQwrjoSvvv06uqXqhzcjuUzeX0w9u26eVVy/u36FG77/v6TNvH9LOKF+pNH7xbd9z5bb33skv0twO354eFJX+tz1SZA1O6+R0D2vipd+RDF49fbx3olKbv0qte79T3ueI1esltw7rdHm+U1Y/CPfJc8wa755Jx8evfYdcO8lR1qvnev9N7ptv0kbrSDDh254rLElus7d0LplC/6M036qsfbNMnvvtpvcjbphtv6tT1Q3fr+iusG+/5ibaek9EtX0/lD0bJI8TBaefK6r/RzaO32IsZXrWUvXv3avTfb1bNK16p0Ec+pKbGBrvosOlV8x+3fEOXv/AvNfn979nHXtP0FrtezlLOOussu+fPpZdudPbkPfbYY7rxX7+s263Hfc//9159PPQRTdw6bj/3cjJBzic+2UuAAwAAcMq4XK2mlo1ZPvgGXViobVPYdo4yPXey27Zpxz1D+mjb3+qqa4e0Y9s39Z2v9+gLD7fYMz4Z2+67V+uLp3o62S56md7dd7vu+OA2dQR79JnX9mj90Od12Q1v1Wfuyej24Jt0szM9+vp3fVEdl+XXbbkfaOQbV+p1FWaWprTuncrqok0/0M3fMsWRvXraVd36h7oDX2vuvlv1nex6Pe3+b+qHC4ZfnXTrn6cXrb9HN381rYurXqmXXiRdaP33Rd479YWvbrP2PU9z5W1+osgND6sw4mzteo9u//Q39bCyuv2GH+gl//x5vei2Af1oQfiD0kSIg9PKueeeqzPPOENf+pd/tpdHHnlET3/6051b51u1apXe39mlpre8VTd99WsajY7p1XWvtXvX9H3+n+1eMw1vfJNz9JExM1OZKc5f95pXaceOrG66edR+vJu++u+65etfU+PrX2sHRibkAQAAAI5KLm0PI7rjtrv0SL5TueXninwpo799V5uaP3azbjO9WG5q0uYbfq7mnmtnG/4P3p/RhetP/vTiszweeb0eZSe/qR1Vr3BCKZ+uHviOrt8xpOmqv9Z3Pn2rcvLr6jpnRinHw9+6ST/UhDpe+XK968MD+s59u7Whpk4PWtfFFC6+rO41C2rBmBmf3qlXv+M+vfu71vUZf4Nuf+PL9YmpuRluT66c7v/6O/XmT0vXf/k6XVyUP3mrevX1992nDlPXx9n38LeGtPXtLXPDwbyv0bvf5tHDEyF9Rt167xUv03vft0Mf7bx1tpAzShchDk4rF5U9057Su1ATp/uj/2j3fPlk+GM6/4ILnKMWZ2anMjNWHeusUY888rC6P3i9vX7LN7+j933gerunjmHOLzI8or7PD+juH03pq8P/Zu8HAAAAlubTS0PXaX6fb4unTJsqXqhNr/+0Jj5WmCr8r9VxU7863vwyXXaRVx5ldEvngNb33Wz3wrn3hjfpqpe9RF33tKnZHp5USnLa7W3Se1//13rR6+/TR63zNOd61Yd/pcuqgur9u8UrD69//c366S9/rek779Y3hnp1/dtfo6ve3Knet12u2WxrVk733/AeffLeJn3jzl5dZXqvXPIO3Rz/uDyfe48i95VCdxWPdFlQX/9apzZ5c/rRh6/WZ+59hV7q9D7yXhHWjR98ntY6p7r+9Tfqxqqf6vqXfVA/u+wiO6i7rGq9vnODR5/oe43MS/TWfV79F39Tn5kslaAKS1mxfWdu/nzLAAAAAAAAKDn0xAEAAAAAAHABQhwAAAAAAAAXIMQBAAAAAABwAUIcAAAAAAAAFyDEAQAAAAAAcAFCHAAAAAAAABcgxAEAAAAAAHABQhwAAAAAAAAXIMQBAAAAAABwAUIcAAAAAAAAFyDEAQAAAAAAcAFCHAAAAAAAABcgxAEAAAAAAHABQhwAAAAAAAAXIMQBAAAAAABwAUIcAAAAAAAAFyDEAQAAAAAAcAFCHAAAAAAAABcgxAEAAAAAAHABQhwAAAAAAAAXIMQBAAAAAABwAUIcAAAAAAAAFyDEAQAAAAAAcAFCHAAAAAAAgJIn/f+yrWtcXBlcgAAAAABJRU5ErkJggg==`

<details><summary>1 previous message</summary>

> 两张图对应两种不同拦截：
>
> - ChatGPT 停在 Logo：页面外壳加载了，但前端初始化、Cloudflare 校验或会话请求没有完成。
> - Gemini 的“此浏览器或应用可能不安全”：Google 识别到这是由 Playwright 启动的自动化浏览器，拒绝 OAuth 登录；不是账号密码问题。
>
> 我先读取当前 CDP 页面状态、Chrome 启动参数和页面可见错误，确认 ChatGPT 是网络资源失败还是自动化校验卡住。只做诊断，不发送消息。
> </details>

结论：不是网页坏了，而是 Playwright 启动方式触发了反自动化检测。

- ChatGPT 当前标题是“请稍候…”，说明卡在 Cloudflare 真人校验。
- Gemini 已跳转到 Google 的 `/signin/rejected`，Google 拒绝自动化浏览器登录。
- 即使设置了 `HEADLESS=0`，Chrome 参数里仍有 `--ozone-platform=headless` 和 Playwright 自动化特征。

另外，Google 拒绝地址中的邮箱域名是 `gamil.com`，请确认是否应为 `gmail.com`。

## 推荐解决方法

不要用 Playwright负责启动浏览器，改用正常 Chrome 启动 CDP；路由器仍然可以连接它。

先在当前 daemon 窗口按 `Ctrl+C`，关闭它打开的 Chrome，然后执行：

```powershell
cd C:\Users\MSN\Desktop\llm\AgentChat-master

$chrome = "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe"
$profile = "$HOME\.chrome-debug-profile"

Start-Process -FilePath $chrome -ArgumentList @(
    "--remote-debugging-port=9222",
    "--remote-debugging-address=127.0.0.1",
    "--remote-allow-origins=*",
    "--user-data-dir=$profile",
    "--proxy-server=http://127.0.0.1:7897",
    "https://gemini.google.com/app",
    "https://chatgpt.com/",
    "https://www.doubao.com/chat/"
)
```

这次是正常 Chrome：

1. 完成 Google 登录。
2. 完成 ChatGPT Cloudflare 验证并登录。
3. 登录豆包。
4. 保持 Chrome 运行。

另开 PowerShell 验证：

```powershell
node index.js --doctor
node index.js --provider=gemini --smoke
node index.js --provider=chatgpt --smoke
node index.js --provider=doubao --smoke
```

如果正常 Chrome 中 ChatGPT 仍卡在“请稍候”，通常是代理节点 IP 被 Cloudflare 风控，需要更换稳定节点。此时不要同时运行 `start-chrome-debug.py`，普通 Chrome 的 CDP 已经足够供项目使用。

> (yolo) PS C:\Users\MSN\Desktop\llm\AgentChat-master> node index.js --provider=doubao "写一段小红书中文旅行标题"
> [router] 选择 doubao：命令行指定 doubao
> [doubao] 连接 Chrome CDP（1/3）
> [doubao] 复用现有会话 https://www.doubao.com/chat/
> [doubao] FATAL: 豆包 Web 输入框未找到；网页 DOM 可能已更新。可通过 DOUBAO_EDITOR_SELECTORS 覆盖。

<details><summary>4 previous messages</summary>

> 这个错误说明豆包页面已经打开，但实际输入组件不符合当前候选选择器，或者被登录弹窗/模式页面遮住。我会直接读取你当前豆包标签页的 DOM、iframe 和可编辑元素属性，找出真实输入框后修正适配器；不会发送消息。
>
> 已经定位：豆包现在使用的是可见的原生 `<textarea class="semi-input-textarea" placeholder="发消息...">`。我们原来的候选只覆盖“输入/豆包”等占位文本，漏了“发消息”，所以是选择器缺口，不是登录问题。
>
> 我再读取输入区按钮属性和现有历史消息的 DOM 标记，确保发送按钮和回复提取也一起适配，避免只修输入框后又卡在下一步。
>
> 豆包当前页面还提供了比较稳定的语义标记：
>
> - 输入框：`textarea.semi-input-textarea`
> - 发送按钮：`#flow-end-msg-send`
> - 回答正文：`[data-copy-telemetry="right_click_copy"] [data-message-id]`
> - 回答完成工具栏：`[data-foundation-type="receive-message-action-bar"]`
>
> 我会把这组选择器加入适配器，先跑 `--smoke`，通过后再用你刚才那句旅行标题做一次端到端验证。
>
> <details><summary>Edited a file</summary>
>
> - Wrote `./AgentChat-master/lib/providers.js` (+8 -0)
>
> </details>
> </details>

已修复。原因是豆包更新了 DOM，输入框现在是：

```css
textarea.semi-input-textarea
```

同时补充了发送按钮、回答正文和完成状态选择器：[providers.js](./AgentChat-master/lib/providers.js)

验证结果：

- `--smoke` 通过
- 你的原命令已端到端执行成功
- 成功获得旅行标题回答

现在直接使用即可：

```powershell
node index.js --provider=doubao "写一段小红书中文旅行标题"
```

> 把 Gemini Web 或者gpt 或者豆包 包装成一个可被程序调用的外部大脑。这样agent就能直接调用网页了，这样是不是非常节省token

是的，这就是“网页端外部大脑”模式，能显著减少 API Token 费用。

```text
Agent（规划）
  → 统一路由器
  → Gemini / ChatGPT / 豆包网页
  → 提取结果
  → 返回 Agent
```

当前项目已经可以被程序调用：

```powershell
node index.js --provider=auto "复杂任务"
```

但要区分两种 Token：

- 网页模型生成的内容：不产生 API Token 账单，消耗网页账号额度。
- 返回给 Agent 的内容：放进 Agent 上下文后，仍会占输入 Token。

最省 Token 的策略是：

- Agent 只负责简短规划和选择模型。
- 复杂推理全部交给网页端。
- 网页端保持同一个会话，避免重复发送背景。
- 要求网页端返回精简、结构化结果。
- 只把结论带回 Agent，不搬运完整推理过程。

下一步可以把现有 CLI 包成：

- MCP 工具：最适合让 Codex、Claude Code等 Agent 直接调用。
- 本地 HTTP 服务：适合 Python、Java、Node 等程序。
- SDK 函数：例如 `ask({ provider, prompt, session })`。

所以结论是：**能大幅节省计费 Token，但不是完全零 Token；真正的关键是减少返回 Agent 的内容量。**