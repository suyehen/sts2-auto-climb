"""Reviewed multi-stage adapters and conservative source terminal projection.

Source dossiers are references, not arbitrary executable C#. An unsupported
branch stays unknown and prevents a complete comparison.
"""
from copy import deepcopy
from autoslay.knowledge_base.behavior import load
from autoslay.knowledge_base.event_model import calls

VERSION='1.0.0'
REVIEWED={
    'EVENT.COLOSSAL_FLOWER':'bb623a8b816ed8477b2185999af535b16d08cb31950620eec255e4ad6b13b6df',
    'EVENT.TABLET_OF_TRUTH':'9771d5cb6f7c76ba38b4f343160c0e113ac13a067403164fba54992da5d20dee',
}
RESOURCE_KINDS={'lose_hp','heal','gain_gold','lose_gold','lose_max_hp'}


def variables(event):
    return {v['name']:v.get('value',v.get('base_value')) for v in event.get('dynamic_variables') or []}


def terminal_effects(option,model,observed):
    """Accept only unconditional terminal methods whose calls are accounted for."""
    method=model['methods'].get(option.get('handler'))
    if not method: raise ValueError('选项处理函数尚未绑定')
    if any(method.get(k) for k in ('conditions','updates','local_calls','random','operations','options')):
        raise ValueError('包含条件、状态修改或辅助函数，尚未通过终结选项规则审核')
    transitions=method.get('transitions',[])
    if len(transitions)!=1 or transitions[0]['kind']!='SetEventFinished':
        raise ValueError('后续事件阶段尚未编译')
    allowed={'CreatureCmd.Damage','CreatureCmd.Heal','CreatureCmd.LoseMaxHp',
             'PlayerCmd.GainGold','PlayerCmd.LoseGold','SetEventFinished','L10NLookup',
             'ThrowingPlayerChoiceContext'}
    if any(c['name'] not in allowed for c in calls(method.get('evidence',''))):
        raise ValueError('处理函数还有未建模调用')
    result=[]
    for effect in method.get('effects',[]):
        if effect['kind'] not in RESOURCE_KINDS or effect.get('conditions'):
            raise ValueError('非资源效果尚未建模')
        amount=effect['amount']; expression=amount.get('expression')
        if expression in observed:
            value=observed[expression]
        elif expression in model['variables']:
            var=model['variables'][expression]
            if var['status']!='fixed': raise ValueError('当前动态数值未从游戏读到')
            value=var['value']
        else: value=amount.get('value') if amount.get('status')=='static' else None
        if type(value) not in (int,float) or value<0 or int(value)!=value:
            raise ValueError('效果数值未确定')
        result.append(dict(kind=effect['kind'],amount=int(value)))
    return result


def flower(entry,event):
    fields=(event.get('runtime_state') or {}).get('fields') or {}
    stage=fields.get('_numberOfDigs',fields.get('NumberOfDigs'))
    if type(stage) is not int or not 0<=stage<=2: raise ValueError('当前深入次数未从游戏读到')
    arrays=entry['event_model']['arrays']
    gold=list(map(int,arrays['_prizeCosts'])); damage=list(map(int,arrays['_prizeDamage']))
    graph={}
    for i in range(stage,3):
        options=[dict(option_id='ExtractInstead' if i==2 else 'ExtractCurrentPrize',effects=[dict(kind='gain_gold',amount=gold[i])])]
        if i<2: options.append(dict(option_id='ReachDeeper',effects=[dict(kind='lose_hp',amount=damage[i])],next_state=str(i+1)))
        else:
            # Relic value and gain hooks are not assigned a fabricated zero.
            options.append(dict(option_id='ObtainPollinousCore',effects=[dict(kind='lose_hp',amount=damage[i]),dict(kind='unmodeled_relic_reward')]))
        graph[str(i)]=options
    return graph,str(stage)


