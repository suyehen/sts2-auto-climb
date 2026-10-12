import test from 'node:test';
import assert from 'node:assert/strict';
import {renderDecision,combatForecastPanel,autonomyPanel,actionName,candidateName} from '../public/presentation.js';

class Element{
 constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.open=false;}
 append(...children){this.children.push(...children)}
 replaceChildren(...children){this.children=children}
 querySelectorAll(tag){return this.children.flatMap(c=>[...(c.tag===tag?[c]:[]),...c.querySelectorAll(tag)])}
 querySelector(tag){return this.querySelectorAll(tag)[0]}
}
const text=node=>[node.textContent,...node.children.map(text)].join(' ');
const visible=node=>[node.textContent,...node.children.filter(c=>node.tag!=='details'||node.open||c.tag==='summary').map(visible)].join(' ');

test('reward candidates show the ranked card or skip with costs and effects in live and history views',()=>{
 const old=globalThis.document;globalThis.document={createElement:tag=>new Element(tag)};
 try{
  const candidates=[{kind:'add',card:{name:'突破',energy_cost:1,description:'失去1点生命。对所有敌人造成9点伤害。'},delta_utility:.021},
   {kind:'skip',card:null,delta_utility:0},
   {kind:'add',card:{name:'头槌',energy_cost:1,description:'将弃牌堆中的一张牌放到抽牌堆顶部。'},delta_utility:null},
   {kind:'add',card:{name:'全身撞击',energy_cost:1,description:'造成等同于你格挡的伤害。'},delta_utility:null}];
  const record={report:{choices:{candidates}},snapshot:{state:{phase:'card_reward_selection'}}},before=JSON.stringify(record);
  for(const live of [true,false]){
   const host=new Element('div');renderDecision(host,record,'summary',null,null,{live});
   const display=visible(host);
   assert.match(display,/1 选择「突破」/);assert.match(display,/2 跳过抓牌/);
   assert.match(display,/3 选择「头槌」/);assert.match(display,/4 选择「全身撞击」/);
   assert.match(display,/1 点能量/);assert.match(display,/对所有敌人造成9点伤害/);
   assert.doesNotMatch(display,/候选方案 [1-4]|undefined/);
  }
  assert.equal(JSON.stringify(record),before);
 }finally{globalThis.document=old}
});

test('reward actions and removal or upgrade candidates retain their specific identities',()=>{
 const s={state:{reward_screen:{cards:[{action_id:'choose',card:{name:'突破'}}],alternatives:[{action_id:'skip',option_id:'Skip',description:'跳过'}]}}};
 assert.equal(actionName({action_id:'choose'},s),'选择「突破」');
 assert.equal(actionName({action_id:'skip'},s),'跳过抓牌');
 assert.equal(candidateName({kind:'remove',card:{name:'打击'}},s),'移除「打击」');
 assert.equal(candidateName({kind:'upgrade',card:{name:'防御'}},s),'升级「防御」');
 assert.equal(candidateName({kind:'add',card:{name:'突破',upgrade_level:1}},s),'选择「突破+」');
});

test('live decisions foreground predictions and hide duplicate game resources and detailed metrics',()=>{
 const old=globalThis.document;globalThis.document={createElement:tag=>new Element(tag)};
 try{
  const host=new Element('div'),record={snapshot:{state:{player:{creature:{hp:73,max_hp:80},energy:3,gold:129}}},report:{combat:{candidates:[{title:'攻击并防御',metrics:{effective_hp_damage:16,projected_hp_loss:2}}]},mechanism_audit:{classification:'estimated',entities:[],enemies:[]}}};
  const before=JSON.stringify(record);renderDecision(host,record,'metrics',null,null,{live:true});
  assert.match(visible(host),/有效伤害/);assert.match(visible(host),/预计失血/);assert.doesNotMatch(visible(host),/当时生命|当时能量|当时金币/);
  assert.ok(host.querySelectorAll('details').every(d=>!d.open));assert.equal(JSON.stringify(record),before);
  renderDecision(host,record);assert.match(text(host),/当时生命/);
 }finally{globalThis.document=old}
});

test('polling preserves expanded calculation details',()=>{
 const old=globalThis.document;globalThis.document={createElement:tag=>new Element(tag)};
 try{
  const host=new Element('div'),record={report:{},snapshot:{state:{}}};
  renderDecision(host,record,'summary',null,null,{live:true});
  const details=host.querySelectorAll('details').find(d=>d.querySelector('summary')?.textContent==='计算细节与评分权重');details.open=true;
  renderDecision(host,record,'summary',null,null,{live:true});
  assert.equal(host.querySelectorAll('details').find(d=>d.querySelector('summary')?.textContent==='计算细节与评分权重').open,true);
 }finally{globalThis.document=old}
});

