"""A bounded current-turn policy, explicitly distinct from a global outcome proof."""
from math import isfinite
from autoslay.decision.contracts import Operation
from autoslay.decision.normalize import normalize
from autoslay.simulation.engine import CombatSimulator
from autoslay.knowledge_base.repository import read_json, dataset_path

VERSION = '1.2.0'


def select_batch(report,snapshot):
    from .deterministic import source,operations,ADMIN
    if any((report.get('source') or {}).get(k)!=v for k,v in source(snapshot).items()):
        return None,['local_combat_requires_matching_analysis']
    batches=(report.get('combat') or {}).get('batches') or {}
    batch=batches.get('selected_batch')
    if not batch or not batch.get('steps'):return None,['combat_batch_analysis_required']
    legal=operations([a for a in snapshot['state'].get('legal_actions') or [] if a.get('type') not in ADMIN|{'discard_potion'}])
    if batch['steps'][0] not in legal:return None,['combat_batch_first_operation_stale']
    return dict(rule_id='local_combat_batch',decision_basis='local_combat_batch',
        reason='本地按操作批次比较战斗与整局资源；接近分数自行取舍，到观察或目标边界重新决策。收益和风险是估计，允许策略误差。',
        steps=batch['steps'],evidence=dict(module_version=batches['module_version'],optimality_proven=False,
            selected_batch=batch,top_batches=batches.get('top_batches'),deck_capabilities=batches.get('deck_capabilities'),
            run_resources=batches.get('run_resources'),assumptions=batches.get('assumptions'),limits=batches.get('limits'),
            decision_unit='operation_batch',stop_reason=batch['stop_reason'])),[]


def select_combat_selection(snapshot):
    """Resolve a public nested combat selector locally, one observed choice at a time."""
    from .deterministic import operations,ADMIN
    from autoslay.planning.combat_batches import visible_action_score
    from autoslay.evaluation.combat_resources import DEFAULTS
    selection=snapshot['state'].get('card_selection') or {}
    if selection.get('decision_ready') is False:return None
    actions=[a for a in snapshot['state'].get('legal_actions') or [] if a.get('type') not in ADMIN]
    ops=operations(actions);cards=selection.get('cards') or [];selected=sum(c.get('selected') is True for c in cards)
    needed=selection.get('min_select')
    needed=needed if type(needed) is int and needed>0 else 1
    confirms={a['action_id'] for a in actions if a.get('type') in {'confirm_card_selection','preview_card_selection','close_card_selection'}}
    chosen=next((op for op in ops if op['action_id'] in confirms),None) if selected>=needed else None
    if chosen is None:
        prompt=selection.get('prompt') or '';negative=any(word in prompt.lower() for word in ('discard','exhaust','移除','消耗','弃'))
        ranked=[]
        for row in cards:
            op=next((op for op in ops if op['action_id']==row.get('action_id')),None)
            if op and row.get('available') is True and row.get('selected') is not True:
                card=row.get('card') or {}
                proxy=dict(state=dict(snapshot['state'],player=dict(snapshot['state']['player'],hand=[card])))
                score=visible_action_score(proxy,Operation('play_'+card['instance_id']),{},DEFAULTS) if card.get('instance_id') else 0
                ranked.append(((-score if negative else score),op))
        if ranked:chosen=max(ranked,key=lambda pair:(pair[0],pair[1]['action_id']))[1]
    if chosen is None:chosen=next((op for op in ops if op['action_id'] in confirms),None)
    if not chosen:return None
    return dict(rule_id='local_combat_selection',decision_basis='local_combat_selection',
        reason='本地按当前选牌提示和可见卡牌收益选择；完成后重新观察父动作与选择界面。',
        steps=[chosen],evidence=dict(optimality_proven=False,decision_unit='observed_selection',prompt=selection.get('prompt')))


