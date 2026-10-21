# GitHub 发布范围

仓库保留原创源码、默认配置、机制规则、协议、测试和使用文档，沿用远程初始仓库的 Apache-2.0 许可证。

以下内容保留在电脑本地，不随 Git 上传：

- `.runtime`：Conda 环境、SDK、ILSpy、令牌、个人配置、密钥、缓存及反编译产物。
- `data/runs`、`data/decisions`、`data/indexes`、`data/replays`、实测校准样本：个人对局与生成索引。
- `data/knowledge/catalog`、`behavior`、`assets`、`coverage`：本机游戏导出百科、行为、图标与生成报告。
- 配置导出包及编译得到的 EXE、DLL、PCK。

这些排除不会删除本机文件。克隆后的用户必须使用自己的游戏安装生成相应资料。规则与事件数学模型保留；它们依据特定游戏版本，不能保证更新后仍有效。

仓库已移除 GitHub Actions 自动检查工作流，后续推送和 PR 不触发测试工作流。相关测试与真实对局验证按需在本机完成。尚未制作可下载安装包，本次按源码发布。
