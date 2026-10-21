"""Visible atomic effects and explicitly supplied probabilistic event outcomes."""
import re
from math import isfinite
from dataclasses import replace
from autoslay.evaluation.run import deck_value,card_features,unmodeled
from autoslay.rules.registry import clean

VERSION='1.2.0'

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

def effect_value(effects,deck,context,entries,policy,*,relics=()):
    from autoslay.evaluation.strategic import RunResources
    from autoslay.rules.run_commands import RunCommands
    before=RunResources(context.hp,context.max_hp,context.gold,dead=context.hp==0,deck=tuple(deck),relics=tuple(relics))
    state=before;commands=RunCommands(references=entries);trace=[];gaps=[]
    for effect in effects:
        kind=effect.get('kind')
        operation={'lose_gold':'pay_gold','add_card':'add_to_deck'}.get(kind,kind)
        command=dict(operation=operation)
        if kind in {'remove_card','upgrade_card'}:command['instance_id']=effect.get('instance_id')
        elif kind=='add_card':command['card']=effect['card']
        else:command['amount']=effect.get('amount',0)
        if kind=='gain_max_hp' and effect.get('also_heals') is False:
            raise ValueError('GainMaxHp includes healing; use a defined SetMaxHp command for other behavior')
        result=commands.execute(state,command,source='structured_event_effect')
        state=result.state;trace.extend(result.trace);gaps.extend(result.gaps)
        if gaps or state.dead or state.cleared:break
    result=transition_value(before,state,context,entries,policy)
    if gaps:raise ValueError(';'.join(gaps))
    return dict(result,command_trace=trace)


def transition_value(before,after,context,entries,policy,*,interactions=None):
    """Price an executed state change, never interpret an entity's effects."""
    old=replace(context,hp=before.hp,max_hp=before.max_hp,gold=before.gold)
    new=replace(context,hp=after.hp,max_hp=after.max_hp,gold=after.gold)
    first=deck_value(list(before.deck),old,entries,policy,interactions)
    last=deck_value(list(after.deck),new,entries,policy,interactions)
    if before.deck!=after.deck and (first['unknown_cards'] or last['unknown_cards']):
        raise ValueError('Unmodeled deck value after actual card change')
    utility=((after.hp-before.hp)*policy.hp_weight*context.urgency+
        (after.max_hp-before.max_hp)*policy.max_hp_weight+(after.gold-before.gold)*policy.gold_weight+
        last['utility_points']-first['utility_points'])
    if before.relics!=after.relics:raise ValueError('Relic continuation value unpriced')
    if before.potions!=after.potions or before.potion_capacity!=after.potion_capacity:
        raise ValueError('Potion inventory continuation value unpriced')
    return dict(utility=utility,fatal=after.dead,hp_after=after.hp,max_hp_after=after.max_hp,
        gold_after=after.gold,deck_after=list(after.deck),warnings=last['warnings'])

def evaluate_events(options,deck,context,entries,policy,*,relics=()):
    evaluated=[]
    for option in options:
        if not option.get('available',True) or option.get('locked'): continue
        output=dict(option_id=option.get('option_id'),action_id=option.get('action_id'),
                    title=option.get('title'),description=option.get('description'),hover_tips=option.get('hover_tips',[]))
        if option.get('will_kill_player'):
            evaluated.append(dict(**output,utility=None,known_fatal=True,reason='game explicitly marks lethal')); continue
        effects=option.get('effects')
        outcomes=option.get('outcomes')
        if outcomes is None and effects is not None: outcomes=[dict(probability=1,effects=effects)]
        if not outcomes or option.get('hover_tips_complete') is False:
            evaluated.append(dict(**output,utility=None,reason='incomplete visible effects; Codex must interpret hover descriptions')); continue
        try:
            probabilities=[o['probability'] for o in outcomes]
            if any(type(p) not in (int,float) or not isfinite(p) or not 0<=p<=1 for p in probabilities) or abs(sum(probabilities)-1)>1e-9:
                raise ValueError('Outcome probabilities must sum to one')
            results=[dict(probability=o['probability'],**effect_value(o['effects'],deck,context,entries,policy,relics=relics)) for o in outcomes]
            expected=sum(r['probability']*r['utility'] for r in results)
            downside=sum(r['probability']*max(0,-r['utility']) for r in results)
            fatal=sum(r['probability'] for r in results if r['fatal'])
            evaluated.append(dict(**output,utility=expected-downside*policy.risk_weight,expected_utility=expected,
                downside_penalty=downside*policy.risk_weight,fatal_probability=fatal,known_fatal=fatal>0,
                outcomes=results,effects_basis='structured_common_commands'))
        except (KeyError,ValueError,TypeError) as exc:
            evaluated.append(dict(**output,utility=None,reason=str(exc)))
    evaluated.sort(key=lambda r:(r.get('utility') is not None,not r.get('known_fatal',False),r.get('utility') or 0),reverse=True)
    return dict(module_version=VERSION,candidates=evaluated,execution_authorized=False,
                units='situational heuristic utility points; supplied probabilities only',
                limitations=['unknown effects are not assigned zero; no invented event outcome probabilities'])
