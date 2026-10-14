"""Strict proofs and an explicitly labeled bounded strategy; scores are never proofs."""
from autoslay.configuration import decision_config
from math import isfinite
from autoslay.paths import ROOT
from autoslay.knowledge_base.repository import read_json, dataset_path, scoped
from autoslay.decision.cache import digest
from autoslay.decision.normalize import normalize
from autoslay.decision.contracts import Operation, UnsupportedEffect
from autoslay.simulation.engine import CombatSimulator
from autoslay.knowledge_base.events import resource_hook_warnings
from autoslay.decision.outcomes import Outcome,compare

VERSION='1.2.0'
POLICY_PATH=ROOT/'config/decision/autonomy.default.json'
CERTIFICATES=dataset_path('certificates')
ADMIN={'return_to_main_menu','quit_game'}

def load_policy():
    return decision_config(POLICY_PATH)

def operations(actions):
    result=[]
    for a in actions:
        if a.get('allows_no_target'): result.append(dict(action_id=a['action_id'],target_id=None))
        result.extend(dict(action_id=a['action_id'],target_id=t) for t in a.get('allowed_target_ids',[]))
    return result

def source(snapshot):
    s=snapshot.get('state') or {}
    return dict(session_epoch=snapshot.get('session_epoch'),state_version=snapshot.get('state_version'),
        run_id=s.get('command_context_id') or s.get('run_id'),state_sha256=digest(s),game_version=snapshot.get('game_version'))

def certificates(snapshot):
    cert=read_json(dataset_path('certificates'))
    if snapshot.get('game_version')!=cert['game_version'] or snapshot.get('game_assembly_sha256')!=cert['game_assembly_sha256']:
        return None
    return cert

def finish_model(snapshot,actions,cert,allowed_effects,allowed_enemy_powers=frozenset()):
    s=snapshot['state'];p=s['player'];reasons=[]
    for r in p.get('relics') or []:
        if r['entity_id'] not in cert['finish_safe_relics']: reasons.append('uncertified_relic_or_counter:'+r['entity_id'])
    for e in s.get('enemies') or []:
        if e['entity_id'] not in cert['finish_safe_monsters']: reasons.append('uncertified_enemy_death_or_phase:'+e['entity_id'])
    if p['creature'].get('powers') or any(power['entity_id'] not in allowed_enemy_powers
        for enemy in s.get('enemies') or [] for power in enemy.get('powers') or []): reasons.append('combat_power_requires_review')
    creatures=[p['creature'],*(s.get('enemies') or [])]
    if any('hidden_power_ids' not in c or c['hidden_power_ids'] for c in creatures): reasons.append('hidden_power_coverage_missing_or_unreviewed')
    for c in [*(p.get('deck') or []),*(p.get('hand') or [])]:
        if c.get('costs_x') or c.get('costs_star_x'):
            reasons.append('variable_card_cost_requires_adapter:'+c['entity_id'])
        if c.get('modifiers_complete') is not True or c.get('enchantment_id') or c.get('affliction_id'):
            reasons.append('card_modifiers_missing_or_unreviewed:'+c['entity_id'])
    if p.get('potions'): reasons.append('potion_alternative_requires_comparison')
    if any(a.get('type') not in {'play_card','end_turn'} for a in actions): reasons.append('unmodeled_action_alternative')
    if reasons: return None,None,reasons
    try:
        model=normalize(snapshot);sim=CombatSimulator(model)
        if model.warnings: return None,None,list(model.warnings)
        # A growth card in the draw pile can make deliberately delaying victory valuable.
        for c in p.get('deck') or []:
            rule=sim.rules.rulebook.require(c['entity_id'],'card')
            if rule.get('unplayable') and c['entity_id'] not in cert['finish_safe_unplayable_cards']:
                return None,None,['deck_end_effect_not_certified:'+c['entity_id']]
            if any(e['operation'] not in allowed_effects for e in rule.get('effects',[])):
                return None,None,['deck_has_optional_resource_or_growth_effect:'+c['entity_id']]
        # Certify ALL held cards, including presently unaffordable optional growth/heal/draw.
        for c in p.get('hand') or []:
            rule=sim.rules.rulebook.require(c['entity_id'],'card')
            if rule.get('unplayable'):
                if c['entity_id'] not in cert['finish_safe_unplayable_cards']: return None,None,['unplayable_end_effect_not_certified:'+c['entity_id']]
            else:
                compiled=sim.compiled.get(c['instance_id'])
                if compiled is None: return None,None,['unmodeled_held_card:'+c['entity_id']]
                if any(e.kind not in allowed_effects for e in compiled.effects):
                    return None,None,['optional_draw_heal_growth_or_resource_effect:'+c['entity_id']]
        return model,sim,[]
    except (ValueError,KeyError,TypeError) as exc: return None,None,['mechanics_incomplete:'+str(exc)]


