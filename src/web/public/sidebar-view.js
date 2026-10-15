import {el,actionName,candidateName,fmt,phase,gameText} from './presentation.js';
import {enemyIntentFacts} from './combat-forecast.js';

const score=c=>c?.score??c?.delta_utility??c?.utility??c?.metrics?.heuristic_score;
const groups=report=>({shortlist:report.shortlist,observations:report.observations,
 ...Object.fromEntries(['choices','shop','event','event_sequence','rest','route'].map(k=>[k,report[k]?.candidates])),
 ...Object.fromEntries(['candidates','wait_candidates','observation_boundaries'].map(k=>['combat.'+k,report.combat?.[k]]))});
const stepKey=steps=>JSON.stringify(steps?.map(s=>[s.action_id,s.target_id??null]));
export function decisionPlans(record,choice){
 const report=record?.report||{},all=groups(report),combat=report.combat;
 // Batch scores compare complete batches; do not mix them with single-turn scores.
 const pool=combat?.batches?.top_batches?.length?combat.batches.top_batches:
  combat?[...(combat.candidates||report.shortlist||[]),...(combat.observation_boundaries||report.observations||[]),...(combat.wait_candidates||[])]:
  report.decision_options?.length?report.decision_options.map(o=>({...o.candidate,steps:o.steps})):
  Object.entries(all).filter(([k])=>!k.startsWith('combat.')&&!['shortlist','observations'].includes(k)).flatMap(([,v])=>v||[]);
 const selected=choice?.candidate||all[choice?.group]?.[choice?.index];
 const steps=choice?.steps||choice?.plan?.steps||(choice?.action_id?[choice]:null);
 const ai=!!choice&&choice.ai_called!==false&&choice.group!=='local_deterministic';
 if(ai){
  const match=selected||(steps&&pool.find(c=>stepKey(c.steps)===stepKey(steps)));
  return [{...(match||{}),...(steps?{steps}:{}),ai:true,custom:!match,reason:choice.reason}];
 }
 const seen=new Set(),ranked=pool.filter(c=>{const key=c.steps?stepKey(c.steps):JSON.stringify(c);if(seen.has(key))return false;seen.add(key);return true})
  .sort((a,b)=>(Number.isFinite(score(b))?score(b):-Infinity)-(Number.isFinite(score(a))?score(a):-Infinity)).slice(0,3);
 if(!ranked.length&&report.autonomy?.selected_steps?.length)return [{steps:report.autonomy.selected_steps}];
 return ranked;
}

export function compactAction(step,snapshot,trace){
 const state=snapshot?.state||{},card=state.player?.hand?.find(c=>'play_'+c.instance_id===step.action_id);
 if(!card)return actionName(step,snapshot);
 const target=state.enemies?.find(e=>e.instance_id===step.target_id);
 // Prefer modeled per-step values, which include earlier buffs and debuffs.
 const before=trace?.before,after=trace?.after,values=[];
 if(before&&after){
  const damage=(before.enemies||[]).reduce((sum,e)=>{const next=after.enemies?.find(n=>n.instance_id===e.instance_id);return sum+(next?Math.max(0,e.hp-next.hp)+Math.max(0,e.block-next.block):0)},0);
  const block=after.block-before.block;
  if(damage>0)values.push(damage);if(block>0)values.push(block);
 }
 return card.name+(values.length?'('+values.join(' / ')+')':'')+(target?' → '+target.name:'');
}

