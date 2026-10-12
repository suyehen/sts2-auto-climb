# 知识数据

此目录存放版本化卡牌、药水、遗物、事件、怪物、意图、机制及策略资料。游戏导出的 catalog、behavior、图片 assets 和生成的 coverage 保存在本机，Git 不提交；规则与机制配置可以随源码发布。目前按实体及相关资料查询，没有将整个资料库放入模型上下文。

建议按游戏版本组织实体资料，规则与策略分开保存，并保留来源和适用条件。检索、索引和未来 RAG 的实现放在 `src/python/src/autoslay/knowledge/`；MCP 工具只调用这些能力。

生成的索引、向量缓存放在项目 `.runtime/cache/knowledge/`，不提交原始凭据或缓存。
