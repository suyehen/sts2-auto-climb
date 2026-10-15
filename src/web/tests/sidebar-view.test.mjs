import test from 'node:test';
import assert from 'node:assert/strict';
import {decisionPlans,renderPlans,renderIntentMachine,renderNodeHistory,actionDetails} from '../public/sidebar-view.js';
class Element{
 constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.dataset={};this.listeners={};this.replacements=0}
 append(...children){for(const child of children){child.remove();child.parent=this;this.children.push(child)}}
 remove(){if(this.parent){const i=this.parent.children.indexOf(this);if(i>=0)this.parent.children.splice(i,1);this.parent=null}}
 insertBefore(child,next){child.remove();child.parent=this;const index=next?this.children.indexOf(next):this.children.length;this.children.splice(index,0,child)}
 replaceChildren(...children){this.replacements++;for(const child of this.children)child.parent=null;this.children=[];this.append(...children)}
 addEventListener(name,fn){this.listeners[name]=fn}
 setAttribute(name,value){(this.attributes??={})[name]=value}
 querySelectorAll(){return this.children.filter(c=>c.tag==='details'&&c.open)}
}
const text=n=>[n.textContent,...n.children.map(text)].join(' ');
const snap={state_version:1,state:{phase:'combat',player:{turn:1,hand:[{instance_id:'a',name:'打击'}]},enemies:[{instance_id:'e',name:'绿色史莱姆',hp:20,block:0,intents:[{type:'Attack',attack_damage:6,attack_repeats:1}]}]}};
const step={action_id:'play_a',target_id:'e'};
const record={snapshot:snap,report:{source:{state_version:1},combat:{candidates:[{steps:[step],metrics:{heuristic_score:2}},{steps:[{action_id:'end_turn'}],metrics:{heuristic_score:10}}],observation_boundaries:[{steps:[{action_id:'draw'}],metrics:{heuristic_score:7}}]}}};
function dom(fn){const previous=globalThis.document;globalThis.document={createElement:t=>new Element(t),createElementNS:(ns,t)=>new Element(t)};try{return fn()}finally{globalThis.document=previous}}
test('TOP3 merges comparable scores, sorts descending and never mutates report',()=>{
 const before=JSON.stringify(record),plans=decisionPlans(record);
 assert.deepEqual(plans.map(p=>p.metrics.heuristic_score),[10,7,2]);assert.equal(JSON.stringify(record),before);
 const batches=structuredClone(record);batches.report.combat.batches={top_batches:[{steps:[step],score:3}]};
 assert.equal(decisionPlans(batches).length,1);
});

test('every card effect is visible, including draw, forge, resources and conditional text',()=>dom(()=>{
 const r=structuredClone(record);r.snapshot.state.player.hand[0].description='获得[gold]8[/gold]点格挡。\n抽[gold]1[/gold]张牌。\n铸造3。如果上回合使用过技能，获得2点星星。消耗。';
 Object.assign(r.snapshot.state.player.hand[0],{energy_cost:1,star_cost:2,keywords:['Exhaust','Retain']});
 r.report.combat.candidates=[{steps:[step],boundary:'draw_requires_observation'}];r.report.combat.observation_boundaries=[];
 const host=new Element('div');renderPlans(host,r);
 assert.match(text(host),/获得8点格挡。\n抽1张牌。\n铸造3。如果上回合使用过技能，获得2点星星。消耗。/);
 assert.match(text(host),/抽牌结算后重新决策/);assert.doesNotMatch(text(host),/\[gold\]/);
 assert.match(text(host),/费用：1 能量 \+ 2 星星.*关键词：消耗 · 保留/);
}));

test('potions show use versus discard, targets, full effects and keyword tooltips',()=>dom(()=>{
 const r=structuredClone(record);r.snapshot.state.player.potions=[{instance_id:'p',name:'力量药水',description:'获得2点力量。',hover_tips:[{title:'力量',description:'提升攻击伤害。'}]}];
 r.report.combat.candidates=[{steps:[{action_id:'use_potion_p',target_id:'e'}]}];r.report.combat.observation_boundaries=[];
 const host=new Element('div');renderPlans(host,r);
 assert.match(text(host),/使用「力量药水」 → 绿色史莱姆.*获得2点力量。.*力量：提升攻击伤害。/);
 const discard=actionDetails({action_id:'discard_potion_p'},r.snapshot);
 assert.match(discard.title,/丢弃/);assert.deepEqual(discard.effects,['丢弃该药水，不触发使用效果。']);
}));

test('missing effect evidence remains explicit and option descriptions are retained',()=>{
 assert.deepEqual(actionDetails(step,snap).effects,['效果说明未记录']);
 const s={state:{event_screen:{options:[{action_id:'option',title:'铸剑',description:'铸造5。失去3点生命。'}]}}};
 assert.deepEqual(actionDetails({action_id:'option'},s),{title:'铸剑',effects:['铸造5。失去3点生命。']});
});

test('unchanged visible plans and intents keep DOM through repeated polling',()=>dom(()=>{
 const plans=new Element('div'),intents=new Element('div');renderPlans(plans,record);renderIntentMachine(intents,record,snap);
 const planChild=plans.children[0],intentChild=intents.children[0];
 for(let i=0;i<5;i++){renderPlans(plans,structuredClone(record));renderIntentMachine(intents,record,{...snap,captured_at:String(i)})}
 assert.equal(plans.replacements,1);assert.equal(intents.replacements,1);
 assert.equal(plans.children[0],planChild);assert.equal(intents.children[0],intentChild);
}));

