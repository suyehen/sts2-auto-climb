import {el,definitions,table,actionName} from './presentation.js';
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
