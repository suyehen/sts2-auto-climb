import {decisionActivity} from './agent-state.js';
import {renderPlans,renderIntentMachine,renderNodeHistory} from './sidebar-view.js';
import {character,phase,stage,stat,renderDecisionStats} from './presentation.js';
const O=window.Observer,$=s=>document.querySelector(s);let data=null,pending=null,busy=false,record=null,evidence=null,agentStatus=null,lastRenderKey=null;
const sectionKeys=new Map();
function updateSection(name,value,draw){const key=JSON.stringify(value);if(sectionKeys.get(name)===key)return;sectionKeys.set(name,key);draw()}
async function loadHistory(id){const [record,evidence]=await Promise.all([O.api('/api/event?id='+encodeURIComponent(id)),O.api('/api/evidence?id='+encodeURIComponent(id))]);return {record,choice:evidence.choice}}
function renderCurrent(){const text=decisionActivity(agentStatus,data?.latest?.kind?stage(data.latest.kind):'等待分析');if($('#current').textContent!==text)$('#current').textContent=text}
function render(){if(!data)return;renderCurrent();const key=JSON.stringify([data.connected,data.snapshot?.session_epoch,data.snapshot?.state_version,data.snapshot?.state,data.monster_graphs,data.decision_id,record?.event_id,data.archive?.last_choice,data.archive?.counts,data.latest?.event_id,data.recent?.map(e=>e.event_id),data.node_decisions?.map(e=>e.event_id),evidence,agentStatus?.phase,data.pending,$('#followSide').checked]);if(key===lastRenderKey)return;lastRenderKey=key;const s=data.snapshot?.state||{},p=s.player||{},c=p.creature||{},a=data.archive;
 $('#link').textContent=data.connected?'游戏已连接':'游戏未连接';$('#link').classList.toggle('connected',!!data.connected);$('#location').textContent=character(p.character)+' · '+phase(s.phase);$('#stamp').textContent=(s.map?.act_index!=null?'第 '+(s.map.act_index+1)+' 幕 · ':'')+'楼层 '+(s.floor??'未记录');
 $('#notice').textContent=!$('#followSide').checked?'画面已冻结，返回实时可继续跟随。':!data.connected&&data.decision?'显示最近一次决策；游戏连接后会继续更新。':data.pending?'部分记录正在等待归档。':'';
 $('#notice').hidden=!$('#notice').textContent;$('#resume').hidden=$('#followSide').checked;
 const resources=[['生命',c.hp!=null?c.hp+' / '+c.max_hp:'未记录'],['能量',p.energy],['金币',p.gold],['格挡',c.block],['手牌',p.hand?.length],['进阶',s.ascension]];
 updateSection('resources',resources,()=>$('#resources').replaceChildren(...resources.map(([k,v])=>stat(k,v))));
 updateSection('stats',[a?.counts?.local_choice,a?.counts?.ai_choice],()=>renderDecisionStats($('#side-decision-stats'),a));
 const currentNode=!(data.node_decisions?.length>=0)||data.node_decisions.some(e=>e.event_id===data.decision_id);
 const r=!currentNode?{}:data.decision_id&&record?.event_id===data.decision_id?record:{report:data.decision,snapshot:data.decision_snapshot||data.snapshot};const choice=a?.last_choice?.analysis_id===data.decision_id?a.last_choice:null;
 $('#decision-context').textContent=r.report?'楼层 '+(r.snapshot?.state?.floor??'未记录')+' · '+phase(r.report.phase||r.snapshot?.state?.phase)+(r.snapshot?.state?.player?.turn!=null?' · 回合 '+r.snapshot.state.player.turn:'')+(r.report.source?.state_version!==data.snapshot?.state_version?' · 最近一次分析':' · 当前分析'):'等待下一次分析';
 renderPlans($('#decision-flow'),r,currentNode?(evidence?.choice||choice):null);
 renderIntentMachine($('#enemy-intents'),r,data.snapshot,data.monster_graphs||[]);
 $('#enemy-count').textContent=s.phase==='combat'?(s.enemies||[]).filter(e=>e.hp==null||e.hp>0).length+' 个存活':'等待战斗';
 const entries=data.node_decisions||[];$('#recent-count').textContent=entries.length+' 条';
 renderNodeHistory($('#recent'),entries,loadHistory,{revision:JSON.stringify([a?.last_choice,(data.recent||[]).filter(e=>['ai_choice','local_choice'].includes(e.kind)).map(e=>e.event_id)])});
 if(window.chrome?.webview)window.chrome.webview.postMessage({kind:'observer_status',connected:data.connected,phase:s.phase,archive_id:a?.archive_id||null,state_version:data.snapshot?.state_version,decision_id:data.decision_id||null});
}
async function refresh(){if(busy)return;busy=true;try{const value=await O.api('/api/live');if($('#followSide').checked){let nextRecord=record,nextEvidence=null;if(value.decision_id){if(record?.event_id!==value.decision_id){try{nextRecord=await O.api('/api/event?id='+encodeURIComponent(value.decision_id))}catch{nextRecord=null}}try{nextEvidence=await O.api('/api/evidence?id='+encodeURIComponent(value.decision_id))}catch{}}else nextRecord=null;if(!$('#followSide').checked){pending=value;return}data=value;record=nextRecord;evidence=nextEvidence;pending=null;render()}else{pending=value;$('#notice').hidden=false;$('#notice').textContent='画面已冻结，返回实时可继续跟随。'}}catch{lastRenderKey=null;$('#link').textContent='观察台连接中断';$('#link').classList.remove('connected');$('#notice').hidden=false;$('#notice').textContent='本地服务暂时不可用，正在重试。'}finally{busy=false}}
window.addEventListener('autoslay:agent-unavailable',()=>{agentStatus=null;if($('#followSide').checked)render()});
window.addEventListener('autoslay:agent',e=>{agentStatus=e.detail;if($('#followSide').checked)render()});
$('#full').onclick=()=>{const l=data?.archive?.location||{};O.openFull(data?.archive?.archive_id,{act:l.act_index,node:l.node_key,visit:l.visit_id,decision:data?.decision_id})};$('#resume').onclick=()=>{$('#followSide').checked=true;refresh()};$('#followSide').onchange=()=>{if($('#followSide').checked)refresh();else render()};renderPlans($('#decision-flow'));refresh();setInterval(refresh,2500);