test('history polling keeps expanded content and refreshes choices without a loading flash',async()=>{
 const previous=globalThis.document;globalThis.document={createElement:t=>new Element(t)};
 try{
  const host=new Element('div'),entries=[{event_id:'a',recorded_at:'2026-10-15T00:00:00Z',location:{phase:'combat'}}];let calls=0,finish;
  const load=async()=>{calls++;return {record}};
  renderNodeHistory(host,entries,load);const item=host.children[0];item.open=true;await item.listeners.toggle();
  const body=item.children[1],content=body.children[0],before=text(body);
  for(let i=0;i<5;i++)renderNodeHistory(host,entries,load);
  assert.equal(calls,1);assert.equal(host.children[0],item);assert.equal(item.open,true);assert.equal(body.children[0],content);
  renderNodeHistory(host,entries,()=>new Promise(resolve=>{finish=resolve}),{revision:'new-choice'});
  assert.equal(text(body),before);assert.doesNotMatch(text(body),/读取方案/);
  finish({record,choice:{group:'combat.candidates',index:0}});await new Promise(setImmediate);
  assert.match(text(body),/AI 已选方案/);assert.equal(item.open,true);
  renderNodeHistory(host,[...entries,{...entries[0],event_id:'b'}],load,{revision:'new-choice'});
  assert.equal(host.children[1],item);assert.equal(item.open,true);
  renderNodeHistory(host,[],load);assert.match(text(host),/暂无决策记录/);
 }finally{globalThis.document=previous}
});
test('AI group/index resolves the selected plan and hides every other candidate',()=>dom(()=>{
 const host=new Element('div');renderPlans(host,record,{group:'combat.candidates',index:0,reason:'优先攻击'});
 assert.match(text(host),/AI 已选方案.*2 分.*打击.*绿色史莱姆/);assert.doesNotMatch(text(host),/TOP|结束回合|执行结果/);
}));
test('per-step modeled damage and block appear as compact operations',()=>dom(()=>{
 const r=structuredClone(record);r.report.combat.candidates=[{steps:[step],state_trace:[{before:{block:0,enemies:[{instance_id:'e',hp:20,block:0}]},after:{block:0,enemies:[{instance_id:'e',hp:14,block:0}]}}]}];r.report.combat.observation_boundaries=[];
 const host=new Element('div');renderPlans(host,r);assert.match(text(host),/打击\(6\) → 绿色史莱姆/);
 const batch=structuredClone(record);batch.report.combat.batches={top_batches:[{steps:[step],score:3,state_trace:[{block:0,enemies:[{instance_id:'e',hp:20,block:0}]},{block:0,enemies:[{instance_id:'e',hp:14,block:0}]}]}]};
 renderPlans(host,batch);assert.match(text(host),/打击\(6\) → 绿色史莱姆/);
}));
test('full SVG state machine shows branches and return loops independently of stale decision forecasts',()=>dom(()=>{
 const machine={instance_id:'e',current:'attack',nodes:[{id:'attack',kind:'MoveState',title:'攻击',effects:['攻击 6']},{id:'buff',kind:'MoveState',title:'强化',effects:['力量 2']},{id:'choose',kind:'RandomBranchState',title:'随机选择'}],edges:[{source:'attack',target:'choose',label:'行动结束'},{source:'buff',target:'choose',label:'行动结束'},{source:'choose',target:'attack',label:'权重 1；最多连续 2 次'},{source:'choose',target:'buff',label:'权重 1'}],initial:[{target:'attack',label:'初始'}],next_moves:[{id:'attack',probability:.5},{id:'buff',probability:.5}]};
 const host=new Element('div');renderIntentMachine(host,record,snap,[machine]);
 assert.match(text(host),/下回合：攻击（50%） \/ 强化（50%）/);assert.match(text(host),/当前状态/);assert.match(text(host),/最多连续 2 次/);
 const graph=host.children[0].children.find(n=>n.tag==='svg');assert.ok(graph);assert.equal(graph.attributes.role,'img');
 assert.equal(graph.children.filter(n=>n.attributes?.class?.includes('machine-node')).length,3);
 const edges=graph.children.find(n=>n.attributes?.class==='machine-edges');assert.equal(edges.children.filter(n=>n.tag==='g').length,4);
 const replacements=host.replacements;renderIntentMachine(host,record,{...snap,state_version:99},[machine]);
 assert.equal(host.replacements,replacements);assert.doesNotMatch(text(host),/等待当前状态的行为模型/);
}));
test('node history expands details in place with no workbench navigation',async()=>{
 const previous=globalThis.document;globalThis.document={createElement:t=>new Element(t)};
 try{const host=new Element('div');let loaded=null;
 renderNodeHistory(host,[{event_id:'a',recorded_at:'2026-10-15T00:00:00Z',location:{phase:'combat',turn:1}}],async id=>{loaded=id;return {record}});
 const item=host.children[0];item.open=true;await item.listeners.toggle();assert.equal(loaded,'a');assert.match(text(item),/TOP 1.*10 分/);
 }finally{globalThis.document=previous}
});
