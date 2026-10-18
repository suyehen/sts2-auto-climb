"""Catalog-wide semantic programs, distinct from verified simulation adapters.

No entity allowlist. Every variant and every clause is retained, including
conditions, costs, unknown quantities and source-defined operations. Numeric
proxies use only recognized terms; they never certify a simulation rule.
"""
from copy import deepcopy
from functools import lru_cache
import json
import re
from autoslay.decision.cache import digest

VERSION='1.0.0'
CATEGORIES=('card','potion','relic')
NUMBER=r'\d+(?:\.\d+)?'
POWER_NAMES={
    '力量':'strength','敏捷':'dexterity','活力':'vigor','格挡':'block',
    '易伤':'vulnerable','虚弱':'weak','中毒':'poison','灾厄':'doom','荆棘':'thorns',
    '集中':'focus','再生':'regeneration','覆甲':'plated_armor','缓冲':'buffer',
    '无实体':'intangible','仪式':'ritual','人工制品':'artifact',
    'Strength':'strength','Dexterity':'dexterity','Vulnerable':'vulnerable',
    'Weak':'weak','Poison':'poison','Block':'block',
}
TOPICS={
    'draw':r'抽.+?牌|\bDraw\b', 'exhaust':r'消耗|\bExhaust\b',
    'discard':r'丢弃|\bDiscard\b','retain':r'保留|\bRetain\b',
    'upgrade':r'升级|\bUpgrade\b','transform':r'变化|\bTransform\b',
    'generate_card':r'加入.+?牌|添加.+?牌|\badd\b.+?\bcard',
    'copy':r'复制|\bcopy\b','cost_change':r'免费|耗能|费用|\bcost\b',
    'summon':r'召唤|奥斯提|\bsummon\b','orbs':r'充能球|\borb\b',
    'forge':r'铸造|\bforge\b','stars':r'\{STAR\}|\bstars?\b',
    'energy':r'\{ENERGY\}|能量|\bEnergy\b','healing':r'回复|恢复|\bheal\b',
    'max_hp':r'最大生命|\bmax(?:imum)?\s+HP\b','gold':r'金币|\bGold\b',
    'potion_inventory':r'药水|\bpotion\b','relic_reward':r'遗物|\brelic\b',
    'map':r'地图|路线|房间|楼层|阶段|\bmap\b','rest':r'休息|烹饪|添火|\brest\b',
    'shop':r'商店|商品|商人|\bshop\b|\bmerchant\b',
    'debuff':r'负面|易伤|虚弱|中毒|灾厄','replay':r'重放|额外打出|多打出|\breplay\b',
    'damage':r'伤害|\bdamage\b','block':r'格挡|\bBlock\b',
}


def normalize_text(text):
    text=re.sub(r'\[img\][^\[]*?energy_icon\.png\[/img\]','{ENERGY}',text or '',flags=re.I)
    text=re.sub(r'\[img\][^\[]*?star_icon\.png\[/img\]','{STAR}',text,flags=re.I)
    # Other icons are evidence, not zero resources.
    text=re.sub(r'\[img\]([^\[]*)\[/img\]',r'{ICON:\1}',text,flags=re.I)
    return re.sub(r'\[/?[a-zA-Z][^\]]*\]','',text).strip()


def trigger_for(text,category):
    trigger='on_play' if category=='card' else 'on_use' if category=='potion' else 'unspecified'
    # The literal condition remains authoritative even for classified triggers.
    for name,pattern in (
        ('pickup',r'拾起时|Upon pickup|When obtained'),
        ('combat_end',r'战斗结束|战斗胜利|战斗后|end of (?:each )?combat'),
        ('combat_start',r'战斗开始|combat start|start of (?:each )?combat'),
        ('turn_end',r'回合结束|end of (?:your |each )?turn'),
        ('turn_start',r'回合开始|start of (?:your |each )?turn'),
        ('enter_shop',r'进入商店|进入.+?商店|遇见商人'),
        ('enter_rest',r'进入休息处|到达休息处'),
        ('rest',r'休息时|休息处休息|每当你休息'),
        ('hp_loss',r'每当.+?失去生命|每次失去生命|损失生命'),
        ('enemy_death',r'每当.+?敌人死亡|每当.+?敌人死于'),
        ('card_exhausted',r'每当.+?消耗|每消耗'),
        ('card_discarded',r'每.+?丢弃'),
        ('card_played',r'每当.+?打出|每打出'),
        ('card_acquired',r'每当.+?获得.+?牌|每当.+?加入.+?牌组'),
        ('floor_entered',r'每攀爬|每经过.+?楼层'),
        ('every_combat_reward',r'敌人额外掉落'),
    ):
        if re.search(pattern,text,re.I):trigger=name;break
    number={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}
    match=re.search(r'第([0-9一二两三四五六七八九十]+)(?:个)?回合',text)
    turn=(int(match[1]) if match[1].isdigit() else number.get(match[1])) if match else None
    if turn and ('回合结束' in text or '回合开始' in text):
        trigger='turn_end' if '回合结束' in text else 'turn_start'
    period=re.search(r'每(?:打出|消耗)?(\d+)(?:个)?(?:回合|张)',text)
    conditions=re.search(r'如果|若|当.+?时|每有|每当|每次|每打出|每消耗|\bif\b|\bwhenever\b',text,re.I)
    return dict(kind=trigger,turn=turn,period=int(period[1]) if period else None,
                conditional=bool(conditions),condition_text=text if conditions else None,
                limited=bool(re.search(r'第一次|首次|仅能|最多|接下来|前\d|后熄灭|仅一次|once|next \d',text,re.I)))


