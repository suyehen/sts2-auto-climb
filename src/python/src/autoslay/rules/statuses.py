"""Native status timelines. Entity effects apply stacks through shared mechanics."""
from dataclasses import replace
from autoslay.decision.contracts import UnsupportedEffect
from autoslay.simulation.atomic_effects import apply_atomic

VERSION = '2.1.0'
PET_POWER_IDS=frozenset({'POWER.DIE_FOR_YOU_POWER'})
PLAYER_TIMELINES=(
    dict(stat='regeneration',event='side_turn_end_early',operation='heal',
         decrement_event='side_turn_end_early',skip_first_turn=False),
    dict(stat='plating',event='side_turn_end_early',operation='unpowered_block',
         decrement_event='side_turn_start',skip_first_turn=True),
    dict(stat='next_turn_block',event='after_block_cleared',operation='unpowered_block',decrement_event='after_block_cleared',remove=True),
    dict(stat='next_turn_energy',event='after_energy_reset',operation='energy',decrement_event='after_energy_reset',remove=True),
    dict(stat='next_turn_stars',event='after_energy_reset',operation='stars',decrement_event='after_energy_reset',remove=True),
    dict(stat='next_turn_draw',event='side_turn_start',operation=None,decrement_event='side_turn_start',remove=True),
    dict(stat='ritual',event='side_turn_end_after',operation='strength',decrement_event=None),
)
STATUS_IDS={'poison':'POWER.POISON_POWER','regeneration':'POWER.REGEN_POWER','plating':'POWER.PLATING_POWER',
            'next_turn_block':'POWER.BLOCK_NEXT_TURN_POWER','block_and_next_turn':'POWER.BLOCK_NEXT_TURN_POWER',
            'next_turn_energy':'POWER.ENERGY_NEXT_TURN_POWER','next_turn_stars':'POWER.STAR_NEXT_TURN_POWER',
            'next_turn_draw':'POWER.DRAW_CARDS_NEXT_TURN_POWER','ritual':'POWER.RITUAL_POWER'}
SUPPORTED_STATUSES = frozenset(STATUS_IDS.values())
PLAYER_POWER_IDS = frozenset({
    'POWER.STRENGTH_POWER','POWER.COLOSSUS_POWER','POWER.NO_DRAW_POWER',
    'POWER.WEAK_POWER','POWER.FRAIL_POWER','POWER.DEXTERITY_POWER',
    'POWER.SPEED_POTION_POWER','POWER.FLEX_POTION_POWER',
}) | (SUPPORTED_STATUSES-{'POWER.POISON_POWER'})
ENEMY_POWER_IDS = frozenset({
    'POWER.VULNERABLE_POWER','POWER.STRENGTH_POWER','POWER.HARD_TO_KILL_POWER',
    'POWER.MINION_POWER','POWER.WEAK_POWER','POWER.POISON_POWER',
})
from autoslay.rules.registry import STACK_POWERS,TEMPORARY_ENEMY_STRENGTH,TEMPORARY_PLAYER_STATS
PLAYER_POWER_IDS = PLAYER_POWER_IDS | (set(STACK_POWERS.values())-{'POWER.DEMISE_POWER','POWER.DOOM_POWER'}-{STACK_POWERS[k] for k in TEMPORARY_ENEMY_STRENGTH})
ENEMY_POWER_IDS = ENEMY_POWER_IDS | {STACK_POWERS[k] for k in ('doom','demise','artifact','buffer','intangible','thorns','barricade','blur')} | {STACK_POWERS[k] for k in TEMPORARY_ENEMY_STRENGTH}


def stack(owner, operation):
    return dict(owner.power_stacks).get(STACK_POWERS.get(operation,operation),0)


def set_stack(owner,operation,amount):
    values=dict(owner.power_stacks);identity=STACK_POWERS.get(operation,operation)
    if amount:values[identity]=amount
    else:values.pop(identity,None)
    return replace(owner,power_stacks=tuple(values.items()))


def add_stack(owner,operation,amount):
    if amount<=0:return owner
    if operation in {'barricade','well_laid_plans','fan_of_knives','the_sealed_throne'}:amount=max(stack(owner,operation),amount)
    else:amount+=stack(owner,operation)
    return set_stack(owner,operation,amount)


def prevent_debuff(owner):
    value=stack(owner,'artifact')
    return (set_stack(owner,'artifact',value-1),True) if value else (owner,False)


def preserve_block(state):
    return bool(stack(state,'barricade') or stack(state,'blur'))


def hand_draw_bonus(state):
    return state.next_turn_draw+int(stack(state,'clarity')>0)+state.extra_hand_draw


def next_turn_energy(state,base):
    return base+(state.energy if state.retain_energy else 0)


def resolve_doom(state):
    enemies=tuple(replace(e,hp=0) if e.hp>0 and stack(e,'doom')>=e.hp else e for e in state.enemies)
    necessary=[e for e in enemies if not e.minion]
    return replace(state,enemies=enemies,combat_won=bool(enemies) and all(e.hp<=0 for e in necessary or enemies))


