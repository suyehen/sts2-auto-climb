import test from 'node:test';
import assert from 'node:assert/strict';
import {controlView,decisionActivity,modelWaitDetail} from '../public/agent-state.js';
const status={running:false,phase:'paused',active:'codex',profiles:[{id:'codex',label:'本机 Codex',ready:true}]};
const combat={connected:true,phase:'combat'};
test('archive polling cannot replace an active model wait with an analysis stage',()=>{
 const pending={...status,running:true,phase:'waiting_model'};
 for(const fallback of ['分析已完成','AI 已作出选择','等待分析','分析已完成'])assert.equal(decisionActivity(pending,fallback),'等待 AI 决策');
 assert.equal(decisionActivity(status,'分析已完成'),'分析已完成');
 assert.match(decisionActivity({...pending,phase:'executing',last:{plan_step:2,plan_length:4}}),/第 2\/4 步/);
});
test('model wait duration belongs to the current request and disappears after pause',()=>{
 const pending={...status,running:true,phase:'waiting_model',model_wait:{request_id:'same',started_at:100,timeout_seconds:120}};
 assert.equal(modelWaitDetail(pending,115000),'本次已等待 15 秒，最长 120 秒');
 assert.match(controlView(pending,combat).detail,/最长 120 秒/);
 assert.equal(modelWaitDetail({...pending,running:false,phase:'paused'},115000),'');
 assert.equal(modelWaitDetail({...pending,phase:'executing'},115000),'');
 assert.match(modelWaitDetail({...pending,model_wait:{...pending.model_wait,attempt:2}},115000),/正在重试 · 第 2 次请求/);
});
test('an executing plan shows step progress rather than another AI wait',()=>{
 const v=controlView({...status,running:true,phase:'executing',last:{plan_step:2,plan_length:4}},combat);
 assert.match(v.detail,/执行动作计划 · 第 2\/4 步/);assert.doesNotMatch(v.detail,/等待 AI/);
});
test('waiting for a model remains visibly running and can be paused',()=>{
 const v=controlView({...status,running:true,phase:'waiting_model'},combat);
 assert.equal(v.label,'Agent 运行中');assert.match(v.detail,/等待 AI/);
 assert.equal(v.startDisabled,true);assert.match(v.startReason,/已启动/);assert.equal(v.pauseDisabled,false);
 assert.match(controlView({...status,running:true},combat).detail,/正在启动/);
});
test('stopped errors expose cause and allow retry in a current run',()=>{
 const v=controlView({...status,phase:'error',error:'MODEL_TIMEOUT'},combat);
 assert.equal(v.tone,'error');assert.match(v.detail,/超时/);assert.equal(v.startLabel,'重试启动 Agent');assert.equal(v.startDisabled,false);
});

test('model and execution errors expose specific causes and current failure stage',()=>{
 const v=controlView({...status,phase:'error',error:'MODEL_OUTPUT_LIMIT',error_details:{stage:'waiting_model'},last:{plan_step:2,plan_length:3}},combat);
 assert.match(v.detail,/等待 AI 决策/);assert.match(v.detail,/长度上限/);assert.match(v.detail,/MODEL_OUTPUT_LIMIT/);assert.doesNotMatch(v.detail,/第 2/);
 const action=controlView({...status,phase:'error',error:'ACTION_REJECTED',error_details:{stage:'executing',plan_step:2,plan_length:3}},combat);
 assert.match(action.detail,/第 2\/3 步/);assert.match(action.detail,/游戏拒绝/);
 assert.match(controlView({...status,phase:'error',error:'MODEL_HTTP_503'},combat).detail,/模型服务/);
 assert.match(controlView({...status,phase:'error',error:'NEW_ERROR'},combat).detail,/NEW_ERROR/);
});
test('manual pause does not present a previous timeout as a current failure',()=>{
 const v=controlView({...status,error:'MODEL_TIMEOUT'},{connected:true,phase:'main_menu'});
 assert.equal(v.label,'Agent 已暂停');assert.equal(v.tone,'paused');assert.match(v.detail,/上次运行/);
 assert.equal(v.startDisabled,false);assert.equal(v.openPlay,true);assert.match(v.startLabel,/开始游玩/);
 assert.equal(controlView({...status,error:'MODEL_TIMEOUT'},combat).startDisabled,false);
});
test('unknown status has a visible reason while menus route to the launch page',()=>{
 for(const game of [null,{connected:true}]){
  const v=controlView(status,game);assert.equal(v.startDisabled,true);assert.ok(v.startReason.length>0);
 }
 for(const game of [{connected:false},{connected:true,phase:'main_menu'},{connected:true,phase:'game_over'}]){
  const v=controlView(status,game);assert.equal(v.startDisabled,false);assert.equal(v.openPlay,true);
 }
 const missing=controlView({...status,profiles:[{id:'codex',ready:false}]},combat);assert.match(missing.startReason,/配置/);
});
test('launching is distinct from stopped and supports cancellation',()=>{
 const v=controlView({...status,launch_active:true},{connected:false});
 assert.equal(v.startDisabled,true);assert.equal(v.pauseDisabled,false);assert.match(v.label,/准备/);assert.match(v.detail,/自动启动/);
});
test('unknown service status never presents stale status as paused or running',()=>{
 const v=controlView({...status,running:true},combat,{available:false});
 assert.equal(v.label,'Agent 未连接');assert.equal(v.startDisabled,true);assert.equal(v.pauseDisabled,true);
});
test('pausing stops automation and temporary game settling does not block restart',()=>{
 const v=controlView(status,combat);assert.equal(v.label,'Agent 已暂停');assert.equal(v.startDisabled,false);assert.match(v.detail,/不会自动执行/);
 const busy=controlView(status,combat,{busy:true});assert.equal(busy.startDisabled,true);assert.equal(busy.pauseDisabled,true);
});
