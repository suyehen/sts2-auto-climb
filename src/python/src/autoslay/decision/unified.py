"""One read-only report for all supported phases, adaptive budgets and versioned caching."""
from autoslay.knowledge_base.repository import scoped, current_snapshot
from autoslay.configuration import decision_config
import json
import uuid
from dataclasses import asdict
from time import perf_counter
from autoslay.paths import ROOT
from autoslay.decision.cache import AnalysisCache,digest
from autoslay.decision.service import analyze,load_policy
from autoslay.planning.search import SearchBudget
from autoslay.knowledge_base.templates import index
from autoslay.evaluation.run import RunContext,RunPolicy,deck_value
from autoslay.evaluation.run import VERSION as RUN_VERSION
from autoslay.evaluation.choices import cards_report
from autoslay.evaluation.shop import evaluate_shop
from autoslay.evaluation.events import evaluate_events
from autoslay.evaluation.route import evaluate_routes
from autoslay.evaluation.rest import evaluate_rest
from autoslay.evaluation.contextual import load_interactions,VERSION as CONTEXT_VERSION
from autoslay.knowledge_base.rulebook import Rulebook
from autoslay.evaluation.event_sequences import evaluate_event_sequence
from autoslay.decision.autonomy import assess
from autoslay.telemetry.decisions import packet
from autoslay.knowledge_base.events import rules as event_rules,resource_hook_warnings
from autoslay.decision.contracts import CONTRACT_VERSION
from autoslay.simulation.engine import VERSION as SIMULATION_VERSION
from autoslay.decision.service import VERSION as COMBAT_SERVICE_VERSION
from autoslay.evaluation.combat import VERSION as COMBAT_EVALUATION_VERSION
from autoslay.planning.lookahead import VERSION as LOOKAHEAD_VERSION
from autoslay.planning.combat_batches import VERSION as BATCH_VERSION
from autoslay.knowledge_base.behavior import relevant as behavior_references
from autoslay.decision.mechanisms import attach as attach_mechanisms
from autoslay.knowledge_base.behavior import load as load_behavior
from autoslay.evaluation.event_adapters import VERSION as EVENT_ADAPTER_VERSION

VERSION='1.9.0'
CACHE=AnalysisCache()

def relevant_entities(value):
    result=set()
    def walk(item):
        if isinstance(item,dict):
            identity=item.get('entity_id')
            if isinstance(identity,str) and identity.startswith(('CARD.','POTION.','RELIC.')): result.add(identity)
            for child in item.values(): walk(child)
        elif isinstance(item,(list,tuple)):
            for child in item: walk(child)
    walk(value)
    return result

def context_for(snapshot,overrides):
    p=snapshot['state'].get('player') or {}; creature=p.get('creature') or {}
    blocked=any(r['entity_id']=='RELIC.FIDDLE' for r in p.get('relics',[])) or (
        snapshot['state'].get('phase')=='combat' and any(power['entity_id']=='POWER.NO_DRAW_POWER' for power in creature.get('powers',[])))
    values=dict(hp=creature.get('hp',0),max_hp=creature.get('max_hp',80),gold=p.get('gold',0),
        potion_slots_free=max(0,p.get('potion_slots',0)-len(p.get('potions') or [])),
        draw_blocked=blocked) | overrides
    values['draw_blocked']=blocked or values['draw_blocked']
    return RunContext(**values)

def source(snapshot):
    s=snapshot['state']
    return dict(session_epoch=snapshot.get('session_epoch'),state_version=snapshot.get('state_version'),
        run_id=s.get('command_context_id') or s.get('run_id'),game_version=snapshot.get('game_version'),
        state_sha256=digest(s))

