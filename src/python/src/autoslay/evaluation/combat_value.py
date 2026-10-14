"""One finite-horizon utility. Model scores are never confidence intervals."""
from dataclasses import dataclass, asdict
from math import isfinite

VERSION = '1.0.0'


@dataclass(frozen=True)
class ValuePolicy:
    version: str = '1.0.0'
    hp_weight: float = 1.0
    hp_sensitivity: float = 2.0
    finish_weight: float = 50.0
    discount: float = 0.9
    enemy_hp_weight: float = 0.4
    enemy_block_weight: float = 0.15
    enemy_strength_weight: float = 2.0
    pollution_weight: float = 1.0
    future_attack_segments: float = 2.0
    strength_discount: float = 0.5
    strength_value_cap: float = 12.0
    worst_loss_weight: float = 0.5

    def __post_init__(self):
        for name, value in asdict(self).items():
            if name == 'version': continue
            if type(value) not in (int, float) or not isfinite(value) or value < 0:
                raise ValueError('Invalid combat value parameter: '+name)
        if self.version != '1.0.0' or not 0 <= self.discount <= 1 or self.strength_discount > 1:
            raise ValueError('Invalid combat value version/discount')


def evaluate_outcome(initial, state, horizon, policy=None):
    policy = policy or ValuePolicy()
    alive = [] if state.combat_won else [e for e in state.enemies if e.hp > 0]
    won = not alive and state.hp > 0
    weight = policy.hp_weight * (1 + policy.hp_sensitivity * (1-initial.hp/max(1, initial.max_hp))**2)
    enemy_loss = state.enemy_hp_loss - initial.enemy_hp_loss
    self_loss = state.self_hp_loss - initial.self_hp_loss
    healing = state.healing - initial.healing
    # A mismatch is a coverage failure, not an invented attribution.
    ledger_matches = initial.hp - state.hp == enemy_loss + self_loss - healing
    growth = state.base_strength + state.strength_delta - state.temporary_strength - initial.base_strength
    strength = min(policy.strength_value_cap, max(0, growth)*policy.future_attack_segments*policy.strength_discount)
    pollution = sum(n for key,n in state.draw_composition+state.discard_composition
                    if 'STATUS' in key or 'SLIMED' in key or 'DAZED' in key or 'WOUND' in key)
    parts = dict(enemy_hp_loss=-weight*enemy_loss, self_hp_loss=-weight*self_loss,
                 healing=weight*healing, finish=policy.finish_weight*won,
                 terminal_enemy_hp=-policy.enemy_hp_weight*sum(e.hp for e in alive),
                 terminal_enemy_block=-policy.enemy_block_weight*sum(e.block for e in alive),
                 terminal_enemy_strength=-policy.enemy_strength_weight*sum(max(0,e.strength) for e in alive),
                 terminal_strength=strength if alive else 0,
                 terminal_pollution=-policy.pollution_weight*pollution if alive else 0)
    for key in parts:
        if key.startswith('terminal_'): parts[key] *= policy.discount**horizon
    return dict(utility=sum(parts.values()), utility_components=parts,
                enemy_hp_loss=enemy_loss, self_hp_loss=self_loss, healing=healing,
                total_hp_loss=enemy_loss+self_loss-healing, death=state.hp<=0, finished=won,
                horizon=horizon, ledger_matches=ledger_matches, policy_version=policy.version,
                terminal_approximation=bool(alive),
                uncertainty=[] if ledger_matches else ['hp_attribution_incomplete'])


def aggregate(outcomes, policy=None, method='exact'):
    """Retain original mass; missing or sampled branches never certify safety."""
    policy = policy or ValuePolicy()
    if any(type(p) not in (int,float) or not isfinite(p) or p<0 for p,_ in outcomes):
        raise ValueError('Invalid outcome probability')
    covered = [(p, value) for p,value in outcomes if p>0 and value is not None and value.get('ledger_matches')]
    mass = sum(p for p,_ in covered)
    exact = method == 'exact'
    complete = exact and abs(mass-1) < 1e-9
    expected = sum(p*v['utility'] for p,v in covered)/mass if mass else None
    loss_upper = max((max(0,v['total_hp_loss']) for _,v in covered), default=None) if complete else None
    components = {k:sum(p*v['utility_components'][k] for p,v in covered)/mass
                  for k in covered[0][1]['utility_components']} if mass else {}
    risk_penalty = policy.worst_loss_weight*loss_upper if loss_upper is not None else None
    return dict(Q_hat=None if expected is None else expected-(risk_penalty or 0),
                conditional_utility=expected, utility_components=components,
                risk_penalty=risk_penalty, risk_kind='conservative_max_loss' if complete else 'unknown',
                loss_upper=loss_upper,
                death_upper=max((int(v['death']) for _,v in covered),default=1) if complete else 1,
                finished=complete and all(v['finished'] for _,v in covered),
                covered_probability=mass if exact else None, observed_sample_fraction=mass if not exact else None,
                uncovered_probability=max(0,1-mass) if exact else None,
                distribution_method=method, complete=complete,
                lower=None, upper=None, bound_kind='uncalibrated_model',
                approximation=any(v['terminal_approximation'] for _,v in covered))
