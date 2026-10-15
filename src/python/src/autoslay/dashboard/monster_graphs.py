"""Read-only state-machine presentation, independent of combat candidate search."""
from dataclasses import replace
from math import isfinite
import re
from autoslay.knowledge_base.behavior import load
from autoslay.knowledge_base.repository import scoped
from autoslay.decision.contracts import Enemy, UnsupportedEffect
from autoslay.rules.monsters import MonsterRules, expression


def condition_label(value):
    text=str(value).removeprefix('() => ')
    for key,title in [('base.Creature.CurrentHp','生命'),('base.Creature.MaxHp','最大生命'),
                      ('IsOffBalance','失衡'),('_isAlone','独处'),('HasBeetleCharged','已冲锋'),
                      ('&&',' 且 '),('||',' 或 '),('true','是'),('false','否')]:
        text=text.replace(key,title)
    return re.sub(r'!(?!=)','非 ',text)


def branch_label(branch,kind):
    labels=[condition_label(c) for c in branch.get('registration_conditions',[])]
    if kind=='ConditionalBranchState':
        labels.append('按顺序匹配 '+str(branch.get('priority',0)+1)+'：'+condition_label(branch.get('condition_expression','条件未收录')))
    else:
        labels.append('权重 '+str(branch.get('weight_expression','1')))
        repeat=branch.get('repeat_type')
        if repeat=='CannotRepeat':labels.append('不可连续重复')
        elif repeat=='UseOnlyOnce':labels.append('整场仅一次')
        elif repeat=='CanRepeatXTimes':labels.append('最多连续 '+str(branch.get('max_consecutive','?'))+' 次')
        if branch.get('cooldown'):labels.append('冷却 '+str(branch['cooldown'])+' 次行动')
    return '；'.join(labels)


def visible_enemy(raw,turn):
    powers={p['entity_id']:p.get('amount',0) for p in raw.get('powers') or []}
    attacks=tuple(i['attack_damage'] for i in raw.get('intents') or []
                  if i.get('attack_damage') is not None and i.get('attack_repeats') is not None
                  for _ in range(min(100,max(0,i['attack_repeats']))))
    uncertain=tuple(i['type'] for i in raw.get('intents') or [] if i.get('attack_damage') is None or i.get('attack_repeats') is None)
    return Enemy(raw['instance_id'],raw['entity_id'],raw.get('hp',0),raw.get('block',0),0,None,attacks,
                 uncertain_intents=uncertain,strength=powers.get('POWER.STRENGTH_POWER',0),
                 weak=powers.get('POWER.WEAK_POWER',0)>0,max_hp=raw.get('max_hp',0),
                 move_state=raw.get('current_move_id'),move_history=tuple(raw.get('move_history') or []),
                 history_complete=raw.get('history_complete',False) or turn==1,
                 runtime_fields=tuple((k,v) for k,v in (raw.get('runtime_state') or {}).get('fields',{}).items()
                                      if type(v) in {int,bool,str}))


def next_moves(model,enemy,entry,current):
    """Keep unresolved branches visible; assign probabilities only with full eligibility."""
    graph=entry['state_graph'];nodes={n['variable']:n for n in graph['nodes']}
    # Transition predicates run AFTER the current move. An unmodeled mutation
    # must not be evaluated using the old field value as if it were unchanged.
    from autoslay.rules.mechanism_scope import reviewed_move_scope
    move=next((m for m in entry.get('moves',[]) if m['variable']==current),{})
    scope=reviewed_move_scope(entry,move) or {};fields=dict(enemy.runtime_fields)
    for update in move.get('state_updates',[]):
        variable=update['variable']
        if variable in scope.get('ignored_state_updates',[]):continue
        field=graph.get('definitions',{}).get(variable,variable)
        value='__unresolved_after_action__'
        if scope and (field in fields or variable in scope.get('initialized_fields',[])):
            try:value=expression(update['value_expression'],model.values(enemy,entry)|fields)
            except UnsupportedEffect:pass
        fields[variable]=value;fields[field]=value
    enemy=replace(enemy,runtime_fields=tuple(fields.items()))
    def truth(text):
        try:return bool(expression(text,model.values(enemy,entry)))
        except UnsupportedEffect:return None
    def walk(target,visited=()):
        if target in visited or target not in nodes:return []
        node=nodes[target]
        if node['kind']=='MoveState':return [(target,1.0)]
        branches=node.get('branches',[]);eligible=[];complete=True
        conditional=node['kind']=='ConditionalBranchState';prior_unknown=False
        for b in branches:
            tests=[truth(c) for c in b.get('registration_conditions',[])]
            if conditional:tests.append(truth(b.get('condition_expression','')))
            if False in tests:continue
            known=None not in tests
            dest=b['target'];history=enemy.move_history
            if not conditional:
                repeat=b.get('repeat_type');cooldown=b.get('cooldown',0)
                if repeat=='CannotRepeat' and history and history[-1]==dest:continue
                if repeat=='UseOnlyOnce':
                    if dest in history:continue
                    known &= enemy.history_complete
                if repeat=='CanRepeatXTimes':
                    count=b.get('max_consecutive')
                    if not isinstance(count,int) or count<1:known=False
                    elif len(history)>=count and all(h==dest for h in history[-count:]):continue
                    elif len(history)<count and all(h==dest for h in history):known &= enemy.history_complete
                if isinstance(cooldown,int) and cooldown>0:
                    if dest in history[-cooldown:]:continue
                    if len(history)<cooldown:known &= enemy.history_complete
                elif cooldown:known=False
                try:
                    weight=expression(b.get('weight_expression','1'),model.values(enemy,entry))
                    if type(weight) not in {int,float} or not isfinite(weight) or weight<0:weight=None
                except UnsupportedEffect:weight=None
                if weight==0:continue
                known &= weight is not None
            else:weight=1
            complete &= known
            eligible.append((dest,weight))
            if conditional:
                if known:break
                prior_unknown=True
        if conditional and prior_unknown:complete=False
        total=sum(w for _,w in eligible if w is not None)
        result=[]
        for dest,weight in eligible:
            probability=(1.0 if conditional else weight/total) if complete and total>0 else None
            for end,p in walk(dest,visited+(target,)):
                result.append((end,probability*p if probability is not None and p is not None else None))
        return result
    result={}
    for edge in graph.get('edges',[]):
        if edge['source']!=current:continue
        for target,p in walk(edge['target']):
            if target not in result:result[target]=p
            elif result[target] is None or p is None:result[target]=None
            else:result[target]+=p
    return [dict(id=k,probability=v) for k,v in result.items()]