export function actionDetails(step,snapshot,trace){
 const s=snapshot?.state||{},p=s.player||{},id=step.action_id;
 const card=(p.hand||[]).find(c=>'play_'+c.instance_id===id);
 const potion=(p.potions||[]).find(v=>[v.use_action_id,v.discard_action_id,'use_potion_'+v.instance_id,'discard_potion_'+v.instance_id].includes(id));
 const action=(s.legal_actions||[]).find(a=>a.action_id===id);
 const sources=[...(s.card_selection?.cards||[]),...(s.reward_screen?.cards||[]),...(s.reward_screen?.alternatives||[]),...(s.reward_screen?.rewards||[]),...(s.event_screen?.options||[]),...(s.rest_site?.options||[]),...(s.shop?.items||[])];
 const option=sources.find(o=>o.action_id===id),entity=card||potion||option?.card||option?.details?.card||option?.details||option||action;
 const discard=action?.type==='discard_potion'||id?.startsWith('discard_potion_');
 let title=compactAction(step,snapshot,trace);
 if(potion){const target=(s.enemies||[]).find(e=>e.instance_id===step.target_id);title=(discard?'丢弃':'使用')+'「'+(potion.name||'药水')+'」'+(target?' → '+target.name:'')}
 else if(!card&&title==='执行当前选项')title=option?.title||option?.text||entity?.name||action?.label||title;
 const description=discard?'丢弃该药水，不触发使用效果。':entity?.description||option?.description||action?.description;
 const effects=[];
 if(description)effects.push(gameText(description).replace(/<[^>]+>/g,'').replace(/\[\/?(?:color|font|size|url)(?:=[^\]]*)?\]/gi,'').trim());
 if(!description&&id!=='end_turn'&&(card||potion))effects.push('效果说明未记录');
 if(card){
  const costs=[];
  if(card.costs_x)costs.push('X 能量');else if(Number.isFinite(card.energy_cost)&&card.energy_cost>=0)costs.push(card.energy_cost+' 能量');
  if(card.costs_star_x)costs.push('X 星星');else if(card.star_cost>0)costs.push(card.star_cost+' 星星');
  if(costs.length)effects.push('费用：'+costs.join(' + '));
  const names={Exhaust:'消耗',Ethereal:'虚无',Retain:'保留',Innate:'固有',Unplayable:'不能打出'};
  const keywords=(card.keywords||[]).map(k=>names[String(k).split('.').at(-1)]||k);
  if(keywords.length)effects.push('关键词：'+keywords.join(' · '));
 }
 // Keep every tooltip: keywords, generated cards and conditional mechanics can
 // live here even when the simulator has no adapter for that effect.
 for(const tip of discard?[]:entity?.hover_tips||[]){
  const body=gameText(tip.description||tip.body||'').trim(),name=gameText(tip.title||'').trim();
  if(body&&!effects.some(e=>e.includes(body)))effects.push((name?name+'：':'')+body);
 }
 if(trace?.boundary==='draw_requires_observation')effects.push('抽牌结算后重新决策');
 return {title,effects:effects.filter(Boolean)};
}

const planViews=new WeakMap(),intentViews=new WeakMap();
export function renderPlans(host,record,choice=null){
 const plans=decisionPlans(record,choice);
 const views=plans.map((plan,index)=>{
  const steps=plan.steps??(plan.action_id?[plan]:null);
  const actions=steps?.length?steps.map((step,i)=>actionDetails(step,record?.snapshot,plan.state_trace?.[i]?.before?plan.state_trace[i]:{before:plan.state_trace?.[i],after:plan.state_trace?.[i+1]})):
   [{title:steps?'结束出牌':candidateName(plan,record?.snapshot,index),effects:plan.card?.description?[gameText(plan.card.description)]:[]}];
  const boundary=plan.stop_reason||plan.boundary;
  if(boundary==='draw_requires_observation'&&actions.length&&!actions.at(-1).effects.includes('抽牌结算后重新决策'))actions.at(-1).effects.push('抽牌结算后重新决策');
  return {label:plan.ai?(plan.custom?'历史 AI 方案':'AI 已选方案'):'TOP '+(index+1),score:Number.isFinite(score(plan))?fmt(score(plan))+' 分':'未评分',actions,reason:plan.reason};
 });
 const key=JSON.stringify([!!record?.report,views]);if(planViews.get(host)===key)return;planViews.set(host,key);
 host.replaceChildren();
 if(!plans.length){host.append(el('p',record?.report?'等待 AI 选择或可展示的方案。':'等待下一次分析。','hint'));return}
 const grid=el('div',undefined,'decision-plan-grid'+(plans[0].ai?' ai-selection':''));
 views.forEach(plan=>{
  const panel=el('article',undefined,'decision-plan'),heading=el('div',undefined,'plan-heading');
  heading.append(el('strong',plan.label));
  heading.append(el('span',plan.score,'plan-score'));panel.append(heading);
  const list=el('ol',undefined,'plan-actions');
  for(const action of plan.actions){const item=el('li',undefined,'action-chip');item.append(el('span',action.title,'action-title'));for(const effect of action.effects)item.append(el('p',effect,'action-effect'));list.append(item)}
  panel.append(list);if(plan.reason)panel.append(el('p',plan.reason,'hint'));grid.append(panel);
 });host.append(grid);
}

