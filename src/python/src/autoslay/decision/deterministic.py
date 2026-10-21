"""Local progression rules and combat strategies; combat finishes use batch analysis."""
from autoslay.configuration import decision_config
from math import isfinite
from autoslay.paths import ROOT
from autoslay.knowledge_base.repository import read_json, dataset_path, scoped
from autoslay.decision.cache import digest
from autoslay.knowledge_base.events import resource_hook_warnings
from autoslay.actions import action_types, operations

VERSION='1.4.0'
POLICY_PATH=ROOT/'config/decision/autonomy.default.json'
CERTIFICATES=dataset_path('certificates')
ADMIN=action_types(agent_allowed=False)

def load_policy():
    return decision_config(POLICY_PATH)

def source(snapshot):
    s=snapshot.get('state') or {}
    return dict(session_epoch=snapshot.get('session_epoch'),state_version=snapshot.get('state_version'),
        run_id=s.get('command_context_id') or s.get('run_id'),state_sha256=digest(s),game_version=snapshot.get('game_version'))

def certificates(snapshot):
    cert=read_json(dataset_path('certificates'))
    if snapshot.get('game_version')!=cert['game_version'] or snapshot.get('game_assembly_sha256')!=cert['game_assembly_sha256']:
        return None
    return cert

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

def event_options_settling(snapshot):
    state=snapshot.get('state') or {}
    event=state.get('event_screen') or {}
    if state.get('phase')!='event' or event.get('supported') is not True:
        return False
    return (event.get('decision_ready') is False or not any(
        a.get('type') in {'choose_event_option','proceed_event'} for a in state.get('legal_actions',[])))


@scoped
def assess(report,snapshot,policy=None):
    policy=load_policy() if policy is None else policy
    if policy.get('complex_strategy_enabled'): raise ValueError('Complex autonomous strategy is not implemented; keep disabled')
    result=dict(module_version=VERSION,policy_version=policy['version'],eligible=False,mode='ai_required',reasons=[],
        selected_steps=None,execution_authorized=False,source=source(snapshot),rule_id=None,evidence={},checks=[],
        score_review=score_review(report))
    def reject(reason,mode='ai_required'):
        result['reasons'].append(reason);result['primary_reason']=reason;result['mode']=mode;return result
    if not policy.get('deterministic_enabled',False): return reject('deterministic_disabled')
    s=snapshot.get('state') or {}
    if s.get('control_mode')=='paused': return reject('control_paused','wait')
    if not snapshot.get('connected') or snapshot.get('error') or not snapshot.get('ready') or snapshot.get('busy'): return reject('state_not_ready','wait')
    if event_options_settling(snapshot): return reject('event_options_not_ready','wait')
    if s.get('pending_combat_action') and s.get('phase')=='card_selection' and policy.get('combat_batches_enabled',False):
        if not certificates(snapshot) or not all(result['source'].get(k) is not None for k in ('session_epoch','state_version','run_id')):
            return reject('selection_execution_identity_unavailable','wait')
        from .local_combat import select_combat_selection
        selected=select_combat_selection(snapshot)
        if not selected:
            return reject('combat_selection_not_ready','wait') if (s.get('card_selection') or {}).get('decision_ready') is False else reject('combat_selection_semantics_unmodeled')
        result.update(eligible=True,mode='local_deterministic',rule_id=selected['rule_id'],reason=selected['reason'],
            selected_steps=selected['steps'],evidence=selected['evidence'],decision_basis=selected['decision_basis'])
        return result
    if s.get('pending_combat_action') or s.get('pending_event_action'): return reject('nested_choice_requires_context')
    if not all(result['source'].get(k) is not None for k in ('session_epoch','state_version','run_id')): return reject('missing_execution_identity')
    cert=certificates(snapshot)
    if not cert: return reject('game_source_not_certified','wait' if s.get('phase')=='combat' and policy.get('combat_batches_enabled',False) else 'ai_required')
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
    batch_mode=s.get('phase')=='combat' and policy.get('combat_batches_enabled',False)
    if batch_mode and report and (report.get('combat') or {}).get('batches'):
        from autoslay.decision.local_combat import select_batch
        proof,reasons=select_batch(report,snapshot)
        result['checks'].append(dict(rule_id='local_combat_batch',blockers=reasons))
        if proof:
            result.update(eligible=True,mode='local_deterministic',rule_id=proof['rule_id'],reason=proof['reason'],
                selected_steps=proof['steps'],evidence=proof['evidence'],decision_basis=proof['decision_basis'])
            batches=report['combat']['batches'];scores=[r['score'] for r in batches.get('top_batches') or []]
            result['score_review']=dict(leading_score=scores[0] if scores else None,runner_up_score=scores[1] if len(scores)>1 else None,
                automatic_gap_selection=True,reason='操作批次按统一资源价值比较；接近分数由本地取舍，校准不是行动前置条件。')
            return result
        reason=reasons[0] if reasons else 'combat_batch_analysis_required'
        return reject(reason) if reason=='combat_batch_unmodeled_actions' else reject(reason,'wait')
    rules=[('eliminate_certain_death','event',event_survival),
           ('open_card_reward','rewards',open_card_reward)]
    for identity,phase,rule in rules:
        if proof is None and s.get('phase')==phase and identity in enabled:
            proof,reasons=rule(snapshot,actions,cert);result['checks'].append(dict(rule_id=identity,blockers=reasons))
    if proof is None and s.get('phase')=='combat' and 'bounded_combat_search' in enabled and report:
        from autoslay.decision.local_combat import select
        proof,reasons=select(report,snapshot)
        result['checks'].append(dict(rule_id='bounded_combat_search',blockers=reasons))
    if proof is None and s.get('phase')=='combat' and report and policy.get('strategy_mode','off')!='off':
        from autoslay.decision.local_combat import select_calibrated
        proof,reasons=select_calibrated(report,snapshot,policy)
        result['checks'].append(dict(rule_id='calibrated_combat_strategy',blockers=reasons))
    if proof: result.update(eligible=True,mode='local_deterministic',rule_id=proof['rule_id'],reason=proof['reason'],selected_steps=proof['steps'],evidence=proof['evidence'],decision_basis=proof.get('decision_basis','strict_proof'))
    else:
        result['reasons']=['no_complete_proof']+[b for check in result['checks'] for b in check['blockers']]
        ordered=[c for c in result['checks'] if c['rule_id']=='bounded_combat_search']
        if policy.get('strategy_mode')=='execute':ordered=[c for c in result['checks'] if c['rule_id']=='calibrated_combat_strategy']+ordered
        result['primary_reason']=next((b for c in ordered for b in c['blockers']),
                                      result['reasons'][1] if len(result['reasons'])>1 else 'no_complete_proof')
    return result