@scoped
def project(snapshot,behavior=None):
    state=(snapshot or {}).get('state') or {}
    if state.get('phase')!='combat':return []
    data=load() if behavior is None else behavior
    entries={e['entity_id']:e for e in data.get('entries',[]) if e.get('category')=='monster'}
    model=MonsterRules(snapshot,{},data);result=[]
    for raw in state.get('enemies') or []:
        if raw.get('hp',1)<=0:continue
        row=dict(instance_id=raw['instance_id'],name=raw.get('name'),nodes=[],edges=[],initial=[],next_moves=[])
        result.append(row);entry=entries.get(raw.get('entity_id'));graph=(entry or {}).get('state_graph') or {}
        if not graph.get('nodes'):
            row['note']='本地源码资料尚未收录该怪物的状态机';continue
        compatible=model.compatible and entry.get('provenance',{}).get('game_assembly_sha256')==data.get('game_assembly_sha256')
        row['version_matches']=compatible
        enemy=visible_enemy(raw,(state.get('player') or {}).get('turn'))
        moves={m['variable']:m for m in entry.get('moves',[])}
        current=next((n['variable'] for n in graph['nodes'] if enemy.move_state in {n['variable'],n['state_id']}),None)
        if current is None and compatible:
            try:current=model.identify(enemy).move_state
            except UnsupportedEffect:pass
        history=tuple(next((m['variable'] for m in entry.get('moves',[]) if m['state_id']==h),h) for h in enemy.move_history)
        if current and (not history or history[-1]!=current):history=history+(current,)
        enemy=replace(enemy,move_state=current,move_history=history)
        row['current']=current
        for node in graph['nodes']:
            move=moves.get(node['variable']);effects=[]
            if move:
                if compatible:
                    try:
                        hits=model.attacks(enemy,entry,move)
                        if hits:effects.append('攻击 '+' + '.join(map(str,hits)))
                    except UnsupportedEffect:pass
                    try:
                        for kind,amount,identity in model.effects(enemy,entry,move):
                            if kind in {'draw','discard'}:effects.append('生成 '+str(amount)+' 张状态牌，置入'+('抽牌堆' if kind=='draw' else '弃牌堆'))
                            else:effects.append({'block':'格挡','strength':'力量','weak':'虚弱','frail':'脆弱'}.get(kind,kind)+' '+str(amount))
                    except (UnsupportedEffect,ValueError,KeyError):pass
                if not effects:
                    intent=move.get('intent_expression','')
                    status=re.search(r'new StatusIntent\(([^()]+)\)',intent)
                    if status and compatible:
                        try:effects.append('生成 '+str(model.number(status[1],enemy,entry))+' 张状态牌')
                        except UnsupportedEffect:pass
                    for key,title in [('AttackIntent','攻击'),('StatusIntent','生成状态牌'),('BuffIntent','强化'),('DebuffIntent','削弱'),('DefendIntent','防御'),('SleepIntent','睡眠'),('EscapeIntent','逃跑')]:
                        if key in intent and not effects:effects.append(title)
            title=(move or {}).get('title') or {'RandomBranchState':'随机选择','ConditionalBranchState':'条件判断'}.get(node['kind'],node['state_id'])
            row['nodes'].append(dict(id=node['variable'],state_id=node['state_id'],kind=node['kind'],title=title,effects=effects))
            for b in node.get('branches',[]):row['edges'].append(dict(source=node['variable'],target=b['target'],label=branch_label(b,node['kind'])))
        row['edges'].extend(dict(source=e['source'],target=e['target'],label='行动结束') for e in graph.get('edges',[]))
        row['initial']=[dict(target=c['target'],label='；'.join(condition_label(x) for x in c.get('conditions',[])) or '初始') for c in graph.get('initial_choices',[])]
        if current and compatible:row['next_moves']=next_moves(model,enemy,entry,current)
        if not compatible:row['note']='行为图来自其他游戏版本，仅供参考'
        elif not current:row['note']='已展示完整规则；当前行动尚无法唯一定位'
        elif any(m['probability'] is None for m in row['next_moves']):row['note']='分支规则已列出；具体概率取决于尚未读取的条件或历史'
    return result
