import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('../public/shared.js',import.meta.url),'utf8');
function api(data,ok=true){const context={window:{addEventListener(){}},fetch:async()=>({ok,status:ok?200:503,json:async()=>data})};vm.runInNewContext(source,context);return context.window.Observer.api;}
test('Agent domain errors remain readable status rather than fake connection failure',async()=>{
 const status={running:false,phase:'error',error:'MODEL_TIMEOUT',profiles:[]};
 for(const path of ['/api/agent/status','/api/agent/pause'])assert.equal((await api(status)(path)).error,'MODEL_TIMEOUT');
});
test('real HTTP and non-status application errors still reject',async()=>{
 const status={running:false,error:'MODEL_TIMEOUT',profiles:[]};
 await assert.rejects(api(status,false)('/api/agent/status'),/MODEL_TIMEOUT/);
 await assert.rejects(api({error:'GAME_NOT_CONNECTED'})('/api/agent/start',{}),/GAME_NOT_CONNECTED/);
 await assert.rejects(api({error:'INVALID_REQUEST'})('/api/config/import',{}),/INVALID_REQUEST/);
});

test('module load failures show a boot error even before sidebar code initializes',()=>{
 let handler;const notice={hidden:true,textContent:''},link={textContent:'正在连接',classList:{remove(){}}};
 const context={window:{addEventListener:(name,fn,capture)=>{assert.equal(name,'error');assert.equal(capture,true);handler=fn}},document:{querySelector:s=>s==='#notice'?notice:s==='#link'?link:null}};
 vm.runInNewContext(source,context);
 handler({target:{tagName:'SCRIPT',type:'module'}});
 assert.equal(notice.hidden,false);assert.match(notice.textContent,/脚本加载失败/);assert.match(notice.textContent,/重启观察台服务/);assert.equal(link.textContent,'界面加载失败');
});
