/* One status projection for every Agent control surface. */
export const phaseNames={paused:'已暂停',reading_state:'读取游戏状态',calculating:'计算候选方案',waiting_model:'等待 AI 决策',waiting_reward_cards:'等待奖励牌就绪',waiting_event_options:'等待事件选项就绪',executing:'执行游戏动作',local_deterministic:'执行本地策略',finished:'本局已结束',error:'异常停止'};
export function decisionActivity(status,fallback='等待分析'){
 if(!status?.running)return fallback;
 if(status.phase==='executing'&&status.last?.plan_length>1)return '执行动作计划 · 第 '+status.last.plan_step+'/'+status.last.plan_length+' 步';
 return phaseNames[status.phase]||'正在运行';
}
export function modelWaitDetail(status,now=Date.now()){
 const request=status?.model_wait;
 if(!status?.running||status.phase!=='waiting_model'||!request)return '';
 const seconds=Math.max(0,Math.floor((now-request.started_at*1000)/1000));
 const total=request.overall_started_at?Math.max(0,Math.floor((now-request.overall_started_at*1000)/1000)):seconds;
 return (request.validation_attempt>1?'正在纠正决策格式 · 第 '+request.validation_attempt+' 次尝试 · ':request.attempt>1?'正在重试 · 第 '+request.attempt+' 次请求 · ':'')+'本次已等待 '+seconds+' 秒，最长 '+request.timeout_seconds+' 秒'+(total>seconds?' · 累计 '+total+' 秒':'');
}
export const agentErrors={MODEL_TIMEOUT:'模型等待超时，Agent 已停止。可以调整最长等待时间或切换后端后重试。',CODEX_LOGIN_OR_REQUEST_FAILED:'本机 Codex 登录或请求失败，请检查登录状态。',CODEX_NOT_INSTALLED:'未找到本机 Codex 程序。',MODEL_UNAVAILABLE:'模型服务无法连接。',INVALID_STATE_CONFIG_OR_ACTION:'游戏状态、配置或动作校验未通过，Agent 已停止。'};
Object.assign(agentErrors,{
 SERVICE_MAINTENANCE:'后台服务正在维护，完成后再启动 Agent。',
 MODEL_GENERATION_INTERRUPTED:'模型服务中途终止生成，有限重试仍未恢复。请稍后重试。',
 MODEL_RESPONSE_INCOMPLETE:'模型回复未正常结束，本次回复没有用于执行。可重试；若反复发生，请检查输出长度上限。',
 MODEL_OUTPUT_LIMIT:'模型输出达到长度上限，决策回复被截断。请提高输出长度上限，或降低推理强度后重试。',
 MODEL_INVALID_DECISION:'模型返回的 JSON 或决策字段不符合要求，本次回复没有用于执行。请检查结构化输出设置后重试。',
 MODEL_TOOL_CALL_REQUIRED:'模型没有提交要求的决策工具调用，纠正后仍未成功，本次回复没有用于执行。',
 MODEL_INVALID_TOOL_CALL:'模型提交的工具调用不符合决策接口要求，本次回复没有用于执行。',
 MODEL_STRICT_UNSUPPORTED:'当前模型接口明确表示不支持严格工具调用，请切换到 JSON 模式。',
 MODEL_INVALID_RESPONSE:'模型接口返回的数据格式无法读取。请检查服务兼容性。',
 MODEL_EMPTY_RESPONSE:'模型没有返回决策内容；自动恢复未成功，Agent 已停止。请重试或切换模型。',
 REWARD_CARDS_NOT_READY:'奖励牌超过 15 秒仍未可选。Agent 已暂停，请检查游戏内选牌界面。',
 EVENT_OPTIONS_NOT_READY:'事件选项超过 15 秒仍未就绪。Agent 已暂停，请检查游戏内事件界面。',
 MODEL_REFUSAL_OR_TOOL_CALL:'模型返回了拒绝或工具调用，未提供可执行的决策。',
 MODEL_RESPONSE_TOO_LARGE:'模型回复超过接收大小限制。请缩短输出。',
 MODEL_HTTP_400:'模型服务拒绝请求（HTTP 400）。请检查模型名称、结构化输出和推理设置。',
 MODEL_HTTP_401:'模型密钥无效或已失效（HTTP 401）。请到 Agent 管理检查密钥。',
 MODEL_HTTP_402:'模型服务余额或额度不足（HTTP 402）。请检查账户额度。',
 MODEL_HTTP_429:'模型服务限流或配额受限（HTTP 429）。请稍后重试或检查服务额度。',
 STALE_MODEL_RESULT:'模型返回的状态标识与本次请求不一致，决策被拒绝。可重新启动分析。',
 ILLEGAL_ACTION:'模型选择了当前不可用的动作，决策被拒绝。可重新启动分析。',
 ILLEGAL_TARGET:'模型选择了当前不可用的目标，决策被拒绝。',
 TARGET_REQUIRED:'模型遗漏了该动作所需的目标，决策被拒绝。',
 MULTI_ACTION_REQUIRES_COMBAT:'模型在非战斗界面返回了多步计划，此界面只支持单次选择。',
 PREVIOUS_ACTION_UNRESOLVED:'上一个动作的结果尚未确认，Agent 已停止以避免重复执行。请检查游戏是否仍在结算。',
 ACTION_FAILED:'游戏动作执行失败，请检查游戏界面和下方动作记录。',
 ACTION_REJECTED:'游戏拒绝了提交的动作，请检查游戏界面后重新读取状态。',
 ACTION_UNKNOWN:'游戏动作结果尚未确认，请先检查游戏是否已执行该动作。',
 GAME_DISCONNECTED:'游戏连接已断开，请恢复游戏连接后重试。',
 EXECUTOR_BUSY:'另一个执行器正在操作游戏，请等待它完成。',
 AGENT_ERROR:'Agent 内部出现异常，已停止。异常代码与发生阶段见下方详情。'
});
export function agentErrorMessage(code){return agentErrors[code]||(/^MODEL_HTTP_5\d\d$/.test(code||'')?'模型服务暂时发生故障（'+code.replace('MODEL_HTTP_','HTTP ')+ '），请稍后重试。':'运行发生异常（'+(code||'UNKNOWN')+'），请查看异常详情。')}
export function agentFailureText(status){
 if(!status?.error)return '';
 const d=status.error_details,where=d?.stage?('发生在'+(phaseNames[d.stage]||d.stage)+(d.stage==='executing'&&d.plan_step?' · 第 '+d.plan_step+'/'+d.plan_length+' 步':'')+'。'):'';
 return where+agentErrorMessage(status.error)+' 错误码：'+status.error;
}
export function controlView(status,game,{busy=false,available=true}={}){
 const running=!!status?.running,launching=!!status?.launch_active,profile=status?.profiles?.find(p=>p.id===status.active);
 const openPlay=game?.connected===false||['main_menu','singleplayer_menu','character_selection','no_run','game_over'].includes(game?.phase);
 let reason='';
 if(!available||!status)reason='Agent 服务未连接，请检查启动脚本。';
 else if(busy)reason='正在处理控制请求，请稍候。';
 else if(running)reason='Agent 已启动，无需重复启动。';
 else if(launching)reason='正在启动游戏与 Agent，请稍候或点击暂停。';
 else if(!profile?.ready)reason='当前决策后端尚未配置完成，请到 Agent 管理检查模型和密钥。';
 else if(game?.connected===false)reason='';
 else if(!game||game.connected!==true||!game.phase)reason='正在确认当前游戏状态，请稍候。';
 const unavailable=!available||!status;
 const stoppedByError=!running&&status?.phase==='error';
 const previousError=!running&&status?.phase==='paused'&&status?.error;
 const waiting=modelWaitDetail(status);
 const runningDetail=status?.phase==='paused'?'正在启动':decisionActivity(status)+(waiting?' · '+waiting:'');
 return {running:running&&!unavailable,tone:unavailable?'offline':running?'running':stoppedByError?'error':'paused',
  label:unavailable?'Agent 未连接':launching?'正在准备自动游玩':running?'Agent 运行中':stoppedByError?'Agent 异常停止':status.phase==='finished'?'Agent 已停止 · 本局结束':'Agent 已暂停',
  detail:unavailable?'无法确认实际运行状态':launching?'正在自动启动游戏、处理菜单并准备决策，可点击暂停取消。':running?runningDetail+' · 会自动执行游戏动作':stoppedByError?agentFailureText(status):'不会自动执行新的游戏动作'+(previousError?' · 上次运行：'+agentFailureText(status):''),
  openPlay,startDisabled:!!reason,startReason:reason,startLabel:busy?'处理中…':launching?'启动中…':running?'Agent 已启动':openPlay?'开始游玩 →':stoppedByError?'重试启动 Agent':'启动 Agent',
  pauseDisabled:busy||unavailable||(!running&&!launching),pauseReason:unavailable?'Agent 服务未连接':!running&&!launching?'Agent 已停止，无需再次暂停':busy?'正在处理控制请求':'暂停后不会提交新动作；已提交的动作可能仍在结算',
  profile:profile?.label||'未选择后端'};
}
