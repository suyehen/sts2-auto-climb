# 开发与贡献

Python 业务实现放在 `src/python/src/autoslay`，命令行入口放在 `src/python/tools`；C# 桥接放在 `src/csharp/bridge`，Web 页面放在 `src/web/public`。默认参数放在 `config/decision`，用户参数和密钥只放在 `.runtime`。

开发环境及知识导出见 README。仓库不配置 GitHub Actions 自动检查，提交前按需在本机运行相关测试。游戏导出知识与回放不随仓库发布；完整 Python 回归需先生成对应版本的本地知识资料，部分回放测试还需要开发者的本地回放夹具。

不提交 API 密钥、Codex 登录凭据、个人配置、对局记录、游戏 DLL／图片／反编译产物。新增配置字段要提供旧版本迁移、校验和测试；升级游戏规则需保留版本及来源证据。规则条目数量不能用于宣称完整精确模拟覆盖。

提交 Issue 时提供游戏版本、复现步骤与脱敏错误信息。贡献代码沿用仓库 Apache-2.0 许可证；游戏原始资源不属于本项目授权范围。