def compile_clause(text,category):
    """Extract terms with spans. Unrecognized residue is kept as an operation."""
    trigger=trigger_for(text,category);effects=[];spans=[]
    target=('all_creatures' if re.search(r'所有玩家和敌人',text) else
            'all_enemies' if re.search(r'所有敌人|all enemies',text,re.I) else
            'random_enemy' if re.search(r'随机.+?敌人|random enemy',text,re.I) else
            'enemy' if re.search(r'敌人|给予|给于|\bApply\b',text,re.I) else 'self_or_declared_target')
    def extract(pattern,kind,amount=None,scale='flat'):
        for match in re.finditer(pattern,text,re.I):
            if any(match.start()<end and match.end()>start for start,end in spans):continue
            value=amount(match) if callable(amount) else amount if amount is not None else float(match['n'])
            if isinstance(value,float) and value.is_integer():value=int(value)
            effects.append(dict(kind=kind,amount=value,scale=scale,target=target,
                                evidence=match[0],span=[match.start(),match.end()]))
            spans.append(match.span())
    extract(r'(?:回复|恢复|Heal\s+)(?:你(?:的)?)?最大生命值的(?P<n>'+NUMBER+r')%','heal',scale='max_hp_percent')
    extract(r'(?:将你回复到|回复到)(?:你(?:的)?)?最大生命值的(?P<n>'+NUMBER+r')%','set_hp',scale='max_hp_percent')
    extract(r'(?:获得|提升|增加|Gain\s+)(?P<n>'+NUMBER+r')(?:点)?最大生命(?:值)?','max_hp')
    extract(r'(?:最大生命(?:值)?(?:提升|增加))(?P<n>'+NUMBER+r')','max_hp')
    extract(r'(?:失去|降低|减少|Lose\s+)(?P<n>'+NUMBER+r')(?:点)?最大生命(?:值)?','lose_max_hp')
    extract(r'(?:回复|恢复|Heal\s+)(?P<n>'+NUMBER+r')(?:点生命(?:值)?|\s+HP)','heal')
    extract(r'(?:失去|Lose\s+)(?P<n>'+NUMBER+r')(?:点生命(?:值)?|\s+HP)','lose_hp')
    extract(r'(?:造成|Deal\s+)(?P<n>'+NUMBER+r')(?:点伤害|\s+damage)','damage')
    extract(r'(?:抽|Draw\s+)(?P<n>'+NUMBER+r')(?:张牌|\s+cards?)','draw')
    extract(r'(?:获得|额外掉落|Gain\s+)(?P<n>'+NUMBER+r')(?:枚|点)?(?:金币|\s+Gold)','gold')
    extract(r'(?:失去|花费|Lose\s+)(?P<n>'+NUMBER+r')(?:枚|点)?(?:金币|\s+Gold)','lose_gold')
    for label,kind in POWER_NAMES.items():
        extract(r'(?P<verb>获得|给予|给于|Gain\s+|Apply\s+)(?:所有敌人)?(?P<n>'+NUMBER+r')(?:点|层|\s+)?'+re.escape(label),kind)
    extract(r'(?:获得|额外获得|Gain\s+)(?:(?P<n>\d+))?(?P<icons>(?:\{ENERGY\})+)','energy',
            lambda m:int(m['n']) if m['n'] else m['icons'].count('{ENERGY}'))
    extract(r'(?:获得|Gain\s+)(?:(?P<n>\d+))?(?P<icons>(?:\{STAR\})+)','stars',
            lambda m:int(m['n']) if m['n'] else m['icons'].count('{STAR}'))
    repeat=re.search(r'(?:伤害|damage)[，,]?\s*(\d+)(?:次| times)',text,re.I)
    for effect in effects:effect['repeats']=int(repeat[1]) if effect['kind']=='damage' and repeat else 1
    residue=text
    for start,end in sorted(spans,reverse=True):residue=residue[:start]+residue[end:]
    # Only exact lifecycle wrappers are discarded. All other text is a gap.
    residue=re.sub(r'(?:在)?(?:你(?:的)?)?(?:每场|每个|每|本场|本|这个)?(?:战斗|回合)(?:的)?(?:开始|结束)(?:时|后)?','',residue)
    residue=re.sub(r'拾起时|Upon pickup|在你的第\d+回合(?:开始|结束)时|在第\d+回合(?:开始|结束)时','',residue,flags=re.I)
    residue=re.sub(r'对所有敌人|额外|你(?:的)?|所有敌人|[\s，,。.;；：:]+','',residue)
    if trigger['kind']=='every_combat_reward' and residue=='敌人':residue=''
    if category=='card':residue=re.sub(r'消耗|Exhaust','',residue,flags=re.I)
    no_effect=bool(re.fullmatch(r'(?:没有任何作用)',residue))
    return dict(text=text,trigger=trigger,target=target,effects=effects,
        topics=sorted(k for k,v in TOPICS.items() if re.search(v,text,re.I)),
        unresolved_text='' if no_effect else residue,
        no_effect_explicit=no_effect,
        random=bool(re.search(r'随机|random',text,re.I)),
        choice=bool(re.search(r'选择|任意|choose|select',text,re.I)))