def finish(snapshot,actions,cert):
    allowed={'damage','unpowered_damage','block','unpowered_block','vulnerable','weak_boundary'}
    model,sim,reasons=finish_model(snapshot,actions,cert,allowed)
    if reasons:return None,reasons
    p=snapshot['state']['player']
    try:
        candidates=[]
        victory_heal=6 if any(r['entity_id']=='RELIC.BURNING_BLOOD' for r in p.get('relics',[])) else 0
        for op in operations(actions):
            if not op['action_id'].startswith('play_'): continue
            try: t=sim.apply(model,Operation(**op))
            except UnsupportedEffect: continue
            if not t.boundary and t.state.hp>0 and t.state.hp==model.hp and not any(e.hp>0 for e in t.state.enemies):
                candidates.append((op,Outcome('combat_victory',(('hp',min(t.state.max_hp,t.state.hp+victory_heal)),('max_hp',t.state.max_hp)),True,True)))
        if candidates:
            # Source-reviewed effects above establish equal persistent state/continuation;
            # compare the remaining terminal resources explicitly, without utility weights.
            candidates.sort(key=lambda pair:pair[0]['action_id'])
            selected,outcome=candidates[0]
            if any(compare(outcome,other,{'hp':1,'max_hp':1})!='equal' for _,other in candidates):
                return None,['terminal_resource_tradeoff']
            return dict(rule_id='certain_combat_finish',reason='确定结束战斗，且已核对没有回血、永久成长、药水或遗物计数取舍。',
                steps=[selected],evidence=dict(enemy_hp_before=sum(e.hp for e in model.enemies),enemy_hp_after=0,
                hp_before=model.hp,hp_after_attack=model.hp,hp_after_victory=min(model.max_hp,model.hp+victory_heal),victory_heal= min(victory_heal,model.max_hp-model.hp),equivalent_finishes=[op for op,_ in candidates],certificate_version=cert['version'],
                comparison='equal',scope='terminal resources; transient energy/block/combat piles discarded')),[]
        return None,['no_certified_immediate_finish']
    except (ValueError,KeyError,TypeError) as exc: return None,['mechanics_incomplete:'+str(exc)]

def event_survival(snapshot,actions,cert):
    s=snapshot['state'];event=s.get('event_screen') or {};p=s.get('player') or {}
    if resource_hook_warnings(p): return None,['event_resource_hooks_not_certified']
    reviewed=cert['safe_event_exits'].get(event.get('entity_id'))
    if not reviewed: return None,['event_transition_not_certified']
    options=[o for o in event.get('options',[]) if o.get('available') and not o.get('locked')]
    if {o.get('action_id') for o in options}!={a['action_id'] for a in actions}: return None,['event_actions_incomplete']
    fatal=[o for o in options if o.get('will_kill_player') is True]
    safe=[o for o in options if o.get('will_kill_player') is False and o.get('text_key') in reviewed]
    if fatal and len(safe)==1 and len(fatal)+1==len(options):
        return dict(rule_id='eliminate_certain_death',reason='游戏明确标记其他选项致死；剩余选项的安全退出效果已核对源码。',
            steps=[dict(action_id=safe[0]['action_id'],target_id=None)],evidence=dict(hp=p['creature']['hp'],
            fatal_options=[o['option_id'] for o in fatal],safe_text_key=safe[0]['text_key'],certificate_version=cert['version'])),[]
    return None,['event_has_unresolved_alternatives']


