/* Lifecycle controls talk directly to the independent manager, even during dashboard restart. */
const phases={checking:'检查服务进程',pausing:'暂停 Agent',settling:'等待游戏动作结算',stopping:'停止后台服务',starting:'启动后台服务',completed:'操作完成',interrupted:'管理操作被中断'};
const names={agent:'Agent',execution:'游戏执行服务',dashboard:'观察台'};
const errors={
 PROCESS_IDENTITY_UNVERIFIED:'无法确认后台进程身份，未终止该进程。请检查启动日志。',
 SERVICE_PORT_OCCUPIED:'服务端口被其他进程占用，无法启动。',
 SERVICE_NOT_READY:'服务进程仍在启动或无响应，请稍后重试重启。',
 SERVICE_OPERATION_BUSY:'已有服务管理操作正在进行，请等待完成。',
 PREVIOUS_ACTION_UNRESOLVED:'游戏动作结果尚未确认，已暂停 Agent；本次没有继续重启。等待游戏结算后再试。',
 EXECUTOR_BUSY:'执行器仍在处理动作，本次没有继续重启。请等待动作完成。',
 GAME_PAUSE_UNCONFIRMED:'无法确认游戏已停止自动操作，本次没有继续重启。',
 GAME_SESSION_CHANGED:'游戏会话在核对期间变化，本次重启已停止。请检查游戏状态。',
 SERVICE_START_FAILED:'服务启动失败，请查看对应启动日志。',
 SERVICE_START_TIMEOUT:'服务未在规定时间内就绪，请查看对应启动日志。',
 SERVICE_RESTART_UNCONFIRMED:'未能确认服务进程已被替换，重启未完成，请检查服务管理记录。',
 SERVICE_STOP_FAILED:'服务未能停止，请查看启动日志。',
 SERVICE_STOP_TIMEOUT:'服务退出超时，请稍后检查状态。',
 MANAGER_INTERRUPTED:'管理进程曾被中断；不会自动重复上次操作，请检查当前服务状态。',
 AGENT_NOT_PAUSED:'新启动的 Agent 未保持暂停，请检查服务状态。',
 SERVICE_MANAGER_ERROR:'服务管理操作失败，请查看本机管理日志。'
};
export function serviceView(data){
 if(!data)return {busy:false,available:false,label:'服务管理未连接',detail:'首次启用请运行启动脚本；此后可在这里重启服务。'};
 const job=data.job,busy=job?.status==='running'||data.maintenance_active===true;
 const service=job?.service?' · '+(names[job.service]||job.service):'';
 const failed=['failed','interrupted'].includes(job?.status);
 return {busy,available:true,label:busy?(phases[job?.phase]||'正在维护服务')+service:failed?'服务操作未完成':'后台服务管理',
  detail:busy?'正在核对和维护服务，请等待完成。':failed?(errors[job.error]||'服务操作失败（'+job.error+'）。')+(service?' '+service:''):
   job?.status==='completed'?(job.action==='stop'?'所选服务已停止。':job.action==='restart'?'服务已重启，Agent 保持暂停。点击启动 Agent 才会继续游玩。':'后台服务已就绪。'):
   '重启后 Agent 保持暂停；需要你手动继续游玩。'};
}
export async function serviceApi(path,body){
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);
 try{
  const response=await fetch('http://127.0.0.1:18770'+path,{method:body===undefined?'GET':'POST',credentials:'omit',signal:controller.signal,
   headers:{'X-Autoslay-ReadOnly':'1',...(body===undefined?{}:{'Content-Type':'application/json'})},body:body===undefined?undefined:JSON.stringify(body)});
  const data=await response.json();
  if(!response.ok||data.error)throw Error(data.error||'SERVICE_MANAGER_ERROR');
  return data;
 }finally{clearTimeout(timer)}
}
export function mountServices({host,quickButtons,onBusy,onReady}){
 const E=window.Observer.el,panel=E('details',undefined,'service-controls'),summary=E('summary','后台服务管理'),message=E('p'),buttons=E('div',undefined,'button-row');
 const restart=E('button','重启 Agent 与执行服务'),all=E('button','重启全部后台服务'),quick=E('button','重启服务');
 message.setAttribute('role','status');quick.title='重启全部后台服务，就绪后刷新页面；Agent 保持暂停';
 buttons.append(restart,all);panel.append(summary,message,buttons);host.append(panel);quickButtons?.append(quick);
 let data=null,requestId=null,posting=false,refreshing=false,startedHere=false,reported=null,localError='';
 function render(){
  const v=serviceView(data),busy=posting||v.busy;
  summary.textContent=v.label;message.textContent=localError||v.detail;quick.title=localError||v.detail;
  for(const button of [restart,all,quick])button.disabled=busy||!v.available;
  restart.textContent=busy?'正在维护…':'重启 Agent 与执行服务';quick.textContent=busy?'维护中…':'重启服务';
  onBusy(busy);
 }
 async function refresh(){
  if(refreshing)return;refreshing=true;
  try{
   data=await serviceApi('/status'+(requestId?'?request_id='+encodeURIComponent(requestId):''));
   if(requestId&&!data.job)localError='没有找到这次重启的管理记录；没有自动重试，请检查服务状态后再操作。';
   render();
   const job=data.job;
   if(job&&job.status==='completed'&&job.request_id!==reported){
    reported=job.request_id;onReady();
    if(startedHere&&job.request_id===requestId&&job.action==='restart')location.reload();
   }
  }catch{
   data=null;render();
   if(requestId){message.textContent='管理连接暂时中断，操作结果尚未确认。恢复连接后会继续核对，不会重复提交。';onBusy(true);for(const button of [restart,all,quick])button.disabled=true;}
  }finally{refreshing=false}
 }
 async function operate(scope){
  if(posting||serviceView(data).busy||!data)return;
  requestId=crypto.randomUUID();startedHere=true;posting=true;localError='';render();message.textContent='正在提交重启请求…';
  try{
   const result=await serviceApi('/operate',{action:'restart',scope,request_id:requestId});data={...data,job:result.job};
  }catch(e){
   // If the response was lost, query this same ID rather than submitting again.
   if(errors[e.message]){localError=errors[e.message];requestId=null;startedHere=false}
   message.textContent=localError||'请求结果尚未确认，正在核对管理记录。';
  }finally{posting=false;await refresh()}
 }
 restart.onclick=()=>operate('agent');all.onclick=quick.onclick=()=>operate('all');
 render();refresh();setInterval(()=>{if(!posting)refresh()},1500);
 return {refresh};
}
