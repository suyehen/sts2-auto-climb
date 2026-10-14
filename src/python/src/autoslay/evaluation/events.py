"""Visible atomic effects and explicitly supplied probabilistic event outcomes."""
import re
from math import isfinite
from dataclasses import replace
from autoslay.evaluation.run import deck_value,card_features,unmodeled
from autoslay.evaluation.resources import item_value
from autoslay.rules.registry import clean

VERSION='1.1.0'

def parse_visible(text):
    """Strict small grammar. Residual text/conditions/chance mean unknown, never assumed zero."""
    text=clean(text).strip()
    patterns=[('lose_hp',r'(?:失去|Lose\s+)(\d+)(?:点生命|\s+HP)'),
              ('heal',r'(?:回复|恢复|Heal\s+)(\d+)(?:点生命|\s+HP)'),
              ('gain_gold',r'(?:获得|Gain\s+)(\d+)(?:枚金币|金币|\s+Gold)'),
              ('lose_gold',r'(?:失去|Lose\s+)(\d+)(?:枚金币|金币|\s+Gold)')]
    spans=[]; effects=[]
    for kind,pattern in patterns:
        for match in re.finditer(pattern,text,re.I):
            spans.append(match.span()); effects.append((match.start(),dict(kind=kind,amount=int(match[1]))))
    residue=text
    for start,end in sorted(spans,reverse=True): residue=residue[:start]+residue[end:]
    residue=re.sub(r'[\s。，；、.!;,]+','',residue)
    if residue or not effects: return None
    return [e for _,e in sorted(effects)]

def effect_value(effects,deck,context,entries,policy):
    modified=list(deck); hp=context.hp; maximum=context.max_hp; gold=context.gold; utility=0; warnings=[]
    base=deck_value(deck,context,entries,policy)['utility_points']
    for effect in effects:
        kind=effect.get('kind'); amount=effect.get('amount',0)
        if type(amount) not in (int,float) or not isfinite(amount) or amount<0: raise ValueError('Invalid effect amount')
        if type(amount) is not int: raise ValueError('Integral event resource amount required')
        current=replace(context,hp=hp,max_hp=maximum,gold=gold)
        if kind=='lose_hp': hp-=amount; utility-=amount*policy.hp_weight*current.urgency
        elif kind=='heal':
            actual=min(amount,max(0,maximum-hp)); hp+=actual; utility+=actual*policy.hp_weight*current.urgency
        elif kind=='gain_max_hp':
            maximum+=amount
            actual=amount if effect.get('also_heals') else 0
            if 'also_heals' not in effect: raise ValueError('Explicit maxHP heal behavior required')
            hp+=actual; utility+=amount*policy.max_hp_weight+actual*policy.hp_weight*current.urgency
        elif kind=='lose_max_hp':
            if amount>=maximum: raise ValueError('Lethal/maxHP clamp behavior needs specific rule')
            maximum-=amount; lost=max(0,hp-maximum); hp=min(hp,maximum)
            utility-=amount*policy.max_hp_weight+lost*policy.hp_weight*current.urgency
        elif kind=='gain_gold': gold+=amount; utility+=amount*policy.gold_weight
        elif kind=='lose_gold': gold-=amount; utility-=amount*policy.gold_weight
        elif kind=='add_card':
            if unmodeled(card_features(effect['card'],entries)): raise ValueError('Unmodeled card reward')
            modified.append(effect['card'])
        elif kind in {'remove_card','upgrade_card'}:
            matched=[i for i,c in enumerate(modified) if c.get('instance_id')==effect.get('instance_id')]
            if len(matched)!=1: raise ValueError('Specific card instance required')
            if unmodeled(card_features(modified[matched[0]],entries)): raise ValueError('Unmodeled card change')
            if kind=='remove_card':
                from autoslay.knowledge_base.templates import canonical
                if 'Eternal' in canonical(modified[matched[0]],entries)[1].get('keywords',[]): raise ValueError('Eternal card cannot be removed')
                modified.pop(matched[0])
            else:
                from autoslay.knowledge_base.templates import canonical
                value=dict(modified[matched[0]]); value['upgrade_level']=value.get('upgrade_level',0)+1
                canonical(value,entries); modified[matched[0]]=value
        elif kind=='gain_item':
            valued=item_value(effect['item'],current,modified,entries,policy)
            if valued['utility'] is None: raise ValueError('Unmodeled item reward')
            utility+=valued['utility']; warnings.append(valued['basis'])
        else: raise ValueError('Unmodeled effect: '+str(kind))
        if hp<=0: return dict(utility=utility, fatal=True, hp_after=0,max_hp_after=maximum,gold_after=gold, warnings=warnings,deck_after=modified)
        if gold<0: raise ValueError('Unaffordable gold effect')
    if not modified: raise ValueError('Empty deck after effect')
    result=deck_value(modified,replace(context,hp=hp,max_hp=maximum,gold=gold),entries,policy)
    utility+=result['utility_points']-base
    return dict(utility=utility,fatal=False,hp_after=hp,max_hp_after=maximum,gold_after=gold,warnings=warnings+result['warnings'],deck_after=modified)

def evaluate_events(options,deck,context,entries,policy):
    evaluated=[]
    for option in options:
        if not option.get('available',True) or option.get('locked'): continue
        output=dict(option_id=option.get('option_id'),action_id=option.get('action_id'),
                    title=option.get('title'),description=option.get('description'),hover_tips=option.get('hover_tips',[]))
        if option.get('will_kill_player'):
            evaluated.append(dict(**output,utility=None,known_fatal=True,reason='game explicitly marks lethal')); continue
        effects=option.get('effects')
        if effects is None: effects=parse_visible(option.get('description',''))
        outcomes=option.get('outcomes')
        if outcomes is None and effects is not None: outcomes=[dict(probability=1,effects=effects)]
        if not outcomes or option.get('hover_tips_complete') is False:
            evaluated.append(dict(**output,utility=None,reason='incomplete visible effects; Codex must interpret hover descriptions')); continue
        try:
            probabilities=[o['probability'] for o in outcomes]
            if any(type(p) not in (int,float) or not isfinite(p) or not 0<=p<=1 for p in probabilities) or abs(sum(probabilities)-1)>1e-9:
                raise ValueError('Outcome probabilities must sum to one')
            results=[dict(probability=o['probability'],**effect_value(o['effects'],deck,context,entries,policy)) for o in outcomes]
            expected=sum(r['probability']*r['utility'] for r in results)
            downside=sum(r['probability']*max(0,-r['utility']) for r in results)
            fatal=sum(r['probability'] for r in results if r['fatal'])
            evaluated.append(dict(**output,utility=expected-downside*policy.risk_weight,expected_utility=expected,
                downside_penalty=downside*policy.risk_weight,fatal_probability=fatal,known_fatal=fatal>0,
                outcomes=results,effects_basis='caller_structured' if 'effects' in option or 'outcomes' in option else 'strict_visible_atomic_parse'))
        except (KeyError,ValueError,TypeError) as exc:
            evaluated.append(dict(**output,utility=None,reason=str(exc)))
    evaluated.sort(key=lambda r:(r.get('utility') is not None,not r.get('known_fatal',False),r.get('utility') or 0),reverse=True)
    return dict(module_version=VERSION,candidates=evaluated,execution_authorized=False,
                units='situational heuristic utility points; supplied probabilities only',
                limitations=['unknown effects are not assigned zero; no invented event outcome probabilities'])
