import test from 'node:test';
import assert from 'node:assert/strict';
import {auditRows,auditTitle} from '../public/audit-view.js';
import {groupHistory} from '../public/history-model.js';

test('audit renders captured names alongside IDs and explains status',()=>{
 const event={details:{event:'action_submitted',event_label:'已提交游戏动作',status:'completed',
  action_id:'play_a',action_label:'打出「打击」',target_id:'enemy_1',target_label:'史莱姆',
  provider:'backend_1',provider_label:'本地模型',trace_id:'trace_1'}};
 const rows=new Map(auditRows(event));
 assert.equal(rows.get('执行动作'),'打出「打击」（play_a）');
 assert.equal(rows.get('动作目标'),'史莱姆（enemy_1）');
 assert.equal(rows.get('决策后端'),'本地模型（backend_1）');
 assert.match(rows.get('处理状态'),/处理已完成/);
 assert.match(auditTitle(event),/已提交游戏动作/);
});
test('missing names stay unknown and failed calls include an explanation',()=>{
 const rows=new Map(auditRows({details:{event:'unrecognized',status:'future',action_id:'custom',target_id:'unknown',code:'MODEL_TIMEOUT'}}));
 assert.match(rows.get('执行动作'),/未知动作.*custom/);
 assert.match(rows.get('动作目标'),/未知目标.*unknown/);
 assert.match(rows.get('异常说明'),/超时/);
 assert.match(rows.get('处理状态'),/未登记释义/);
});
test('execution traces join analysis, transport attempts and action receipts',()=>{
 const events=[
  {event_id:'start',kind:'agent_audit',details:{trace_id:'t'}},
  {event_id:'analysis',kind:'decision'},
  {event_id:'packet',kind:'agent_packet',details:{trace_id:'t',analysis_id:'analysis',request_id:'model'}},
  {event_id:'attempt',kind:'agent_audit',details:{trace_id:'t',plan_request_id:'model'}},
  {event_id:'dispatch',kind:'agent_audit',details:{trace_id:'t',request_id:'action'}},
  {event_id:'receipt',kind:'action_receipt',request:{request_id:'action'}},
  {event_id:'other',kind:'agent_audit',details:{trace_id:'other'}},
 ].map((e,i)=>({...e,recorded_at:'2026-10-14T07:00:00Z',sequence:i,location:{phase:'combat',turn:1}}));
 const sections=groupHistory(events);
 const groups=sections.flatMap(s=>s.groups);
 assert.equal(groups.length,2);
 assert.equal(groups.find(g=>g.events.some(e=>e.event_id==='analysis')).events.length,6);
});
test('cached analysis completion links to its explicit trace instead of the previous request',()=>{
 const events=[
  {event_id:'analysis',kind:'decision'},
  {event_id:'a1',kind:'agent_audit',details:{trace_id:'t1',analysis_id:'analysis'}},
  {event_id:'p1',kind:'agent_packet',details:{trace_id:'t1',analysis_id:'analysis',request_id:'model1'}},
  {event_id:'a2',kind:'agent_audit',details:{trace_id:'t2',analysis_id:'analysis'}},
  {event_id:'p2',kind:'agent_packet',details:{trace_id:'t2',analysis_id:'analysis',request_id:'model2'}},
 ].map((e,i)=>({...e,recorded_at:'2026-10-14T07:00:0'+i+'Z',location:{phase:'combat',turn:1}}));
 const groups=groupHistory(events).flatMap(s=>s.groups);
 assert.equal(groups.length,2);
 const second=groups.find(g=>g.events.some(e=>e.event_id==='p2'));
 assert.deepEqual(second.events.map(e=>e.event_id),['a2','p2']);
});
