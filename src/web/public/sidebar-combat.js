import {el,definitions,table,actionName,autonomyPanel,observedEffectsPanel,phase,reason,renderDecision,disclosureState,restoreDisclosures} from './presentation.js';
import {enemyIntentFacts,candidateIntentFacts} from './combat-forecast.js';

export function renderSidebarCombat(intents,calculation,record,latest){
 intents.replaceChildren();calculation.replaceChildren();
 const snapshot=latest||{},s=snapshot.state||{},report=record?.report||{},forecast=report.combat?.lookahead;
 if(s.phase!=='combat'){
  intents.append(el('p','当前不在战斗中。','hint'));calculation.append(el('p','进入战斗并完成分析后展示。','hint'));return;
 }
 const enemies=enemyIntentFacts(snapshot);
 for(const enemy of enemies){
  const section=el('div',undefined,'intent-enemy');
  section.append(el('h3',enemy.name),definitions([['生命',enemy.hp],['格挡',enemy.block]]));
  for(const fact of enemy.intents)section.append(el('p','当前可见：'+fact,'result-description'));
  intents.append(section);
 }
 if(!enemies.length)intents.append(el('p','当前没有可见的存活怪物。','hint'));
 const runId=s.command_context_id||s.run_id;
 const fresh=snapshot.state_version!=null&&report.source?.state_version===snapshot.state_version
  &&(snapshot.session_epoch==null||report.source?.session_epoch===snapshot.session_epoch)
  &&(runId==null||report.source?.run_id===runId);
 if(!fresh){
  const text='后续意图等待当前状态的分析；最近一次决策对应其他状态或版本未记录。';
  intents.append(el('p',text,'decision-note'));calculation.append(el('p',text,'decision-note'));return;
 }
 if(!forecast){
  const text='当前分析尚无后续意图模型，后续行动与失血未知。';
  intents.append(el('p',text,'hint'));calculation.append(el('p',text,'hint'));return;
 }
 const modeled=forecast.enemy_model?.mode==='local_behavior_graph';
 intents.append(el('p',modeled?'以下后续行动按各方案出牌后的状态预测。':'以下为指定攻击场景，不能当作怪物真实后续意图。','decision-note'));
 calculation.append(el('p','出牌方案 → 击杀、格挡及怪物状态变化 → 敌方行动分支 → 后续抽牌与出牌 → 窗口内结果。','result-description'));
 calculation.append(definitions([['推演窗口',(forecast.horizon??'未知')+' 回合'],['行为模型',modeled?'本地怪物行动图':'指定攻击场景'],['推演耗时',forecast.elapsed_ms!=null?forecast.elapsed_ms.toFixed(1)+' 毫秒':'未记录']]));
 for(const candidate of forecast.candidates||[]){
  const title=(candidate.group==='observation_boundaries'?'抽牌方案':'战斗方案')+' '+(candidate.index+1);
  const steps=candidate.steps||[],name=steps.length?steps.map(step=>actionName(step,record.snapshot)).join(' → '):'保留资源，结束出牌';
  const moves=el('div',undefined,'intent-plan');moves.append(el('h3',title+' · '+name));
  if(modeled)for(const fact of candidateIntentFacts(candidate,record.snapshot))moves.append(el('p',fact,'result-description'));
  else moves.append(el('p','此场景没有真实怪物后续行动预测。','hint'));
  intents.append(moves);
  const panel=el('div',undefined,'intent-plan');panel.append(el('h3',title+' · '+name));
  const source=report.combat?.[candidate.group]?.[candidate.index];
  if(source?.metrics){panel.append(el('small','本回合计算'),table(source.metrics));}
  for(const scenario of candidate.scenarios||[]){
   const result=scenario.result;
   if(forecast.candidates.some(c=>c.scenarios?.length>1))panel.append(el('p','场景：'+scenario.name,'hint'));
   panel.append(el('small','后续窗口（包含本回合）'));
   if(!result?.conditional_metrics||!(result.covered_probability>0))panel.append(el('p','后续结果未知，不能据此判断安全。','decision-note'));
   else{
    panel.append(table({covered_probability:result.covered_probability,hp_loss:result.conditional_metrics.hp_loss,remaining_primary_enemy_hp:result.conditional_metrics.remaining_enemy_hp}));
    panel.append(el('p','结果仅适用于已计算分支；失血涵盖整个推演窗口。','hint'));
   }
  }
  calculation.append(panel);
 }
 if(!forecast.candidates?.length){intents.append(el('p','尚无候选方案的后续行动记录。','hint'));calculation.append(el('p','尚无可比较的推演结果。','hint'));}
 if(forecast.budget_exhausted||forecast.unmodeled?.length||forecast.enemy_model?.source_issues?.length)calculation.append(el('p','计算预算或未支持的行为使部分分支未覆盖；未知结果没有按零伤害处理。','decision-note'));
 calculation.append(el('p','转移概率依赖该方案出牌后的怪物状态，不是整场战斗胜率；推演结果供决策参考，不单独证明最优。','hint'));
}
const outcomeStatus={completed:'执行完成',awaiting_choice:'等待继续选择',running:'执行中',stopped:'执行已停止',failed:'执行失败',rejected:'动作被拒绝',unknown:'结果尚未确认',unresolved:'结果尚未确认',dispatch_pending:'动作待提交'};
export function renderDecisionFlow(host,record,choice=null,evidence=null,status=null,{history=false}={}){
 const opened=disclosureState(host);host.replaceChildren();
 const report=record?.report,snapshot=record?.snapshot,autonomy=report?.autonomy;
 const selected=evidence?.choice||choice,local=selected?.ai_called===false||selected?.group==='local_deterministic';
 const steps=selected?.steps||selected?.plan?.steps||(selected?.action_id?[selected]:[]);
 const add=(number,title,label,tone,body)=>{
  const section=el('section',undefined,'flow-step '+tone),heading=el('div',undefined,'flow-step-heading');
  heading.append(el('span',number,'flow-number'),el('h3',title),el('span',label,'flow-status'));
  section.append(heading);for(const node of body)section.append(node);host.append(section);
 };
 const text=value=>el('p',value,'flow-copy');
 const localBody=[text(!report?'等待游戏状态与分析。':history&&!autonomy?'这条旧记录未保存自主检查依据。':autonomy?.eligible?(autonomy.decision_basis==='bounded_combat_strategy'?'本地当前回合策略覆盖检查通过；执行首步后重新观察，不代表整局最优证明。':'确定性检查通过，可自主执行。'):autonomy?.mode==='wait'?'状态尚未就绪，等待下一次检查。':'未通过确定性检查，交由 AI 综合判断。')];
 if(autonomy?.eligible&&autonomy.selected_steps?.length)localBody.push(text(autonomy.selected_steps.map(s=>actionName(s,snapshot)).join(' → ')));
 if(autonomy?.reason)localBody.push(el('p',reason(autonomy.reason),'hint'));
 if(autonomy)localBody.push(autonomyPanel(autonomy,snapshot));
 add('01','自主决策',!report?'等待分析':history&&!autonomy?'未记录依据':autonomy?.eligible?'可自主执行':autonomy?.mode==='wait'?'等待状态':'交由 AI',autonomy?.eligible?'complete':'',localBody);
 const skip=local||autonomy?.eligible;
 const aiBody=[text(skip?'本次由自主决策处理，未调用 AI。':selected?selected.reason||'AI 已登记选择。':!report?'完成自主检查后显示 AI 决策。':status?.phase==='waiting_model'?'AI 正在比较方案，等待回复。':'尚未记录与本次分析匹配的 AI 选择。')];
 if(!skip&&steps.length)aiBody.push(text(steps.map(s=>actionName(s,snapshot)).join(' → ')));
 add('02','AI 决策',skip?'已跳过':selected?'已选择':history?'未记录选择':status?.phase==='waiting_model'&&report?'思考中':'等待选择',skip?'skipped':selected?'complete':'',aiBody);
 const observations=evidence?.observations||[],plans=evidence?.plans||[],body=[];
 for(const o of observations){
  if(o.status)body.push(text(outcomeStatus[o.status]||'执行状态已记录'));
  if(o.note)body.push(text(o.note));
  const actual=o.actual||o.observed;
  if(actual){body.push(table(actual));if(actual.phase)body.push(el('p','结算后：'+phase(actual.phase),'hint'))}
  const comparison=o.event_comparison;
  if(comparison?.actual_change)body.push(el('p','实际资源变化','hint'),table(comparison.actual_change));
  if(o.reason)body.push(el('p',reason(o.reason),'hint'));
 }
 for(const p of plans){body.push(text(outcomeStatus[p.status]||'执行计划已记录'));if(p.reason)body.push(el('p',reason(p.reason),'hint'))}
 if(evidence?.executed_effects?.length){const effects=observedEffectsPanel(evidence.executed_effects);effects.open=false;body.push(effects)}
 if(!body.length)body.push(text(history?(evidence===null?'执行证据暂时无法读取，请重新选择这条记录重试。':'这条记录未保存匹配的执行结果。'):!report?'选择并执行后，显示本次实际结果。':evidence===null?'正在读取本次结果；暂未确认结算。':'暂无与本次决策匹配的结果，等待执行或结算。'));
 const latest=observations.at(-1)?.status||plans.at(-1)?.status;
 const observed=observations.length||evidence?.executed_effects?.some(f=>f.status==='observed');
 add('03','执行结果',outcomeStatus[latest]||(observed?'已有观测':history?'未记录结果':'等待结果'),['failed','rejected','stopped'].includes(latest)?'failed':observed||latest==='completed'?'complete':'',body);
 restoreDisclosures(host,opened);
}

