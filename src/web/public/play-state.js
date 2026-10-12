/* Pure launch-page projection, shared by rendering and tests. */
export const launchPhases={checking:'检查配置与存档',closing_finished_run:'关闭本局结算并返回菜单',launching_game:'启动游戏',waiting_game:'等待游戏连接与菜单就绪',continuing_save:'载入存档',setting_up_run:'选择职业与进阶',starting_agent:'启动自动决策',completed:'已进入自动游玩',cancelled:'启动已取消',interrupted:'上次启动被中断',failed:'启动失败'};
export const launchErrors={CHARACTER_UNAVAILABLE:'所选职业尚未解锁或不可用，请选择其他职业。',ASCENSION_UNAVAILABLE:'指定进阶尚未解锁，请降低等级或选择最高已解锁进阶。',UNSUPPORTED_MENU:'当前菜单不支持此操作，请返回游戏主菜单。',MENU_STATE_CHANGED:'菜单选择已变化，启动已停止，请重新检查设置。',GAME_START_FAILED:'启动未完成，请检查游戏、模组与本机服务。',EXECUTOR_BUSY:'另一个执行器正在操作游戏，请稍后重试。',EXISTING_SAVE_REQUIRES_CONFIRMATION:'已有未完成存档，请继续存档，或明确勾选替换存档。',SAVE_CONFIRMATION_EXPIRED:'游戏状态已变化，请重新检查存档并确认。',SAVE_AND_EXIT_FIRST:'当前正在对局中，请先保存并返回主菜单后开始新局。',NO_LOADABLE_SAVE:'没有可读取的存档。',NO_CURRENT_RUN:'当前没有进行中的对局。',GAME_START_TIMEOUT:'游戏连接等待超时，请检查游戏与模组。',GAME_LAUNCHER_UNAVAILABLE:'未找到 Steam 启动程序，请检查本机安装配置。',MENU_ACTION_UNRESOLVED:'菜单操作结果尚未确认，已暂停。请检查游戏状态，不会重复提交。',PREVIOUS_ACTION_UNRESOLVED:'上一次操作结果尚未确认，已阻止新操作。',AGENT_ALREADY_ACTIVE:'正在启动或游玩，请先暂停。',START_INTERRUPTED:'服务重启中断了上次启动，请检查游戏后重试。',PROVIDER_NOT_CONFIGURED:'请先在 Agent 管理配置模型和密钥。',SESSION_CHANGED:'游戏会话已变化，启动已停止。',MENU_ACTION_UNAVAILABLE:'当前菜单无法执行此操作，请检查游戏页面。',GAME_NOT_READY:'游戏尚未就绪，启动已停止。'};
export function playView(data,{profileId,replaceSave=false,busy=false}={}){
 const a=data?.agent,g=data?.game,p=a?.profiles?.find(x=>x.id===(profileId||a.active));
 const current=!!g?.run_id&&!['main_menu','singleplayer_menu','character_selection','game_over','no_run'].includes(g.phase);
 const active=!!a?.running||!!a?.launch_active;
 const common=!data?'正在连接本地服务。':busy?'正在提交启动请求。':active?'正在启动或自动游玩，可随时暂停。':!p?.ready?'请先配置所选决策后端。':'';
 const saved=!!g?.menu?.has_saved_run;
 return {current,saved,active,newReason:common||(current?'当前对局中，请先保存并返回主菜单。':saved&&!replaceSave?'已有存档；继续游玩，或勾选替换后再开始新局。':''),
  continueReason:common||(!current&&g?.connected&&(!saved||!g.menu.save_loadable)?'没有可继续的存档。':''),
  continueLabel:current?'接管当前对局':'继续存档并自动游玩',
  progress:data?.job?.status==='completed'&&!active?'Agent 已暂停':data?.job?launchPhases[data.job.phase]||data.job.phase:active?'自动游玩中':'准备开始',
  error:data?.job?.error?launchErrors[data.job.error]||'启动未完成，请切换工程视图查看详细原因。':''};
}
