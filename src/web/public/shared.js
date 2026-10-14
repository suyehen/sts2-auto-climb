/* Shared loopback transport. Agent mutations are exposed only by explicit user controls. */
// Module import failures happen before sidebar/workspace error handlers can start.
window.addEventListener('error',event=>{
 const script=event.target;
 if(script?.tagName!=='SCRIPT'||script.type!=='module')return;
 const notice=document.querySelector('#notice')||document.querySelector('#status');
 if(notice){notice.hidden=false;notice.textContent='观察界面脚本加载失败。请重新打开观察窗口；若仍失败，请重启观察台服务。'}
 const link=document.querySelector('#link');if(link){link.textContent='界面加载失败';link.classList.remove('connected')}
},true);
window.Observer=(()=>{
 const phase={combat:'战斗',event:'事件',shop:'商店',rest_site:'休息处',treasure:'宝箱',map:'地图',card_reward_selection:'抓牌',card_selection:'选牌',main_menu:'主菜单',game_over:'终局',no_run:'未开始',reward:'奖励',rewards:'奖励'};
 const stage={analysis_started:'正在计算',decision:'决策分析 / 确定性检查完成',ai_choice:'已登记 AI 选择',local_choice:'本地选择 · 未调用 AI',local_outcome:'本地动作结果已记录',action_submitted:'动作已提交',action_receipt:'等待游戏结算',action_result:'收到执行结果',outcome_observed:'结果评估已记录',snapshot:'状态更新',heartbeat:'状态采样'};
 const metric={projected_hp_loss:'预计总失血',effective_hp_damage:'有效生命伤害',effective_block:'有效格挡',heuristic_score:'收益分',remaining_energy:'剩余能量',remaining_stars:'剩余星星',fatal_probability:'致死概率',covered_probability:'模拟覆盖概率',delta_utility:'边际收益',utility:'收益分',spent:'金币支出',projected_death:'预测死亡',all_enemies_killed:'全部击杀',prevented_attack_damage:'避免攻击伤害',self_hp_loss:'出牌自损',incoming_after_kills:'击杀后攻击',uncertainty:'未知项',confidence:'计算范围',expected_utility:'概率加权收益'};
 const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n};
 const fmt=v=>v===null||v===undefined?'未知':typeof v==='boolean'?(v?'是':'否'):typeof v==='number'?(Number.isInteger(v)?String(v):v.toFixed(2)):typeof v==='object'?JSON.stringify(v):String(v);
 const time=v=>v?new Date(v).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'未记录';
 const duration=v=>typeof v==='number'?`${Math.floor(v/3600)}:${String(Math.floor(v%3600/60)).padStart(2,'0')}:${String(Math.floor(v%60)).padStart(2,'0')}`:'未知';
 async function api(path,body,raw=false){const r=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'X-Autoslay-ReadOnly':'1',...(raw?{'Content-Type':'application/octet-stream'}:{'Content-Type':'application/json'})},body:body===undefined?undefined:raw?body:JSON.stringify(body)});const data=await r.json();const agentStatus=path.startsWith('/api/agent/')&&typeof data.running==='boolean'&&Array.isArray(data.profiles);if(!r.ok||(data.error&&!agentStatus))throw Error(data.error||`HTTP ${r.status}`);return data}
 function detail(title,value,id){const d=el('details');if(id)d.dataset.detail=id;d.append(el('summary',title),el('pre',JSON.stringify(value,null,2)));return d}
 async function connect(change,status){let cursor=(await api('/api/archive/cursor')).cursor;await change();
  while(true){try{const response=await fetch(`/api/stream?after=${cursor}`,{headers:{'X-Autoslay-ReadOnly':'1'}});if(!response.ok)throw Error(`HTTP ${response.status}`);status?.(true);const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
    while(true){const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});let cut;while((cut=buffer.indexOf('\n\n'))>=0){const part=buffer.slice(0,cut);buffer=buffer.slice(cut+2);const line=part.split('\n').find(l=>l.startsWith('data: '));if(line){const data=JSON.parse(line.slice(6));await change(data.events);cursor=data.cursor}}}
   }catch(e){status?.(false,e);await new Promise(resolve=>setTimeout(resolve,2000));try{await change()}catch{}}
  }
 }
 function openFull(archive,target={}){const q=new URLSearchParams();if(archive)q.set('archive',archive);for(const[k,v]of Object.entries(target))if(v!=null)q.set(k,v);const url='/'+(q.size?'?'+q:'');if(window.chrome?.webview)window.chrome.webview.postMessage({kind:'open_full',archive_id:archive||null,...target});else window.open(url,'_blank','noopener')}
 Object.assign(metric,{base_value:'基础价值',synergy_correction:'协同修正',environment_correction:'环境修正',constraint_correction:'约束修正',cost_penalty:'成本负担'});
 return {api,connect,el,fmt,time,duration,detail,openFull,phase:p=>phase[p]||p||'未知阶段',stage:s=>stage[s]||s||'等待记录',metric};
})();
