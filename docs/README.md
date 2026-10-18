# 文档导航与实现状态

整理日期：2026-10-18。先在这里找事项和状态，再进入具体文档。

## 目录怎么放

```text
docs/
├─ README.md       文档导航、事项状态和维护约定
├─ reference/      仍供使用和维护的配置、接口与技术说明
├─ plans/          待实现、正在实现及仍有后续工作的方案
└─ archive/        历史方案、实施、验收、审计记录和实验 JSON
```

只保留这三个子目录，不按主题继续分层；根目录只保留本导航。`reference/` 中的说明也有适用版本与未验证分支，不代表所有功能已经完成。

## 待实现／正在实现

以下状态结合方案与实施记录维护；单元测试、构建和游戏验收分别记录。没有证据的事项标为待核实；不把历史缺口自动认定为当前缺陷。

| 事项／方案 | 实现状态 | 验证情况与剩余工作 |
| --- | --- | --- |
| [战斗数学模型共建](plans/战斗决策数学模型共建草案.md) | 首版接口与基线观测已实施 | 联合结果、预算统计、概率耗药及公平预算实验；默认评分保留基线，见[实施记录](reference/路线与战斗协同模型首版实现.md) |
| [路线规划与先古、事件遗物协同建模](plans/路线规划与先古事件遗物协同建模方案.md) | 分支资源情景与全量目录程序已实施 | 961 个条目、1518 个版本已接入；完整数值／模拟仍有缺口，见[全量效果模型](reference/全量卡牌遗物药水效果模型.md)；尚未验证通关率收益 |
| [统一知识库与检索](plans/统一知识库与检索实施方案.md) | 部分实现 | 首版读取、检索和审核经验发布已有实现；版本目录迁移、多数据集事务与完整质量评测仍待实施，当前用法见 [说明](reference/统一知识库使用说明.md) |

目前没有足够依据将其他事项直接标为“正在实现”。实施开始时更新此表，并在方案里维护已完成／待完成清单。

## 已有实现与验证边界

这里区分“有实现”和“完成验收”。历史记录只作为对应日期与范围的证据，不充当本次重新验证。

| 事项 | 实现情况 | 验证边界与关联文档 |
| --- | --- | --- |
| 本地战斗批次决策 | 已有实现，仍有模型缺口 | [当前完整决策逻辑](reference/当前一轮完整出牌决策逻辑-2026-10-15.md)、[实施记录](archive/本地战斗批次决策实施记录-2026-10-15.md)；近似估值与跨战斗资源价值不等于完整建模或实战收益证明 |
| 观察台双窗口与对局归档 | 已有实现 | [实施与使用](reference/观察台双窗口实施与使用.md) 有接口与部署验证记录，整局真实游玩验收在该记录中仍待完成；最新是否补齐待核实 |
| 统一知识库 | 首版已实现 | [使用说明](reference/统一知识库使用说明.md)；后续事项见上表，资料收录不等于完整模拟 |
| 事件后续推演与校准 | 部分适配已实现 | [说明](reference/事件后续推演与实测校准.md) 有离线验证，实际字段与转移仍需实测；最新样本情况待核实 |
| 游戏动作接口 | 各阶段已有实现 | 下方接口文档分别保留已测与未测分支；不能据一次基础验收推断复杂分支全部通过 |
| 旧严格战斗方案与自主调优 | 部分已有实现，后续方向调整 | [严格方案讨论记录](archive/统一严格战斗决策约束与现状差异-2026-10-15.md)、[调优方案](archive/战斗自主决策调优实施方案-2026-10-14.md)；整套严格模式暂不引入，历史内容不直接作为待实施清单 |

## 当前说明索引

这些说明按自身的核对日期和版本使用；涉及算法现状时优先读最近核对的决策逻辑，旧算法说明已归档。

### 使用、配置与技术说明

