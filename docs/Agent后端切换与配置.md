# Agent 后端切换与配置

运行 `launch/start_agent.cmd`（或原有 `launch/start_observer.cmd`）会启动本地执行服务、Agent 服务、观察台和实时侧窗。Agent 默认暂停；启动服务不会打开游戏、调用模型或自动出牌。

## 一键切换

完整观察页和实时侧窗均有“Agent · 决策后端”区域。选择配置，点击“切换并暂停”。选中的后端写入本地配置，重启后保留；随后点击“自动游玩当前局”进入闭环。游戏需已进入当前对局，开始新局/续档仍使用现有桥接入口。

切换会撤销当前等待中的模型请求、递增决策代次并暂停游戏控制。旧代次的结果不能提交动作。已经发给游戏的动作无法撤销；其请求标识事先持久化，恢复时只查询状态，不重新提交。不确定结果会阻止继续出牌。

## 支持的后端

| 配置 | 接口与认证 | 首次配置 |
| --- | --- | --- |
| 本机 Codex | 本机 CLI 的已登录账号；不提取 OAuth 令牌 | 安装并登录 Codex，模型名可留空使用默认值 |
| DeepSeek | OpenAI 兼容 Chat Completions；API Key | 填 Key；可读取模型列表确认模型名 |
| OpenAI API | Chat Completions；API Key | 填 Key、可用模型名 |
| Ollama | 本地 OpenAI 兼容接口，默认 11434 端口 | 自行加载模型，填模型名 |
| LM Studio | 本地 OpenAI 兼容接口，默认 1234 端口 | 启动本地 API 服务，填模型名 |
| 其他服务 | OpenAI 兼容 Chat Completions | 填 Base URL、模型、Key；改配置 ID 可创建多份配置 |

vLLM 等本地 OpenAI 兼容服务也可以新建 `local` 类型配置。当前未实现各厂商独有的非兼容协议，也不负责下载模型或启动推理服务器。本地模型可以不填 Key；需要认证时亦可填入。远端连接要求 HTTPS，本地 HTTP 限定回环地址。

Base URL 不含 `/chat/completions`，适配器会补齐该路径。服务要求 `/v1` 时需把 `/v1` 写在 Base URL 中。DeepSeek 预置 `https://api.deepseek.com`、`deepseek-flash`、JSON 模式，模型名和地址都允许更新。

支持三种输出模式：JSON、严格 JSON Schema、文本 JSON。DeepSeek 预设 JSON；不支持 JSON 参数的兼容服务可选择文本 JSON。三个模式都要求本地严格验证动作、目标和状态令牌，不能通过文本执行系统命令。拒绝、截断、空响应、工具调用、非法动作会暂停 Agent，不会悄悄切换后端或重复调用。

“读取可用模型”只调用选定服务的 `/models`，不发起推理，不操作游戏。先保存配置再读取。厂商不支持模型列表时可以手填。

## 密钥及文件

- `.runtime/local/agent/profiles.json`：非敏感配置、当前后端。
- `.runtime/local/agent/credentials.dpapi.json`：Windows DPAPI 加密的密钥；绑定保存时的 Windows 用户。界面仅返回“是否已配置”，不会回传 Key。
- 环境变量：DeepSeek 默认 `DEEPSEEK_API_KEY`，OpenAI 默认 `OPENAI_API_KEY`。配置指定的环境变量优先于保存的密钥；修改环境变量后需重启 Agent 服务。
- `.runtime/logs/agent/`：执行请求日志，提交前写入，用于恢复时查询状态。
- `data/runs/YYYY-MM-DD/run_.../`：实际发送的决策数据包、AI 选择及动作结果，继续使用已有逐局归档。

不要把密钥发到聊天、文档或知识库中。界面的 Key 输入框留空会保留原密钥；勾选删除会删除本地保存值，但不会删除环境变量。密钥不会发送给决策模型，只有 HTTP 适配器在请求头中使用。

## 解耦结构

`src/python/src/autoslay/agent/` 中：

- `config.py`：配置验证、预置档案、密钥存储。
- `providers.py`：HTTP/Codex 调用；新增协议在这里增加适配器。
- `contracts.py`：统一输出契约与合法动作校验。
- `context.py`：当前可见状态、计算报告、相关百科条目及最近结果；不发送整个百科。
- `runtime.py`：确定性判断、模型调用、状态复查、持久化执行、暂停和切换。
- `server.py`：带本地桥接令牌认证的 18769 服务。

Codex 第一版使用官方非交互 `codex exec` 接口，便于隔离用户 MCP/插件配置；没有实现常驻 App Server。每次调用是独立临时会话，禁用 shell、应用、浏览器及 hooks，采用只读沙箱，结构化输出由 CLI 约束。登录由 CLI 管理。该方案有进程启动开销，后续可以在相同适配器契约下替换为 App Server。更换后端不会改变知识库和数学模型。

第一版每次选一个动作，执行后重新读取状态，因此抽牌、喝药水、事件重复选择等都经过新状态决策；没有把随机抽牌结果当作已知。确定性证明优先于模型调用。模型决策记录的是简要解释及 token/耗时统计，不记录模型内部思考链。

## 验证范围

测试覆盖兼容接口请求、三种输出模式、响应拒绝、凭据隔离、DPAPI 往返、切换时撤销等待中的请求、状态过期、提交前持久化、未知结果查询、确定性跳过模型及暂停。完整回归 288 项通过。

2026-10-11：使用本机 ChatGPT 登录的 Codex 对固定虚拟战斗做了一次真实调用，返回合法 JSON，状态令牌、动作、目标校验通过，并选择了预期的击杀动作。耗时 100,890 毫秒，未向游戏提交动作。此结果仅证明本机真实调用链路可用，不能证明实际对局效果、响应速度或复杂机制覆盖。耗时偏高的原因尚未定位，不能直接归因于启动进程。DeepSeek、本地模型和真实游戏闭环仍待联调。

复现入口：`src/python/tools/runtime/probe_agent_backend.py --expect-provider codex`。它会实际调用当前后端、使用相应额度，固定状态与结果存于 `.runtime/logs/agent-probes/`，不会导入游戏执行器或发送游戏动作。默认模型测试未记录 token 用量。

参考：[OpenAI 认证](https://learn.chatgpt.com/docs/auth)、[Codex 开发者命令](https://learn.chatgpt.com/docs/developer-commands)、[DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。
