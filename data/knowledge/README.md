# 知识数据

此目录存放版本化卡牌、药水、遗物、事件、怪物、意图、机制及策略资料。游戏导出的 catalog、behavior、图片 assets 和生成的 coverage 保存在本机，Git 不提交；规则与机制配置可以随源码发布。目前按实体及相关资料查询，没有将整个资料库放入模型上下文。

`manifest.json` 是唯一正式数据集清单。历史、原始事件观察、源码缓存和测试夹具不自动进入检索。`aliases.json` 管理别名，`relations.json` 管理明确关联；经审核的策略与耗时画像位于 `experience/`。游戏数值、机制和规则仍保留现有路径，并通过统一清单管理，避免一次迁移全部本机资料。

实现代码在 `src/python/src/autoslay/knowledge_base/`，维护命令在 `src/python/tools/kb_admin/`。统一仓库负责读取、缓存和修订快照；业务模块不自行解析知识 JSON。

检索使用实体 ID、中文别名、二字词、SQLite FTS5/BM25 和明确关系，全程本地运行，无 embedding、rerank 或向量数据库。派生索引位于 `.runtime/cache/knowledge/<revision>/lexical.sqlite`，可删除重建。

参见 [统一知识库使用说明](../../docs/统一知识库使用说明.md)。
