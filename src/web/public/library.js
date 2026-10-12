import {el,empty,gameText} from './presentation.js';
import {eventVariableText,eventOptionFacts,eventVariableName,eventFormula} from './event-model.js';
const $=s=>document.querySelector(s);
export function branchCondition(expression,definitions={}){
 for(const [key,value]of Object.entries(definitions)){const count=value.match(/^base\.CombatState\.GetTeammatesOf\(base\.Creature\)\.Count\(\(Creature c\) => c\.IsAlive\)\s*([<>]=?)\s*(\d+)$/);if(count)expression=expression.replace(new RegExp('\\b'+key+'\\b','g'),`己方存活生物数（含自身） ${count[1]} ${count[2]}`)}
 const words={HasBeetleCharged:'已经使用甲虫冲锋',IsOffBalance:'处于失衡状态',HasAmalgamDied:'融合体已经死亡',Respawns:'复活次数',CanLay:'可以产卵',CanFabricate:'可以制造',StartStunned:'开场眩晕',IsFront:'位于前排',IsAlone:'独自存活',_isAlone:'独自存活',_curseOfKnowledgeCounter:'知识诅咒计数'};
 return expression.replace(/\(\(\w+\)base\.Creature\.Monster\)\./g,'').replace(/base\.Creature\.HasPower<(AsleepPower|SlumberPower)>\(\)/g,(_,p)=>p==='AsleepPower'?'处于沉睡状态':'处于酣睡状态').replace(/GetAllyCount\(\)/g,'存活队友数').replace(/base\.Creature\.MaxHp\s*\/\s*2/g,'最大生命的一半（向下取整）').replace(/base\.Creature\.CurrentHp/g,'当前生命').replace(/base\.Creature\.MaxHp/g,'最大生命').replace(/base\.Creature\.SlotName/g,'站位').replace(/\b(?:HasBeetleCharged|IsOffBalance|HasAmalgamDied|Respawns|CanLay|CanFabricate|StartStunned|IsFront|IsAlone|_isAlone|_curseOfKnowledgeCounter)\b/g,x=>words[x]).replace(/\|\|/g,' 或 ').replace(/&&/g,' 且 ').replace(/!(?!=)/g,'非 ').replace(/>=/g,' ≥ ').replace(/<=/g,' ≤ ').replace(/==/g,' = ');
}
export function branchPaths(entry,start){
 const nodes=new Map((entry.state_graph?.nodes||[]).map(n=>[n.variable,n])),names=new Map((entry.moves||[]).map((m,i)=>[m.variable,/^[A-Z0-9_]+$/.test(m.title)?'行动 '+(i+1):m.title]));
 function walk(variable,conditions,seen){
  if(seen.has(variable))return [conditions.concat('循环至 '+(names.get(variable)||variable)).join(' → ')];
  const node=nodes.get(variable);if(!node||node.kind==='MoveState')return [conditions.concat(names.get(variable)||variable).join(' → ')];
  const visited=new Set(seen);visited.add(variable);
  return node.branches.flatMap(b=>{
   let label=b.condition_expression?branchCondition(b.condition_expression,entry.state_graph.definitions):`权重 ${branchCondition(b.weight_expression,entry.state_graph.definitions).replace(/(?<=\d)f\b/g,'')}`;
   if(b.registration_conditions?.length)label='开场条件：'+b.registration_conditions.map(x=>branchCondition(x,entry.state_graph.definitions)).join(' 且 ')+'；'+label;
   if(b.repeat_type==='CannotRepeat')label+='；不能连续重复';if(b.repeat_type==='UseOnlyOnce')label+='；整场仅一次';if(b.max_consecutive!==null&&b.max_consecutive!==undefined)label+=`；最多连续 ${b.max_consecutive} 次`;if(b.cooldown)label+=`；最近 ${b.cooldown} 次行动内未使用`;
   return walk(b.target,conditions.concat(label),visited);
  });
 }
 return walk(start,[],new Set());
}
export function powerDescription(entry,power){
 const matching=e=>e.power&&power.entity_id==='POWER.'+e.power.replace(/([a-z])([A-Z])/g,'$1_$2').toUpperCase();
 const amounts=(entry.initial_effects||[]).flatMap(b=>b.effects.filter(matching).map(e=>e.amount));
 const amount=amounts.length&&amounts.every(a=>a===amounts[0])&&amounts[0]!==null?String(amounts[0]):'当前层数';
 const vars={...(power.variable_values||{}),Amount:amount,OwnerName:entry.name};
 let text=(amounts.length?power.localization?.[power.entity_id.slice(6)+'.smartDescription']:null)||power.description||'';
 text=text.replace(/\{(\w+)\}/g,(raw,key)=>vars[key]===undefined||vars[key]===null?raw:String(vars[key]));
 return gameText(text.replace(/\{IsMultiplayer:([^{}|]*)\|([^{}]*)\}/g,'$2'));
}
export function formatMonsterEffect(effect){
 const value=v=>v===null||v===undefined?'动态':String(v);
 if(effect.kind==='attack')return `伤害 ${value(effect.damage)} × ${value(effect.repeats)}`+(effect.total_damage===null||effect.total_damage===undefined?'':` = ${effect.total_damage}`);
 if(effect.kind==='block')return `格挡 ${value(effect.amount)}`;
 if(effect.kind==='power')return `${effect.name||'状态效果'} ${value(effect.amount)} 层`;
 return '需读取实时状态';
}
// Display-only comparison. Never annotates or changes the encyclopedia objects.
export function upgradeChanges(base,upgraded){
 const a=gameText(base||'').match(/\d+(?:\.\d+)?|[\s\S]/gu)||[],b=gameText(upgraded||'').match(/\d+(?:\.\d+)?|[\s\S]/gu)||[];
 const table=Array.from({length:a.length+1},()=>new Uint16Array(b.length+1));
 for(let i=a.length-1;i>=0;i--)for(let j=b.length-1;j>=0;j--)table[i][j]=a[i]===b[j]?table[i+1][j+1]+1:Math.max(table[i+1][j],table[i][j+1]);
 const parts=[],removed=[];let i=0,j=0,deleted='',added='';
 const flush=()=>{if(added)parts.push({text:added,changed:true});else if(deleted.trim())removed.push(deleted.trim());deleted='';added=''};
 while(i<a.length||j<b.length){if(i<a.length&&j<b.length&&a[i]===b[j]){flush();parts.push({text:b[j++],changed:false});i++}else if(j<b.length&&(i===a.length||table[i][j+1]>=table[i+1][j]))added+=b[j++];else deleted+=a[i++]}
 flush();return {parts,removed};
}
export function createLibrary({api,onData,onError,ruleStatus}){
 let category='card',page=1,request=0,timer;
 const filters=$('#library-filters'),tabs=$('#library-tabs');
 for(const[value,label,description]of[['card','卡牌','基础与升级'],['potion','药水','效果与用途'],['relic','遗物','职业与来源'],['event','事件','选项与流程'],['monster','怪物','行动与进阶'],['intent','意图','行动提示']]){const b=el('button');b.append(el('span',label),el('small',description));b.setAttribute('aria-label',label);b.dataset.category=value;b.classList.toggle('active',value===category);b.setAttribute('aria-pressed',String(value===category));b.onclick=()=>{category=value;page=1;$('#library-type').value='';$('#library-owner').value='';$('#library-rarity').value='';tabs.querySelectorAll('button').forEach(n=>{n.classList.toggle('active',n===b);n.setAttribute('aria-pressed',String(n===b))});filters.classList.toggle('behavior-filters', ['event','monster','intent'].includes(category));load().catch(onError)};tabs.append(b)}
 function selector(id,label,initial){const l=el('label',label),s=el('select');s.id=id;s.append(el('option',initial));s.firstChild.value='';l.append(s);filters.append(l);s.onchange=()=>{page=1;load().catch(onError)};return s}
 selector('library-owner','职业 / 来源','全部职业与来源');selector('library-rarity','稀有度','全部稀有度');selector('library-type','卡牌类型','全部卡牌类型');
 const include=el('label',undefined,'check-label'),box=el('input');box.type='checkbox';box.checked=true;box.id='library-shared';include.append(box,document.createTextNode('包含通用资料'));filters.append(include);box.onchange=()=>{page=1;load().catch(onError)};
 const reset=el('button','重置筛选','text-button');reset.onclick=()=>{for(const id of['owner','rarity','type'])$('#library-'+id).value='';$('#knowledge-search').value='';box.checked=true;page=1;load().catch(onError)};filters.append(reset);
 function facet(id,list,label){const s=$('#library-'+id),value=s.value;s.replaceChildren(el('option',label));s.firstChild.value='';for(const item of list){const o=el('option',item.label+' · '+item.count);o.value=item.value;s.append(o)}s.value=list.some(i=>i.value===value)?value:''}
 async function load(){const behavior=['event','monster','intent'].includes(category);for(const id of ['owner','rarity','type'])$('#library-'+id).parentElement.hidden=behavior||(id==='type'&&category!=='card');include.hidden=behavior;filters.classList.toggle('behavior-filters',behavior);const current=++request,q=new URLSearchParams({category,q:$('#knowledge-search').value,owner:behavior?'':$('#library-owner').value,rarity:behavior?'':$('#library-rarity').value,card_type:category==='card'?$('#library-type').value:'',include_shared:box.checked?'1':'0',page,limit:48});const data=await api('/api/catalog?'+q);if(current!==request)return;onData(data);facet('owner',data.facets.scopes,'全部职业与来源');facet('rarity',data.facets.rarities,'全部稀有度');facet('type',data.facets.card_types,'全部卡牌类型');box.disabled=!['ironclad','silent','defect','necrobinder','regent'].includes($('#library-owner').value);render(data)}
 function effect(variant,label,base){
  const panel=el('section',undefined,'effect-panel');panel.append(el('h4',label));
  const energy=v=>v.costs_x?'X':v.energy_cost<0?'不能主动打出':v.energy_cost,stars=v=>v.costs_star_x?'X':Math.max(0,v.star_cost||0);
  const cost=(title,value,old)=>{const row=el('p',undefined,'hint');row.append(document.createTextNode(title+'：'),el(base&&value!==old?'mark':'span',value,base&&value!==old?'upgrade-change':undefined));panel.append(row)};
  cost('能量',energy(variant),base&&energy(base));if(stars(variant)!==0||base&&stars(base)!==0)cost('星星',stars(variant),base&&stars(base));
  const description=el('p',undefined,'entry-description');if(base){const diff=upgradeChanges(base.description,variant.description);for(const part of diff.parts)description.append(part.changed?el('mark',part.text,'upgrade-change'):document.createTextNode(part.text));if(diff.removed.length){const note=el('p',undefined,'upgrade-removed');note.append(document.createTextNode('升级后移除：'),el('mark',diff.removed.join(' / '),'upgrade-change'));panel.append(description,note)}else panel.append(description)}else{description.textContent=gameText(variant.description||'说明尚未记录');panel.append(description)}return panel;
 }
 function behaviorBody(entry){
  const section=el('div',undefined,'behavior-reference');
  const bandLabel=b=>b.ascension_min===b.ascension_max?`进阶 ${b.ascension_min}`:`进阶 ${b.ascension_min}–${b.ascension_max}`;
  if(entry.health_variants?.length){const health=el('div',undefined,'monster-health');health.append(el('strong','初始生命'));for(const band of entry.health_variants)health.append(el('p',`${bandLabel(band)} · ${band.min_hp===null||band.max_hp===null?'随战斗状态变化':band.min_hp===band.max_hp?band.min_hp:band.min_hp+'–'+band.max_hp}`,'hint'));section.append(health)}
  if(entry.state_graph?.initial_choices?.length){const d=el('section',undefined,'monster-health');d.append(el('strong','开场行动'));for(const choice of entry.state_graph.initial_choices)for(const path of branchPaths(entry,choice.target))d.append(el('p',(choice.conditions.length?choice.conditions.map(x=>branchCondition(x,entry.state_graph.definitions)).join(' 且 ')+' → ':'')+gameText(path),'hint'));section.append(d)}
  const attributes={IsHealthBarVisible:'显示生命条',ShouldDisappearFromDoom:'灾厄击杀后移除身体'};for(const [key,label]of Object.entries(attributes)){const value=entry.special_attributes?.[key];if(value!==undefined)section.append(el('p',label+'：'+(value==='true'?'是':value==='false'?'否':branchCondition(value)),'hint'))}
  if(entry.category==='intent'&&entry.description)section.append(el('p',gameText(entry.description),'entry-description'));
  if(entry.initial_effects?.some(b=>b.effects.length)){const d=el('section',undefined,'monster-health');d.append(el('strong','入场状态'));for(const b of entry.initial_effects)d.append(el('p',`${bandLabel(b)} · ${b.effects.map(formatMonsterEffect).join('；')}`));section.append(d)}
  if(entry.powers?.length){const d=el('details');d.open=true;d.append(el('summary','状态与特殊机制'));for(const p of entry.powers)d.append(el('strong',p.name),el('p',powerDescription(entry,p),'entry-description'));section.append(d)}
  if(entry.description&&entry.category==='event'){const story=el('details');story.append(el('summary','事件剧情'),el('p',gameText(entry.description),'entry-description'));section.append(story)}
  if(entry.event_model){const rules=el('details');rules.open=true;rules.append(el('summary','事件数值与变化规则'));for(const [name,v]of Object.entries(entry.event_model.variables))if(v.kind!=='StringVar'&&v.kind!=='formatted_runtime_value')rules.append(el('p',eventVariableName(name)+'：'+eventVariableText(v),'entry-description'));for(const [name,list]of Object.entries(entry.event_model.arrays))if(list.every(x=>/^\d+[mf]?$/.test(x)))rules.append(el('p',eventVariableName(name)+'：'+list.map(eventFormula).join(' → '),'hint'));section.append(rules)}
  if(entry.options?.length){const options=entry.options.filter(o=>o.source_status!=='localization_only'),d=el('div',undefined,'event-options');d.append(el('h4',`事件选项 · ${options.length} 项`));for(const option of options){d.append(el('strong',gameText(option.title)),el('p',gameText(option.description),'entry-description'));for(const card of option.referenced_cards||[])d.append(el('p',`${card.name} · ${card.entity_id}`,'hint'));const facts=eventOptionFacts(entry,option);if(facts.length){const flow=el('details');flow.append(el('summary','具体效果与后续选择'));for(const fact of facts)flow.append(el('p',fact,'entry-description'));d.append(flow)}}section.append(d)}
  if(entry.moves?.length){const d=el('div',undefined,'monster-moves');d.append(el('h4',`行动与后续衔接 · ${entry.moves.length} 项`));const moveNames=new Map(entry.moves.map((m,i)=>[m.variable,/^[A-Z0-9_]+$/.test(m.title)?'行动 '+(i+1):m.title]));for(const move of entry.moves){const action=el('section',undefined,'monster-move');const next=(entry.transitions||[]).filter(t=>t.source===move.variable).flatMap(t=>branchPaths(entry,t.target));action.append(el('strong',gameText(moveNames.get(move.variable))));for(const band of move.numeric_variants||[]){const row=el('div',undefined,'monster-value-row');row.append(el('span',bandLabel(band),'badge'),el('span',band.effects.length?band.effects.map(formatMonsterEffect).join('；'):'其他行动效果','monster-value'));action.append(row)}for(const update of move.state_updates||[])action.append(el('p',branchCondition(update.variable)+'：'+(update.value_expression==='true'?'是':update.value_expression==='false'?'否':update.value_expression),'hint'));for(const path of next)action.append(el('p','之后：'+gameText(path),'hint'));d.append(action)}section.append(d)}
  return section;
 }
 function render(data){const host=$('#knowledge-content');host.replaceChildren();const behavior=['event','monster','intent'].includes(category);const grid=el('div',undefined,'encyclopedia-grid');for(const entry of data.entries){const c=el('article',undefined,'encyclopedia-card');const variant=entry.category==='card'?(entry.variants.find(v=>v.upgrade_level===0)||entry.variants[0]):entry;
  if(entry.category!=='card')c.classList.add('icon-entry');if(behavior)c.classList.add('behavior-entry');if(entry.category==='monster')c.classList.add('monster-entry');if(entry.portrait_url){const img=el('img');img.src=entry.portrait_url;img.alt=entry.name+(entry.category==='card'?'的游戏插画':'的游戏图标');img.loading='lazy';c.append(img)}else if(!behavior||entry.category==='monster')c.append(el('div','游戏无独立图像','portrait-unavailable'));
  const body=el('div',undefined,'encyclopedia-body');body.append(el('h3',entry.name));const tags=el('div',undefined,'button-row');for(const text of(behavior?[]:[entry.classification.scope_label,entry.classification.rarity_label]))tags.append(el('span',text,'badge'));if(entry.category==='card')tags.append(el('span',({Attack:'攻击',Skill:'技能',Power:'能力',Curse:'诅咒',Status:'状态',Quest:'任务'})[variant.type]||'其他','badge'));if(entry.portrait_kind==='beta')tags.append(el('span','测试插画','badge'));body.append(tags);
  if(entry.category==='card'){const comparison=el('div',undefined,'effect-comparison');comparison.append(effect(variant,'基础效果'));const upgraded=entry.variants.find(v=>v.upgrade_level>0);if(upgraded){const panel=effect(upgraded,'升级后',variant);panel.classList.add('upgraded-effect');comparison.append(panel)}else comparison.append(el('p','此牌没有百科升级预览','hint'));body.append(comparison)}else if(behavior)body.append(behaviorBody(entry));else body.append(el('p',entry.description||'说明尚未记录','entry-description'));
  const tips=[...new Map((entry.category==='card'?entry.variants.flatMap(v=>v.hover_tips||[]):entry.hover_tips||[]).map(t=>[JSON.stringify(t),t])).values()];if(tips.length){const d=el('details');d.append(el('summary','关键词与悬浮说明'));for(const tip of tips){if(tip.title)d.append(el('strong',tip.title));if(tip.description)d.append(el('p',tip.description,'entry-description'))}body.append(d)}
  c.append(body);grid.append(c)}host.append(grid);if(!data.entries.length)host.append(empty('没有符合条件的资料','尝试调整筛选条件或搜索词。'));
  const pagination=el('div',undefined,'pagination'),prev=el('button','上一页'),next=el('button','下一页');prev.disabled=page<=1;next.disabled=page*data.limit>=data.total;prev.onclick=()=>{page--;load().catch(onError)};next.onclick=()=>{page++;load().catch(onError)};pagination.append(prev,el('span',`共 ${data.total} 项 · 第 ${page} / ${Math.max(1,Math.ceil(data.total/data.limit))} 页`),next);host.append(pagination)}
 $('#knowledge-search').oninput=()=>{clearTimeout(timer);timer=setTimeout(()=>{page=1;load().catch(onError)},250)};
 return {load};
}