@scoped
def analyze_decision(snapshot,context=None,scenarios=None,use_cache=True):
    started=perf_counter(); s=snapshot['state']; phase=s.get('phase'); context=context or {}
    if snapshot.get('game_version')!='0.111.0' or snapshot.get('schema_version')!='1.0': raise ValueError('Unsupported snapshot version')
    qualification=assess({},snapshot)
    if qualification['eligible']:
        # Proof precedes heuristic search and AI packet construction. No game action here.
        report=dict(schema_version='decision/unified/1.0',module_version=VERSION,analysis_id=uuid.uuid4().hex,
            source=source(snapshot),phase=phase,execution_authorized=False,cache_hit=False,live_state_ready=True,
            legal_actions=s.get('legal_actions',[]),autonomy=qualification,ai_required=False,ai_called=False,
            request_elapsed_ms=(perf_counter()-started)*1000,module_versions=dict(unified=VERSION,deterministic=qualification['module_version']),
            limitations=['local proof only; executor independently rechecks fresh state'],shortlist=[])
        attach_mechanisms(report,snapshot)
        report['ai_packet']=packet(report)
        return report
    adaptive=decision_config(ROOT/'config/decision/analysis.default.json')
    policy=RunPolicy(**decision_config(ROOT/'config/decision/run.default.json'))
    combat_policy=load_policy(ROOT/'config/decision/combat.default.json')
    autonomy_policy=decision_config(ROOT/'config/decision/autonomy.default.json')
    from autoslay.calibration.combat import load as load_combat_calibration
    calibration=load_combat_calibration(autonomy_policy.get('combat_calibration_id',''))
    from autoslay.execution.batch_models import load as batch_model_status
    interactions=load_interactions()
    entries,knowledge_hash=index(expected_version=snapshot['game_version'])
    rulebook=Rulebook()
    if snapshot.get('game_assembly_sha256') and snapshot['game_assembly_sha256']!=rulebook.data.get('game_assembly_sha256'):
        raise ValueError('Game implementation changed; review rules before calculation')
    key=digest(dict(knowledge_revision=current_snapshot().revision,state=s,game=snapshot['game_version'],context=context,scenarios=scenarios,adaptive=adaptive,
                    run_policy=asdict(policy),combat_policy=asdict(combat_policy),interactions=asdict(interactions),context_version=CONTEXT_VERSION,knowledge=knowledge_hash,
                    version=VERSION,combat=COMBAT_SERVICE_VERSION,simulation=SIMULATION_VERSION,contract=CONTRACT_VERSION,
                    monster_source=digest(load_behavior()) if phase=='combat' else None,
                    combat_evaluation=COMBAT_EVALUATION_VERSION,rules=rulebook.fingerprint,lookahead=LOOKAHEAD_VERSION,
                    combat_batches=BATCH_VERSION,
                    event_rules=digest(event_rules()),
                    event_adapter=EVENT_ADAPTER_VERSION,
                    event_source=digest(next((e for e in load_behavior()['entries'] if e['entity_id']==(s.get('event_screen') or {}).get('entity_id')),None)) if phase=='event' else None,
                    combat_calibration=digest(calibration),autonomy=autonomy_policy,
                    batch_model_status=batch_model_status() if autonomy_policy.get('combat_batches_enabled') else None))
    cached=CACHE.get(key) if use_cache else None
    if cached is not None:
        cached.update(source=source(snapshot),analysis_id=uuid.uuid4().hex,cache_hit=True,
                      live_state_ready=bool(snapshot.get('ready') and not snapshot.get('busy')),
                      request_elapsed_ms=(perf_counter()-started)*1000)
        if 'combat' in cached: cached['combat']['source']=cached['source']
        cached['autonomy']=assess(cached,snapshot)
        cached['ai_required']=cached['autonomy']['mode']=='ai_required'
        cached['behavior_references']=behavior_references(snapshot)
        attach_mechanisms(cached,snapshot)
        cached['ai_packet']=packet(cached)
        return cached
    report=dict(schema_version='decision/unified/1.0',module_version=VERSION,analysis_id=uuid.uuid4().hex,
        source=source(snapshot),phase=phase,execution_authorized=False,cache_hit=False,
        live_state_ready=bool(snapshot.get('ready') and not snapshot.get('busy')),ai_required=qualification['mode']=='ai_required',
        legal_actions=s.get('legal_actions',[]),controls=dict(pending_combat=s.get('pending_combat_action'),
            pending_event=s.get('pending_event_action'),selection=s.get('card_selection'),menu=s.get('menu')),
        knowledge_hash=knowledge_hash,weights=asdict(policy),interaction_weights=asdict(interactions),combat_weights=asdict(combat_policy),analysis_policy_hash=digest(adaptive),
        calibration_status='uncalibrated',knowledge_compatibility=rulebook.audit(relevant_entities(s)),
        module_versions=dict(unified=VERSION,run_evaluation=RUN_VERSION,contextual=CONTEXT_VERSION,rules=rulebook.data['version']))
    if phase=='combat' and not s.get('pending_combat_action'):
        p=s['player']; hp=p['creature']['hp']; maximum=p['creature']['max_hp']
        incoming=sum((i.get('attack_damage') or 0)*(i.get('attack_repeats') or 0) for e in s.get('enemies') or [] for i in e.get('intents',[]))
        map_view=s.get('map') or {}
        boss=any(n.get('coordinate')==map_view.get('current_position') and n.get('type')=='Boss' for n in map_view.get('nodes',[]))
        danger=hp/maximum<=adaptive['low_hp_fraction'] or incoming>=hp or boss
        tier='danger' if danger else 'normal'
        settings=dict(adaptive['lookahead'])
        if not danger: settings.update(branches=4,time_ms=250)
        report.update(budget_tier=tier,combat=analyze(snapshot,budget=SearchBudget(**adaptive[tier]),policy=combat_policy,
                       lookahead_settings=settings,scenarios=scenarios))
        if autonomy_policy.get('combat_batches_enabled',False):
            from autoslay.planning.combat_batches import analyze as analyze_batches
            report['combat']['batches']=analyze_batches(snapshot,entries,combat_policy,adaptive['strategy'],SearchBudget(**adaptive[tier]))
        if autonomy_policy.get('strategy_mode','off')!='off':
            from autoslay.planning.combat_strategy import analyze as analyze_strategy
            from autoslay.decision.normalize import normalize
            strategy=analyze_strategy(snapshot,normalize(snapshot),entries,combat_policy,
                      adaptive['strategy'],SearchBudget(**adaptive[tier]),calibration,current_snapshot().revision)
            if adaptive['strategy']['extra_budget_enabled'] and not strategy['problems'] and strategy['truncation_reasons']:
                available=[r['comparison_utility'] for r in strategy['first_actions'] if r.get('comparison_utility') is not None]
                available.sort(reverse=True)
                close=len(available)>=2 and available[0]-available[1]<=autonomy_policy['robust_margin']
                if danger or close:
                    extended=dict(adaptive[tier]);extended['max_nodes']=min(100000,extended['max_nodes']*2)
                    extended['time_ms']=min(5000,extended['time_ms']*2)
                    retry=analyze_strategy(snapshot,normalize(snapshot),entries,combat_policy,
                         adaptive['strategy'],SearchBudget(**extended),calibration,current_snapshot().revision)
                    retry['budget_stages']=[dict(nodes=strategy['nodes'],elapsed_ms=strategy['elapsed_ms'],
                                                reasons=strategy['truncation_reasons']),dict(nodes=retry['nodes'],elapsed_ms=retry['elapsed_ms'])]
                    strategy=retry
            strategy['mode']=autonomy_policy['strategy_mode']
            report['combat']['strategy']=strategy
        report['shortlist']=report['combat']['candidates'][:3]
        report['observations']=report['combat']['observation_boundaries'][:3]
        if report['combat'].get('batches'):
            report['shortlist']=report['combat']['batches']['top_batches']
    elif s.get('player') and (s['player'].get('deck') or []):
        run=context_for(snapshot,context); deck=s['player']['deck']
        report.update(context=asdict(run),deck=deck_value(deck,run,entries,policy))
        if phase=='card_reward_selection':
            offers=[v['card'] for v in s['reward_screen'].get('cards',[]) if v.get('available')]
            report['choices']=cards_report(deck,offers,run,entries,policy=policy)
            ids={v['card']['instance_id']:v.get('action_id') for v in s['reward_screen'].get('cards',[])}
            for candidate in report['choices']['candidates']:
                candidate['action_id']=ids.get((candidate.get('card') or {}).get('instance_id'))
            report['choices']['alternatives']=s['reward_screen'].get('alternatives',[])
        elif phase=='shop':
            items=[]
            for raw in (s.get('shop') or {}).get('items',[]):
                categories={'MerchantCardEntry':'card','MerchantRelicEntry':'relic','MerchantPotionEntry':'potion','MerchantCardRemovalEntry':'removal'}
                items.append(dict(raw.get('details') or {},item_id=raw['item_id'],category=categories.get(raw['type'],'unknown'),
                    price=raw['cost'],available=raw.get('available',False),action_id=raw.get('action_id')))
            report['shop']=evaluate_shop(deck,items,run,entries,policy)
        elif phase=='event':
            event=dict(s.get('event_screen') or {})
            event['game_assembly_sha256']=snapshot.get('game_assembly_sha256')
            from autoslay.calibration.events import runtime_validation
            report['event_runtime_validation']=runtime_validation(snapshot)
            report['event']=evaluate_events(event.get('options',[]),deck,run,entries,policy)
            # Full noncombat resource hook coverage is not yet certified for owned relics.
            event['interaction_warnings']=resource_hook_warnings(s['player'])
            report['event_sequence']=evaluate_event_sequence(event,deck,run,entries,policy,snapshot['game_version'])
            report['module_versions'].update(event_adapter=EVENT_ADAPTER_VERSION,
                event_sequence=report['event_sequence'].get('module_version'),event_runtime_validation='1.0.0')
        elif phase=='map': report['route']=evaluate_routes(s.get('map') or {},run)
        elif phase=='rest_site': report['rest']=evaluate_rest((s.get('rest_site') or {}).get('options',[]),deck,run,entries,policy)
        elif phase=='card_selection':
            selection=s.get('card_selection') or {}; offered=[v['card'] for v in selection.get('cards',[]) if v.get('available')]
            screen=selection.get('screen_type','')
            mode='upgrade' if 'Upgrade' in screen else 'remove' if 'Remove' in screen else None
            if mode: report['choices']=cards_report(deck,offered,run,entries,policy=policy,mode=mode)
            else: report['selection_requires_context']='Selection meaning is not safely inferable from generic screen; use explicit evaluate_run_choices mode'
    report['behavior_references']=behavior_references(snapshot)
    report['limitations']=['heuristic utility is not calibrated win probability','Codex chooses, then execution validates fresh state',
                          'unknown card/event/item choices remain unranked; deck totals cover known components only']
    report['request_elapsed_ms']=(perf_counter()-started)*1000
    report['autonomy']=assess(report,snapshot)
    if phase=='combat':
        from autoslay.decision.coverage import diagnose
        primary=report['autonomy'].get('primary_reason')
        report['combat_diagnostics']=diagnose(snapshot,report,primary if not report['autonomy']['eligible'] else None)
    report['ai_required']=report['autonomy']['mode']=='ai_required'
    attach_mechanisms(report,snapshot)
    report['ai_packet']=packet(report)
    if use_cache: CACHE.put(key,report)
    return report

@scoped
def evaluate_run_choices(snapshot,kind,options,context=None):
    entries,_=index(expected_version=snapshot['game_version']); run=context_for(snapshot,context or {})
    book=Rulebook()
    if snapshot.get('game_assembly_sha256') and snapshot['game_assembly_sha256']!=book.data.get('game_assembly_sha256'):
        raise ValueError('Game implementation changed; review rules before calculation')
    deck=snapshot['state']['player']['deck']
    policy=RunPolicy(**decision_config(ROOT/'config/decision/run.default.json'))
    if kind in {'add','remove','upgrade'}: return cards_report(deck,options,run,entries,policy=policy,mode=kind)
    if kind=='event': return evaluate_events(options,deck,run,entries,policy)
    if kind=='shop': return evaluate_shop(deck,options,run,entries,policy)
    raise ValueError('Unknown comparison kind')
