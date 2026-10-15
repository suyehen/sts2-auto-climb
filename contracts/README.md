# 跨语言协议

0.30.0 新增全局动作目录 `data/knowledge/rules/actions.json`，覆盖当前桥接的 42 种命令。C# 将该文件嵌入程序集，Python 通过知识清单读取同一文件；目录定义、版本与 SHA256 是共同依据。`legal-action.schema.json` 描述实时绑定，新增 `definition_id`、`source`；`state.action_catalog` 给出版本与指纹。旧快照缺少新增元数据时兼容读取，声明了不同目录身份时拒绝混用。动作请求格式保持不变。

对象能力、效果/触发引用与实时可操作性分别记录。详见 `docs/design/全局动作目录与对象能力记录-2026-10-15.md`。目录和能力记录不是执行权限，提交前仍检查新鲜 `legal_actions`。

状态、动作、错误与实体资料的 JSON Schema 放在此目录，供 C#、Python 和 MCP 共同遵循。已定义 `action-request.schema.json`；P1 工具和动作生命周期见 `docs/P1接口与验收.md`，0.3.0 奖励动作及打开/选择两阶段语义见 `docs/奖励接口与验收.md`。奖励动作继续使用同一请求结构，target_id=null；状态与错误的完整 Schema 尚待补齐。

0.4.0 支持没有对局的主菜单动作：请求 run_id 使用快照中的 `state.command_context_id`（对局内等于真实 run_id，主菜单为菜单实例 ID）。state.run_id 在没有对局时仍为 null。详见 `docs/主菜单接口与验收.md`。

0.5.0 新增 state.map 与 choose_map_node 动作，使用原请求结构且 target_id=null；节点必须来自当前 legal_actions。详见 docs/地图接口与验收.md。

0.6.0 新增 state.event_screen、choose_event_option 与 proceed_event。选项包含动态描述及 hover_tips；仍使用原请求结构、target_id=null。子选择完成边界见 docs/事件接口与验收.md。

0.7.0 新增 state.card_selection 与分步选择、预览、确认和取消动作，仍使用原请求结构且 target_id=null。事件打开受支持的选择器后，原事件请求 completed 表示界面就绪，效果在后续确认后结算。详见 docs/牌组选择接口与验收.md。

0.8.0 新增 state.shop 以及 open_shop、buy_shop_item、close_shop、proceed_shop。买删牌服务只打开后续选择器，最终扣费由游戏在确认后处理。详见 docs/商店接口与验收.md。

0.9.0 新增 state.rest_site、choose_rest_option 和 proceed_rest。锻造打开升级选择器后，由后续选牌动作确认升级。详见 docs/篝火接口与验收.md。

0.10.0 扩展 player.potions，并新增 use_potion、discard_potion；目标使用当前 legal_actions 中的实例 ID。补齐当前遗物、可见状态及意图的悬浮资料，满药水栏不再提供领取药水奖励动作。详见 docs/药水接口与验收.md。

0.10.1 修复 hover_tips 中展示卡牌的实例 ID：展示卡牌只包含内容字段，不提供 instance_id；可行动卡牌仍有实例 ID。此差异避免游戏重建提示模型导致状态版本无意义变化。

0.11.0 新增 return_to_main_menu 和 quit_game。退出回执 completed 表示退出已调度，进程结束必须由本地重启脚本另行验证；重启后须使用新的会话和实例 ID。详见 docs/退出与重启接口.md。
# 0.12 宝箱扩展

state.treasure 提供 screen_id、decision_ready、opened、picking、relics、controls。未开箱不暴露隐藏遗物。open_chest 回执完成只表示下一个宝箱决策已就绪；领取或跳过分别请求，等待正常父效果结束。新增 open_chest、take_treasure_relic、skip_treasure_relic、proceed_treasure，宝箱房支持 return_to_main_menu。跨进程重启后必须重新读取 session_epoch 与实例 ID。

0.13.1 player.relics 新增 status（游戏公开的 RelicStatus）与 counter（仅 ShowCounter=true 时读取 DisplayAmount，否则 null）。这是可见 UI 数据，不保证所有遗物都有计数；不能把 null 当作计数 0。

0.14.0 card_selection.enchantment 可选对象：entity_id、amount、title、description；只在牌组附魔选择界面存在。候选牌为当前真实牌，附魔在确认前不修改牌组。预览沿用既有 preview 和 controls 字段。


0.21.0 新增 close_map_overlay、continue_game_over、return_after_game_over，仍使用相同动作请求及新鲜 legal_actions，target_id=null；game_over 阶段与普通战斗分开。恢复动作遵循游戏正常按钮／界面方法，不能跳过模态。内部 Python 契约 1.3.0 新增百科模板标记及弱化／脆弱持续信息；统一报告 decision/unified/1.0 与动作权限分离。详见 docs/决策评估与效率升级.md。
