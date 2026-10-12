import test from 'node:test';
import assert from 'node:assert/strict';
import {enemyIntentFacts,candidateIntentFacts} from '../public/combat-forecast.js';
import {renderSidebarCombat} from '../public/sidebar-combat.js';

class Element{
 constructor(tag){this.tag=tag;this.children=[];this.textContent='';}
 append(...children){this.children.push(...children)}
 replaceChildren(...children){this.children=children}
}
const text=node=>[node.textContent,...node.children.map(text)].join(' ');
const snapshot={state_version:7,state:{phase:'combat',player:{turn:2,hand:[{instance_id:'a',name:'重击'}]},enemies:[{instance_id:'e',name:'怪物',hp:20,block:0,intents:[{type:'Attack',attack_damage:4,attack_repeats:2}]}]}};
const row={instance_id:'e',turn:2,enemy_hp:12,move:'attack',move_title:'攻击',next_move:'buff',next_move_title:'强化',next_attacks:[],probability:.5};
const candidate={group:'candidates',index:0,steps:[{action_id:'play_a',target_id:'e'}],scenarios:[{enemy_turns:[row],result:{covered_probability:.25,conditional_metrics:{hp_loss:3,remaining_enemy_hp:12}}}]};
const record={snapshot,report:{source:{state_version:7},combat:{candidates:[{metrics:{projected_hp_loss:1,effective_block:7}}],lookahead:{horizon:2,enemy_model:{mode:'local_behavior_graph'},candidates:[candidate],unmodeled:['unknown']}}}};
function render(value=record,current=snapshot){
 const old=globalThis.document;globalThis.document={createElement:tag=>new Element(tag)};
 try{const intents=new Element('div'),calculation=new Element('div');renderSidebarCombat(intents,calculation,value,current);return {intents:text(intents),calculation:text(calculation)}}finally{globalThis.document=old}
}
test('current intent projection keeps multi-hit damage and unknown repeat count distinct',()=>{
 assert.match(enemyIntentFacts(snapshot)[0].intents[0],/4 × 2 = 8/);
 const partial=structuredClone(snapshot);delete partial.state.enemies[0].intents[0].attack_repeats;
 assert.match(enemyIntentFacts(partial)[0].intents[0],/未知/);
 assert.doesNotMatch(enemyIntentFacts(partial)[0].intents[0],/= 0/);
});
test('next moves remain attached to their post-play candidate and first enemy turn',()=>{
 const c=structuredClone(candidate);c.scenarios[0].enemy_turns.push(row,{...row,turn:3,next_move_title:'不应出现'});
 const facts=candidateIntentFacts(c,snapshot);
 assert.equal(facts.length,1);assert.match(facts[0],/出牌后生命 12/);assert.match(facts[0],/攻击 → 强化/);assert.match(facts[0],/该状态下转移概率 50%/);
});
test('sidebar links action, current metrics and conditional window without claiming safety',()=>{
 const before=JSON.stringify(record),view=render();
 assert.match(view.intents,/战斗方案 1 · 重击 → 怪物/);assert.match(view.intents,/攻击 → 强化/);
 assert.match(view.calculation,/本回合计算/);assert.match(view.calculation,/25.0%/);assert.match(view.calculation,/已计算分支/);assert.match(view.calculation,/部分分支未覆盖/);
 assert.equal(JSON.stringify(record),before);
});
test('new game state never reuses old next moves or computed losses',()=>{
 const view=render(record,{...snapshot,state_version:8});
 assert.match(view.intents,/当前可见/);assert.match(view.calculation,/等待当前状态/);
 assert.doesNotMatch(view.intents,/攻击 → 强化/);assert.doesNotMatch(view.calculation,/25.0%/);
 assert.match(render(record,{...snapshot,session_epoch:'new-session'}).calculation,/等待当前状态/);
 assert.match(render(record,{...snapshot,state:{...snapshot.state,run_id:'new-run'}}).calculation,/等待当前状态/);
});
test('missing forecasts, results, trace and noncombat states are explicit',()=>{
 assert.match(render({report:{source:{state_version:7}}}).calculation,/失血未知/);
 const missing=structuredClone(record);missing.report.combat.lookahead.candidates[0].scenarios=[{result:null}];
 const view=render(missing);assert.match(view.calculation,/后续结果未知/);assert.match(view.intents,/不能据此按零伤害/);
 assert.match(render(record,{state:{phase:'map'}}).intents,/不在战斗/);
});
test('explicit attack scenarios are not presented as real next moves',()=>{
 const explicit=structuredClone(record);explicit.report.combat.lookahead.enemy_model.mode='explicit_attack_scenario';
 const view=render(explicit);assert.match(view.intents,/不能当作怪物真实/);assert.doesNotMatch(view.intents,/攻击 → 强化/);
});
