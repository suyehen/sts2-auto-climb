"""Bounded combination valuation for observed selections with declared effects."""
from dataclasses import asdict
from itertools import combinations
from autoslay.decision.contracts import UnsupportedEffect
from autoslay.rules.selections import SelectionRules
from autoslay.rules.registry import GAME_VERSION,IMPLEMENTATION_SHA256
from autoslay.evaluation.strategic import RunResources
from autoslay.evaluation.events import transition_value

VERSION='1.0.0'


def declared_card_proxy(raw):
    """Small fixed-effect proxy for local combat choices; no text fallback."""
    from autoslay.rules.registry import KnowledgeRules
    from autoslay.rules.amounts import has_state
    from autoslay.decision.normalize import card_view
    from autoslay.decision.contracts import CombatState
    rules=KnowledgeRules();definition=rules.definition(raw['entity_id'],'card')
    if any(t.event!='on_play' for t in definition.triggers):raise UnsupportedEffect('Selection card lifecycle value unpriced')
    if any(has_state(e.amount) or has_state(e.repeats) for e in definition.effects('on_play')):
        raise UnsupportedEffect('Selection state-dependent card value unpriced')
    card=card_view(raw);compiled=rules.card(card,CombatState(80,80,0,3,(),(),()))
    weights={'damage':1,'unpowered_damage':1,'block':1.5,'unpowered_block':1.5,'draw':3,'strength':3,'energy':4,'vulnerable':1}
    if any(e.kind not in weights for e in compiled.effects):raise UnsupportedEffect('Selection card effect value unpriced')
    if card.cost<0 or card.star_cost>0:raise UnsupportedEffect('Selection variable resource value unpriced')
    return sum(weights[e.kind]*e.amount*e.repeats for e in compiled.effects)-card.cost-(8 if card.card_type in {'Curse','Status'} else 0)


def next_operation(selection,desired,actions):
    """One existing UI operation; never invent the selector's later actions."""
    legal={a['action_id']:a for a in actions if a.get('allows_no_target')}
    rows=selection.get('cards') or []
    def operation(row):
        aid=row.get('action_id')
        return aid if row.get('available') is True and aid in legal else None
    selected={r['card']['instance_id'] for r in rows if r.get('selected') is True}
    target=set(desired)
    if selection.get('preview') is True and selected!=target:return None
    remove=selected-target
    if remove:
        if selection.get('interaction')!='toggle':return None
        return next((aid for row in rows if row['card']['instance_id'] in remove and (aid:=operation(row))),None)
    add=target-selected
    if add:return next((aid for row in rows if row['card']['instance_id'] in add and (aid:=operation(row))),None)
    types=('confirm_card_selection','confirm_combat_selection','preview_card_selection')
    for kind in types:
        for control in selection.get('controls') or []:
            if control.get('type')==kind and (aid:=operation(control)):return aid
    return None