test('combat forecast separates scope, plans, enemies, rounds and branch metrics',()=>{
 const old=globalThis.document;globalThis.document={createElement:tag=>new Element(tag)};
 try{
  const row={instance_id:'e',name:'史莱姆',turn:1,move_title:'黏液',next_move_title:'冲撞',next_attacks:[9],probability:1,enemy_hp:20};
  const forecast={horizon:2,enemy_model:{mode:'local_behavior_graph'},candidates:[{group:'candidates',index:0,steps:[],scenarios:[{result:null,enemy_turns:[row,row,{...row,turn:2}]}]},{group:'candidates',index:1,steps:[],scenarios:[{result:{covered_probability:.25,conditional_metrics:{hp_loss:4,remaining_enemy_hp:10}},enemy_turns:[{...row,name:'其他敌人',instance_id:'f'}]}]}]};
  const panel=combatForecastPanel(forecast),plans=panel.children.filter(c=>c.className==='forecast-plan');
  assert.equal(plans.length,2);assert.equal(panel.children[1].className,'forecast-scope');
  assert.ok(panel.querySelectorAll('details').every(d=>!d.open));
  assert.match(text(plans[0]),/后续结果未知/);assert.match(text(plans[0]),/史莱姆/);assert.doesNotMatch(text(plans[0]),/其他敌人/);
  assert.match(text(plans[1]),/25.0%/);assert.match(text(plans[1]),/未覆盖分支/);
  const rounds=plans[0].querySelectorAll('section').filter(c=>c.className==='forecast-turn');
  assert.equal(rounds.length,2);assert.equal(rounds[0].children.filter(c=>c.className==='forecast-branch').length,1);
  assert.match(text(rounds[0]),/第 1 回合/);assert.match(text(rounds[1]),/第 2 回合/);
 }finally{globalThis.document=old}
});

test('live polling preserves opened plans and monsters without expanding other groups',()=>{
 const old=globalThis.document;globalThis.document={createElement:tag=>new Element(tag)};
 try{
  const host=new Element('div'),record={snapshot:{state:{}},report:{combat:{lookahead:{horizon:2,enemy_model:{mode:'local_behavior_graph'},candidates:[{group:'candidates',index:0,scenarios:[{enemy_turns:[{instance_id:'e',name:'怪物',turn:1}]}]}]}}}};
  renderDecision(host,record,'metrics',null,null,{live:true});
  for(const id of ['combat-forecast-panel','forecast-candidates-0','forecast-candidates-0-scenario-0-enemy-e'])host.querySelectorAll('details').find(d=>d.id===id).open=true;
  renderDecision(host,record,'metrics',null,null,{live:true});
  for(const id of ['combat-forecast-panel','forecast-candidates-0','forecast-candidates-0-scenario-0-enemy-e'])assert.equal(host.querySelectorAll('details').find(d=>d.id===id).open,true);
  assert.equal(host.querySelectorAll('details').find(d=>d.id==='combat-forecast-scope').open,false);
 }finally{globalThis.document=old}
});

test('local comparison explains the next action and complete proof without calling it a score margin',()=>{
 const old=globalThis.document;globalThis.document={createElement:tag=>new Element(tag)};
 try{
  const autonomy={eligible:true,rule_id:'dominant_outcome',selected_steps:[{action_id:'play_a',target_id:'e'}],evidence:{search:{complete:true,nodes:5},finish_count:3,equivalent_best_count:2,dominated_finish_count:1,hp_after_victory:30,selected_continuation:[{action_id:'play_a',target_id:'e'}],compared_finishes:[{steps:[{action_id:'play_a',target_id:'e'}],persistent_outcome:{hp:28},relation_to_selected:'dominates'}]}};
  const panel=autonomyPanel(autonomy,{state:{player:{hand:[{instance_id:'a',name:'打击'}]},enemies:[{instance_id:'e',name:'史莱姆'}]}});
  assert.match(text(panel),/未调用 AI/);assert.match(text(panel),/战后资源严格支配/);assert.match(text(panel),/打击 → 史莱姆/);assert.match(text(panel),/仅执行下一步/);
  assert.ok(panel.querySelectorAll('details').every(d=>!d.open));
 }finally{globalThis.document=old}
});

test('score margin explains why AI is still required when there is no error bound',()=>{
 const old=globalThis.document;globalThis.document={createElement:tag=>new Element(tag)};
 try{
  const panel=autonomyPanel({eligible:false,score_review:{leading_score:100,runner_up_score:1,score_gap:99,tied_leaders:1,reason:'分差尚无校准误差界，不能单独用于跳过 AI。'}});
  assert.match(text(panel),/收益分差/);assert.match(text(panel),/99/);assert.match(text(panel),/AI 综合判断/);assert.match(text(panel),/不能单独用于跳过/);
  const paused=autonomyPanel({eligible:false,mode:'wait',score_review:{score_gap:99,reason:'分差不是执行证明。'}});
  assert.match(text(paused),/当前暂停/);assert.doesNotMatch(text(paused),/AI 综合判断/);
 }finally{globalThis.document=old}
});
