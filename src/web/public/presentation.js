/* Human-readable projections. The source objects remain intact for engineering inspection. */
import {eventForecastFacts,eventBranchFacts} from './event-forecast.js';
import {combatForecastFacts} from './combat-forecast.js';
export function gameText(value){return String(value).replace(/(\[img\]([^\[]+)\[\/img\])(?:\1)*/g,(match,whole,path,offset,source)=>{const images=[...match.matchAll(/\[img\]([^\[]+)\[\/img\]/g)];const unit=images[0][1].includes('energy_icon')?'能量':images[0][1].includes('star_icon')?'星星':'图标';return (images.length===1&&/[0-9X]/.test(source[offset-1]||'')?'':images.length)+'点'+unit;}).replace(/\[\/?(?:gold|blue|red|green|purple|orange|white|gray|grey|pink|yellow|b|i)\]/g,'');}
export const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=tag==='pre'?text:gameText(text);if(cls)n.className=cls;return n};
export const fmt=v=>v==null?'未记录':typeof v==='boolean'?(v?'是':'否'):typeof v==='number'?(Number.isInteger(v)?String(v):v.toFixed(2)):String(v);
export const character=v=>({IRONCLAD:'铁甲战士',SILENT:'静默猎手',DEFECT:'故障机器人',NECROBINDER:'亡灵契约师',REGENT:'储君'})[v?.split('.').at(-1)]||'未记录职业';
export const phase=v=>({combat:'战斗',event:'事件',shop:'商店',rest_site:'休息处',treasure:'宝箱',map:'地图',card_reward_selection:'抓牌',card_selection:'选牌',main_menu:'主菜单',singleplayer_menu:'单人菜单',character_selection:'角色选择',game_over:'对局结束',no_run:'未进入对局',rewards:'领取奖励',reward:'领取奖励',outside_combat:'房间内'})[v]||'等待状态';
export const result=v=>({win:'胜利',loss:'失败',abandoned:'已放弃',unknown:'未记录结果'})[v]||'尚未记录终局';
export const room=v=>({Monster:'普通战斗',Elite:'精英战斗',Boss:'首领',RestSite:'休息处',Shop:'商店',Treasure:'宝箱',Unknown:'未知事件',Ancient:'远古之民',AncientMapPoint:'远古之民'})[v]||phase(v);
export const stage=v=>({analysis_started:'正在计算',decision:'分析已完成',ai_choice:'AI 已作出选择',local_choice:'已作出确定性选择',local_outcome:'自动动作已结算',action_submitted:'正在执行',action_receipt:'等待结算',action_result:'动作已结算',outcome_observed:'结果已记录',snapshot:'状态快照',agent_packet:'已提交给决策模型',agent_outcome:'Agent 执行结果',agent_request:'模型请求状态',agent_error:'Agent 异常停止'})[v]||'对局记录';
export const agentPhase=v=>({paused:'已暂停',reading_state:'读取游戏状态',calculating:'比较候选方案',waiting_model:'等待 AI 决策',waiting_reward_cards:'等待奖励牌就绪',executing:'正在执行',local_deterministic:'执行确定性策略',finished:'对局已结束',error:'运行已中止'})[v]||'等待状态';
export const reason=v=>({PLAN_COMPLETED:'动作计划已完成',OBSERVATION_REQUIRED:'需要读取新状态后继续判断',STATE_CHANGED:'游戏状态已变化，需要重新判断',MODEL_TIMEOUT:'模型等待超时',UNKNOWN_EFFECT:'存在尚未支持的效果'})[v]||(/^[A-Z][A-Z0-9_]+$/.test(v||'')?'详细原因尚未提供中文解释':v||'');
export const metricValue=(key,value)=>key.endsWith('probability')&&typeof value==='number'?(value*100).toFixed(1)+'%':fmt(value);
export function disclosureState(host){return new Map(Array.from(host.querySelectorAll?.('details')||[],d=>[d.id||d.querySelector('summary')?.textContent,d.open]))}
export function restoreDisclosures(host,state){for(const d of host.querySelectorAll?.('details')||[]){const key=d.id||d.querySelector('summary')?.textContent;if(state.has(key))d.open=state.get(key)}}
export const metrics={projected_hp_loss:'预计失血',effective_hp_damage:'有效伤害',effective_block:'有效格挡',heuristic_score:'综合收益分',remaining_energy:'剩余能量',remaining_stars:'剩余星星',fatal_probability:'致死概率',covered_probability:'已计算分支概率',delta_utility:'卡组边际收益',utility:'收益分',spent:'金币支出',projected_death:'是否预测死亡',all_enemies_killed:'是否全部击杀',prevented_attack_damage:'避免的攻击伤害',self_hp_loss:'自身效果失血',incoming_after_kills:'击杀后剩余攻击',expected_utility:'概率加权收益',base_value:'基础价值',synergy_correction:'卡组协同修正',environment_correction:'当前环境修正',constraint_correction:'机制限制修正',cost_penalty:'费用负担',draw_disabled_or_capacity:'抽牌限制修正',hp:'生命',block:'格挡',energy:'能量',max_hp:'生命上限',draw_count:'抽牌数',remaining_primary_enemy_hp:'主要敌人剩余生命',projected_attack_hp_loss:'预计受击失血',block_after_plays:'出牌后格挡',initial_visible_attack:'当前可见攻击',utility_points:'卡组综合分',damage:'伤害',downside_penalty:'风险扣分',hp_loss:'生命损失',survived:'条件生存率',finished:'条件完成率'};
Object.assign(metrics,{permanent_strength_gain:'永久力量增益',requested_draw:'请求抽牌数',remaining_hand_size:'剩余手牌数',projected_hp_loss_is_upper_bound:'失血预测是否仅为上限',hand_limit:'手牌上限',effect_draw_blocked:'效果抽牌是否被禁止',turn:'回合数',stars:'星星',base_strength:'基础力量',strength_delta:'力量变化',temporary_strength:'临时力量',hp_loss_reduction:'失血减免',turn_hp_loss_cap:'本回合失血上限',damage_weight:'伤害权重',block_weight:'格挡权重',draw_weight:'抽牌权重',energy_weight:'能量权重',strength_weight:'力量权重',vulnerable_weight:'易伤权重',curse_weight:'诅咒权重',hp_weight:'生命权重',max_hp_weight:'生命上限权重',gold_weight:'金币权重',risk_weight:'风险权重',opening_draw:'首回合抽牌',permanent_strength_weight:'永久力量权重',draw_option_weight:'抽牌机会权重',strength_multihit_weight:'力量与多段攻击协同',draw_cheap_card_weight:'抽牌与低费牌协同',exhaust_block_weight:'消耗与格挡协同',deck_size_penalty:'卡组厚度负担'});
export function empty(title,text){const n=el('div',undefined,'empty-state');n.append(el('div','◇','empty-symbol'),el('strong',title),el('p',text));return n}
export function stat(label,value,note){const n=el('div',undefined,'stat-card');n.append(el('span',label),el('strong',fmt(value)));if(note)n.append(el('small',note));return n}
export function decisionSourceStats(manifest){
 const counts=manifest?.counts;
 if(!counts)return {local:null,ai:null,total:null,localRate:null,aiRate:null};
 const local=counts.local_choice??0,ai=counts.ai_choice??0,total=local+ai;
 return {local,ai,total,localRate:total?local/total:null,aiRate:total?ai/total:null};
}
export function renderDecisionStats(host,manifest){
 const stats=decisionSourceStats(manifest),grid=el('div',undefined,'decision-stats-grid');
 for(const [label,count,rate] of [['本地决策率',stats.local,stats.localRate],['AI 决策率',stats.ai,stats.aiRate]]){
  grid.append(stat(label,rate===null?'—':(rate*100).toFixed(1)+'%',count===null?'数量未记录':count+' 次'));
 }
 host.replaceChildren(grid,el('p',stats.total===null?'暂无本局选择统计':stats.total===0?'本局尚未记录决策选择':'本局已记录 '+stats.total+' 次选择 · 占比按本地 + AI 选择计算','decision-stats-note'));
}
export function definitions(rows){const n=el('div');for(const [key,value]of rows){const r=el('div',undefined,'definition-row');r.append(el('span',key),el('strong',fmt(value)));n.append(r)}return n}
export function table(value){const t=el('table',undefined,'human-table');for(const [k,v]of Object.entries(value||{})){if(!metrics[k]||typeof v==='object'&&v!==null)continue;const r=el('tr');r.append(el('th',metrics[k]),el('td',metricValue(k,v)));t.append(r)}return t}
export function raw(title,value){const d=el('details');d.append(el('summary',title),el('pre',JSON.stringify(value,null,2)));return d}
export const calculationTier=v=>({exact:'确定性证明',estimated:'有条件估算',ai_required:'需要 AI 判断',adapter:'已支持基础计算',reference_only:'仅有参考资料',observed:'已观测',unknown:'未知'})[v]||'未评估';
export function mechanismPanel(audit){const panel=el('details');panel.open=true;panel.append(el('summary','本次计算范围与动态状态'));panel.append(definitions([['判断等级',calculationTier(audit.classification)],['尚未观测的状态',audit.unknown_state_count],['战斗后续分支','需要 AI 判断'],['收益分与胜率','收益分尚未校准为胜率']]));
 for(const b of audit.blockers||[])if(!/^[\x00-\x7f]*$/.test(b))panel.append(el('p',b,'decision-note'));
 for(const enemy of audit.enemies||[]){const section=el('div');section.append(el('h3',enemy.name||'敌人'));for(const f of enemy.fields||[]){const alias=Object.entries(enemy.field_aliases||{}).find(([,v])=>v===f.name)?.[0],label=({HasBeetleCharged:'已使用甲虫冲锋',_hasBeetleCharged:'已使用甲虫冲锋',_respawnCount:'复活次数',_isAlone:'是否独处',_stockAmount:'剩余储备'})[alias||f.name]||alias||f.name;section.append(definitions([[label,f.status==='observed'?fmt(f.value):'未知（未读取，不能按初始值计算）']]))}if(!enemy.fields?.length)section.append(el('p','当前资料未登记额外动态字段。','hint'));panel.append(section)}
 const changes=audit.tracking?.changes||[];if(changes.length){panel.append(el('h3','本次观测到的状态变化'));for(const c of changes)panel.append(el('p',c.name+'：'+fmt(c.before)+' → '+fmt(c.after)))}
 const models=el('details');models.append(el('summary','当前效果的建模情况'));for(const e of audit.entities||[])models.append(definitions([[e.name||e.entity_id,calculationTier(e.tier)]]));panel.append(models);return panel;
}
export function actionName(step,snapshot){const s=snapshot?.state||{},a=(s.legal_actions||[]).find(a=>a.action_id===step?.action_id),card=(s.player?.hand||[]).find(c=>'play_'+c.instance_id===step?.action_id),potion=(s.player?.potions||[]).find(c=>step?.action_id?.includes(c.instance_id)),target=(s.enemies||[]).find(e=>e.instance_id===step?.target_id),option=(s.event_screen?.options||[]).find(o=>o.action_id===step?.action_id);
 const reward=(s.reward_screen?.cards||[]).find(o=>o.action_id===step?.action_id),alternative=(s.reward_screen?.alternatives||[]).find(o=>o.action_id===step?.action_id),claim=(s.reward_screen?.rewards||[]).find(o=>o.action_id===step?.action_id);
 if(reward?.card?.name)return '选择「'+reward.card.name+'」';
 if(alternative&&String(alternative.option_id).toLowerCase()==='skip')return '跳过抓牌';
 if(alternative?.description)return alternative.description;
 if(claim?.type==='CardReward')return '打开卡牌奖励';
 let name=card?.name||potion?.name||option?.title||option?.text||({end_turn:'结束回合',proceed_rewards:'离开奖励房间',choose_map_node:'前往选中的房间',choose_event_option:'选择事件选项',skip_card_reward:'跳过抓牌',confirm_selection:'确认选牌'})[a?.type||step?.action_id]||'执行当前选项';return name+(target?' → '+(target.name||'目标敌人'):'');}
export function candidateName(candidate,snapshot,index=0){
 const c=candidate;
 if(c.kind==='skip')return '跳过抓牌';
 const named=c.card?.name;
 if(named){const name=named+(c.card.upgrade_level>0&&!named.endsWith('+')?'+':'');return (({add:'选择',remove:'移除',upgrade:'升级'})[c.kind]||'选择')+'「'+name+'」';}
 if(c.title||c.name||c.label)return c.title||c.name||c.label;
 if(c.steps)return c.steps.length?c.steps.map(a=>actionName(a,snapshot)).join(' → '):'保留资源，暂不继续出牌';
 if(c.action_id)return actionName(c,snapshot);
 const option=(snapshot?.state?.event_screen?.options||[]).find(o=>o.option_id===c.option_id);
 return option?.title||option?.text||'候选方案 '+(index+1);
}
export function candidateGroups(report,snapshot,detailed=false){const out=el('div');const groups=[['战斗方案',report.combat?.candidates||report.shortlist],['抽牌与观察方案',report.combat?.observation_boundaries||report.observations],['抓牌、删牌与升级',report.choices?.candidates],['商店购买方案',report.shop?.candidates],['事件选择',report.event?.candidates],['事件后续路线',report.event_sequence?.candidates],['休息与升级',report.rest?.candidates],['地图路线',report.route?.candidates]];let count=0;
 for(const [title,list]of groups){if(!list?.length)continue;const group=el('div',undefined,'candidate-group');group.append(el('h3',title));for(const [i,c]of list.entries()){count++;const card=el('div',undefined,'candidate-card'),head=el('div',undefined,'candidate-heading');const name=candidateName(c,snapshot,i);head.append(el('span',i+1,'candidate-rank'),el('span',name,'candidate-name'));const score=c.delta_utility??c.utility??c.metrics?.heuristic_score;if(score!=null){const badge=el('strong',fmt(score),'candidate-score');badge.title='收益评分，不是胜率';head.append(badge)}card.append(head);
  if(c.card){const cost=c.card.costs_x?'X 点能量':typeof c.card.energy_cost==='number'&&c.card.energy_cost>=0?c.card.energy_cost+' 点能量':'费用未记录';card.append(el('p',cost+(c.card.costs_star_x?' · X 点星星':c.card.star_cost>0?' · '+c.card.star_cost+' 点星星':''),'hint'));if(c.card.description)card.append(el('p',c.card.description,'result-description'));}
  const strip=el('div',undefined,'metric-strip');for(const [key,v]of Object.entries(c.metrics||{})){if(!metrics[key]||v==null||typeof v==='object')continue;const span=el('span',metrics[key]);span.append(el('strong',metricValue(key,v)));strip.append(span)}card.append(strip);if(c.reason)card.append(el('p',reason(c.reason),'result-description'));
  if(title==='事件后续路线'){for(const fact of eventForecastFacts(c))card.append(el('p',fact,'result-description'));if(c.branches){const d=el('details');d.open=detailed;d.append(el('summary','随机结果与后续选项逐步推演'));for(const fact of eventBranchFacts(c))d.append(el('p',fact,'result-description'));card.append(d)}}
  if(detailed){card.append(table(c.metrics||c),table(c),table(c.components||c.component_deltas));if(c.state_trace){const d=el('details');d.append(el('summary','逐步伤害与资源变化'));for(const step of c.state_trace){d.append(el('p',actionName(step.action,snapshot),'result-description'),el('small','执行前'),table(step.before),el('small','执行后'),table(step.after))}card.append(d)}}group.append(card)}out.append(group)}
 if(!count)out.append(empty('尚无候选分析','未计算的收益与概率不会按零处理。'));return out;
}
export function combatForecastPanel(forecast,snapshot={}){
 const panel=el('details',undefined,'combat-forecast');panel.id='combat-forecast-panel';panel.append(el('summary','怪物行动、分支与计算范围'));
 const scope=el('details',undefined,'forecast-scope');scope.id='combat-forecast-scope';scope.append(el('summary','计算范围与未覆盖内容'));
 const modeled=forecast.enemy_model?.mode==='local_behavior_graph';
 scope.append(definitions([['推演窗口',(forecast.horizon??'未知')+' 回合'],['行为模型',modeled?'本地怪物行动图':'指定攻击场景'],['计算预算',forecast.budget_exhausted?'已耗尽，部分分支未计算':'未标记耗尽']]));
 scope.append(el('p',modeled?'后续分支按各方案出牌后的状态计算。':'指定攻击场景不能当作怪物真实后续行为。','hint'));
 if(forecast.unmodeled?.length||forecast.enemy_model?.source_issues?.length)scope.append(el('p','存在未支持的行为、状态或交互，缺失部分没有按零伤害计算。','decision-note'));
 scope.append(el('p','分支概率依赖对应状态；窗口内条件结果不代表整场战斗胜率。','hint'));panel.append(scope);
 for(const candidate of forecast.candidates||[]){
  const label=(candidate.group==='observation_boundaries'?'抽牌方案':'战斗方案')+' '+(candidate.index+1);
  const plan=el('details',undefined,'forecast-plan');plan.id=`forecast-${candidate.group}-${candidate.index}`;plan.append(el('summary',label));
  const steps=candidate.steps||[];plan.append(el('p',steps.length?steps.map(step=>actionName(step,snapshot)).join(' → '):'保留资源，暂不继续出牌','forecast-actions'));
  for(const [index,scenario] of (candidate.scenarios||[]).entries()){
   const section=el('section',undefined,'forecast-scenario');
   if(candidate.scenarios.length>1)section.append(el('h3','场景 '+(index+1)+' · '+scenario.name));
   const result=scenario.result;
   section.append(el('h4','窗口内计算结果'));
   if(result?.conditional_metrics&&result.covered_probability>0){
    section.append(table({covered_probability:result.covered_probability,hp_loss:result.conditional_metrics.hp_loss,remaining_primary_enemy_hp:result.conditional_metrics.remaining_enemy_hp}));
    if(result.covered_probability<1)section.append(el('p','尚有未覆盖分支；以上结果仅适用于已计算部分。','decision-note'));
   }else section.append(el('p','后续结果未知，不能据此判断安全。','decision-note'));
   section.append(el('h4','怪物行动分支'));
   const enemies=new Map();
   for(const row of modeled?scenario.enemy_turns||[]:[]){
    if(!enemies.has(row.instance_id))enemies.set(row.instance_id,[]);
    const rows=enemies.get(row.instance_id);if(!rows.some(r=>JSON.stringify(r)===JSON.stringify(row)))rows.push(row);
   }
   for(const [id,rows] of enemies){
    const enemy=el('details',undefined,'forecast-enemy');enemy.id=`${plan.id}-scenario-${index}-enemy-${id}`;
    enemy.append(el('summary',rows[0].name||snapshot.state?.enemies?.find(e=>e.instance_id===id)?.name||'敌人'));
    const turns=new Map();for(const row of rows){if(!turns.has(row.turn))turns.set(row.turn,[]);turns.get(row.turn).push(row)}
    for(const [turn,branches] of turns){
     const round=el('section',undefined,'forecast-turn');round.append(el('h4',turn==null?'回合未记录':'第 '+turn+' 回合'));
     for(const [branchIndex,row] of branches.entries()){
      const branch=el('div',undefined,'forecast-branch');branch.append(el('strong','分支 '+(branchIndex+1)),el('p',(row.move_title||row.move||'当前行动')+' → '+(row.next_move_title||row.next_move||'后续未预测'),'forecast-transition'));
      const hits=row.next_attacks,attack=hits==null?'未预测':hits.length?hits.map(fmt).join(' + ')+' = '+fmt(hits.reduce((sum,v)=>sum+v,0))+' 点':'不包含攻击';
      branch.append(definitions([['出牌后怪物生命',row.enemy_hp],['下一招攻击',attack],['该状态下转移概率',metricValue('probability',row.probability)]]));
      for(const card of row.generated_cards||[])branch.append(el('p',`生成 ${card.amount} 张${card.name||'状态牌'} → ${card.pile==='discard'?'弃牌堆':'抽牌堆'}`,'hint'));
      round.append(branch);
     }
     enemy.append(round);
    }
    section.append(enemy);
   }
   if(!enemies.size)section.append(el('p',modeled?'本场景未记录怪物行动；不能据此推断没有后续威胁。':'此场景没有真实怪物后续行动预测。','hint'));
   plan.append(section);
  }
  panel.append(plan);
 }
 if(!forecast.candidates?.length)panel.append(el('p','暂无候选方案推演。','hint'));
 return panel;
}
export function autonomyPanel(autonomy,snapshot={}){
 const panel=el('details',undefined,'autonomy-proof');panel.id='autonomy-proof';
 panel.append(el('summary',autonomy.eligible?'本地决策依据 · 未调用 AI':'评分比较与 AI 分流'));
 const evidence=autonomy.evidence||{},review=autonomy.score_review;
 if(autonomy.eligible){
  const names={forced_action:'必须推进的唯一动作',forced_end_turn:'只能结束回合',mandatory_card_selection:'必须选择全部候选牌',certain_combat_finish:'确定立即结束战斗',eliminate_certain_death:'排除明确致死选项',equivalent_outcomes:'完整结果等价',dominant_outcome:'战后资源严格支配',common_first_step:'最佳路线的共同首步'};
  panel.append(definitions([['分流规则',names[autonomy.rule_id]||autonomy.rule_id],['下一步',(autonomy.selected_steps||[]).map(s=>actionName(s,snapshot)).join(' → ')],['AI 调用','跳过']]));
  if(evidence.search)panel.append(definitions([['路线搜索完整',evidence.search.complete],['已搜索状态',evidence.search.nodes],['可结束战斗的路线',evidence.finish_count],['最佳等价路线',evidence.equivalent_best_count],['被严格支配的路线',evidence.dominated_finish_count],['战后生命',evidence.hp_after_victory]]));
  if(evidence.selected_continuation?.length)panel.append(el('p','证明使用的路线：'+evidence.selected_continuation.map(step=>actionName(step,snapshot)).join(' → '),'result-description'));
  if(evidence.search)panel.append(el('p','仅执行下一步，结算后重新读取状态和证明。比较范围为已核验的本回合结束战斗场景。','hint'));
  for(const [index,finish] of (evidence.compared_finishes||[]).entries()){
   const row=el('details');row.id='autonomy-finish-'+index;row.append(el('summary','结束路线 '+(index+1)));
   row.append(el('p',finish.steps.map(step=>actionName(step,snapshot)).join(' → '),'result-description'),definitions([['战后生命',finish.persistent_outcome?.hp],['所选路线与此路线比较',({equal:'结果等价',dominates:'所选路线更好'})[finish.relation_to_selected]||'尚未确定']]));panel.append(row);
  }
 }else panel.append(el('p',autonomy.mode==='wait'?'当前暂停或状态未就绪，等待新状态后再判断。':'没有完整的确定性证明，需要 AI 综合判断。','result-description'));
 if(review){
  panel.append(definitions([['最高收益分',review.leading_score],['第二高收益分',review.runner_up_score],['收益分差',review.score_gap],['最高分并列方案',review.tied_leaders]]));
  panel.append(el('p',review.reason,'hint'));
 }
 return panel;
}
export function observedEffectsPanel(facts){
 const panel=el('details');panel.open=true;
 panel.append(el('summary','Agent 实际执行效果与收益'));
 const labels={deck:'牌组',relics:'遗物',potions:'药水',hand:'手牌',exhaust:'消耗堆'};
 const fields={upgrade_level:'升级等级',enchantment_id:'附魔',affliction_id:'附着效果',description:'效果说明',energy_cost:'能量费用'};
 for(const fact of facts){
  if(fact.status!=='observed'){
   for(const note of fact.notes||[])panel.append(el('p',note,'hint'));
   continue;
  }
  for(const [key,value]of Object.entries(fact.resources||{})){
   if(value.change!==0)panel.append(definitions([[metrics[key]||key,fmt(value.before)+' → '+fmt(value.after)+'（'+(value.change>0?'+':'')+fmt(value.change)+'）']]));
  }
  for(const [key,value]of Object.entries(fact.collections||{})){
   for(const item of value.added||[]){
    panel.append(el('p',labels[key]+'新增：'+(item.name||item.entity_id)+(item.count>1?' × '+item.count:'')));
    if(item.description)panel.append(el('p',item.description,'hint'));
   }
   for(const item of value.removed||[])panel.append(el('p',labels[key]+'移出：'+(item.name||item.entity_id)));
   for(const item of value.modified||[]){
    panel.append(el('p',(item.name||item.entity_id)+'已修改'));
    for(const [field,change]of Object.entries(item.changes||{}))panel.append(el('p',(fields[field]||field)+'：'+fmt(change.before)+' → '+fmt(change.after),'hint'));
   }
  }
  for(const creature of fact.creatures||[]){
   const name=creature.name||(creature.instance_id==='player'?'玩家':'敌人');
   if(creature.removed)panel.append(el('p',name+'离开战场；击杀归因需结合动作确认。','hint'));
   if(creature.instance_id!=='player'&&creature.hp_before!=null&&creature.hp_after!=null&&creature.hp_before!==creature.hp_after)
    panel.append(el('p',name+' · 生命：'+fmt(creature.hp_before)+' → '+fmt(creature.hp_after)));
   for(const power of creature.powers||[])panel.append(el('p',name+' · '+(power.name||power.entity_id)+'：'+fmt(power.before)+' → '+fmt(power.after)));
  }
  for(const note of fact.notes||[])panel.append(el('p',note,'hint'));
 }
 return panel;
}
export function renderDecision(host,record,tab='summary',choice=null,evidence=null,options={}){const disclosures=options.live?disclosureState(host):null;host.replaceChildren();if(!record){host.append(empty('选择一条记录','在时间线中点击决策或状态，查看对应房间的详细信息。'));return}
 const r=record.report||{},snapshot=record.snapshot||{},s=snapshot.state||{};
 if(evidence?.executed_effects?.length)host.append(observedEffectsPanel(evidence.executed_effects));
 if(tab==='evidence'){for(const observation of evidence?.observations||[]){const c=observation.event_comparison;if(!c)continue;const d=el('details');d.open=true;d.append(el('summary','事件选择：预测与实测对照'));for(const [key,label]of [['hp','生命'],['max_hp','生命上限'],['gold','金币']])d.append(definitions([[label+'预测变化',c.predicted_change?.[key]],[label+'实际变化',c.actual_change?.[key]],[label+'实际减预测',c.residual?.[key]],[label+'是否在模型结果范围内',c.within_modeled_support?.[key]]]));d.append(el('p','仅核对本次选择；随机结果偏离平均值不代表规则有误，权重不会自动修改。','result-description'));host.append(d)}}
 if(tab==='evidence'){const grid=el('div',undefined,'evidence-layout');const intended=el('div',undefined,'evidence-cell'),actual=el('div',undefined,'evidence-cell');intended.append(el('h3','当时选择了什么'),el('p',evidence?.choice?.reason||choice?.reason||'尚未记录选择理由。'));actual.append(el('h3','实际发生了什么'));const outcomes=evidence?.observations||[];if(!outcomes.length)actual.append(el('p','暂无与这次决策匹配的结果记录。'));for(const o of outcomes){if(o.note)actual.append(el('p',o.note,'result-description'));actual.append(table(o.actual||o.observed||o));}const plans=evidence?.plans||[];for(const p of plans)actual.append(el('p',({completed:'执行完成',stopped:'已停止',running:'执行中'})[p.status]||'计划已记录'));grid.append(intended,actual);host.append(grid);return}
 const p=s.player||{},c=p.creature||{};if(!options.live)host.append(definitions([['房间',phase(r.phase||s.phase)],['当时生命',c.hp!=null?c.hp+' / '+c.max_hp:'未记录'],['当时能量',p.energy],['当时金币',p.gold]]));
 if(choice?.reason)host.append(el('p',choice.reason,'decision-note'));
 if(r.autonomy?.eligible)host.append(el('p','这一步已通过确定性检查，可以直接执行，无需等待 AI。','decision-note'));
 if(r.autonomy?.eligible||r.autonomy?.score_review)host.append(autonomyPanel(r.autonomy,snapshot));
 if(r.combat?.lookahead){const facts=combatForecastFacts(r.combat.lookahead,snapshot),summary=el('div',undefined,'combat-forecast-summary');summary.append(el('h3',r.combat.lookahead.horizon+' 回合推演'));for(const fact of facts.filter(f=>/^(战斗方案|抽牌方案)/.test(f)))summary.append(el('p',fact,'result-description'));host.append(summary);const panel=combatForecastPanel(r.combat.lookahead,snapshot);panel.open=tab==='metrics'&&!options.live;host.append(panel)}
 if(options.live)host.append(candidateGroups(r,snapshot,false));
 if(r.mechanism_audit){const panel=mechanismPanel(r.mechanism_audit);if(options.live)panel.open=false;host.append(panel)}
 if(r.event_runtime_validation){const v=r.event_runtime_validation;const d=el('details');d.append(el('summary','事件实时读取核对'),definitions([['资料版本一致',v.source_matches],['已读取动态字段数量',v.observed_field_count],['未读取的字段',v.missing_fields?.join('、')||'无']]));for(const [name,value]of Object.entries(v.variables||{}))d.append(definitions([[name,value]]));host.append(d)}
 if(r.event_sequence){const f=r.event_sequence;host.append(el('p',f.comparison_complete===false?'部分后续选项尚不能计算；显示的已知方案不代表全部方案中的最优解。':f.search_truncated?'推演达到搜索上限，仍有未计算的后续。':f.supported?'事件后续推演已完成，分数用于比较收益，不代表胜率。':'本事件尚无可用推演，当前选项与源码参考由 AI 综合判断。','decision-note'));}
 if(r.combat?.coverage?.unknown_cards?.length||r.limitations?.length)host.append(el('p','部分效果或后续结果尚不能精确计算，当前评分需结合实际场景判断。','result-description'));
 if(!options.live)host.append(candidateGroups(r,snapshot,tab==='metrics'));
 if(tab==='metrics'||options.live){const w=el('details');w.append(el('summary','计算细节与评分权重'));if(options.live)w.append(candidateGroups(r,snapshot,true));w.append(table(r.weights),table(r.interaction_weights),table(r.combat_weights));host.append(w);if(r.combat)host.append(definitions([['已比较分支',r.combat.nodes],['计算耗时',r.combat.elapsed_ms!=null?r.combat.elapsed_ms.toFixed(1)+' 毫秒':'未记录'],['搜索范围是否耗尽预算',r.combat.search_truncated],['是否已证明范围内最优',r.combat.optimality_proven]]));}if(tab==='metrics'&&r.deck){const d=el('details');d.open=!options.live;d.append(el('summary','当前卡组价值分解'),table(r.deck.metrics),table(r.deck.components));host.append(d)}
 if(s.event_screen?.title||s.event_screen?.options?.length){const d=el('details');d.append(el('summary','当时的事件与悬浮提示'));if(s.event_screen.description)d.append(el('p',s.event_screen.description,'result-description'));for(const o of s.event_screen.options||[]){d.append(el('p',o.title||o.text||'事件选项','decision-summary'));for(const tip of o.hover_tips||[])if(tip.description||tip.body)d.append(el('p',tip.description||tip.body,'result-description'))}host.append(d)}
 if(disclosures)restoreDisclosures(host,disclosures);
}