def open_card_reward(snapshot,actions,cert):
    s=snapshot['state'];view=s.get('reward_screen') or {};player=s.get('player') or {}
    reviewed=cert.get('reward_inspection') or {}
    if not reviewed or any(r.get('entity_id') not in reviewed['safe_relics'] for r in player.get('relics') or []):
        return None,['reward_open_hooks_not_certified']
    if (player.get('creature') or {}).get('powers') or s.get('room_type')!='CombatRoom':
        return None,['reward_open_context_not_certified']
    rewards=view.get('rewards') or []
    if view.get('screen_type')!='NRewardsScreen' or len(rewards)!=1:
        return None,['reward_order_requires_review']
    reward=rewards[0]
    if reward.get('type')!='CardReward' or reward.get('supported') is not True or reward.get('available') is not True:
        return None,['not_available_standard_card_reward']
    claims=[a for a in actions if a.get('type')=='claim_reward']
    if (len(claims)!=1 or claims[0].get('action_id')!=reward.get('action_id')
            or any(a.get('type') not in {'claim_reward','proceed_rewards'} for a in actions)
            or operations(claims)!=[dict(action_id=reward['action_id'],target_id=None)]):
        return None,['reward_actions_not_certified']
    return dict(rule_id='open_card_reward',reason='直接打开普通卡牌奖励查看选项；此动作不选牌、不跳过、不改变牌组，选项出现后再决策。',
                steps=[dict(action_id=reward['action_id'],target_id=None)],
                evidence=dict(reward_id=reward.get('reward_id'),effect='open_options_only',
                              next_phase='card_reward_selection',ai_choice_required_after_open=True,
                              certificate_version=cert['version'])),[]


def score_review(report):
    """Diagnostic only: neither ties nor an uncalibrated gap authorize actions."""
    candidates=(report.get('combat') or {}).get('candidates') or report.get('shortlist') or []
    values=[c.get('metrics',{}).get('heuristic_score') for c in candidates]
    values=[v for v in values if type(v) in (int,float) and isfinite(v)]
    if len(values)<2:return None
    ranked=sorted(values,reverse=True)
    return dict(leading_score=ranked[0],runner_up_score=ranked[1],score_gap=ranked[0]-ranked[1],
        tied_leaders=sum(v==ranked[0] for v in ranked),compared_candidate_count=len(values),
        automatic_gap_selection=False,error_bound=None,
        reason='收益分是启发式估算；同分不证明结果等价，分差尚无校准误差界，不能单独用于跳过 AI。')

