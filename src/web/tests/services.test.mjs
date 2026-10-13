import test from 'node:test';
import assert from 'node:assert/strict';
import {serviceView,serviceApi} from '../public/services.js';

test('service maintenance and unknown game actions have separate outcomes',()=>{
 assert.equal(serviceView(null).available,false);
 assert.equal(serviceView({job:null,maintenance_active:true}).busy,true);
 const settling=serviceView({job:{status:'running',phase:'settling'}});
 assert.equal(settling.busy,true);assert.match(settling.label,/等待游戏动作结算/);
 const failed=serviceView({job:{status:'failed',error:'PREVIOUS_ACTION_UNRESOLVED'}});
 assert.equal(failed.busy,false);assert.match(failed.detail,/没有继续重启/);
 assert.match(serviceView({job:{status:'completed',action:'restart',scope:'agent'}}).detail,/保持暂停/);
 assert.doesNotMatch(serviceView({job:{status:'completed',action:'start'}}).detail,/已重启|保持暂停/);
});

test('manager transport stays independent of dashboard and never exposes a token',async()=>{
 const old=globalThis.fetch,calls=[];
 globalThis.fetch=async(url,options)=>{calls.push([url,options]);return {ok:true,json:async()=>({ready:true})}};
 try{
  await serviceApi('/operate',{action:'restart',scope:'agent',request_id:'same'});
  assert.equal(calls[0][0],'http://127.0.0.1:18770/operate');
  assert.equal(calls[0][1].headers['X-Autoslay-ReadOnly'],'1');
  assert.equal(calls[0][1].credentials,'omit');
  assert.equal(calls[0][1].headers.Authorization,undefined);
 }finally{globalThis.fetch=old}
});
