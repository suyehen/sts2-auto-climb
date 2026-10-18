import test from 'node:test';
import assert from 'node:assert/strict';
import {liveDecision,renderPlans,renderDecisionDetail} from '../public/sidebar-view.js';
import {decisionHistory} from '../public/history-model.js';

class Element{
 constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.attributes={}}
 append(...children){this.children.push(...children)}
 replaceChildren(...children){this.children=children}
 setAttribute(name,value){this.attributes[name]=value}
}
const text=n=>[n.textContent,...n.children.map(text)].join(' ');
function dom(fn){const previous=globalThis.document;globalThis.document={createElement:t=>new Element(t),createElementNS:(_,t)=>new Element(t)};try{return fn()}finally{globalThis.document=previous}}
const snapshot={state_version:3,state:{phase:'combat',floor:2,player:{turn:1,hand:[{instance_id:'draw',name:'耸肩无视',energy_cost:1,description:'获得8点格挡。抽1张牌。'},{instance_id:'forge',name:'铸剑',description:'铸造3。'}]},enemies:[{instance_id:'e',name:'史莱姆',hp:12,block:0,intents:[{type:'Attack',attack_damage:5,attack_repeats:1}]}]}};
const record={event_id:'analysis',kind:'decision',snapshot,report:{source:{state_version:3},combat:{candidates:[{steps:[{action_id:'play_draw'}],metrics:{heuristic_score:-2}},{steps:[{action_id:'play_forge'}],metrics:{heuristic_score:3}},{steps:[{action_id:'end_turn'}],metrics:{heuristic_score:-5}},{steps:[{action_id:'play_draw'},{action_id:'end_turn'}],metrics:{heuristic_score:-8}}]}}};
const data={connected:true,decision_id:'analysis',snapshot,decision:record.report,node_decisions:[{event_id:'analysis'}],archive:{last_choice:null}};

test('workbench live view uses full archived actions and sorted TOP3 with complete effects',()=>dom(()=>{
 const current=liveDecision(data,record,null),host=new Element('div');renderPlans(host,current.record,current.choice);
 assert.match(current.context,/楼层 2.*回合 1.*当前分析/);
 assert.match(text(host),/TOP 1.*3 分.*铸造3.*TOP 2.*-2 分.*抽1张牌.*TOP 3.*-5 分/);
 assert.doesNotMatch(text(host),/TOP 4|执行结果|候选方案与计算依据/);
}));

test('workbench AI view resolves group/index choice from evidence and shows only that plan',()=>dom(()=>{
 const current=liveDecision(data,record,{choice:{analysis_id:'analysis',group:'combat.candidates',index:0,reason:'补充手牌'}}),host=new Element('div');
 renderPlans(host,current.record,current.choice);
 assert.match(text(host),/AI 已选方案.*耸肩无视.*抽1张牌.*补充手牌/);
 assert.doesNotMatch(text(host),/TOP|铸剑|结束回合|执行结果/);
}));

test('changing map nodes never displays the previous node analysis or AI choice',()=>{
 const current=liveDecision({...data,node_decisions:[]},record,{choice:{steps:[{action_id:'end_turn'}]}});
 assert.deepEqual(current.record,{});assert.equal(current.choice,null);assert.match(current.context,/等待当前节点/);
});

test('archive navigation lists analyses only, including ones without choices, and respects node filters',()=>{
 const entries=[
  {event_id:'a',kind:'decision',recorded_at:'2026-10-15T00:00:01Z',location:{act_index:0,node_key:'1:2',phase:'combat',turn:1}},
  {event_id:'choice',kind:'local_choice',details:{analysis_id:'a'},recorded_at:'2026-10-15T00:00:02Z',location:{act_index:0,node_key:'1:2',phase:'combat',turn:1}},
  {event_id:'outcome',kind:'local_outcome',details:{analysis_id:'a'},recorded_at:'2026-10-15T00:00:03Z',location:{act_index:0,node_key:'1:2',phase:'combat',turn:1}},
  {event_id:'b',kind:'decision',recorded_at:'2026-10-15T00:00:04Z',location:{act_index:0,node_key:'1:2',phase:'combat',turn:1}},
  {event_id:'c',kind:'decision',recorded_at:'2026-10-15T00:00:05Z',location:{act_index:0,node_key:'2:3',phase:'event'}}
 ];
 assert.deepEqual(decisionHistory(entries,'1:2').flatMap(s=>s.records.map(r=>r.event.event_id)),['a','b']);
 assert.equal(decisionHistory(entries).flatMap(s=>s.records).length,3);
});

test('archive details use the saved snapshot state machine without execution or calculation sections',()=>dom(()=>{
 const historical=structuredClone(record);historical.monster_graphs=[{instance_id:'e',current:'hit',nodes:[{id:'hit',kind:'MoveState',title:'冲撞',effects:['攻击 5']}],edges:[{source:'hit',target:'hit',label:'行动结束'}],initial:[{target:'hit',label:'初始'}],next_moves:[{id:'hit',probability:1}]}];
 const host=new Element('div');renderDecisionDetail(host,historical,{group:'combat.candidates',index:1});
 assert.match(text(host),/AI 已选方案.*铸造3.*当时的怪物状态机.*下回合：冲撞（100%）/);
 assert.doesNotMatch(text(host),/执行结果|计算依据|推演窗口|执行证据/);
 assert.equal(host.children.at(-1).children[0].children.some(n=>n.tag==='svg'),true);
}));
