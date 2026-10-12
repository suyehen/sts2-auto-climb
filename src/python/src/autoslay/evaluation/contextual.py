"""Explainable base value, deck interactions, hard constraints and situational corrections."""
from autoslay.configuration import decision_config
from dataclasses import dataclass,asdict
from math import isfinite
import json
from functools import lru_cache
from autoslay.paths import ROOT

VERSION='1.0.0'

@dataclass(frozen=True)
class InteractionPolicy:
    version:str='1.0.0'
    strength_multihit_weight:float=1.5
    draw_cheap_card_weight:float=2.0
    exhaust_block_weight:float=1.0
    deck_size_penalty:float=.12
    def __post_init__(self):
        for key,value in asdict(self).items():
            if key!='version' and (type(value) not in (int,float) or not isfinite(value) or value<0):
                raise ValueError('Finite nonnegative interaction weights required')

def load_interactions():
    return InteractionPolicy(**decision_config(ROOT/'config/decision/context.default.json'))

@lru_cache(maxsize=4)
def _load(path,mtime,size):
    from pathlib import Path
    return InteractionPolicy(**json.loads(Path(path).read_text(encoding='utf-8-sig')))

def score(features,context,policy,interaction):
    n=len(features); draw=min(policy.opening_draw,n)
    demand=draw*sum(max(0,f['cost']) for f in features)/n
    utilization=min(1,policy.energy/demand) if demand else 1
    expected=lambda key:draw*sum(f.get(key,0) for f in features)/n*utilization
    attack_ratio=sum(f['type']=='Attack' for f in features)/n
    cheap_ratio=sum(f['cost']==0 and f['type'] not in {'Curse','Status'} for f in features)/n
    extra_hits=draw*sum(max(0,f.get('hits',0)-1) for f in features)/n*utilization
    hand_after=policy.opening_draw-1 if context.expected_hand_after_play is None else context.expected_hand_after_play
    capacity=max(0,context.hand_limit-hand_after)
    effective_draw=0 if context.draw_blocked else draw*sum(min(f.get('draw',0),capacity) for f in features)/n*utilization
    if context.expected_draw_available is not None and not context.draw_blocked:
        effective_draw=draw*sum(min(f.get('draw',0),capacity,context.expected_draw_available) for f in features)/n*utilization
    damage=expected('damage'); block=expected('block'); raw_draw=expected('draw'); strength=expected('strength')
    base=dict(damage=damage*policy.damage_weight,block=block*policy.block_weight,
              draw=raw_draw*policy.draw_weight,energy=expected('energy')*policy.energy_weight,strength=strength*policy.strength_weight,
              vulnerable=expected('vulnerable')*policy.vulnerable_weight)
    nonattacks=max(0,draw-1)*sum(f['type'] not in {'Attack',None} for f in features)/n
    exhaust_block=expected('exhaust_nonattack_block')*nonattacks*interaction.exhaust_block_weight
    synergy=dict(strength_attack_density=strength*policy.strength_weight*attack_ratio,
                 strength_multihit=strength*extra_hits*interaction.strength_multihit_weight,
                 draw_cheap_cards=effective_draw*cheap_ratio*interaction.draw_cheap_card_weight,
                 exhaust_nonattack_block=exhaust_block*policy.block_weight)
    area_damage=draw*sum(f['damage']*(context.enemy_count-1) for f in features if f.get('area'))/n*utilization
    environment=dict(low_hp_block=block*policy.block_weight*(context.urgency-1),
                     low_hp_exhaust_block=synergy['exhaust_nonattack_block']*(context.urgency-1),
                     multiple_enemies=area_damage*policy.damage_weight,
                     vulnerable_targets=base['vulnerable']*(attack_ratio-1))
    constraints=dict(draw_disabled_or_capacity=(effective_draw-raw_draw)*policy.draw_weight)
    costs=dict(curse_status=-sum(f['type'] in {'Curse','Status'} for f in features)/n*draw*policy.curse_weight,
               self_hp=-expected('self_hp')*policy.hp_weight*context.urgency,
               deck_size=-n*interaction.deck_size_penalty)
    parts=dict(base_value=sum(base.values()),synergy_correction=sum(synergy.values()),
               environment_correction=sum(environment.values()),constraint_correction=sum(constraints.values()),cost_penalty=sum(costs.values()))
    return dict(utility_points=sum(parts.values()),components=parts,
                breakdown=dict(base=base,synergy=synergy,environment=environment,constraints=constraints,costs=costs),
                effective=dict(damage=damage+area_damage,block=block+exhaust_block,draw=effective_draw,strength=strength),
                model_version=VERSION,interaction_policy=asdict(interaction),
                assumptions=dict(opening_hand=policy.opening_draw,hand_after_play=hand_after,energy=policy.energy,
                    ordinary_future_draw_available=context.expected_draw_available,cheap_card_ratio=cheap_ratio,attack_ratio=attack_ratio,
                    strength_extra_hits_proxy=extra_hits,nonattack_exhaust_targets_proxy=nonattacks),
                constraints=dict(effect_draw_blocked=context.draw_blocked,hand_limit=context.hand_limit,free_hand_slots=capacity),
                demand=demand,utilization=utilization,attack_ratio=attack_ratio)