def select(report, snapshot):
    from .deterministic import source, operations, ADMIN
    expected = source(snapshot)
    if not report or any((report.get('source') or {}).get(k) != v for k, v in expected.items()):
        return None, ['local_combat_requires_matching_analysis']
    combat = report.get('combat') or {}
    coverage = combat.get('coverage') or {}
    if any(key not in coverage for key in ('unsupported_cards', 'unsupported_potions', 'interaction_warnings')) or coverage['interaction_warnings']:
        return None, ['local_combat_mechanics_incomplete']
    if combat.get('search_truncated') is not False or combat.get('problems') != [] or combat.get('candidate_pool_incomplete'):
        return None, ['local_combat_search_incomplete']
    state = snapshot['state'];player = state.get('player') or {}
    creatures = [player.get('creature') or {}, *(state.get('enemies') or [])]
    if any(c.get('hidden_power_ids') != [] for c in creatures):
        return None, ['local_combat_hidden_hooks_not_observed']
    if any(c.get('modifiers_complete') is not True for c in player.get('hand') or []):
        return None, ['local_combat_card_modifiers_not_observed']
    reviewed=read_json(dataset_path('certificates')).get('local_combat_modifiers') or {}
    if any(c.get('affliction_id') or c.get('enchantment_id') and (
               c['enchantment_id'] not in reviewed or c['entity_id'] not in reviewed[c['enchantment_id']].get('card_ids',[]))
           for c in player.get('hand') or []):
        return None, ['local_combat_card_modifier_not_certified']
    actions = [a for a in state.get('legal_actions') or [] if a.get('type') not in ADMIN | {'discard_potion'}]
    if any(a.get('type') not in {'play_card', 'end_turn', 'use_potion'} for a in actions):
        return None, ['local_combat_action_not_modeled']
    legal = {Operation(**op) for op in operations(actions)}
    try:
        initial = normalize(snapshot);simulator = CombatSimulator(initial)
        if initial.warnings or legal - {Operation('end_turn')} != set(simulator.actions(initial)) - {Operation('end_turn')}:
            return None, ['local_combat_legal_coverage_incomplete']
        # Unplayable/too expensive held cards need not block a currently legal
        # prefix. However, a resource/cost-changing card could enable them later.
        legal_cards={a.get('card_instance_id') or a['action_id'].removeprefix('play_')
                     for a in actions if a.get('type')=='play_card'}
        unsupported=set(coverage['unsupported_cards'])
        if unsupported & legal_cards:
            return None, ['local_combat_playable_card_unmodeled']
        if unsupported and any(effect.kind not in {'damage','unpowered_damage','block','unpowered_block',
                                                   'vulnerable','weak_boundary','lose_hp','draw'}
                               for key,card in simulator.compiled.items() if key in legal_cards for effect in card.effects):
            return None, ['local_combat_resource_change_could_enable_unknown_card']
    except (ValueError, KeyError, TypeError):
        return None, ['local_combat_normalization_incomplete']
    candidates = combat.get('decision_candidates')
    if candidates is None:
        candidates = (combat.get('candidates') or []) + (combat.get('wait_candidates') or []) + (combat.get('observation_boundaries') or [])
    options = [];potion_tradeoffs=[]
    for candidate in candidates:
        metrics = candidate.get('metrics') or {}
        boundary=candidate.get('boundary')
        if boundary not in {None,'draw_requires_observation','next_turn_requires_observation'}:
            continue
        uncertainties=set(metrics.get('uncertainty') or [])
        if uncertainties-({boundary} if boundary else set()) or metrics.get('confidence') not in {'modeled_current_turn','partial'}:
            continue
        if metrics.get('confidence')=='partial' and (not boundary or uncertainties!={boundary}):
            continue
        if metrics.get('projected_death') is not False:
            continue
        if any(type(metrics.get(k)) not in (int, float) or not isfinite(metrics[k]) for k in
               ('projected_hp_loss', 'heuristic_score')):
            continue
        steps = candidate.get('steps') or [dict(action_id='end_turn', target_id=None)]
        if Operation(**steps[0]) not in legal:
            continue
        # Scarce consumables require a separate long-term comparison with AI.
        if any(step['action_id'].startswith('use_potion_') for step in steps):
            potion_tradeoffs.append((metrics.get('all_enemies_killed') is True,-metrics['projected_hp_loss']))
            continue
        # This policy does not price persistent card growth.
        if metrics.get('permanent_strength_gain', 0):
            continue
        rank = (metrics.get('all_enemies_killed') is True, -metrics['projected_hp_loss'], metrics['heuristic_score'],
                candidate.get('requested_draw',0) if boundary=='draw_requires_observation' else 0)
        options.append((rank, steps, metrics))
    if not options:
        return None, ['local_combat_no_safe_modeled_candidate']
    options.sort(key=lambda item: (tuple(-float(x) for x in item[0]), len(item[1]),
                                   tuple((s['action_id'], s.get('target_id') or '') for s in item[1])))
    _, steps, metrics = options[0]
    if any(value>(metrics.get('all_enemies_killed') is True,-metrics['projected_hp_loss']) for value in potion_tradeoffs):
        return None, ['local_combat_potion_survival_or_finish_tradeoff']
    return dict(rule_id='bounded_combat_search',
                reason='本地当前回合策略：在已完整覆盖的动作中，优先结束战斗、减少本回合失血，再比较战斗收益；保留药水，只执行首步后重新观察。这不是整局最优证明。',
                steps=[steps[0]], decision_basis='bounded_combat_strategy',
                evidence=dict(module_version=VERSION, decision_basis='bounded_combat_strategy',
                              optimality_proven=False, scope='visible action prefixes up to observation boundaries; known current intents',
                              search_complete=True, search_stops_at_observation=True,modeled_legal_operations=len(legal),
                              projected_hp_loss=metrics['projected_hp_loss'], heuristic_score=metrics['heuristic_score'],
                              selected_continuation=steps, consumables_preserved=True,
                              decision_candidate_count=len(candidates),
                              long_term_value_proven=False)), []