- [Agent后端切换与配置](reference/Agent后端切换与配置.md)
- [Agent日志审计](reference/Agent日志审计.md)
- [GitHub发布说明](reference/GitHub发布说明.md)
- [事件后续推演与实测校准](reference/事件后续推演与实测校准.md)
- [事件怪物资料与更新](reference/事件怪物资料与更新.md)
- [事件机制资料与更新](reference/事件机制资料与更新.md)
- [全局动作目录与对象能力记录-2026-10-15](reference/全局动作目录与对象能力记录-2026-10-15.md)
- [启动工作流与工程验证](reference/启动工作流与工程验证.md)
- [对局记录层级与原生地图图标](reference/对局记录层级与原生地图图标.md)
- [当前一轮完整出牌决策逻辑-2026-10-15](reference/当前一轮完整出牌决策逻辑-2026-10-15.md)
- [当前战斗出牌决策规则说明-2026-10-15](reference/当前战斗出牌决策规则说明-2026-10-15.md)
- [战斗推演与怪物规则](reference/战斗推演与怪物规则.md)
- [机制覆盖与动态状态](reference/机制覆盖与动态状态.md)
- [百科资料库](reference/百科资料库.md)
- [目录规范与百科分类](reference/目录规范与百科分类.md)
- [统一知识库使用说明](reference/统一知识库使用说明.md)
- [观察台双窗口实施与使用](reference/观察台双窗口实施与使用.md)
- [观察台界面与展示模式](reference/观察台界面与展示模式.md)
- [配置导入导出与版本迁移](reference/配置导入导出与版本迁移.md)

### 接口与对应版本验收

- [P1接口与验收](reference/P1接口与验收.md)
- [主菜单接口与验收](reference/主菜单接口与验收.md)
- [事件接口与验收](reference/事件接口与验收.md)
- [商店接口与验收](reference/商店接口与验收.md)
- [地图接口与验收](reference/地图接口与验收.md)
- [奖励接口与验收](reference/奖励接口与验收.md)
- [宝箱接口与验收](reference/宝箱接口与验收.md)
- [新局默认配置与验收](reference/新局默认配置与验收.md)
- [牌组选择接口与验收](reference/牌组选择接口与验收.md)
- [篝火接口与验收](reference/篝火接口与验收.md)
- [药水接口与验收](reference/药水接口与验收.md)
- [退出与重启接口](reference/退出与重启接口.md)

## 历史归档索引

归档包含已完成的历史记录，也包含被替代、暂缓或尚未完整验收的设计。归档位置本身不表达实现状态。以下保留原文件名，便于按日期和名称查找。

<details>
<summary>展开历史方案、实施、验收与审计记录</summary>

