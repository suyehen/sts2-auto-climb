import test from 'node:test';
import assert from 'node:assert/strict';
import {serviceView,serviceApi,mountServices} from '../public/services.js';

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

async function mountedScenario({failed=false,lostResponse=false}={},run){
 const saved=Object.fromEntries(['window','location','fetch','setInterval'].map(k=>[k,globalThis[k]]));
 const requests=[],nodes=[],reloads=[];let job=null;
 const el=(tag,text)=>{const node={tag,textContent:text,children:[],append(...children){this.children.push(...children)},setAttribute(){}};nodes.push(node);return node};
 globalThis.window={Observer:{el}};
 globalThis.location={reload:()=>reloads.push('reload')};
 globalThis.setInterval=()=>0;
 globalThis.fetch=async(url,options)=>{
  if(options.method==='POST'){
   const body=JSON.parse(options.body);requests.push(body);
   job={...body,status:failed?'failed':'completed',error:failed?'SERVICE_START_FAILED':null};
   if(lostResponse)throw Error('response lost');
   return {ok:true,json:async()=>({job:{...job,status:'running'}})};
  }
  return {ok:true,json:async()=>({ready:true,job,maintenance_active:false})};
 };
 try{
  const mounted=mountServices({host:el('host'),quickButtons:el('quickHost'),onBusy(){},onReady(){}});
  // Let the initial read-only status query finish before clicking.
  await new Promise(resolve=>setImmediate(resolve));
  await run({mounted,requests,reloads,nodes});
 }finally{for(const [key,value] of Object.entries(saved))if(value===undefined)delete globalThis[key];else globalThis[key]=value;}
}

test('quick restart replaces all backend services and refreshes the page once',async()=>{
 await mountedScenario({},async({mounted,requests,reloads,nodes})=>{
  await nodes.find(n=>n.tag==='button'&&n.textContent==='重启服务').onclick();
  assert.equal(requests.length,1);assert.equal(requests[0].scope,'all');
  assert.deepEqual(reloads,['reload']);
  await mounted.refresh();assert.equal(reloads.length,1);
 });
});
test('explicit Agent restart refreshes the page after completion',async()=>{
 await mountedScenario({},async({requests,reloads,nodes})=>{
  await nodes.find(n=>n.tag==='button'&&n.textContent==='重启 Agent 与执行服务').onclick();
  assert.equal(requests[0].scope,'agent');assert.equal(reloads.length,1);
 });
});
test('lost operation response is resolved by its same ID without duplicate restart',async()=>{
 await mountedScenario({lostResponse:true},async({requests,reloads,nodes})=>{
  await nodes.find(n=>n.tag==='button'&&n.textContent==='重启服务').onclick();
  assert.equal(requests.length,1);assert.equal(reloads.length,1);
 });
});
test('failed restart stays on the page and exposes the failure',async()=>{
 await mountedScenario({failed:true},async({reloads,nodes})=>{
  await nodes.find(n=>n.tag==='button'&&n.textContent==='重启服务').onclick();
  assert.equal(reloads.length,0);assert.ok(nodes.some(n=>/服务启动失败/.test(n.textContent)));
 });
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