@scoped
def assess(report,snapshot,policy=None):
    policy=load_policy() if policy is None else policy
    if policy.get('complex_strategy_enabled'): raise ValueError('Complex autonomous strategy is not implemented; keep disabled')
    result=dict(module_version=VERSION,policy_version=policy['version'],eligible=False,mode='ai_required',reasons=[],
        selected_steps=None,execution_authorized=False,source=source(snapshot),rule_id=None,evidence={},checks=[],
        score_review=score_review(report))
    def reject(reason,mode='ai_required'):
        result['reasons'].append(reason);result['mode']=mode;return result
    if not policy.get('deterministic_enabled',False): return reject('deterministic_disabled')
    s=snapshot.get('state') or {}
    if s.get('control_mode')=='paused': return reject('control_paused','wait')
    if not snapshot.get('connected') or snapshot.get('error') or not snapshot.get('ready') or snapshot.get('busy'): return reject('state_not_ready','wait')
    if s.get('pending_combat_action') or s.get('pending_event_action'): return reject('nested_choice_requires_context')
    if not all(result['source'].get(k) is not None for k in ('session_epoch','state_version','run_id')): return reject('missing_execution_identity')
    cert=certificates(snapshot)
    if not cert: return reject('game_source_not_certified')
    actions=[a for a in s.get('legal_actions',[]) if a.get('type') not in ADMIN];ops=operations(actions)
    if not ops: return reject('no_legal_operation','wait')
    enabled=set(policy.get('enabled_rules',[]));proof=None
    if len(ops)==1 and 'forced_action' in enabled:
        a=actions[0];phase=s.get('phase');selection=s.get('card_selection') or {}
        forced=(a.get('type')=='choose_map_node' and phase=='map' or
            a.get('type')=='proceed_event' and (s.get('event_screen') or {}).get('finished') is True or
            phase=='rewards' and a.get('type')=='proceed_rewards' and not (s.get('reward_screen') or {}).get('rewards'))
        if forced: proof=dict(rule_id='forced_action',reason='当前只有一个有效动作及目标，且该阶段必须推进。',steps=ops,evidence=dict(operation_count=1))
    if proof is None and s.get('phase')=='card_selection' and 'mandatory_card_selection' in enabled:
        selection=s.get('card_selection') or {};cards=selection.get('cards') or []
        minimum,maximum=selection.get('min_select'),selection.get('max_select')
        if cards and selection.get('cancelable') is False and type(minimum) is int and type(maximum) is int and minimum==len(cards) and maximum>=minimum:
            unselected=[c for c in cards if c.get('selected') is False and c.get('available') is True]
            action_ids={a['action_id']:a for a in actions}
            chosen=next((c['action_id'] for c in unselected if action_ids.get(c.get('action_id'),{}).get('type')=='toggle_selection_card'),None)
            if chosen is None and all(c.get('selected') is True for c in cards):
                chosen=next((a['action_id'] for a in actions if a.get('type') in {'confirm_card_selection','preview_card_selection'}),None)
            if chosen and all(a.get('type') in {'toggle_selection_card','confirm_card_selection','preview_card_selection','cancel_selection_preview'} for a in actions):
                proof=dict(rule_id='mandatory_card_selection',reason='必须选择全部候选牌且不能跳过；选择顺序不会改变最终集合。',
                    steps=[dict(action_id=chosen,target_id=None)],evidence=dict(min_select=minimum,card_count=len(cards),selected_count=sum(c.get('selected') is True for c in cards)))
    if proof is None and s.get('phase')=='combat' and 'forced_end_turn' in enabled and len(ops)==1 and actions[0].get('type')=='end_turn':
        proof=dict(rule_id='forced_end_turn',reason='没有可执行的牌、药水或其他回合内操作，只能结束回合。',steps=ops,evidence=dict(operation_count=1))
    rules=[('certain_combat_finish','combat',finish),('eliminate_certain_death','event',event_survival),
           ('open_card_reward','rewards',open_card_reward)]
    for identity,phase,rule in rules:
        if proof is None and s.get('phase')==phase and identity in enabled:
            proof,reasons=rule(snapshot,actions,cert);result['checks'].append(dict(rule_id=identity,blockers=reasons))
    comparison_rules=enabled&{'equivalent_outcomes','dominant_outcome','common_first_step'}
    if proof is None and s.get('phase')=='combat' and comparison_rules:
        from autoslay.decision.terminal_comparison import prove
        proof,reasons=prove(snapshot,actions,cert,comparison_rules)
        result['checks'].append(dict(rule_id='complete_outcome_comparison',blockers=reasons))
    if proof is None and s.get('phase')=='combat' and 'bounded_combat_search' in enabled and report:
        from autoslay.decision.local_combat import select
        proof,reasons=select(report,snapshot)
        result['checks'].append(dict(rule_id='bounded_combat_search',blockers=reasons))
    if proof: result.update(eligible=True,mode='local_deterministic',rule_id=proof['rule_id'],reason=proof['reason'],selected_steps=proof['steps'],evidence=proof['evidence'],decision_basis=proof.get('decision_basis','strict_proof'))
    else: result['reasons']=['no_complete_proof']+[b for check in result['checks'] for b in check['blockers']]
    return result
