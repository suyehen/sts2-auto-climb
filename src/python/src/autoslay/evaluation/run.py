"""Shared situational utility. Points are explicit proxies, not win probabilities."""
from dataclasses import asdict, dataclass
from math import isfinite
from autoslay.knowledge_base.templates import canonical
from autoslay.rules.registry import IroncladRules
from autoslay.decision.contracts import CombatState, UnsupportedEffect
from autoslay.probability.draw import at_least_one
from autoslay.evaluation.contextual import load_interactions,score

VERSION = "1.3.0"

@dataclass(frozen=True)
class RunPolicy:
    version: str = "1.0.0"
    damage_weight: float = 1.0
    block_weight: float = 1.2
    draw_weight: float = 2.0
    energy_weight: float = 4.0
    strength_weight: float = 3.0
    vulnerable_weight: float = 1.0
    curse_weight: float = 8.0
    hp_weight: float = 2.0
    max_hp_weight: float = 1.0
    gold_weight: float = 0.04
    risk_weight: float = 0.5
    opening_draw: int = 5
    energy: int = 3
    shop_nodes: int = 500
    shop_time_ms: int = 300
    def __post_init__(self):
        for key, value in asdict(self).items():
            if key == "version": continue
            if type(value) not in (int, float) or not isfinite(value) or value < 0:
                raise ValueError("Finite nonnegative policy values required")
        if not 1 <= self.opening_draw <= 10 or not 1 <= self.energy <= 10 or not 1 <= self.shop_nodes <= 10000 or not 1 <= self.shop_time_ms <= 5000:
            raise ValueError("Run analysis budget outside limits")
        if any(type(getattr(self,key)) is not int for key in ('opening_draw','energy','shop_nodes','shop_time_ms')):
            raise ValueError('Integer run analysis budgets required')

@dataclass(frozen=True)
class RunContext:
    hp: int
    max_hp: int
    gold: int
    next_room: str = "unknown"
    enemy_count: int = 1
    draw_blocked: bool = False
    potion_slots_free: int = 0
    hand_limit: int = 10
    expected_hand_after_play: int | None = None
    expected_draw_available: int | None = None
    def __post_init__(self):
        if any(type(getattr(self,key)) is not int for key in ('hp','max_hp','gold','enemy_count','potion_slots_free')) or type(self.draw_blocked) is not bool:
            raise ValueError('Integer run resources and boolean draw_blocked required')
        if not 0 <= self.hp <= self.max_hp or self.max_hp < 1 or self.gold < 0 or not 1 <= self.enemy_count <= 10 or self.potion_slots_free < 0:
            raise ValueError("Invalid run context")
        if type(self.hand_limit) is not int or self.hand_limit!=10:
            raise ValueError('Unsupported hand limit')
        if self.expected_hand_after_play is not None and (type(self.expected_hand_after_play) is not int or not 0<=self.expected_hand_after_play<=self.hand_limit):
            raise ValueError('Invalid expected hand occupancy')
        if self.expected_draw_available is not None and (type(self.expected_draw_available) is not int or self.expected_draw_available<0):
            raise ValueError('Invalid available draw count')
    @property
    def urgency(self):
        return 1 + 2 * (1 - self.hp / self.max_hp) + (0.5 if self.next_room in {"Elite", "Boss"} else 0)

def card_features(value, entries):
    warnings=[]
    try:
        card, variant = canonical(value, entries)
    except ValueError as exc:
        from autoslay.evaluation.entity_effects import immediate_features
        semantic=immediate_features(value,entries)
        if semantic is not None:
            semantic['warnings'].append(str(exc))
            return semantic
        return dict(entity_id=value.get("entity_id"), cost=value.get("energy_cost", -1), type=value.get("type"), damage=0, block=0, draw=0, strength=0, vulnerable=0, self_hp=0, warnings=[str(exc)])
    features=dict(entity_id=card.entity_id, cost=card.cost, type=card.card_type,
                  damage=0, block=0, draw=0, energy=0, strength=0, vulnerable=0, self_hp=0,hits=0,exhaust_nonattack_block=0)
    try:
        rule=IroncladRules().card(card, CombatState(80,80,0,3,(),(),()))
        for effect in rule.effects:
            if effect.kind in {"damage", "dismantle"}:
                features["damage"] += effect.amount * effect.repeats
                features['hits'] += effect.repeats
            elif effect.kind in {"block", "draw", "energy", "strength", "vulnerable"}: features[effect.kind] += effect.amount
            elif effect.kind == "lose_hp": features["self_hp"] += effect.amount
            elif effect.kind=='exhaust_nonattacks': features['exhaust_nonattack_block']+=effect.amount
            elif effect.kind not in {"temporary_strength", "enemy_strength", "colossus", "double_vulnerable"}:
                warnings.append("CONDITIONAL_EFFECT:"+effect.kind)
            if effect.kind not in {'damage','dismantle','block','draw','energy','strength','vulnerable','lose_hp','exhaust_nonattacks'}:
                warnings.append('UNMODELED_CARD_VALUE:EFFECT:'+effect.kind)
        if any(e.kind in {"enemy_strength", "colossus", "double_vulnerable", "dismantle", "temporary_strength"} for e in rule.effects):
            warnings.append("CONDITIONAL_VALUE_NOT_CALIBRATED")
        if card.target_type == "AllEnemies": features["area"] = True
    except UnsupportedEffect:
        from autoslay.evaluation.entity_effects import immediate_features
        semantic=immediate_features(value,entries)
        if semantic is not None:
            return semantic
        if card.card_type not in {"Curse", "Status"}: warnings.append("UNMODELED_CARD_VALUE:"+card.entity_id)
    features["warnings"]=warnings
    if card.star_cost>0: warnings.append('UNMODELED_CARD_VALUE:STAR_RESOURCE:'+card.entity_id)
    return features