def compile_entry(entry,source=None,rule_status='unmodeled'):
    category=entry['category']
    if category not in CATEGORIES:raise ValueError('Unsupported effect profile category')
    variants=entry.get('variants',[]) if category=='card' else [entry]
    programs=[]
    for variant in variants:
        text=normalize_text(variant.get('description',''))
        clauses=[compile_clause(t.strip(),category) for t in re.split(r'[。；;\n]+|(?<!\d)\.(?!\d)',text) if t.strip()]
        programs.append(dict(upgrade_level=variant.get('upgrade_level',0),
            description=variant.get('description',''),normalized_description=text,
            costs={k:variant.get(k) for k in ('energy_cost','costs_x','star_cost','costs_star_x')},
            card_type=variant.get('type'),target_type=variant.get('target_type',entry.get('target_type')),
            keywords=deepcopy(variant.get('keywords') or []),
            dynamic_variables=deepcopy(variant.get('dynamic_variables') or []),
            hover_tips=deepcopy(variant.get('hover_tips') or []),clauses=clauses,
            restrictions=[dict(kind=kind,evidence=text) for kind,pattern in (
                ('no_gold_gain',r'(?:不能|无法).{0,8}获得.{0,4}金币'),
                ('no_potion_gain',r'(?:不能|无法).{0,8}获得.{0,4}药水'),
                ('no_midturn_draw',r'回合进行中不再能抽'),
                ('no_rest',r'(?:不能|无法).{0,4}休息'),
                ('no_heal',r'(?:不能|无法).{0,4}(?:回复|恢复|治疗).{0,4}生命')) if re.search(pattern,text)],
            coverage=dict(description_present=bool(text),
                structured_effects=sum(len(c['effects']) for c in clauses),
                unresolved_clauses=sum(bool(c['unresolved_text']) for c in clauses),
                fully_parsed=bool(text) and all(not c['unresolved_text'] for c in clauses))))
    source=source or {}
    return dict(entity_id=entry['entity_id'],category=category,name=entry.get('name'),
        model_version=VERSION,reference_sha256=digest(entry),rarity=entry.get('rarity'),
        pool=entry.get('pool'),pools=deepcopy(entry.get('pools') or []),usage=entry.get('usage'),
        variants=programs,source=dict(model_type=entry.get('model_type'),
            source_sha256=source.get('source_sha256'),hooks=deepcopy(source.get('hooks') or []),
            commands=deepcopy(source.get('commands') or [])),
        simulation_status=rule_status,simulation_adapter_enabled=rule_status=='compatible',
        profile_scope='all catalog semantics; parsed terms plus retained source-defined clauses')