def evaluate_selection(snapshot,context,entries,policy,*,max_combinations=None):
    selection=snapshot['state'].get('card_selection') or {}
    base=dict(module_version=VERSION,supported=False,candidates=[],combinations=[],execution_authorized=False,
        comparison_complete=False,search_truncated=False)
    if snapshot.get('game_version')!=GAME_VERSION or snapshot.get('game_assembly_sha256')!=IMPLEMENTATION_SHA256:
        return dict(base,reason='selection_rule_version_or_hash_mismatch')
    budget=policy.shop_nodes if max_combinations is None else max_combinations
    if type(budget) is not int or not 1<=budget<=100000:raise ValueError('Invalid selection combination budget')
    rules=SelectionRules(references=entries)
    try:
        record,variables=rules.bind(selection)
        low=selection.get('min_select');high=selection.get('max_select')
        if type(low) is not int or type(high) is not int or not 0<=low<=high<=500:
            raise UnsupportedEffect('Observed selection count bounds required')
        if type(selection.get('cancelable')) is not bool or type(selection.get('require_manual_confirmation')) is not bool:
            raise UnsupportedEffect('Observed selection confirmation/cancellation constraints required')
        if selection.get('interaction') not in {'toggle','select_only'}:raise UnsupportedEffect('Observed selection interaction required')
        rows=selection.get('cards') or []
        if not isinstance(rows,list) or not rows:raise UnsupportedEffect('Observed selection candidates required')
        eligible=[];instances=set()
        for row in rows:
            card=row.get('card') or {};identity=card.get('instance_id')
            if not isinstance(identity,str) or not identity or identity in instances:raise UnsupportedEffect('Unique selection instances required')
            instances.add(identity)
            if row.get('eligible') is not True:raise UnsupportedEffect('Complete observed candidate eligibility required')
            eligible.append(card)
        if high>len(eligible) or low>len(eligible):raise UnsupportedEffect('Selection counts exceed observed candidate pool')
        player=snapshot['state']['player']
        if (player.get('creature') or {}).get('powers'):
            raise UnsupportedEffect('Selection player power hooks not modeled')
        initial=RunResources(context.hp,context.max_hp,context.gold,dead=context.hp==0,
            deck=tuple(player.get('deck') or []),relics=tuple(player.get('relics') or []))
    except (ValueError,KeyError,TypeError) as exc:return dict(base,reason=str(exc))
    evaluated=[];truncated=False;count=0
    for size in range(low,high+1):
        for group in combinations(eligible,size):
            if count>=budget:truncated=True;break
            count+=1
            identities=[card['instance_id'] for card in group]
            output=dict(instances=identities,known=False,utility=None,mechanism_gaps=[],command_trace=[])
            try:
                result=rules.apply(initial,record,variables,group)
                output.update(command_trace=list(result.trace),mechanism_gaps=list(result.gaps),after=asdict(result.state))
                if initial.deck!=result.state.deck and any(c.get('enchantment_id') or c.get('affliction_id') for c in initial.deck):
                    raise UnsupportedEffect('Deck continuation value with card modifiers unpriced')
                if not result.gaps:output.update(transition_value(initial,result.state,context,entries,policy),known=True)
            except (ValueError,KeyError,TypeError) as exc:output['mechanism_gaps'].append(str(exc))
            output['action_id']=next_operation(selection,identities,snapshot['state'].get('legal_actions') or [])
            evaluated.append(output)
        if truncated:break
    # Cancel is the callback's empty-return path, not a free zero-valued option
    # invented for selectors that cannot be cancelled.
    cancel=next((c for c in selection.get('controls') or [] if c.get('type')=='close_card_selection'
        and c.get('available') is True and c.get('action_id')),None)
    legal={a['action_id'] for a in snapshot['state'].get('legal_actions') or [] if a.get('allows_no_target')}
    if selection['cancelable'] and cancel and cancel['action_id'] in legal:
        output=dict(instances=[],kind='cancel',known=False,utility=None,action_id=cancel['action_id'],mechanism_gaps=[])
        try:
            result=rules.apply(initial,record,variables,())
            output.update(command_trace=list(result.trace),mechanism_gaps=list(result.gaps),after=asdict(result.state))
            if not result.gaps:output.update(transition_value(initial,result.state,context,entries,policy),known=True)
        except (ValueError,KeyError,TypeError) as exc:output['mechanism_gaps'].append(str(exc))
        evaluated.append(output)
    evaluated.sort(key=lambda r:(not r['known'],-r['utility'] if r['known'] else 0,tuple(r['instances'])))
    # Several combinations may share a next toggle. Keep their separate outcomes
    # while exposing only the best known value for each immediate real action.
    candidates=[];seen=set()
    for row in evaluated:
        if row.get('action_id') and row['action_id'] not in seen:
            seen.add(row['action_id']);candidates.append(dict(row))
    return dict(base,supported=True,comparison_complete=bool(evaluated) and not truncated and all(r['known'] for r in evaluated),
        candidates=candidates,combinations=evaluated,search_truncated=truncated,
        selection_model_id=record['entity_id'],rule_fingerprint=rules.models.fingerprint,
        source=dict(record['source']),execution_source='models_json_and_common_commands',
        forecast_scope='observed filtered pool and declared caller continuation',
        limitations=['heuristic deck/resource value is not win probability','only next action is bound; reobserve after every UI operation',
            'unmodeled hooks and budget truncation prevent complete comparisons'])