def unmodeled(features):
    return any(w.startswith('UNMODELED_CARD_VALUE:') or w in {'Card not in local encyclopedia','Unsupported card variant/cost'} for w in features['warnings'])

def intrinsic_value(value,entries,policy=None):
    policy=policy or RunPolicy(); f=card_features(value,entries)
    if unmodeled(f):
        from autoslay.evaluation.entity_effects import evaluate_entity
        return dict(utility=None,features=f,warnings=f['warnings'],
                    semantic_analysis=evaluate_entity(value,RunContext(80,80,0),entries,policy))
    parts=dict(damage=f['damage']*policy.damage_weight,block=f['block']*policy.block_weight,
               draw=f['draw']*policy.draw_weight,energy=f.get('energy',0)*policy.energy_weight,strength=f['strength']*policy.strength_weight,
               vulnerable=f['vulnerable']*policy.vulnerable_weight,self_hp=-f['self_hp']*policy.hp_weight,
               curse_status=-policy.curse_weight if f['type'] in {'Curse','Status'} else 0)
    return dict(utility=sum(parts.values()),components=parts,energy_cost=f['cost'],features=f,
                basis='neutral single-card resources before deck/environment constraints; cost shown separately',
                warnings=f['warnings'])

def deck_value(deck, context, entries, policy=None, interactions=None):
    policy=policy or RunPolicy()
    features=[card_features(c,entries) for c in deck]
    n=len(features)
    if not n: raise ValueError("Cannot value empty deck")
    draw=min(policy.opening_draw,n)
    evaluated=score(features,context,policy,interactions or load_interactions())
    productive=[f for f in features if 0 <= f['cost'] <= policy.energy and f['type'] not in {'Curse','Status'}]
    demand=draw * sum(max(0,f['cost']) for f in features)/n
    utilization=min(1,policy.energy/demand) if demand else 1
    expected=lambda key: draw*sum(f[key] for f in features)/n
    attack_ratio=sum(f['type']=='Attack' for f in features)/n
    damage=draw*sum(f['damage']*(context.enemy_count if f.get('area') else 1) for f in features)/n*utilization
    block=expected('block')*utilization
    cycling=0 if context.draw_blocked else expected('draw')*utilization
    strength=expected('strength')*utilization
    curses=sum(f['type'] in {'Curse','Status'} for f in features)
    damage=evaluated['effective']['damage']; block=evaluated['effective']['block']
    cycling=evaluated['effective']['draw']
    warnings=sorted({w for f in features for w in f['warnings']})
    unknown=[f['entity_id'] for f in features if unmodeled(f)]
    return dict(utility_points=evaluated['utility_points'],scoring=evaluated,components=evaluated['components'], unknown_cards=unknown, score_scope='known components only' if unknown else 'modeled heuristic components', metrics=dict(size=n, attack_ratio=attack_ratio,
        mean_fixed_cost=sum(max(0,f['cost']) for f in features)/n,
        opening_energy_demand=demand, energy_utilization_proxy=utilization,
        opening_damage_proxy=damage, opening_block_proxy=block, opening_draw_proxy=cycling,
        opening_strength_proxy=strength, curse_status_count=curses,
        opening_productive_probability=float(at_least_one(n,len(productive),draw))),
        warnings=warnings, basis='canonical local descriptions; average opening resources capped by energy demand',
        confidence='partial' if warnings else 'heuristic',
        limitations=['unknown card contributions are excluded; known-component deltas are provisional and may mis-rank synergies',
                     'resource proxies are not simulated encounter damage/HP loss',
                     'conditional triggers, exhaust engine and card retention require dedicated valuations'])

def compare_choice(deck, context, entries, *, add=None, remove=None, upgrade=None, policy=None,interactions=None):
    before=deck_value(deck,context,entries,policy,interactions)
    after=list(deck)
    if sum(x is not None for x in (add,remove,upgrade)) > 1: raise ValueError('One deck change per comparison')
    if add is not None: after.append(add)
    if remove is not None or upgrade is not None:
        wanted=remove if remove is not None else upgrade
        matches=[i for i,c in enumerate(after) if c.get('instance_id')==wanted]
        if len(matches)!=1: raise ValueError('Card instance must match exactly once')
        if remove is not None:
            _,variant=canonical(after[matches[0]],entries)
            if 'Eternal' in variant.get('keywords',[]): raise ValueError('Eternal card cannot be removed')
            after.pop(matches[0])
        else:
            c=dict(after[matches[0]]); c['upgrade_level']=c.get('upgrade_level',0)+1
            canonical(c,entries)  # No invented upgraded variant.
            after[matches[0]]=c
    result=deck_value(after,context,entries,policy,interactions)
    return dict(delta_utility=result['utility_points']-before['utility_points'], before=before, after=result,
                component_deltas={k:result['components'][k]-v for k,v in before['components'].items()},
                metric_deltas={k:result['metrics'][k]-v for k,v in before['metrics'].items()})
