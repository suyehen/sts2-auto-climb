"""Shared resource proxy and joint outcome contract; never a win probability."""
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from math import isfinite
from autoslay.decision.cache import digest

VERSION = '1.0.0'


@dataclass(frozen=True)
class ValueContract:
    state_version: str = 'run-resources/1'
    value_version: str = 'resource-proxy/1'
    risk_version: str = 'death-first/1'
    transition_version: str = 'public-scenarios/1'
    policy_version: str = 'bounded-observe-replan/1'
    valuation_inputs_sha256: str = 'unbound'
    units: str = 'resource proxy points'
    death_value: float = -10000.0
    clear_value: float = 10000.0
    gold_weight: float = .04
    max_hp_weight: float = 1.0

    def __post_init__(self):
        values=(self.death_value,self.clear_value,self.gold_weight,self.max_hp_weight)
        if any(type(v) not in (int,float) or not isfinite(v) for v in values):
            raise ValueError('Finite value contract required')
        if self.death_value>=self.clear_value or min(self.gold_weight,self.max_hp_weight)<0:
            raise ValueError('Invalid value contract scale')


@dataclass(frozen=True)
class RunResources:
    hp: int
    max_hp: int
    gold: int
    potions: tuple[str, ...] = ()
    relic_counters: tuple[tuple[str, int | None], ...] = ()
    growth_proxy: float = 0.0
    dead: bool = False
    cleared: bool = False

    def __post_init__(self):
        if any(type(v) is not int for v in (self.hp,self.max_hp,self.gold)):
            raise ValueError('Integral resource state required')
        if self.max_hp<1 or not 0<=self.hp<=self.max_hp or self.gold<0:
            raise ValueError('Invalid joint resource state')
        if type(self.dead) is not bool or type(self.cleared) is not bool or self.dead!=(self.hp==0) or (self.dead and self.cleared):
            raise ValueError('Inconsistent absorbing outcome')
        if not isfinite(self.growth_proxy):raise ValueError('Finite growth proxy required')


def resource_value(state, parameters, route, contract=None):
    from autoslay.evaluation.combat_resources import hp_value
    contract=contract or ValueContract()
    if state.dead:return contract.death_value
    if state.cleared:return contract.clear_value
    return (hp_value(state.hp,state.max_hp,parameters,route)+state.gold*contract.gold_weight
            +state.max_hp*contract.max_hp_weight+state.growth_proxy)


def settle_victory(state, heal):
    """Only at the pre-heal -> post-heal boundary, never per turn."""
    if type(heal) is not int or heal<0:raise ValueError('Integral nonnegative victory healing required')
    return state if state.dead else replace(state,hp=min(state.max_hp,state.hp+heal))


def strategic_context(snapshot):
    state=snapshot['state'];player=state.get('player') or {};creature=player.get('creature') or {}
    view=deepcopy(state.get('map') or {})
    resources=RunResources(creature.get('hp',0),creature.get('max_hp',80),player.get('gold',0),
        tuple(p.get('instance_id') or p.get('entity_id','unknown') for p in player.get('potions') or []),
        tuple((r.get('entity_id','unknown'),r.get('counter')) for r in player.get('relics') or []),
        dead=creature.get('hp',0)==0)
    return dict(schema_version='run-strategic/1',contract=asdict(ValueContract()),resources=asdict(resources),
        identity=dict(run_id=state.get('run_id') or state.get('command_context_id'),
                      act_index=view.get('act_index'),floor=state.get('floor'),
                      character=player.get('character_id'),ascension=state.get('ascension')),
        deck=deepcopy(player.get('deck') or []),relics=deepcopy(player.get('relics') or []),
        map=view,map_sha256=digest(view),map_source=view.get('topology_source','current_public_snapshot'),
        coverage=dict(map_available=bool(view.get('nodes')),encounter_probabilities='unknown',
                      inventory_value='unpriced; joint identities retained'))


def summarize_outcomes(outcomes, contract, unknown_mass=0.0):
    """No renormalization or independent-sample claim for mixed search trees."""
    if not isfinite(unknown_mass) or not 0<=unknown_mass<=1:raise ValueError('Invalid unknown mass')
    for row in outcomes:
        if row['contract']!=asdict(contract):raise ValueError('Incompatible outcome contract')
        if not isfinite(row['weight']) or not 0<=row['weight']<=1:raise ValueError('Invalid outcome weight')
        if type(row['potions_used']) is not int or row['potions_used']<0:raise ValueError('Invalid potion consumption')
        RunResources(**row['resources'])
    mass=sum(r['weight'] for r in outcomes)
    if abs(mass+unknown_mass-1)>1e-8:raise ValueError('Joint probability mass must be preserved')
    return dict(represented_mass=mass,unknown_mass=unknown_mass,
        mechanism_approximation_mass=sum(r['weight'] for r in outcomes if r.get('mechanism_unknown')),
        death_mass=sum(r['weight'] for r in outcomes if r['resources']['dead']),
        expected_potions_used=sum(r['weight']*r['potions_used'] for r in outcomes),
        worst_potions_used=max((r['potions_used'] for r in outcomes if r['weight']>0),default=0),
        probability_basis='conditional modeled tree weights; not calibrated safety',
        independent_sample_count=None)