def select_calibrated(report, snapshot, policy):
    """Interval dominance/regret under a version-matched independent calibration."""
    from .deterministic import source, operations, ADMIN
    if policy.get('strategy_mode','off')!='execute':return None,['combat_strategy_'+policy.get('strategy_mode','off')]
    if any((report.get('source') or {}).get(k)!=v for k,v in source(snapshot).items()):
        return None,['local_combat_requires_matching_analysis']
    strategy=(report.get('combat') or {}).get('strategy') or {}
    from .combat_circuit import blocked
    if blocked((strategy.get('policy_identity') or {}).get('policy_fingerprint')):
        return None,['combat_strategy_disabled_after_mismatch']
    reasons=list(strategy.get('calibration_reasons') or [])
    if not strategy.get('calibration_version'):reasons.append('combat_calibration_missing')
    if not strategy.get('search_complete') and not strategy.get('comparison_bounds_complete'):
        reasons.append('combat_strategy_search_incomplete')
    state=snapshot['state'];player=state.get('player') or {}
    if any(c.get('hidden_power_ids')!=[] for c in [player.get('creature') or {},*(state.get('enemies') or [])]):
        reasons.append('local_combat_hidden_hooks_not_observed')
    reviewed=read_json(dataset_path('certificates')).get('local_combat_modifiers') or {}
    for card in player.get('hand') or []:
        if card.get('modifiers_complete') is not True:reasons.append('local_combat_card_modifiers_not_observed')
        enchant=card.get('enchantment_id')
        if card.get('affliction_id') or enchant and (enchant not in reviewed or card['entity_id'] not in reviewed[enchant].get('card_ids',[])):
            reasons.append('local_combat_card_modifier_not_certified')
    actions=[a for a in state.get('legal_actions') or [] if a.get('type') not in ADMIN|{'discard_potion'}]
    legal={Operation(**op) for op in operations(actions)}
    rows=strategy.get('first_actions') or []
    if {Operation(**r['first_step']) for r in rows}!=legal:reasons.append('combat_strategy_first_step_missing')
    if any(r.get('upper') is None for r in rows):
        reasons.append('combat_strategy_alternative_bound_unknown')
    if reasons:return None,list(dict.fromkeys(reasons))
    suggested=strategy.get('suggested_first_step')
    best=next((r for r in rows if r['first_step']==suggested),None)
    if not best or not best.get('complete') or best.get('death_upper')!=0 or best.get('lower') is None:
        return None,['combat_strategy_risk_constraint']
    # Fully modeled alternatives violating the zero-death risk constraint are
    # infeasible; a finite utility must never make one defeat the only safe path.
    feasible=[r for r in rows if not (r.get('complete') and r.get('death_upper')==1)]
    other=max((r['upper'] for r in feasible if r is not best),default=best['lower'])
    gap=best['lower']-other;regret=max(0,other-best['lower'])
    low=player['creature']['hp']/max(1,player['creature']['max_hp'])<=.35
    irreversible=bool(best.get('consumed_potions'))
    epsilon=policy.get('low_risk_epsilon',0) if low or irreversible else policy.get('regret_epsilon',0)
    if not (gap>policy.get('robust_margin',0) or regret<=epsilon):
        return None,['combat_strategy_intervals_overlap']
    return dict(rule_id='calibrated_combat_strategy',decision_basis='calibrated_combat_strategy',
                reason='经过独立校准的短期战斗策略：比较全部合法首步的收益区间并通过生存约束，只执行首步后重新观察。',
                steps=[best['first_step']],evidence=dict(module_version=VERSION,optimality_proven=False,
                  policy_identity=strategy.get('policy_identity'),calibration_version=strategy.get('calibration_version'),
                  bound_kind=best['bound_kind'],lower=best['lower'],upper=best['upper'],robust_gap=gap,
                  regret_upper=regret,epsilon=epsilon,death_upper=best['death_upper'],
                  utility_components=best.get('utility_components'),consumed_potions=best.get('consumed_potions'),
                  reserve_lower=best.get('reserve_lower'),reserve_upper=best.get('reserve_upper'),
                  first_actions=rows,search_complete=strategy.get('search_complete'),
                  comparison_bounds_complete=strategy.get('comparison_bounds_complete'),
                  unsearched_global_model_upper=strategy.get('unsearched_global_model_upper'),
                  selected_continuation=[best['first_step']])),[]