export function renderHistoryDecision(host,record,tab='summary',evidence=null){
 host.replaceChildren();
 const s=record.snapshot?.state||{};
 host.append(el('p','楼层 '+(s.floor??'未记录')+' · '+phase(record.report?.phase||s.phase)+(s.player?.turn!=null?' · 回合 '+s.player.turn:''),'history-decision-context'));
 const flow=el('div',undefined,'history-decision-flow');
 renderDecisionFlow(flow,record,evidence?.choice,evidence,null,{history:true});host.append(flow);
 for(const [key,title] of [['metrics','候选方案与计算依据'],['evidence','完整执行证据与预测对照']]){
  const panel=el('details',undefined,'history-decision-details');panel.id='history-decision-'+key;panel.open=tab===key;
  panel.append(el('summary',title));const content=el('div');
  renderDecision(content,record,key,evidence?.choice,evidence);panel.append(content);host.append(panel);
 }
}

export function historyDecisionEvidence(record,events,evidence=null){
 const id=record.event_id||record.report?.analysis_id;
 const linked=id?events.filter(e=>e.details?.analysis_id===id||e.details?.decision_id===id):[];
 const choice=linked.filter(e=>['ai_choice','local_choice'].includes(e.kind)).at(-1)?.details;
 const observations=linked.filter(e=>['local_outcome','agent_outcome','outcome_observed'].includes(e.kind)).map(e=>e.details);
 return {...evidence,choice:evidence?.choice||choice||null,observations:[...(evidence?.observations||[]),...observations.filter(o=>!evidence?.observations?.some(v=>JSON.stringify(v)===JSON.stringify(o)))],plans:evidence?.plans||[]};
}
