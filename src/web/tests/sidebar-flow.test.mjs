import test from 'node:test';
import assert from 'node:assert/strict';
import {renderDecisionFlow,renderHistoryDecision,historyDecisionEvidence} from '../public/sidebar-combat.js';

class Element{
 constructor(tag){this.tag=tag;this.children=[];this.textContent=''}
 append(...nodes){this.children.push(...nodes)}
 replaceChildren(...nodes){this.children=nodes}
}
const text=node=>[node.textContent,...node.children.map(text)].join(' ');
const record={report:{autonomy:{eligible:true,reason:'唯一可执行动作',selected_steps:[{action_id:'end_turn'}]}},snapshot:{state:{}}};
function render(r=record,choice=null,evidence=null,status=null){
 const old=globalThis.document;globalThis.document={createElement:tag=>new Element(tag)};
 try{const host=new Element('div');renderDecisionFlow(host,r,choice,evidence,status);return host.children.map(text)}finally{globalThis.document=old}
}
test('autonomous choice, skipped AI and real outcome are shown in order',()=>{
 const view=render(record,{ai_called:false},{observations:[{status:'completed',actual:{hp:42,phase:'map'}}]});
 assert.match(view[0],/自主决策.*可自主执行.*结束回合/);
 assert.match(view[1],/AI 决策.*已跳过.*未调用 AI/);
 assert.match(view[2],/执行结果.*执行完成.*42.*地图/);
});
test('AI choice and reason remain separate from execution success',()=>{
 const r=structuredClone(record);r.report.autonomy.eligible=false;
 const view=render(r,{steps:[{action_id:'end_turn'}],reason:'保留资源'}, {observations:[],plans:[]});
 assert.match(view[0],/交由 AI/);assert.match(view[1],/已选择.*保留资源.*结束回合/);
 assert.match(view[2],/等待结果.*暂无与本次决策匹配/);assert.doesNotMatch(view[2],/执行完成/);
});
test('AI wait, failed action and missing evidence are explicit',()=>{
 const r=structuredClone(record);r.report.autonomy.eligible=false;
 assert.match(render(r,null,null,{phase:'waiting_model'})[1],/思考中/);
 assert.match(render(r,null,{observations:[{status:'failed'}]})[2],/执行失败/);
 assert.match(render(r)[2],/暂未确认结算/);
});
test('empty state has three stages and never claims autonomous execution',()=>{
 const view=render(null);assert.equal(view.length,3);assert.match(view[0],/等待分析/);assert.match(view[1],/等待选择/);assert.match(view[2],/等待结果/);
 assert.doesNotMatch(view.join(' '),/执行完成|已跳过/);
});

test('history keeps the shared sequence and folds evidence and calculations by default',()=>{
 const old=globalThis.document;globalThis.document={createElement:tag=>new Element(tag)};
 try{
  const host=new Element('div');
  const evidence={choice:{ai_called:false},observations:[{status:'completed',actual:{hp:42}}]};
  renderHistoryDecision(host,record,'summary',evidence);
  const flow=host.children[1];assert.equal(flow.children.length,3);
  assert.match(text(flow),/自主决策.*AI 决策.*执行结果.*执行完成/);
  assert.equal(host.children[2].open,false);assert.equal(host.children[3].open,false);
  renderHistoryDecision(host,record,'metrics',evidence);
  assert.equal(host.children[2].open,true);assert.equal(host.children[3].open,false);
  renderHistoryDecision(host,record,'evidence',evidence);
  assert.equal(host.children[2].open,false);assert.equal(host.children[3].open,true);
 }finally{globalThis.document=old}
});

test('old history with missing evidence does not pretend to wait for new execution',()=>{
 const old=globalThis.document;globalThis.document={createElement:tag=>new Element(tag)};
 try{
  const host=new Element('div');renderHistoryDecision(host,{report:{},snapshot:{}},'summary',{observations:[],plans:[]});
  const view=text(host.children[1]);assert.match(view,/未记录依据.*未记录选择.*未记录结果/);
  assert.doesNotMatch(view,/等待结果|正在读取本次结果|执行完成/);
 }finally{globalThis.document=old}
});

test('archive fallback links only matching choices and outcomes and avoids duplicates',()=>{
 const events=[{kind:'ai_choice',details:{analysis_id:'a',reason:'保留资源'}},{kind:'agent_outcome',details:{analysis_id:'a',status:'completed'}},{kind:'local_outcome',details:{analysis_id:'b',status:'failed'}}];
 const evidence=historyDecisionEvidence({event_id:'a'},events);
 assert.equal(evidence.choice.reason,'保留资源');assert.deepEqual(evidence.observations,[events[1].details]);
 assert.equal(historyDecisionEvidence({event_id:'a'},events,evidence).observations.length,1);
 assert.equal(historyDecisionEvidence({},events).observations.length,0);
});
