"""Native phase triggers; observed starts are never replayed."""
from dataclasses import replace
from autoslay.decision.contracts import ScheduledRelic,UnsupportedEffect
from autoslay.rules.registry import KnowledgeRules,PHASES
from autoslay.simulation.atomic_effects import apply_atomic

VERSION = '2.0.0'


def read_scheduled(relic,turn,rules=None):
    rules=rules or KnowledgeRules();identity=relic['entity_id']
    rule=rules.definitions.get(identity)
    if rule is None or not any(t.event in PHASES for t in rule.triggers):return ()
    if type(turn) is not int or turn<1:raise UnsupportedEffect('Observed player turn required for '+identity)
    variables=relic.get('dynamic_variables') or rules.references[identity].get('dynamic_variables') or []
    from autoslay.rules.amounts import has_state
    result=[]
    for ti,trigger in enumerate(rule.triggers):
        if trigger.event not in PHASES:continue
        for ei,spec in enumerate(trigger.effects):
            dynamic=has_state(spec.amount) or has_state(spec.repeats)
            effect=None if dynamic else rules.effects((spec,),'',variables=variables)[0]
            result.append(ScheduledRelic(identity,trigger.event,trigger.turn,spec.operation,
                None if dynamic else effect.amount,turn-1 if trigger.event=='side_turn_end' else turn,
                1 if dynamic else effect.repeats,(ti,ei) if dynamic else None,
                tuple((v['name'],v.get('base_value')) for v in variables) if dynamic else ()))
    return tuple(result)


def apply_phase(state,phase,rules=None):
    from autoslay.rules.statuses import apply_player_phase
    if phase=='side_turn_end':state=apply_player_phase(state,'side_turn_end_early')
    elif phase=='side_turn_start':state=apply_player_phase(state,'side_turn_start')
    if not state.scheduled_relics or state.hp<=0 or state.combat_won:return state
    if type(state.turn) is not int or state.turn<1:raise UnsupportedEffect('Scheduled relic player turn unknown')
    tracked=list(state.scheduled_relics)
    for index,relic in enumerate(tracked):
        if relic.phase!=phase or relic.last_turn>=state.turn or relic.turn not in {None,state.turn}:continue
        tracked[index]=replace(relic,last_turn=state.turn)
        amount=relic.amount;repeats=relic.repeats
        if relic.binding is not None:
            rules=rules or KnowledgeRules()
            ti,ei=relic.binding
            spec=rules.definition(relic.entity_id,'relic').triggers[ti].effects[ei]
            effect=rules.effects((spec,),'',state,[dict(name=k,base_value=v) for k,v in relic.variables])[0]
            amount,repeats=effect.amount,effect.repeats
        if relic.operation in {'strength','dexterity'}:
            field=relic.operation+'_delta';state=replace(state,**{field:getattr(state,field)+amount})
        else:
            state=apply_atomic(state,relic.operation,amount,
                               {e.instance_id for e in state.enemies if e.hp>0},repeats)
        state=replace(state,scheduled_relics=tuple(tracked))
        if state.combat_won:break
    return state


def before_hand_draw(state):
    from autoslay.rules.triggered_relics import reset_for_turn
    from autoslay.rules.statuses import apply_player_phase
    state=reset_for_turn(state)
    state=apply_player_phase(state,'after_block_cleared')
    state=apply_player_phase(apply_phase(state,'after_block_cleared'),'after_energy_reset')
    return apply_phase(state,'after_energy_reset_late')


def after_hand_draw(state):
    # CombatManager.SetupPlayerTurn draws and runs AfterPlayerTurnStart before
    # the enclosing side-start hook, in the supported single-player flow.
    return apply_phase(apply_phase(state,'after_hand_draw'),'side_turn_start')
