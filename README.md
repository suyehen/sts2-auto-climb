# STS2 Auto Climb

让本地 Agent 读取《杀戮尖塔 2》状态，结合计算器与模型逐步决策，并通过游戏模组执行操作。支持本机 Codex 登录、DeepSeek／其他 OpenAI 兼容 API，以及本地模型。

项目处于开发阶段，主要支持 Windows 单人游玩。当前源码依据游戏 0.111.0，模组版本 0.29.0；游戏更新后需重新导出资料并核实兼容性。百科全量收录不等于所有机制均已支持精确模拟。

## 功能

- C# 模组：实时状态、合法动作及操作结果读取，菜单、地图、战斗、奖励、事件等接口。
- Python Agent：状态校验、计算与模型决策、确定性步骤执行、暂停和模型后端切换。
- 决策观察台：实时侧窗、系统浏览器工作台、按对局／幕／地图节点回看；普通视图与工程视图切换。
- 本地百科：卡牌升级效果、遗物、药水、事件分支、怪物行为与意图，预留版本更新流程。
- 配置迁移：默认标准导出不含密钥，完整导出使用密码加密；导入预览、旧格式迁移及备份恢复。只包含业务配置，不包含对局数据。

## 从源码安装

需要已安装的游戏、Windows、Anaconda／Miniconda、Git；开发检查另需 Node.js。所有 Python 依赖都安装到项目环境。

```powershell
git clone git@github.com:suyehen/sts2-auto-climb.git
cd sts2-auto-climb
conda env create --prefix .runtime/conda --file src/python/environment.yml
.runtime/conda/python.exe -m pip install -e "./src/python[test,assets]"
.runtime/conda/python.exe src/python/tools/setup/install_dotnet.py
```

最后一条从微软下载并校验 .NET 9 SDK，安装到 `.runtime/tools/dotnet`。若需严格复现现有 Python 环境，可进入 `src/python` 后用项目环境执行 `pip install -r requirements.lock.txt`；其中 `-e .` 以该目录为基准。

关闭游戏后构建并部署，替换下面的游戏路径：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build/Build-Bridge.ps1 -GamePath "D:\SteamLibrary\steamapps\common\Slay the Spire 2"
powershell -ExecutionPolicy Bypass -File scripts/build/Deploy-Bridge.ps1 -GamePath "D:\SteamLibrary\steamapps\common\Slay the Spire 2"
.runtime/tools/dotnet/dotnet.exe build src/csharp/observer/AutoSlay.Observer.csproj -c Release
```

模组安装在游戏的 `mods/AutoSlayBridge`。首次启动按游戏提示启用模组并重启。部署会生成仅存本机的桥接令牌与安装路径配置，不需要上传创意工坊。

## 生成本地百科与图片

仓库不分发游戏 DLL、反编译代码、百科导出或图片。请使用自己的游戏安装生成：

1. 启动装有模组的游戏，在菜单运行百科导出。
2. 安装本地 ILSpy 工具，离线提取事件／怪物／意图资料。
3. 导出卡牌、遗物、药水图片和怪物图像。

```powershell
.runtime/conda/python.exe src/python/tools/kb_admin/export_encyclopedia.py --refresh
.runtime/tools/dotnet/dotnet.exe tool install ilspycmd --version 9.1.0.7988 --tool-path .runtime/tools/ilspy
.runtime/conda/python.exe src/python/tools/kb_admin/export_behavior.py --game-path "D:\SteamLibrary\steamapps\common\Slay the Spire 2"
.runtime/conda/python.exe src/python/tools/kb_admin/export_portraits.py --game-path "D:\SteamLibrary\steamapps\common\Slay the Spire 2"
.runtime/conda/python.exe src/python/tools/kb_admin/export_monster_portraits.py --game-path "D:\SteamLibrary\steamapps\common\Slay the Spire 2"
```

离线导出工具需要本地 SDK；怪物图像生成使用游戏自带渲染器和资源。游戏更新后按相同顺序重新导出，版本不一致时不要沿用旧行为计算结果。

## 日常使用

双击 `launch/start_agent.cmd`，打开服务、独立实时侧窗和系统默认浏览器工作台。默认保持暂停：在「Agent 管理」配置后端，再到首页「开始游玩」选择新局或继续存档。职业默认铁甲战士，进阶默认该职业已解锁的最高难度；点击后会自动启动游戏、处理菜单并启动 Agent，无需在游戏里点击继续或开始。已有存档必须明确勾选替换才能开始新局；当前对局可直接接管。

- 本机 Codex：需本机可用的 Codex CLI 及已有登录；会使用账号共享额度。
- API／本地模型：在工作台填写服务地址和模型，API 密钥仅保存在本机。
- `launch/start_history.cmd`：只打开完整工作台。
- `launch/start_observer.cmd`：只打开实时侧窗。

端口：游戏桥接 18765、执行服务 18766、工作台 18767、Agent 18769；均为本机服务。关闭观察窗口不会停止后台服务或暂停 Agent，应使用工作台的暂停按钮。

## 测试与目录

```powershell
.runtime/conda/python.exe -m pytest src/python/tests/test_configuration.py -q --basetemp=.runtime/test-config
node --test src/web/tests/*.test.mjs
.runtime/tools/dotnet/dotnet.exe run --project src/csharp/tests/CommandTests
# 完整本机回归：需要相应版本的游戏知识和回放夹具
powershell -ExecutionPolicy Bypass -File scripts/build/Test-Project.ps1
```

仓库不配置 GitHub Actions 自动检查，推送和 PR 不触发测试工作流；相关测试按需在本机运行。普通单元测试使用临时模拟知识库，不读取本机导出资料；依赖真实导出资料的测试标记为 `local_knowledge`，可通过 `-m 'not local_knowledge'` 排除。展示机制缺少审核资料或来源指纹不匹配时仍保持未建模，不放行预测。完整本机测试与真实对局验证仍需本地完成。

| 目录 | 内容 |
| --- | --- |
| `src/csharp` | 游戏桥接、实时侧窗、C# 测试 |
| `src/python` | Agent、计算器、知识查询、服务与工具 |
| `src/web` | 工作台、侧窗页面、前端测试 |
| `src/godot` | 本地图像导出脚本 |
| `config`、`contracts` | 默认策略参数与接口协议 |
| `data/knowledge` | 可发布的机制规则及本机生成资料 |
| `docs` | 设计、使用与开发记录 |
| `launch`、`scripts` | 日常启动与构建部署入口 |
| `.runtime` | 本机环境、密钥、用户配置和缓存，不提交 |

## 文档与贡献

- [文档导航与实现状态](docs/README.md)：待实施方案、已有实现及验证边界、历史归档。

- [配置导入导出与版本迁移](docs/reference/配置导入导出与版本迁移.md)
- [GitHub 发布范围](docs/reference/GitHub发布说明.md)
- [贡献指南](CONTRIBUTING.md) · [凭据与安全](SECURITY.md)
- [开发阶段记录](docs/archive/开发阶段记录.md)：历史验收记录，不代表当前安装指南。

原创代码采用 [Apache-2.0](LICENSE)。游戏名称、资源及其权利归相应权利人，本项目许可证不授予游戏资源的再分发权。

## 统一知识库

知识资料统一由 `data/knowledge/manifest.json` 管理，实现位于 `autoslay.knowledge_base`，维护命令位于 `src/python/tools/kb_admin/`。支持离线实体/别名/中文关键词检索，无需模型 key 或向量数据库。详见 [使用说明](docs/reference/统一知识库使用说明.md)。
