# 日常启动入口

双击 `start_agent.cmd` 打开服务、实时侧窗和完整观察台。默认暂停，点击自动游玩才会执行游戏动作。

`start_observer.cmd` 只打开侧窗，`start_history.cmd` 在系统默认浏览器打开完整页面。所有入口会检查已有服务，避免重复启动。

观察台顶部和侧窗的“重启服务”重启 Agent；Agent 管理页的“后台服务管理”还可重启全部后台服务（Agent、游戏执行服务、观察台）。管理进程独立运行，观察台重启期间仍可查询进度；完成后页面自动刷新，Agent 保持暂停，手动点击启动才会继续游玩。

命令行使用同一管理入口：

```powershell
.\launch\manage_agent.cmd status
.\launch\manage_agent.cmd start
.\launch\manage_agent.cmd stop
.\launch\manage_agent.cmd restart
.\launch\manage_agent.cmd restart --scope all
```

默认只管理 Agent；`--scope all` 管理三个后台服务，管理进程本身保留。首次安装该功能后运行任一启动入口，或先执行 `manage_agent.cmd restart --scope all` 并刷新页面。启动入口只补齐缺少的服务，不会重新加载已经运行的 Python 服务。

重启会先暂停自动操作、核对当前会话的持久化动作回执，然后退出已核验身份的服务进程。动作尚未确认或进程身份无法核验时停止重启，不重复提交游戏动作；历史会话的未确认日志保留。不会关闭游戏、改变存档或自动开始游玩。

独立管理器位于本机 `127.0.0.1:18770`，仅接受观察台来源或持有本机凭证的命令行请求。进程编号位于 `.runtime/local/`，启动日志为 `.runtime/logs/18766.*.log`、`18767.*.log`、`18769.*.log`、`18770.*.log`，最近管理操作记录为 `.runtime/local/service-operation.json`。

本目录只放启动入口；Python 服务和工具实现位于 `src/python/tools`。构建与部署使用 `scripts/build`。
