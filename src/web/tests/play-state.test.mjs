import test from 'node:test';
import assert from 'node:assert/strict';
import {playView} from '../public/play-state.js';
const agent={active:'codex',running:false,profiles:[{id:'codex',ready:true}]};
const ready={agent,game:{connected:true,phase:'main_menu',menu:{}}};
test('new run is available from the main menu or while the game is offline',()=>{
 assert.equal(playView(ready).newReason,'');
 assert.equal(playView({...ready,game:{connected:false}}).newReason,'');
 assert.match(playView(null).newReason,/连接/);
});
test('saved run enables continue and requires explicit replacement for a new run',()=>{
 const saved={...ready,game:{...ready.game,menu:{has_saved_run:true,save_loadable:true}}};
 assert.match(playView(saved).newReason,/勾选/);assert.equal(playView(saved).continueReason,'');
 assert.equal(playView(saved,{replaceSave:true}).newReason,'');
 assert.match(playView(ready).continueReason,/没有/);
});
test('current run can be taken over without creating a new save',()=>{
 const v=playView({...ready,game:{connected:true,phase:'combat',run_id:'run'}});
 assert.equal(v.current,true);assert.match(v.continueLabel,/接管/);assert.equal(v.continueReason,'');assert.match(v.newReason,/保存/);
});
test('startup, automation, backend configuration and pending requests disable duplicate starts',()=>{
 for(const data of [{...ready,agent:{...agent,running:true}},{...ready,agent:{...agent,launch_active:true}}]){
  const v=playView(data);assert.ok(v.newReason);assert.ok(v.continueReason);assert.equal(v.active,true);
 }
 assert.match(playView(ready,{profileId:'missing'}).newReason,/配置/);
 assert.match(playView(ready,{busy:true}).newReason,/提交/);
});
test('startup errors and phases are readable without JSON',()=>{
 const v=playView({...ready,job:{phase:'failed',error:'MENU_ACTION_UNRESOLVED'}});
 assert.equal(v.progress,'启动失败');assert.match(v.error,/不会重复/);
 assert.equal(playView({...ready,job:{phase:'setting_up_run'}}).progress,'选择职业与进阶');
});
