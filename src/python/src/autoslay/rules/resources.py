"""Shared resource command arithmetic for combat and run transitions."""
from dataclasses import replace


def heal(state, amount):
    actual=int(min(amount,state.max_hp-state.hp)) if state.hp>0 else 0
    changes=dict(hp=state.hp+actual)
    if hasattr(state,'healing'):changes['healing']=state.healing+actual
    return replace(state,**changes)


def gain_max_hp(state, amount):
    if state.hp<=0:return state
    maximum=int(min(999999999,state.max_hp+amount))
    return heal(replace(state,max_hp=maximum),maximum-state.max_hp)


def lose_max_hp(state,amount,damage):
    """CreatureCmd damages first, then sets/clamps maximum HP, even after a block."""
    if state.hp<=0:return state
    proposed=state.max_hp-amount
    state=damage(state,max(0,state.hp-proposed))
    maximum=max(1,int(proposed));clamped=max(0,state.hp-maximum)
    changes=dict(max_hp=maximum,hp=min(state.hp,maximum))
    if hasattr(state,'self_hp_loss'):changes['self_hp_loss']=state.self_hp_loss+clamped
    if hasattr(state,'dead'):changes['dead']=changes['hp']==0
    return replace(state,**changes)
