# 日常启动入口

双击 `start_agent.cmd` 打开服务、实时侧窗和完整观察台。默认暂停，点击自动游玩才会执行游戏动作。

`start_observer.cmd` 只打开侧窗，`start_history.cmd` 在系统默认浏览器打开完整页面。所有入口会检查已有服务，避免重复启动。

本目录只放启动入口；Python 服务和工具实现位于 `src/python/tools`。构建与部署使用 `scripts/build`。