def after_card(state,card,prior_powers):
    """Afterimage snapshots its amount before play; newly applied Rage triggers immediately."""
    if state.hp<=0 or state.combat_won:return state
    block=dict(prior_powers).get(STACK_POWERS['afterimage'],0)
    if card.card_type=='Attack':block+=stack(state,'rage')
    state=apply_atomic(state,'unpowered_block',block)
    if card.entity_id=='CARD.SOUL' and stack(state,'devour_life'):
        state=apply_atomic(state,'summon',stack(state,'devour_life'))
    return state


def before_card(state,card,keywords=None):
    if 'Ethereal' in (keywords if keywords is not None else card.keywords or ()):
        state=apply_atomic(state,'unpowered_block',stack(state,'spirit_of_ash'))
    return state


def after_enemy_side(state):
    """Doom and Demise resolve at enemy-side end, after that side's actions."""
    from autoslay.simulation.atomic_effects import damage_enemy
    state=resolve_doom(state)
    if state.combat_won:return state
    for enemy in state.enemies:
        if enemy.hp>0 and stack(enemy,'demise'):
            state=damage_enemy(state,enemy.instance_id,stack(enemy,'demise'),hp_loss=True)
    if not state.combat_won:
        state=set_stack(state,'intangible',max(0,stack(state,'intangible')-1))
        state=set_stack(state,'flame_barrier',0)
        state=replace(state,enemies=tuple(set_stack(e,'intangible',max(0,stack(e,'intangible')-1)) for e in state.enemies))
    return state


def apply_player_phase(state,event):
    if state.hp<=0 or state.combat_won:return state
    if event=='side_turn_end_after':state=expire_temporary_stats(state)
    if not any(getattr(state,p['stat']) for p in PLAYER_TIMELINES) and not state.power_stacks:return state
    if type(state.turn) is not int or state.turn<1:raise UnsupportedEffect('Status phase requires observed player turn')
    marks=dict(state.status_phase_marks)
    if marks.get(event,0)>=state.turn:return state
    for timeline in PLAYER_TIMELINES:
        value=getattr(state,timeline['stat'])
        if value<=0:continue
        if timeline['event']==event and timeline['operation']:
            if timeline['operation']=='strength':state=replace(state,strength_delta=state.strength_delta+value)
            else:state=apply_atomic(state,timeline['operation'],value)
        if timeline['decrement_event']==event and not (timeline.get('skip_first_turn',False) and state.turn==1):
            state=replace(state,**{timeline['stat']:0 if timeline.get('remove') else value-1})
    marks[event]=state.turn
    if event=='side_turn_start':
        state=replace(state,strength_delta=state.strength_delta+stack(state,'demon_form'))
        state=set_stack(state,'blur',max(0,stack(state,'blur')-1))
        state=set_stack(state,'clarity',max(0,stack(state,'clarity')-1))
    elif event=='after_energy_reset':
        state=replace(state,energy=state.energy+stack(state,'pyre'))
        if stack(state,'radiance'):
            state=replace(state,energy=state.energy+1)
            state=set_stack(state,'radiance',stack(state,'radiance')-1)
    elif event=='side_turn_end_after':
        state=set_stack(state,'rage',0)
        state=set_stack(state,'retain_hand',max(0,stack(state,'retain_hand')-1))
    return replace(state,status_phase_marks=tuple(sorted(marks.items())))


def expire_temporary_stats(state):
    """Temporary powers are removed before applying their blockable stat penalty."""
    pending=[];totals={'strength':0,'dexterity':0}
    # Keep the observed application order: an Artifact may block only the first
    # of several different temporary powers expiring at this phase.
    by_id={STACK_POWERS[k]:(k,field) for k,field in TEMPORARY_PLAYER_STATS.items()}
    for identity,amount in state.power_stacks:
        if identity in by_id:
            operation,field=by_id[identity];pending.append((operation,field,amount));totals[field]+=amount
    for field in totals:
        legacy=max(0,getattr(state,'temporary_'+field)-totals[field])
        if legacy:pending.append((None,field,legacy))
    for operation,field,amount in pending:
        if operation:state=set_stack(state,operation,0)
        state,blocked=prevent_debuff(state)
        if not blocked:state=replace(state,**{field+'_delta':getattr(state,field+'_delta')-amount})
    return replace(state,temporary_strength=0,temporary_dexterity=0)


def enemy_poison_start(state):
    """Enemy-side start: poison before moves, with Accelerant's repeated ticks."""
    if state.hp<=0 or state.combat_won:return state
    from autoslay.simulation.atomic_effects import damage_enemy
    for enemy in tuple(state.enemies):
        for _ in range(min(enemy.poison,1+stack(state,'accelerant'))):
            current=next(e for e in state.enemies if e.instance_id==enemy.instance_id)
            if current.hp<=0 or state.combat_won:break
            state=damage_enemy(state,current.instance_id,current.poison,hp_loss=True)
            state=replace(state,enemies=tuple(replace(e,poison=max(0,e.poison-1)) if e.instance_id==enemy.instance_id and e.hp>0 else e for e in state.enemies))
    return state
