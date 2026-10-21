# 跨语言协议

2026-10-20：独立 C# 原生规则复刻正在实施。新协议是 `rules-host.schema.json`（目前仅身份握手），数值数据合同是 `rule-parameters.schema.json`；基础数据不包含效果程序。见[构建与更新说明](../docs/reference/独立规则内核构建与源码参数更新.md)。下文 `game-models.schema.json` 的效果与选择 JSON 属于仍在旧生产入口使用的历史路线，冻结扩展，不作为新内核执行定义或回退；默认入口尚未切换。

2026-10-21：新增 `rules-resource-snapshot.schema.json`，仅表示当前独立内核的自有资源/遗物检查点；绑定实际 rules DLL 与参数包 SHA，保留有限历史、变量状态、轨迹及未知钩子顺序。它不是完整游戏观测合同或真实执行授权；Power、模型私有数据和附加程序集目前明确拒绝导出，不能静默丢字段。Host 仍仅提供 hello，已加载并返回编译合同匹配的数值参数包身份；全目录暂存参数不是当前运行包。

2026-10-21 后续：独立内核增加有限卡牌费用/牌堆、升级/降级、能量/星数状态与支付成员，但没有扩张 resource snapshot 的承载范围；导出会拒绝丢弃这些新增观测。CardEnergyCostObservation 及变量/星数观察仅恢复编译类型的有限状态，不是效果 DSL 或完整游戏卡牌观测合同。完整出牌与支付调用方、附魔/侵蚀和卡牌私有状态恢复仍待迁入，Host 仍仅 hello。

同日后续：运行数值包增至七条，加入 Injury/Dazed/Enlightenment 的构造费用；构造工厂显式传入 RuleParameters，构造阶段的费用和变量读取使用不可变参数上下文。关键词、升级等级、变量和星数状态另需明确观测，未知不会自动取原始定义或 0；现有资源快照仍拒绝承载这些卡牌状态。全目录 2397 条暂存绑定（含 22 个基础星数费用）不是当前运行合同或模型支持数。资源支付历史只支持明确为空的独立观察域，不代表已提供完整历史序列化协议。

同日后续：独立 C# 增加按状态域恢复的动态战斗监听、Power 命令与接收历史、共享缩放模型。它们尚未成为新的完整观测/Host 模拟协议；有限资源 checkpoint 遇到这些状态、幕/遭遇信息或脱离持有者但仍被追踪的模型时明确拒绝导出。phase 增加原生 setup 对应的 Starting=4；它不会授予战斗开始/结束流程支持。所有状态观察、源码清单和验收报告都没有效果执行权。

0.30.0 新增全局动作目录 `data/knowledge/rules/actions.json`，覆盖当前桥接的 42 种命令。C# 将该文件嵌入程序集，Python 通过知识清单读取同一文件；目录定义、版本与 SHA256 是共同依据。`legal-action.schema.json` 描述实时绑定，新增 `definition_id`、`source`；`state.action_catalog` 给出版本与指纹。旧快照缺少新增元数据时兼容读取，声明了不同目录身份时拒绝混用。动作请求格式保持不变。

对象能力、效果/触发引用与实时可操作性分别记录。详见 `docs/reference/全局动作目录与对象能力记录-2026-10-15.md`。目录和能力记录不是执行权限，提交前仍检查新鲜 `legal_actions`。

状态、动作、错误与实体资料的 JSON Schema 放在此目录，供 C#、Python 和 MCP 共同遵循。已定义 `action-request.schema.json`；P1 工具和动作生命周期见 `docs/reference/P1接口与验收.md`，0.3.0 奖励动作及打开/选择两阶段语义见 `docs/reference/奖励接口与验收.md`。奖励动作继续使用同一请求结构，target_id=null；状态与错误的完整 Schema 尚待补齐。

0.4.0 支持没有对局的主菜单动作：请求 run_id 使用快照中的 `state.command_context_id`（对局内等于真实 run_id，主菜单为菜单实例 ID）。state.run_id 在没有对局时仍为 null。详见 `docs/reference/主菜单接口与验收.md`。

0.5.0 新增 state.map 与 choose_map_node 动作，使用原请求结构且 target_id=null；节点必须来自当前 legal_actions。详见 docs/reference/地图接口与验收.md。

0.6.0 新增 state.event_screen、choose_event_option 与 proceed_event。选项包含动态描述及 hover_tips；仍使用原请求结构、target_id=null。子选择完成边界见 docs/reference/事件接口与验收.md。

