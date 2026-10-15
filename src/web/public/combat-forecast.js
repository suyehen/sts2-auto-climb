/* Read-only human projection of bounded combat forecasts. */
const number=v=>typeof v==='number'&&Number.isFinite(v)?Number(v.toFixed(2)).toString():'未知';
const known=v=>typeof v==='number'&&Number.isFinite(v);
const intentNames={Attack:'攻击',SingleAttack:'单次攻击',MultiAttack:'多段攻击',Defend:'防御',Buff:'强化',Debuff:'削弱',StatusCard:'生成状态牌',Escape:'逃跑',Unknown:'未知意图'};
export function enemyIntentFacts(snapshot={}){
 return (snapshot.state?.enemies||[]).filter(e=>e.hp==null||e.hp>0).map(e=>({
  id:e.instance_id,name:e.name||'敌人',hp:e.hp,block:e.block,
  intents:e.intents?.length?e.intents.map(i=>{
   const label=i.label&&!/^[\s\d+×x.,%−-]+$/.test(i.label)?i.label:null;
   const title=label||intentNames[i.type]||i.type||'未知意图';
   const attack=known(i.attack_damage)?` · ${number(i.attack_damage)} × ${number(i.attack_repeats)}${known(i.attack_repeats)?' = '+number(i.attack_damage*i.attack_repeats)+' 点':''}`:'';
   const tip=i.hover_tip?.description||i.hover_tip?.body;
   return title+attack+(tip?'；'+tip:'');
  }):['当前意图未读取，伤害未知。']
 }));
}
/* Trace probabilities belong to a move transition under its post-play state.
   They must never be added across candidates or treated as joint outcome mass. */
export function candidateIntentFacts(candidate,snapshot={}){
 const turn=snapshot.state?.player?.turn,lines=[];
 for(const scenario of candidate.scenarios||[]){
  const seen=new Set();
  for(const row of scenario.enemy_turns||[]){
   if(turn==null||row.turn!==turn)continue;
   const key=JSON.stringify([row.instance_id,row.move,row.next_move,row.next_attacks,row.probability,row.enemy_hp,row.effects]);
   if(seen.has(key))continue;seen.add(key);
   const name=row.name||snapshot.state?.enemies?.find(e=>e.instance_id===row.instance_id)?.name||'敌人';
   const hits=row.next_attacks;
   const attack=hits==null?'下一招未预测':!hits.length?'下一招不包含攻击':`下一招 ${hits.map(number).join(' + ')} = ${number(hits.reduce((sum,v)=>sum+v,0))} 点攻击`;
   lines.push(`${name}（出牌后生命 ${number(row.enemy_hp)}）：${row.move_title||row.move||'当前行动'} → ${row.next_move_title||row.next_move||'未知后续'}；${attack}；该状态下转移概率 ${number(known(row.probability)?row.probability*100:null)}%。`);
   for(const card of row.generated_cards||[])lines.push(`本回合生成 ${card.amount} 张${card.name||'状态牌'}，置入${card.pile==='discard'?'弃牌堆':'抽牌堆'}。`);
  }
 }
 if(!lines.length)lines.push('本方案未记录可展示的后续行动；可能已击杀、未覆盖或未读取回合数，不能据此按零伤害判断。');
 return lines;
}
export function combatForecastFacts(forecast,snapshot={}){
 if(!forecast)return [];
 const lines=[`推演窗口：${forecast.horizon??'未知'} 回合。`];
 if(forecast.enemy_model?.mode==='local_behavior_graph')lines.push('使用当前游戏版本的怪物行动图；按出牌后的状态重新计算后续分支。');
 else lines.push('这是指定攻击场景，不能当作怪物真实后续行为。');
 if(forecast.budget_exhausted)lines.push('已达到计算预算，部分分支未计算。');
 for(const candidate of forecast.candidates||[]){
  const label=(candidate.group==='observation_boundaries'?'抽牌方案':'战斗方案')+' '+(candidate.index+1);
  for(const scenario of candidate.scenarios||[]){
   const result=scenario.result,metrics=result?.conditional_metrics;
   if(!metrics||!result.covered_probability){lines.push(`${label}：后续结果未知，不能据此判断安全。`);continue}
   lines.push(`${label}：已计算分支概率 ${number(result.covered_probability*100)}%；这些分支内预计失血 ${number(metrics.hp_loss)}，主要敌人剩余生命 ${number(metrics.remaining_enemy_hp)}。`);
   if(result.covered_probability<1)lines.push('尚有未覆盖分支；以上数值只适用于已计算部分。');
   const currentTurn=snapshot.state?.player?.turn;
   for(const row of (scenario.enemy_turns||[]).filter(x=>currentTurn==null||x.turn===currentTurn)){
    const name=row.name||snapshot.state?.enemies?.find(e=>e.instance_id===row.instance_id)?.name||'敌人';
    const attack=row.next_attacks==null?'下一招在本次窗口之外，未预测':row.next_attacks.length?'下一招攻击 '+number(row.next_attacks.reduce((sum,v)=>sum+v,0))+' 点':'下一招不包含攻击';
    lines.push(`${name}：${row.move_title||'当前行动'} → ${row.next_move_title||'后续行动'}，${attack}，分支概率 ${number(row.probability*100)}%。`);
    for(const card of row.generated_cards||[])lines.push(`本回合生成 ${card.amount} 张${card.name||'状态牌'}，置入${card.pile==='discard'?'弃牌堆':'抽牌堆'}。`);
   }
  }
 }
 if(forecast.unmodeled?.length||forecast.enemy_model?.source_issues?.length)lines.push('存在尚未支持的怪物行为、状态或交互；缺失部分没有按零伤害计算。');
 lines.push('分支覆盖和条件结果仅针对当前推演窗口，不代表整场战斗胜率。');
 return lines;
}
