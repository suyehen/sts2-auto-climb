import test from 'node:test';
import assert from 'node:assert/strict';
import {groupHistory} from '../public/history-model.js';
const event=(id,kind,turn,details={},extra={})=>({event_id:id,kind,recorded_at:'2026-10-12T10:00:'+id.padStart(2,'0')+'Z',details,location:{act_index:0,generation:1,node_key:'2:3',phase:'combat',turn},...extra});
test('one AI plan and all its action receipts stay in one decision under the source turn',()=>{
 const rows=[event('1','decision',1),event('2','agent_packet',1,{analysis_id:'1',request_id:'p'}),event('3','ai_choice',1,{analysis_id:'1',plan_request_id:'p'}),event('4','action_submitted',1,{}, {request:{request_id:'r'}}),event('5','agent_outcome',2,{analysis_id:'secondary',plan_request_id:'p',request:{request_id:'r'}}),event('6','snapshot',2)];
 const sections=groupHistory(rows);assert.equal(sections.length,2);assert.equal(sections[0].turn,1);assert.equal(sections[0].groups.length,1);assert.equal(sections[0].groups[0].events.length,5);assert.equal(sections[0].groups[0].ai,'used');assert.equal(sections[1].turn,2);
});
test('a reused cached analysis does not merge separate AI requests',()=>{
 const rows=[event('1','decision',1),event('2','agent_packet',1,{analysis_id:'1',request_id:'first'}),event('3','ai_choice',1,{analysis_id:'1'}),event('4','agent_packet',1,{analysis_id:'1',request_id:'second'}),event('5','ai_choice',1,{analysis_id:'1'})];
 const groups=groupHistory(rows)[0].groups;assert.equal(groups.length,2);assert.deepEqual(groups.map(g=>g.events.map(e=>e.event_id)),[['1','2','3'],['4','5']]);
});
test('local decisions and an awaiting AI request remain distinct',()=>{
 const rows=[event('1','decision',1),event('2','local_choice',1,{analysis_id:'1',ai_called:false}),event('3','local_outcome',2,{analysis_id:'1',request_id:'det'}),event('4','decision',1),event('5','agent_packet',1,{analysis_id:'4',request_id:'ai'})];
 const groups=groupHistory(rows)[0].groups;assert.equal(groups.length,2);assert.deepEqual(groups.map(g=>g.ai),['skipped','requested']);assert.equal(groups[0].events.length,3);
});
test('rounds, map nodes and rewards have separate sections; snapshots do not invent decisions',()=>{
 const rows=[event('1','snapshot',1),event('2','snapshot',2),event('3','snapshot',null,{}, {location:{act_index:0,node_key:'2:3',phase:'rewards'}}),event('4','decision',1,{}, {location:{act_index:0,node_key:'4:5',phase:'combat',turn:1}})];
 const sections=groupHistory(rows);assert.equal(sections.length,4);assert.deepEqual(sections.map(s=>s.kind),['turn','turn','between','turn']);assert.equal(sections[0].groups.length,0);assert.equal(sections[3].groups.length,1);
});