0.7.0 新增 state.card_selection 与分步选择、预览、确认和取消动作，仍使用原请求结构且 target_id=null。事件打开受支持的选择器后，原事件请求 completed 表示界面就绪，效果在后续确认后结算。详见 docs/reference/牌组选择接口与验收.md。

0.8.0 新增 state.shop 以及 open_shop、buy_shop_item、close_shop、proceed_shop。买删牌服务只打开后续选择器，最终扣费由游戏在确认后处理。详见 docs/reference/商店接口与验收.md。

0.9.0 新增 state.rest_site、choose_rest_option 和 proceed_rest。锻造打开升级选择器后，由后续选牌动作确认升级。详见 docs/reference/篝火接口与验收.md。

0.10.0 扩展 player.potions，并新增 use_potion、discard_potion；目标使用当前 legal_actions 中的实例 ID。补齐当前遗物、可见状态及意图的悬浮资料，满药水栏不再提供领取药水奖励动作。详见 docs/reference/药水接口与验收.md。

0.10.1 修复 hover_tips 中展示卡牌的实例 ID：展示卡牌只包含内容字段，不提供 instance_id；可行动卡牌仍有实例 ID。此差异避免游戏重建提示模型导致状态版本无意义变化。

0.11.0 新增 return_to_main_menu 和 quit_game。退出回执 completed 表示退出已调度，进程结束必须由本地重启脚本另行验证；重启后须使用新的会话和实例 ID。详见 docs/reference/退出与重启接口.md。
# 0.12 宝箱扩展

state.treasure 提供 screen_id、decision_ready、opened、picking、relics、controls。未开箱不暴露隐藏遗物。open_chest 回执完成只表示下一个宝箱决策已就绪；领取或跳过分别请求，等待正常父效果结束。新增 open_chest、take_treasure_relic、skip_treasure_relic、proceed_treasure，宝箱房支持 return_to_main_menu。跨进程重启后必须重新读取 session_epoch 与实例 ID。

0.13.1 player.relics 新增 status（游戏公开的 RelicStatus）与 counter（仅 ShowCounter=true 时读取 DisplayAmount，否则 null）。这是可见 UI 数据，不保证所有遗物都有计数；不能把 null 当作计数 0。

0.14.0 card_selection.enchantment 可选对象：entity_id、amount、title、description；只在牌组附魔选择界面存在。候选牌为当前真实牌，附魔在确认前不修改牌组。预览沿用既有 preview 和 controls 字段。


0.21.0 新增 close_map_overlay、continue_game_over、return_after_game_over，仍使用相同动作请求及新鲜 legal_actions，target_id=null；game_over 阶段与普通战斗分开。恢复动作遵循游戏正常按钮／界面方法，不能跳过模态。内部 Python 契约 1.3.0 新增百科模板标记及弱化／脆弱持续信息；统一报告 decision/unified/1.0 与动作权限分离。详见 docs/archive/决策评估与效率升级.md。


游戏模型规则版本 3.1.0 增加 `game-models.schema.json` 的 `selections` 选择合同集合，仍属于同一 models 数据集。合同以来源模型 ID/类型、回调、稳定选项键及实际 API 绑定后续命令；内部 `SELECTION.*` ID 单独统计，不冒充游戏 ModelDb ID。

`state.card_selection` 新增 `semantics`（`observed-selection/1`）、`require_manual_confirmation`、`interaction`，候选新增 `eligible`。semantics 包含 `operation`、`api_path`、`source_model_id/type/instance_id/callback/option_key`、来源公开动态变量及状态；这些字段由实际游戏调用观测，不能从 `screen_type` 或 `prompt` 推断。`continuation_observed=false` 明确表示选择 API 本身不保证调用方效果已完成，效果只由匹配的 JSON 合同预测。缺少来源或合同则不生成确定的效果评分。

同一牌选择、预览、确认、取消仍使用现有动作目录与新鲜 legal_actions；合同、候选组合及预测出的后续页面都不能授予未来动作权限。转化选择器加入现有原界面回调覆盖，但没有为未声明的随机转化或附魔虚构预测。

规则版本 3.2.0 新增 Cook 选择后续合同，并由休息 Smith 预测复用同一合同。实体描述资料明确为 `reference_only`，不产生 utility 或触发次数；药水数值代理只读取模型声明与实际变量，储备区间使用现有策略配置。
