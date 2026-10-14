"""Event-ID reproducible battle metrics. Unknown observations stay unknown."""
from collections import Counter, defaultdict
from statistics import median


def distribution(values):
    values=sorted(v for v in values if type(v) in (int,float))
    if not values:return None
    from math import ceil
    return dict(count=len(values),median=median(values),p95=values[max(0,ceil(.95*len(values))-1)])


def summarize(events, load=None):
    events=list({e['event_id']:e for e in events}.values())
    events.sort(key=lambda e:(e.get('recorded_at',''),e['event_id']))
    records={}
    def record(e):
        if e['event_id'] not in records:records[e['event_id']]=load(e['event_id']) if load else e
        return records[e['event_id']]
    def combat(e):return (e.get('location') or {}).get('phase')=='combat'
    def battle(e):
        loc=e.get('location') or {}
        return loc.get('visit_id') or str((loc.get('act_index'),loc.get('node_key'),loc.get('generation')))
    choices=[e for e in events if combat(e) and e['kind'] in {'local_choice','ai_choice'}]
    analyses={e['event_id']:e for e in events if combat(e) and e['kind']=='decision'}
    from autoslay.decision.deterministic import operations, ADMIN
    active=[];unclassified=[];hits=Counter()
    for e in choices:
        details=e.get('details') or {};identity=details.get('analysis_id')
        analysis=analyses.get(identity)
        if analysis:
            actions=(record(analysis).get('snapshot') or {}).get('state',{}).get('legal_actions')
            if actions is None:unclassified.append(e['event_id'])
            elif len(operations([a for a in actions if a.get('type') not in ADMIN|{'discard_potion'}]))!=1:active.append(e)
        else:unclassified.append(e['event_id'])
        if e['kind']=='local_choice':hits[details.get('rule_id','unknown')]+=1
    local=[e['event_id'] for e in choices if e['kind']=='local_choice'];ai=[e['event_id'] for e in choices if e['kind']=='ai_choice']
    active_local=[e['event_id'] for e in active if e['kind']=='local_choice']
    calls=[e for e in events if combat(e) and e['kind']=='agent_audit'
           and (e.get('details') or {}).get('event')=='model_attempt'
           and e['details'].get('status')=='started']
    failures=[e['event_id'] for e in events if combat(e) and e['kind']=='agent_audit'
              and (e.get('details') or {}).get('event')=='model_attempt' and e['details'].get('status')=='failed']
    retries=[e['event_id'] for e in calls if e['details'].get('attempt',1)>1]
    corrections=[e['event_id'] for e in calls if e['details'].get('validation_attempt',1)>1]
    battle_ids={battle(e) for e in events if combat(e)}
    turn_ids={(battle(e),(e.get('location') or {}).get('turn')) for e in events if combat(e) and (e.get('location') or {}).get('turn') is not None}
    per_battle=defaultdict(list);per_turn=defaultdict(list)
    for e in calls:
        per_battle[battle(e)].append(e['event_id']);per_turn[(battle(e),(e.get('location') or {}).get('turn'))].append(e['event_id'])
    submissions={};receipts={};pre_rejected=[];local_traces=set()
    waits=[];analysis_times={'local':[],'ai':[],'unchosen':[]};choice_path={}
    for e in choices:choice_path[(e.get('details') or {}).get('analysis_id')]='local' if e['kind']=='local_choice' else 'ai'
    for e in events:
        d=e.get('details') or {}
        if e['kind']=='agent_audit' and d.get('event')=='local_requested':local_traces.add(d.get('trace_id'))
        if e['kind']=='action_submitted' and combat(e) and (e.get('request') or {}).get('request_id'):
            submissions[e['request']['request_id']]=e['event_id']
        if e['kind'] in {'action_result','action_receipt'}:
            req=(e.get('request') or {}).get('request_id');status=(e.get('receipt') or {}).get('status')
            if req and status:receipts[req]=status
        if e['kind']=='agent_audit' and combat(e):
            if d.get('event')=='local_returned' and d.get('reason') in {'STATE_CHANGED','EXECUTOR_BUSY'}:pre_rejected.append(e['event_id'])
            if d.get('event') in {'model_call_returned','model_call_failed','model_call_cancelled'}:waits.append(d.get('elapsed_ms'))
    for e in events:
        d=e.get('details') or {}
        if e['kind']=='agent_audit' and d.get('event')=='analysis_completed' and d.get('trace_id') in local_traces:
            choice_path[d.get('analysis_id')]='local'
    for identity,e in analyses.items():
        analysis_times[choice_path.get(identity,'unchosen')].append((record(e).get('report') or {}).get('request_elapsed_ms'))
    statuses=Counter(receipts.get(req,'unknown') for req in submissions)
    requests_known=bool(calls) or (not ai and bool(local_traces))
    quality={};potion_requests={};observed_survival={};turn_counts={}
    for e in events:
        if e['kind'] not in {'snapshot','decision','local_outcome','agent_outcome'}:continue
        value=(record(e).get('snapshot') or {}).get('state',{}).get('combat_quality')
        if value and value.get('combat_instance_id'):quality[value['combat_instance_id']]=value
        state=(record(e).get('snapshot') or {}).get('state') or {}
        scope=state.get('combat_instance_id') or (value or {}).get('combat_instance_id')
        hp=((state.get('player') or {}).get('creature') or {}).get('hp')
        turn=(state.get('player') or {}).get('turn')
        if scope and hp is not None:observed_survival[scope]=hp>0
        if scope and type(turn) is int:turn_counts[scope]=max(turn_counts.get(scope,0),turn)
        effects=(record(e).get('details') or {}).get('observed_effects') or {}
        removed=(effects.get('collections') or {}).get('potions',{}).get('removed') or []
        if removed and effects.get('request_id'):
            potion_requests[effects['request_id']]=dict(event_id=e['event_id'],removed=removed)
    return dict(schema_version='combat-metrics/1.0',
        choices=dict(local=len(local),ai=len(ai),total=len(choices),local_rate=len(local)/len(choices) if choices else None,
                     local_event_ids=local,ai_event_ids=ai,rule_hits=dict(hits)),
        active_choices=dict(local=len(active_local),total=len(active),local_rate=len(active_local)/len(active) if active else None,
                            event_ids=[e['event_id'] for e in active],unclassified_event_ids=unclassified),
        model_requests=dict(count=len(calls) if requests_known else None,event_ids=[e['event_id'] for e in calls],
                            failure_event_ids=failures,transport_retry_event_ids=retries,correction_event_ids=corrections,
                            per_battle=[dict(battle=k,request_ids=per_battle[k]) for k in sorted(battle_ids)],
                            per_turn=[dict(battle=k,turn=t,request_ids=per_turn[(k,t)]) for k,t in sorted(turn_ids,key=str)],
                            requests_per_battle=len(calls)/len(battle_ids) if requests_known and battle_ids else None,
                            requests_per_turn=len(calls)/len(turn_ids) if requests_known and turn_ids else None),
        execution=dict(submitted=len(submissions),completed=statuses['completed'],status_counts=dict(statuses),
                       success_rate=statuses['completed']/len(submissions) if submissions else None,
                       submitted_request_ids=sorted(submissions),submission_event_ids=list(submissions.values()),
                       pre_submission_rejection_event_ids=pre_rejected),
        cost=dict(analysis_ms={k:distribution(v) for k,v in analysis_times.items()},model_wait_ms=distribution(waits)),
        quality=dict(combats=list(quality.values()),observed_survival=observed_survival,max_observed_turn=turn_counts,
                     potion_consumptions=potion_requests,combat_results={k:v['result'] for k,v in quality.items() if v.get('result') in {'win','loss'}},
                     basis='game callback counters; absent/partial counters do not become net-HP estimates'),
        limits=['Observed battles/turns may omit archive gaps; denominators are listed.',
                'Legacy archives without transport attempts have unknown request density, not zero.',
                'Retries and corrections can overlap; their event sets must not be added.',
                'Choice events are distinct from confirmed successful actions; shadow events are excluded.'])