@lru_cache(maxsize=4096)
def _cached(encoded):return compile_entry(json.loads(encoded))


def profile_for(value,entries):
    entry=entries.get(value.get('entity_id'))
    if not entry or entry.get('category') not in CATEGORIES:return None
    profile=deepcopy(_cached(json.dumps(entry,ensure_ascii=False,sort_keys=True)))
    # Current formatted values override exported defaults for the observed variant.
    if value.get('description') is not None:
        changed=deepcopy(entry)
        if changed['category']=='card':
            for variant in changed.get('variants',[]):
                if variant.get('upgrade_level',0)==value.get('upgrade_level',0):
                    variant.update({k:value[k] for k in ('description','dynamic_variables','energy_cost','star_cost','costs_x','costs_star_x','target_type','type','hover_tips') if k in value})
        else:changed.update({k:value[k] for k in ('description','dynamic_variables','target_type','hover_tips') if k in value})
        observed=compile_entry(changed)
        profile['variants']=observed['variants'];profile['observed_override_sha256']=digest(changed)
    return profile


def selected_variant(profile,level=0):
    return next((v for v in profile['variants'] if v['upgrade_level']==level),None)


def compile_catalog(entries,inventory=None,statuses=None):
    """Complete input registry. Duplicate identities are an error, never overwritten."""
    sources={r['entity_id']:r for r in (inventory or {}).get('entries',[])}
    statuses=statuses or {};profiles={}
    for entry in entries:
        identity=entry['entity_id']
        if identity in profiles:raise ValueError('Duplicate effect identity: '+identity)
        profiles[identity]=compile_entry(entry,sources.get(identity),statuses.get(identity,'unmodeled'))
    counts={}
    for category in CATEGORIES:
        rows=[p for p in profiles.values() if p['category']==category]
        variants=[v for p in rows for v in p['variants']]
        counts[category]=dict(entities=len(rows),profiles=len(rows),variants=len(variants),
            description_present=sum(v['coverage']['description_present'] for v in variants),
            fully_parsed_variants=sum(v['coverage']['fully_parsed'] for v in variants),
            source_linked=sum(bool(p['source']['source_sha256']) for p in rows),
            simulation_adapters=sum(p['simulation_adapter_enabled'] for p in rows),
            unresolved_clauses=sum(v['coverage']['unresolved_clauses'] for v in variants))
    return dict(schema_version='effect-profiles/1',module_version=VERSION,profiles=profiles,counts=counts,
                entity_count=len(profiles),data_coverage_complete=True,full_simulation_support=False)


def resource_terms(profile,level=0):
    """Immediate unconditional terms only. No trigger counts invented."""
    variant=selected_variant(profile,level)
    if variant is None:return []
    return [dict(e,clause=c['text']) for c in variant['clauses']
            if c['trigger']['kind'] in {'on_play','on_use','pickup'}
            and not c['trigger']['conditional'] and not c['random'] and not c['choice']
            for e in c['effects']]


def interaction_edges(profiles):
    """Mechanism candidates, not additive synergy scores or required deck roles."""
    events={'exhaust':'card_exhausted','discard':'card_discarded','self_hp_loss':'hp_loss'}
    rows=[]
    for left in profiles:
        producers=set(t for v in left['variants'] for c in v['clauses'] for t in c['topics'])
        if any(e['kind']=='lose_hp' for v in left['variants'] for c in v['clauses'] for e in c['effects']):
            producers.add('self_hp_loss')
        for right in profiles:
            if left['entity_id']==right['entity_id']:continue
            triggers=set(c['trigger']['kind'] for v in right['variants'] for c in v['clauses'])
            matches=sorted(topic for topic,event in events.items() if topic in producers and event in triggers)
            supplied={e['kind'] for v in left['variants'] for c in v['clauses'] for e in c['effects']}
            if 'energy' in supplied and any((v['costs'].get('energy_cost') or 0)>0 or v['costs'].get('costs_x') for v in right['variants']):
                matches.append('energy_supply_and_cost_competition')
            if 'strength' in supplied and any(v['card_type']=='Attack' for v in right['variants']):
                matches.append('strength_and_attack_scaling')
            if 'dexterity' in supplied and any(e['kind']=='block' for v in right['variants'] for c in v['clauses'] for e in c['effects']):
                matches.append('dexterity_and_card_block_scaling')
            if matches:rows.append(dict(source=left['entity_id'],target=right['entity_id'],
                mechanisms=matches,status='conditional_interaction_requires_tactical_or_event_evaluation'))
    return rows
