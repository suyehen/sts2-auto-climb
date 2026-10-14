/* Human-readable audit evidence, with original identifiers kept alongside names. */
const statuses={started:'已开始',returned:'已返回，尚不代表通过校验',completed:'处理已完成',failed:'处理失败',cancelled:'已取消',rejected:'已拒绝',interrupted:'计划已中断',not_executed:'未执行',awaiting_choice:'等待后续选择',unknown:'结果未确认',wait:'等待游戏状态',ai_required:'需要模型决策'};
const codes={MODEL_TIMEOUT:'模型调用超时',MODEL_UNAVAILABLE:'无法连接模型服务',MODEL_OUTPUT_LIMIT:'回复达到输出长度上限',MODEL_GENERATION_INTERRUPTED:'模型生成被服务中断',MODEL_EMPTY_RESPONSE:'模型未返回决策内容',MODEL_INVALID_DECISION:'回复的 JSON 或决策字段不符合要求',MODEL_TOOL_CALL_REQUIRED:'未提交要求的决策工具调用',MODEL_INVALID_TOOL_CALL:'决策工具调用格式不符合要求',MODEL_STRICT_UNSUPPORTED:'接口明确不支持严格工具调用',MODEL_INVALID_RESPONSE:'无法解析模型服务响应',MODEL_RESPONSE_INCOMPLETE:'模型回复未正常结束',MODEL_REFUSAL_OR_TOOL_CALL:'返回了拒绝或非预期工具调用',MODEL_RESPONSE_TOO_LARGE:'回复超过接收大小限制',STALE_MODEL_RESULT:'回复状态标识与请求不一致',ILLEGAL_ACTION:'动作当前不可用',ILLEGAL_TARGET:'目标当前不可用',TARGET_REQUIRED:'遗漏必要的动作目标',MULTI_ACTION_REQUIRES_COMBAT:'非战斗界面只允许单步决策',PROVIDER_NOT_CONFIGURED:'决策后端尚未配置',CODEX_NOT_INSTALLED:'未找到本机 Codex CLI',CODEX_START_FAILED:'无法启动本机 Codex CLI',CODEX_LOGIN_OR_REQUEST_FAILED:'本机 Codex 登录或请求失败',CODEX_INVALID_RESPONSE:'本机 Codex 回复不可读取',GAME_DISCONNECTED:'游戏连接已断开',PREVIOUS_ACTION_UNRESOLVED:'上一个动作的执行结果尚未确认',AGENT_ERROR:'Agent 处理异常'};
const explainCode=code=>codes[code]||(/^MODEL_HTTP_\d+$/.test(code)?'模型服务返回 HTTP 错误':'未登记中文释义的错误');
const reasons={state_changed_or_observation_required:'游戏状态变化或需要重新观察',next_action_no_longer_legal:'下一步动作已不再合法',state_changed_or_agent_paused:'游戏状态变化或 Agent 已暂停',agent_paused_or_switched:'Agent 暂停或切换后端',state_changed_before_correction:'校验纠正前游戏状态已变化'};
const named=(label,id)=>label+(id?'（'+id+'）':'');
export function auditTitle(event){const d=event.details||{};return named(d.event_label||'未登记释义的审计事件',d.event)}
export function auditRows(event){
 const d=event.details||{},rows=[['审计事件',auditTitle(event)]];
 if(d.status)rows.push(['处理状态',named(statuses[d.status]||'未登记释义的状态',d.status)]);
 if(d.provider)rows.push(['决策后端',named(d.provider_label||'未记录后端名称',d.provider)]);
 if(d.model)rows.push(['模型',d.model]);
 if(d.code)rows.push(['异常说明',named(explainCode(d.code),d.code)]);
 if(d.retry_reason)rows.push(['重试原因',named(explainCode(d.retry_reason),d.retry_reason)]);
 if(d.rule_id)rows.push(['本地策略规则',named(({certain_combat_finish:'确定结束战斗',eliminate_certain_death:'排除确定致死选项',open_card_reward:'打开卡牌奖励',forced_action:'唯一可执行动作',mandatory_card_selection:'必须选择全部候选牌',forced_end_turn:'只能结束回合',bounded_combat_search:'有界战斗方案搜索',complete_outcome_comparison:'完整结果比较'})[d.rule_id]||'未登记释义的规则',d.rule_id)]);
 if(d.decision_basis)rows.push(['决策依据',named(({strict_proof:'确定性规则证明',bounded_combat_strategy:'有界战斗策略'})[d.decision_basis]||'未登记释义的决策依据',d.decision_basis)]);
 if(d.reason)rows.push(['处理原因',reasons[d.reason]?named(reasons[d.reason],d.reason):d.reason]);
 if(d.action_id)rows.push(['执行动作',named(d.action_label||'未知动作（当时未记录释义）',d.action_id)],['动作目标',named(d.target_label||'未知目标（当时未记录名称）',d.target_id)]);
 for(const issue of d.validation_issues||[]){const fields={json:'JSON 格式',state_token:'状态校验标识',steps:'动作步骤',reason:'决策理由',action_id:'动作编号',target_id:'目标编号','<extra_field>':'未允许的额外字段'},types={invalid_json:'JSON 格式无法解析',missing:'缺少必填字段',extra_forbidden:'包含未允许的字段',string_type:'字段需要文本值',too_short:'字段内容过短',too_long:'字段内容过长'};const field=String(issue.field||'').split('.').map(p=>fields[p]||(/^\d+$/.test(p)?'第 '+(Number(p)+1)+' 项':'未知字段')).join(' / ');rows.push(['校验问题',named(field||'决策格式',issue.field)+'：'+named(types[issue.type]||'字段未通过校验',issue.type)]);}
 for(const [i,s] of (d.step_labels||[]).entries())rows.push(['计划第 '+(i+1)+' 步',named(s.action_label,s.action_id)+' → '+named(s.target_label,s.target_id)]);
 for(const [field,label] of [['trace_id','本轮执行链编号'],['analysis_id','候选分析编号'],['plan_request_id','模型决策请求编号'],['request_id','执行请求编号'],['validation_attempt','决策校验尝试次数'],['attempt','本次传输尝试序号'],['elapsed_ms','本次耗时（毫秒）'],['max_tokens','输出 token 上限'],['state_version','当时状态版本'],['expected_state_version','动作绑定状态版本'],['session_epoch','游戏会话编号'],['run_id','游戏对局编号']])if(d[field]!=null)rows.push([label,d[field]]);
 for(const [field,label] of [['prompt_tokens','输入 token'],['completion_tokens','输出 token'],['total_tokens','合计 token'],['reasoning_tokens','推理 token']])if(d.usage?.[field]!=null)rows.push([label,d.usage[field]]);
 return rows;
}
