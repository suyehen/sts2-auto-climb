# Python 命令行工具

使用项目环境 `.runtime/conda/python.exe` 运行。按用途分为 runtime、game、analysis、kb_admin、archive、setup。

知识维护入口为 `kb_admin/manage.py`，支持 `status`、`search`、`rebuild-index` 和 `publish-experience`；原导出与审计脚本也已移入 `kb_admin/`。

这些文件只是命令行入口；可复用的业务实现位于 `src/autoslay`。需要日常启动时，优先使用项目根目录 `launch` 中的入口。