- [DeepSeek严格决策输出-2026-10-14](archive/DeepSeek严格决策输出-2026-10-14.md)
- [X费用牌导致Agent停止修复-2026-10-13](archive/X费用牌导致Agent停止修复-2026-10-13.md)
- [事件奖励子流程验收](archive/事件奖励子流程验收.md)
- [全量知识规则与决策面板实施记录](archive/全量知识规则与决策面板实施记录.md)
- [决策层设计与验收](archive/决策层设计与验收.md)
- [决策评估与效率升级](archive/决策评估与效率升级.md)
- [卡牌选择与牌组估值优化审计-2026-10-14](archive/卡牌选择与牌组估值优化审计-2026-10-14.md)
- [实施进度](archive/实施进度.md)
- [实时决策观察台](archive/实时决策观察台.md)
- [常驻执行与动作序列](archive/常驻执行与动作序列.md)
- [开发阶段记录](archive/开发阶段记录.md)
- [总体技术规划](archive/总体技术规划.md)
- [战斗自主决策调优全项目复核-2026-10-14](archive/战斗自主决策调优全项目复核-2026-10-14.md)
- [战斗自主决策调优实施方案-2026-10-14](archive/战斗自主决策调优实施方案-2026-10-14.md)
- [战斗自主决策调优实施记录-2026-10-14](archive/战斗自主决策调优实施记录-2026-10-14.md)
- [战斗自主决策调优补充实施记录-2026-10-14](archive/战斗自主决策调优补充实施记录-2026-10-14.md)
- [战斗药水选牌验收](archive/战斗药水选牌验收.md)
- [抽牌机制与遗物时机](archive/抽牌机制与遗物时机.md)
- [数学模型详细说明与评估](archive/数学模型详细说明与评估.md)
- [整局Agent未知项与实际收益审计-2026-10-13](archive/整局Agent未知项与实际收益审计-2026-10-13.md)
- [本地战斗决策率与Agent异常修复-2026-10-14](archive/本地战斗决策率与Agent异常修复-2026-10-14.md)
- [本地战斗批次决策实施记录-2026-10-15](archive/本地战斗批次决策实施记录-2026-10-15.md)
- [确定性决策模块实施说明](archive/确定性决策模块实施说明.md)
- [确定性方案比较与AI分流](archive/确定性方案比较与AI分流.md)
- [第二第三战斗衔接日志审计-2026-10-13](archive/第二第三战斗衔接日志审计-2026-10-13.md)
- [统一严格战斗决策约束与现状差异-2026-10-15](archive/统一严格战斗决策约束与现状差异-2026-10-15.md)
- [观察台双窗口与对局归档方案](archive/观察台双窗口与对局归档方案.md)
- [评分分解与实战校准](archive/评分分解与实战校准.md)

</details>

<details>
<summary>展开实验与基准数据（JSON）</summary>

- [combat-baseline-history-20261014.json](archive/combat-baseline-history-20261014.json)
- [combat-baseline-new-20261014.json](archive/combat-baseline-new-20261014.json)
- [combat-batch-validation-20261015.json](archive/combat-batch-validation-20261015.json)
- [combat-search-benchmark-20261014.json](archive/combat-search-benchmark-20261014.json)
- [combat-strategy-validation-20261014.json](archive/combat-strategy-validation-20261014.json)
- [combat-validation-history-20261014-baseline.json](archive/combat-validation-history-20261014-baseline.json)
- [combat-validation-history-20261014-combined.json](archive/combat-validation-history-20261014-combined.json)
- [combat-validation-history-20261014-pool.json](archive/combat-validation-history-20261014-pool.json)
- [combat-validation-history-20261014-scope.json](archive/combat-validation-history-20261014-scope.json)
- [combat-validation-new-20261014-baseline.json](archive/combat-validation-new-20261014-baseline.json)
- [combat-validation-new-20261014-combined.json](archive/combat-validation-new-20261014-combined.json)
- [combat-validation-new-20261014-pool.json](archive/combat-validation-new-20261014-pool.json)
- [combat-validation-new-20261014-scope.json](archive/combat-validation-new-20261014-scope.json)

</details>

## 后续产文档的约定

1. 使用、配置、当前架构与接口说明放 `reference/`，优先更新现有说明；方案放 `plans/`，实施、验收、审计和实验产物放 `archive/`。
2. 新建方案时在上方登记一行，实现状态使用：待讨论、待实施、正在实现、部分实现。验证情况单独写：未验证、自动化检查通过、部分实测通过、目标范围验收通过；依据不足写待核实。
3. 方案开头注明状态、更新日期、适用范围；实施后关联代码或实施记录，验收后关联验证证据，并同步首页状态。文档修改日期不能代替代码核对日期。
4. 方案全部落地后，更新 `reference/` 中的使用／技术说明，将方案移入 `archive/`，从待办表移至已有实现表，并修复引用。部分完成的方案继续留在 `plans/`，逐项说明缺口。
5. 取消、暂缓或被替代的方案也进入 `archive/`，开头明确原因与替代文档；不标记为已实现。历史记录保留当时结论，不用今天的状态改写过去的证据。
6. 文件名保持稳定，优先使用描述性中文名称；历史记录可带日期。避免每次调整状态就改文件名或新增一份相同用途的进度文档。
