"""Shared arithmetic for context-free effects; trigger dispatch lives above it."""
from dataclasses import replace
from autoslay.decision.contracts import UnsupportedEffect
from autoslay.rules.registry import ATOMIC_OPERATIONS


def lose_player_hp(state,amount,*,self_inflicted=False):
    from autoslay.rules.statuses import stack,set_stack
    if amount<=0:return state
    amount=min(amount,1) if stack(state,'intangible') else amount
    amount=max(0,amount-state.hp_loss_reduction)
    if amount and stack(state,'buffer'):
        return set_stack(state,'buffer',stack(state,'buffer')-1)
    loss=min(state.hp,amount)
    return replace(state,hp=state.hp-loss,
                   self_hp_loss=state.self_hp_loss+(loss if self_inflicted else 0),
                   enemy_hp_loss=state.enemy_hp_loss+(0 if self_inflicted else loss))


def damage_player(state,amount,*,self_inflicted=False,powered=False):
    """Unpowered, blockable damage; HP-loss modifiers run after block."""
    from autoslay.rules.statuses import stack
    amount=max(0,amount)
    if stack(state,'intangible'):amount=min(amount,1)
    absorbed=min(state.block,amount)
    state=replace(state,block=state.block-absorbed)
    amount-=absorbed
    if powered and state.osty and state.osty.hp>0:
        absorbed=min(state.osty.hp,amount)
        state=replace(state,osty=replace(state.osty,hp=state.osty.hp-absorbed))
        amount-=absorbed
    return lose_player_hp(state,amount,self_inflicted=self_inflicted)


def damage_osty(state,amount):
    """A directly targeted pet spends its owner's block; no owner overkill."""
    if not state.pets_known:raise UnsupportedEffect('Pet damage requires observed pets')
    if not state.osty or state.osty.hp<=0:return state
    absorbed=min(state.block,amount)
    return replace(state,block=state.block-absorbed,
        osty=replace(state.osty,hp=max(0,state.osty.hp-(amount-absorbed))))


def enemy_attack_hit(state,instance_id,amount):
    """One powered hit. Thorns runs before damage, Flame Barrier after it."""
    from autoslay.rules.statuses import stack
    enemy=next(e for e in state.enemies if e.instance_id==instance_id)
    if state.hp<=0 or state.combat_won or enemy.hp<=0:return state
    if state.colossus and enemy.vulnerable:amount//=2
    if stack(state,'thorns'):
        state=damage_enemy(state,instance_id,stack(state,'thorns'))
    # CreatureCmd completes this hit even when BeforeDamageReceived killed its dealer.
    state=damage_player(state,amount,powered=True)
    if stack(state,'flame_barrier'):
        state=damage_enemy(state,instance_id,stack(state,'flame_barrier'))
    return state


def visible_enemy_attacks(state):
    """Forecast only observed hits, sharing damage/retaliation with monster turns."""
    for enemy in tuple(state.enemies):
        for amount in enemy.attacks:
            state=enemy_attack_hit(state,enemy.instance_id,amount)
            if state.hp<=0 or state.combat_won:break
    return state


def damage_enemy(state,instance_id,amount,*,hp_loss=False):
    from autoslay.rules.statuses import stack,set_stack
    enemies=[]
    for enemy in state.enemies:
        if enemy.instance_id!=instance_id or enemy.hp<=0:
            enemies.append(enemy);continue
        damage=amount
        if stack(enemy,'intangible'):damage=min(damage,1)
        if enemy.damage_cap is not None:damage=min(damage,enemy.damage_cap)
        absorbed=0 if hp_loss else min(enemy.block,damage)
        loss=max(0,damage-absorbed)
        if loss and stack(enemy,'buffer'):
            enemy=set_stack(enemy,'buffer',stack(enemy,'buffer')-1);loss=0
        enemies.append(replace(enemy,block=enemy.block-absorbed,hp=max(0,enemy.hp-loss)))
    necessary=[e for e in enemies if not e.minion]
    return replace(state,enemies=tuple(enemies),combat_won=bool(enemies) and all(e.hp<=0 for e in necessary or enemies))


def apply_atomic(state, operation, amount, targets=(), repeats=1):
    if operation not in ATOMIC_OPERATIONS:
        raise UnsupportedEffect('Not an atomic operation: ' + str(operation))
    if type(amount) is not int or not 0 <= amount <= 1000000000 or type(repeats) is not int or not 0 <= repeats <= 100:
        raise UnsupportedEffect('Invalid atomic effect binding')
    if operation not in {'unpowered_damage','summon'} and repeats != 1:
        raise UnsupportedEffect('Atomic resource repeats must be one')
    if state.hp <= 0 or state.combat_won:
        return state
    if operation in {'energy', 'stars'}:
        return replace(state, **{operation: getattr(state, operation) + amount})
    if operation=='multiply_energy':
        return replace(state,energy=state.energy*amount)
    if operation=='lose_energy':
        return replace(state,energy=max(0,state.energy-amount))
    if operation == 'unpowered_block':
        return replace(state, block=state.block + amount)
    if operation in {'summon','heal_osty'}:
        if not state.pets_known:raise UnsupportedEffect('Summon/healing requires observed pets')
        from autoslay.decision.contracts import OstyState
        for _ in range(repeats):
            if not amount:continue
            pet=state.osty
            if operation=='summon':
                pet=(replace(pet,max_hp=pet.max_hp+amount,hp=pet.hp+amount) if pet and pet.hp>0 else OstyState(amount,amount))
            elif pet and pet.hp>0:pet=replace(pet,hp=min(pet.max_hp,pet.hp+amount))
            state=replace(state,osty=pet)
        return state
    if operation in {'heal', 'heal_percent', 'gain_max_hp'}:
        from autoslay.rules.resources import heal, gain_max_hp
        if operation=='gain_max_hp':return gain_max_hp(state,amount)
        return heal(state,state.max_hp*amount//100 if operation=='heal_percent' else amount)
    for _ in range(repeats):
        for enemy in tuple(state.enemies):
            if enemy.instance_id in targets:state=damage_enemy(state,enemy.instance_id,amount)
    return state