def tablet(entry,event,deck,context,entries,max_states=180):
    fields=(event.get('runtime_state') or {}).get('fields') or {}
    stage=fields.get('_decipherCount',fields.get('DecipherCount'))
    if type(stage) is not int or not 0<=stage<=4: raise ValueError('当前解读次数未从游戏读到')
    observed=variables(event); cost=observed.get('DecipherMaxHpLoss')
    if type(cost) not in (int,float) or cost<0 or int(cost)!=cost: raise ValueError('当前解读费用未从游戏读到')
    graph={}; sequence=0
    def build(count,cards,maximum,first=False):
        nonlocal sequence
        sequence+=1; key=str(sequence)
        if sequence>max_states:
            graph[key]=[dict(option_id='unknown',effects=[dict(kind='unmodeled_search_budget')])]; return key
        rows=[dict(option_id='Smash' if count==0 else 'GiveUp',effects=[dict(kind='heal',amount=20)] if count==0 else [])]
        graph[key]=rows
        loss=int(cost) if first else {1:6,2:12,3:24,4:maximum-1}[count]
        if loss>=maximum:
            rows.append(dict(option_id='Decipher',effects=[dict(kind='lose_hp',amount=maximum)]));return key
        resources=[dict(kind='lose_max_hp',amount=loss)]
        pool=[]
        for c in cards:
            canonical=entries.get(c['entity_id'])
            if canonical is None or 'max_upgrade_level' not in canonical:
                rows.append(dict(option_id='Decipher',effects=[dict(kind='unmodeled_upgrade_pool')]));return key
            if c.get('upgrade_level',0)<canonical['max_upgrade_level']: pool.append(c)
        outcomes=[]
        choices=[pool] if count==4 else [[c] for c in pool] if pool else [[]]
        for chosen in choices:
            effects=resources+[dict(kind='upgrade_card',instance_id=c['instance_id']) for c in chosen]
            modified=deepcopy(cards)
            for c in modified:
                if any(c['instance_id']==p['instance_id'] for p in chosen): c['upgrade_level']=c.get('upgrade_level',0)+1
            target=build(count+1,modified,maximum-loss) if count<4 else None
            outcomes.append(dict(probability=1/len(choices),effects=effects,next_state=target))
        rows.append(dict(option_id='Decipher',outcomes=outcomes))
        return key
    start=build(stage,deck,context.max_hp,True)
    return graph,start


def evaluate_source_event(event,deck,context,entries,policy,game_version,*,max_steps=12):
    from autoslay.evaluation.event_sequences import evaluate_graph
    data=load(); entry=next((e for e in data['entries'] if e['entity_id']==event.get('entity_id')),None)
    base=dict(module_version=VERSION,supported=False,candidates=[],execution_authorized=False)
    if not entry: return dict(base,reason='event_source_missing')
    if game_version!=data.get('game_version') or event.get('game_assembly_sha256')!=data.get('game_assembly_sha256'):
        return dict(base,reason='event_source_version_or_hash_mismatch')
    if event.get('interaction_warnings'): return dict(base,reason='event_resource_hooks_unmodeled',warnings=event['interaction_warnings'])
    model=entry['event_model']; options={o['text_key']:o for o in entry['options']}
    live=[o for o in event.get('options',[]) if o.get('available',True) and not o.get('locked')]
    try:
        identity=entry['entity_id']
        if identity in REVIEWED:
            if entry['provenance']['source_sha256']!=REVIEWED[identity]: raise ValueError('规则源码已变化，需要重新审核适配器')
            graph,start=flower(entry,event) if identity.endswith('COLOSSAL_FLOWER') else tablet(entry,event,deck,context,entries)
            by_handler={options[o['text_key']]['handler']:o for o in live if o.get('text_key') in options}
            expected={r['option_id'] for r in graph[start]}
            if set(by_handler)!=expected: raise ValueError('当前可用选项与预期阶段不一致')
            for row in graph[start]:
                selected=by_handler[row['option_id']]; row.update(action_id=selected.get('action_id'),title=selected.get('title'))
        else:
            graph={'current':[]};start='current'
            for option in live:
                row=dict(option_id=option.get('option_id'),action_id=option.get('action_id'),title=option.get('title'))
                try:
                    reference=options[option['text_key']]
                    row['effects']=terminal_effects(reference,model,variables(event))
                except (KeyError,ValueError) as exc:
                    row.update(effects=[dict(kind='unmodeled_source_option')],unknown_reason=str(exc))
                graph[start].append(row)
        result=evaluate_graph(graph,start,deck,context,entries,policy,max_depth=min(max_steps,20))
        result.update(supported=True,source=entry['provenance'],adapter_version=VERSION,
                      validation='source_checked; live_transition_pending',
                      limitations=['仅模型范围内的收益评分，不是胜率','每次选择后重新读取游戏状态','未知效果阻止完整比较'])
        for row in result['candidates']:
            declared=next(r for r in graph[start] if r['option_id']==row['option_id'])
            row.update(action_id=declared.get('action_id'),title=declared.get('title'))
        return result
    except (KeyError,ValueError,TypeError) as exc:
        return dict(base,reason=str(exc))
