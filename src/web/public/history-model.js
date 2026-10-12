/* Join explicit correlation IDs first, then place decisions under their original turn. */
const context=e=>[e.location?.act_index,e.location?.generation,e.location?.node_key].join(':');
const identity=e=>e.event_id||e.record_id;
function links(e,scope){
 const d=e.details||{},r=e.request||d.request||{};
 const values=[];
 if(e.kind==='decision')values.push('analysis:'+identity(e)+scope(e,identity(e)));
 for(const v of [d.analysis_id,d.decision_id,e.analysis_id,e.ai_payload?.analysis_id])if(v)values.push('analysis:'+v+scope(e,v));
 for(const v of [d.plan_request_id,e.kind==='agent_packet'?d.request_id:null])if(v)values.push('plan:'+v);
 for(const v of [r.request_id,e.kind==='local_outcome'?d.request_id:null])if(v)values.push('action:'+v);
 if(e.kind==='action_plan')for(const step of d.steps||[])if(step.request?.request_id)values.push('action:'+step.request.request_id);
 return values;
}
const roots=new Set(['decision','agent_packet','ai_choice','local_choice']);
export function groupHistory(entries){
 const events=[...entries].filter(e=>e.kind!=='heartbeat').sort((a,b)=>String(a.recorded_at).localeCompare(String(b.recorded_at))||(a.sequence||a.cursor||0)-(b.sequence||b.cursor||0));
 const requests=new Map();
 for(const e of events){const d=e.details||{},id=d.plan_request_id||(e.kind==='agent_packet'?d.request_id:null);if(id&&d.analysis_id){const values=requests.get(d.analysis_id)||[];if(!values.some(v=>v.id===id))values.push({id,time:e.recorded_at});requests.set(d.analysis_id,values)}}
 const scope=(e,analysis)=>{const d=e.details||{},explicit=d.plan_request_id||(e.kind==='agent_packet'?d.request_id:null);if(explicit)return '@'+explicit;const values=requests.get(analysis)||[];const value=values.filter(v=>String(v.time)<=String(e.recorded_at)).at(-1)||values[0];return value?'@'+value.id:''};
 const parents=new Map(),find=id=>{if(!parents.has(id))parents.set(id,id);if(parents.get(id)!==id)parents.set(id,find(parents.get(id)));return parents.get(id)};
 for(const e of events){const ids=links(e,scope);for(const id of ids.slice(1))parents.set(find(id),find(ids[0]));}
 const grouped=new Map();
 for(const e of events){if(e.kind==='snapshot')continue;const ids=links(e,scope),key=ids.length?find(ids[0]):'event:'+identity(e);if(!grouped.has(key))grouped.set(key,{id:key,events:[]});grouped.get(key).events.push(e);}
 const sections=new Map();
 function sectionFor(e){
  const loc=e.location||{},combat=loc.phase==='combat',turn=combat?loc.turn:null;
  const key=context(e)+':'+(combat?'turn:'+String(turn):'phase:'+String(loc.phase));
  if(!sections.has(key))sections.set(key,{id:key,node:loc.node_key,location:loc,kind:combat?'turn':'between',turn,phase:loc.phase,groups:[],snapshots:[],recorded_at:e.recorded_at});
  if(String(e.recorded_at)<String(sections.get(key).recorded_at))sections.get(key).recorded_at=e.recorded_at;
  return sections.get(key);
 }
 for(const group of grouped.values()){
  const anchor=group.events.find(e=>roots.has(e.kind))||group.events[0];
  const section=sectionFor(anchor);group.anchor=anchor;
  group.ai=group.events.some(e=>e.kind==='ai_choice')?'used':group.events.some(e=>e.kind==='local_choice')?'skipped':group.events.some(e=>e.kind==='agent_packet')?'requested':'none';
  group.number=section.groups.length+1;section.groups.push(group);
 }
 for(const e of events)if(e.kind==='snapshot')sectionFor(e).snapshots.push(e);
 for(const section of sections.values()){
  section.decisions=section.groups.filter(g=>g.ai!=='none');
  section.otherGroups=section.groups.filter(g=>g.ai==='none');
  section.decisions.forEach((g,i)=>g.number=i+1);
 }
 return [...sections.values()].sort((a,b)=>String(a.recorded_at).localeCompare(String(b.recorded_at)));
}
