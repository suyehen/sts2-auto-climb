"""Recorded observations, not inferred damage attribution or unseen outcomes."""
from collections import Counter
from datetime import datetime
from statistics import mean

def seconds(a,b):
    try:return max(0,(datetime.fromisoformat(b)-datetime.fromisoformat(a)).total_seconds())
    except (TypeError,ValueError):return None

def stats(values):
    values=sorted(v for v in values if isinstance(v,(int,float)))
    return dict(count=len(values),total=sum(values),mean=mean(values),p95=values[min(len(values)-1,int(len(values)*.95))],maximum=max(values)) if values else None

def detail(repo,run_id,entries,meta):
    visits={};requests={};waits=[];execution=[];analyses=[];version_history=[];decisions={};actions=Counter();decision_sources=Counter()
    for e in entries:
        loc=e.get('location') or {};visit=loc.get('visit_id')
        if visit:
            v=visits.setdefault(visit,dict(visit_id=visit,act_index=loc.get('act_index'),node_key=loc.get('node_key'),generation=loc.get('generation'),room_type=loc.get('room_type'),first_recorded_at=e['recorded_at'],last_recorded_at=e['recorded_at'],enemies=[],max_turn=None,hp_first=None,hp_last=None,events=0))
            v['events']+=1;v['last_recorded_at']=e['recorded_at']
            if loc.get('room_type'):v['room_type']=loc['room_type']
            if loc.get('turn') is not None:v['max_turn']=max(v['max_turn'] or 0,loc['turn'])
        if e['kind'] in {'snapshot','decision'}:
            p=repo.load_event(e['event_id']);snap=p.get('snapshot') or {};s=snap.get('state') or {}
            versions={k:snap.get(k) for k in ('game_version','game_assembly_sha256','bridge_version')}
            versions['module_versions']=(p.get('report') or {}).get('module_versions')
            if versions not in version_history:version_history.append(versions)
            if visit:
                hp=((s.get('player') or {}).get('creature') or {}).get('hp')
                if v['hp_first'] is None:v['hp_first']=hp
                if hp is not None:v['hp_last']=hp
                for enemy in s.get('enemies') or []:
                    name=enemy.get('name') or enemy.get('entity_id')
                    if name and name not in v['enemies']:v['enemies'].append(name)
            if e['kind']=='decision':
                decisions[e['event_id']]=e['recorded_at'];analyses.append((p.get('report') or {}).get('request_elapsed_ms'))
        if e['kind']=='ai_choice':
            decision_sources['ai']+=1
            identity=(e.get('details') or {}).get('analysis_id')
            if identity in decisions:waits.append(seconds(decisions[identity],e['recorded_at']))
        if e['kind']=='local_choice':decision_sources['local_deterministic']+=1
        request=e.get('request') or {};req=request.get('request_id')
        if e['kind']=='action_submitted' and req:
            requests[req]=e['recorded_at'];actions[request.get('action_id','unknown').split('_')[0]]+=1
        if e['kind'] in {'action_result','action_receipt'} and req in requests and (e.get('receipt') or {}).get('status') in {'completed','failed','rejected','unknown','needs_user_input'}:
            execution.append(seconds(requests.pop(req),e['recorded_at']))
    for v in visits.values():
        v['observed_span_seconds']=seconds(v['first_recorded_at'],v['last_recorded_at'])
        v['observed_hp_change']=None if v['hp_first'] is None or v['hp_last'] is None else v['hp_last']-v['hp_first']
    folder=repo.folder(run_id);files=[p for p in folder.rglob('*') if p.is_file()]
    from autoslay.telemetry.combat_metrics import summarize
    return dict(visits=list(visits.values()),room_visit_counts=dict(Counter(v['room_type'] or 'unknown' for v in visits.values())),
        combat_metrics=summarize(entries,repo.load_event),
        analysis_ms=stats(analyses),execution_seconds=stats(execution),selection_wait_seconds=stats(waits),recorded_decision_sources=dict(decision_sources),
        wall_span_seconds=seconds(meta.get('started_at'),meta.get('ended_at')),recorded_action_prefix_counts=dict(actions),
        unresolved_request_count=len(requests),manual_interventions=None,version_history=version_history,
        storage=dict(files=len(files),bytes=sum(p.stat().st_size for p in files)),
        limits=['到访跨度包含暂停/离线，不能当作战斗工作时长','生命变化可能包含治疗、自伤与上限变化，不能当作敌人战损',
                '选择等待包含 AI、人工和工具延迟，不能当作纯 AI 推理时间','回合数为观察到的最大回合；缺口不补造',
                '未关联的请求和未记录的人工介入保持未知'])
