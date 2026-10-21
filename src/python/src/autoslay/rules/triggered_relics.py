"""Shared post-play counter dispatcher. No relic identity branches."""
from dataclasses import replace
from autoslay.decision.contracts import TriggeredRelic,UnsupportedEffect
from autoslay.rules.registry import KnowledgeRules,REACTIVE_EVENTS


def read_triggered(relic,turn,rules=None):
    rules=rules or KnowledgeRules()
    identity=relic['entity_id']
    rule=rules.definitions.get(identity)
    if rule is None:return ()
    triggers=[t for t in rule.triggers if t.event in REACTIVE_EVENTS]
    if not triggers:return ()
    if type(turn) is not int or turn<1:raise UnsupportedEffect('Observed player turn required for counter: '+identity)
    variables=relic.get('dynamic_variables') or rules.references[identity].get('dynamic_variables') or []
    result=[]
    for trigger in triggers:
        metadata=trigger.counter;period=metadata['period']
        if isinstance(period,dict):period=KnowledgeRules.variable(variables,period['variable'])
        counter=0 if period==1 else relic.get('counter')
        fields=(relic.get('runtime_state') or {}).get('fields') or {}
        remaining=None
        if metadata.get('once_field'):
            observed=fields.get(metadata['once_field'])
            if type(observed) is not bool:raise UnsupportedEffect('Observed one-shot relic state required: '+identity)
            remaining=0 if observed else 1
        if metadata.get('field') in fields:
            observed=fields[metadata['field']]
            if type(observed) is not int or observed<0 or not 1<=period<=1000:
                raise UnsupportedEffect('Invalid observed relic progress: '+identity)
            counter=observed%period
        effects=rules.effects(trigger.effects,'',variables=variables)
        result.append(TriggeredRelic(identity,metadata['card_type'],period,counter,metadata['reset']=='turn',turn,
                                     tuple((e.kind,e.amount,e.repeats) for e in effects),remaining))
    return tuple(result)


def reset_for_turn(state):
    if not state.triggered_relics:return state
    if type(state.turn) is not int or state.turn<1:raise UnsupportedEffect('Relic counter reset requires player turn')
    return replace(state,triggered_relics=tuple(replace(r,counter=0,last_reset_turn=state.turn)
        if r.reset_each_turn and r.last_reset_turn<state.turn else r for r in state.triggered_relics))


def advance_card_relic(state,index,card_type):
    # Hook.AfterCardPlayed deliberately still dispatches after a kill. The
    # counter advances; resource/power commands then reject combat-ending gains.
    if state.hp<=0:return state,()
    state=reset_for_turn(state)
    if card_type not in {'Attack','Skill','Power','Status','Curse'}:
        raise UnsupportedEffect('Observed card type required for post-play counter')
    tracked=list(state.triggered_relics);relic=tracked[index];effects=()
    if relic.remaining_triggers==0 or relic.remaining_triggers is not None and state.combat_won:return state,()
    if relic.card_type==card_type:
        tracked[index]=replace(relic,counter=(relic.counter+1)%relic.period)
        if tracked[index].counter==0:
            effects=relic.effects
            if relic.remaining_triggers is not None:
                tracked[index]=replace(tracked[index],remaining_triggers=relic.remaining_triggers-1)
    return replace(state,triggered_relics=tuple(tracked)),effects