const svgNS='http://www.w3.org/2000/svg';
let graphNumber=0;
function svg(tag,attrs={},text){const n=document.createElementNS(svgNS,tag);for(const [key,value]of Object.entries(attrs))n.setAttribute(key,String(value));if(text!=null)n.textContent=text;return n}
function svgLines(parent,x,y,text,cls,width=15){
 const lines=[];for(const part of String(text).split(/[；\n]/))for(let i=0;i<part.length;i+=width)lines.push(part.slice(i,i+width));
 const node=svg('text',{x,y,class:cls,'text-anchor':'middle'});
 lines.forEach((line,i)=>node.append(svg('tspan',{x,dy:i?'1.25em':'0'},line)));parent.append(node);return lines.length;
}
export function renderStateGraph(machine){
 const width=660,middle=330,positions=new Map();let bottom=35;
 const lineCount=(text,width)=>String(text).split(/[；\n]/).reduce((sum,s)=>sum+Math.max(1,Math.ceil(s.length/width)),0);
 machine.nodes.forEach((node,index)=>{const titles=lineCount(node.title,8),effects=node.effects?.length?lineCount(node.effects.join('；'),11):0;const h=Math.max(node.kind==='MoveState'?92:106,36+titles*35+effects*27);const y=bottom+h/2+35;positions.set(node.id,{x:middle,y,h,node,index,titles});bottom=y+h/2+55});
 const height=Math.max(230,bottom+30);
 const root=svg('svg',{viewBox:`0 0 ${width} ${height}`,class:'monster-state-svg',role:'img','aria-label':machine.name+'行动状态机'});
 const marker='monster-arrow-'+(++graphNumber),defs=svg('defs'),arrow=svg('marker',{id:marker,viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:7,markerHeight:7,orient:'auto-start-reverse'});
 arrow.append(svg('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:'#a9bcdc'}));defs.append(arrow);root.append(defs);
 const edges=svg('g',{class:'machine-edges'});root.append(edges);
 const next=new Set((machine.next_moves||[]).map(n=>n.id));
 for(const [i,edge]of machine.edges.entries()){
  const from=positions.get(edge.source),to=positions.get(edge.target);if(!from||!to)continue;
  let path,lx,ly;
  if(from===to){
   const x=middle+125,y=from.y;path=`M ${x} ${y-23} C 610 ${y-100},610 ${y+100},${x} ${y+23}`;lx=560;ly=y;
  }else if(to.index===from.index+1){
   path=`M ${middle} ${from.y+from.h/2+2} L ${middle} ${to.y-to.h/2-2}`;lx=middle+90;ly=(from.y+to.y)/2-7;
  }else{
   const left=to.y<from.y,x=middle+(left?-125:125),lane=left?70+(i%3)*25:590-(i%3)*25;
   path=`M ${x} ${from.y} C ${lane} ${from.y},${lane} ${to.y},${x} ${to.y}`;lx=left?95:565;ly=(from.y+to.y)/2-9;
  }
  const active=edge.source===machine.current;
  const group=svg('g',{class:'machine-edge'+(active?' active-edge':'')});group.append(svg('title',{},edge.label));
  group.append(svg('path',{d:path,fill:'none','marker-end':`url(#${marker})`}));
  svgLines(group,lx,ly,edge.label,'edge-label',9);edges.append(group);
 }
 for(const initial of machine.initial||[]){const to=positions.get(initial.target);if(!to)continue;
  edges.append(svg('circle',{cx:26,cy:to.y-46,r:7,class:'machine-start'}));
  edges.append(svg('path',{d:`M 26 ${to.y-37} Q 26 ${to.y},${middle-132} ${to.y}`,class:'machine-entry',fill:'none','marker-end':`url(#${marker})`}));
  svgLines(edges,80,to.y-34,initial.label,'edge-label',10);
 }
 for(const {x,y,h,node,titles}of positions.values()){
  const branch=node.kind!=='MoveState',group=svg('g',{class:'machine-node'+(branch?' branch-node':'')+(node.id===machine.current?' current-node':'')+(next.has(node.id)?' next-node':'')});
  const title=[node.title,...(node.effects||[])].join(' · ');group.append(svg('title',{},title));
  group.append(branch?svg('path',{d:`M ${x} ${y-h/2} L ${x+125} ${y} L ${x} ${y+h/2} L ${x-125} ${y} Z`}):svg('rect',{x:x-125,y:y-h/2,width:250,height:h,rx:18}));
  svgLines(group,x,branch?y+8:y-h/2+32,node.title,'state-title',8);
  if(node.effects?.length)svgLines(group,x,y-h/2+32+titles*35,node.effects.join('；'),'state-effects',11);
  if(node.id===machine.current)svgLines(group,x,y-h/2-12,'当前状态','current-label',12);
  root.append(group);
 }
 return root;
}

export function renderIntentMachine(host,record,snapshot,machines=[]){
 snapshot=snapshot||{};const s=snapshot.state||{},facts=enemyIntentFacts(snapshot);
 const key=JSON.stringify([s.phase,facts,machines]);if(intentViews.get(host)===key)return;intentViews.set(host,key);host.replaceChildren();
 if(s.phase!=='combat'){host.append(el('p','当前不在战斗中。','hint'));return}
 for(const enemy of facts){
  const machine=machines.find(m=>m.instance_id===enemy.id),section=el('section',undefined,'intent-machine');
  section.append(el('h3',enemy.name+' · 生命 '+fmt(enemy.hp)+' · 格挡 '+fmt(enemy.block)));
  section.append(el('p','当前可见：'+enemy.intents.join('；'),'machine-visible'));
  if(machine?.nodes?.length){
   const names=new Map(machine.nodes.map(n=>[n.id,n.title]));
   if(machine.next_moves?.length)section.append(el('p','下回合：'+machine.next_moves.map(n=>(names.get(n.id)||n.id)+(n.probability==null?'（条件分支）':'（'+fmt(n.probability*100)+'%）')).join(' / '),'machine-next'));
   section.append(renderStateGraph({...machine,name:enemy.name}));
   section.append(el('p','紫色：当前；绿色：下回合可能行动。箭头标注转移规则。','hint'));
  }
  if(machine?.note)section.append(el('p',machine.note,'decision-note'));
  else if(!machine?.nodes?.length)section.append(el('p','尚无该怪物的源码状态机资料；当前可见意图如上。','hint'));
  host.append(section);
 }
 if(facts.length)host.append(el('p','分支按当前可见状态计算；出牌改变生命或状态后，条件分支可能变化。','hint'));
 if(!facts.length)host.append(el('p','当前没有可见的存活怪物。','hint'));
}

const histories=new WeakMap();
export function renderNodeHistory(host,entries,load,{revision=''}={}){
 let rows=histories.get(host);if(!rows){rows=new Map();histories.set(host,rows);host.replaceChildren()}
 const ids=new Set((entries||[]).map(e=>e.event_id));
 for(const [id,row] of rows)if(!ids.has(id)){row.item.remove();rows.delete(id)}
 if(!entries?.length){if(!host.children.length)host.append(el('p','当前地图节点暂无决策记录。','hint'));return}
 if(!rows.size)host.replaceChildren();
 for(const [index,entry] of [...entries].reverse().entries()){
  const existing=rows.get(entry.event_id);
  if(existing){
   if(host.children[index]!==existing.item)host.insertBefore(existing.item,host.children[index]||null);
   existing.load=load;
   if(existing.revision!==revision){existing.revision=revision;existing.loaded=false;existing.show()}
   continue;
  }
  const item=el('details',undefined,'node-decision');item.dataset.id=entry.event_id;
  const summary=el('summary','楼层 '+(entry.location?.floor??'未记录')+' · '+phase(entry.location?.phase)+(entry.location?.turn!=null?' · 回合 '+entry.location.turn:'')+' · '+new Date(entry.recorded_at).toLocaleTimeString('zh-CN'));
  const body=el('div');item.append(summary,body);
  const row={item,body,load,revision,loading:false,loaded:false};
  row.show=async()=>{
   if(!item.open||row.loading||row.loaded)return;row.loading=true;const requestedRevision=row.revision;
   const firstLoad=!body.children.length;
   if(firstLoad)body.append(el('p','读取方案…','hint'));
   try{const {record,choice}=await row.load(entry.event_id);renderPlans(body,record,choice);row.loaded=requestedRevision===row.revision}
   catch{if(firstLoad)body.replaceChildren(el('p','读取失败，收起后展开可重试。','hint'));row.failed=true}
   finally{row.loading=false;if(!row.loaded&&!row.failed&&requestedRevision!==row.revision)row.show()}
  };
  item.addEventListener('toggle',()=>{row.failed=false;return row.show()});rows.set(entry.event_id,row);
  host.insertBefore(item,host.children[index]||null);
 }
}
