"""Complete public decision snapshots, separate from calibration and replay labels."""
import json
import re
from datetime import datetime,timezone
from pathlib import Path
from threading import RLock
from autoslay.paths import ROOT
from autoslay.knowledge_base.repository import current_snapshot

DIRECTORY=ROOT/'data/decisions'
LOCK=RLock()
PUBLIC_STATE={'phase','floor','ascension','room_type','player','enemies','run_id','run_history_id','act_count','command_context_id','map',
              'legal_actions','event_screen','reward_screen','card_selection','rest_site','shop','treasure','menu',
              'pending_event_action','pending_combat_action','control_mode','combat_instance_id','combat_quality'}

def safe_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',value): raise ValueError('Invalid decision ID')
    return value

def public_snapshot(snapshot):
    result={k:snapshot.get(k) for k in ('schema_version','game_version','game_assembly_sha256','bridge_version','session_epoch','state_version','connected','ready','busy','run_clock')}
    result['state']={k:v for k,v in snapshot.get('state',{}).items() if k in PUBLIC_STATE}
    return result

def archive(report,snapshot,origin='live',directory=None):
    if origin not in {'live','replay'}: raise ValueError('Invalid evidence origin')
    identity=safe_id(report['analysis_id']); base=Path(directory or DIRECTORY)/origin
    from autoslay.telemetry import runs
    location=runs.location(snapshot,origin)
    payload=dict(schema_version='decision-record/1.0',origin=origin,recorded_at=datetime.now(timezone.utc).isoformat(),
                 location=location,snapshot=public_snapshot(snapshot),report=report,ai_payload=compact(report)|dict(decision_record=dict(recorded=True,record_id=identity,origin=origin)),
                 ai_delivery='not sent to AI; bounded local combat strategy' if (report.get('autonomy') or {}).get('decision_basis')=='bounded_combat_strategy' else 'not sent to AI; local deterministic proof' if report.get('ai_called') is False else 'compact decision API payload; full report stays local' if origin=='live' else
                             'offline replay payload preview; not an actual AI decision or live execution')
    if origin=='live':
        from autoslay.archive.collector import store
        pointer=store(dict(payload,event_id=identity,kind='decision'),directory)
    else:
        with LOCK:
            base.mkdir(parents=True,exist_ok=True); path=base/(identity+'.json'); temp=path.with_suffix('.tmp')
            temp.write_text(json.dumps(payload,ensure_ascii=False,allow_nan=False,indent=2),encoding='utf-8'); temp.replace(path)
        pointer=dict(recorded=True)
    if origin=='live': runs.observe(snapshot,directory)
    return dict(pointer,record_id=identity,origin=origin)

def list_records(directory=None,limit=100):
    base=Path(directory or DIRECTORY); rows=[]
    for origin in ('live','replay'):
        for p in (base/origin).glob('*.json'):
            rows.append((p.stat().st_mtime_ns,p,origin))
    rows.sort(reverse=True,key=lambda r:r[0]); result=[]
    for _,p,origin in (rows if limit is None else rows[:limit]):
        try:
            data=json.loads(p.read_text(encoding='utf-8-sig')); report=data['report']
            from autoslay.telemetry.runs import location
            result.append(dict(record_id=p.stem,origin=origin,recorded_at=data['recorded_at'],phase=report.get('phase'),
                               location=data.get('location') or location(data.get('snapshot',{}),origin),
                               source=report.get('source'),analysis_ms=report.get('request_elapsed_ms')))
        except (OSError,ValueError,KeyError): continue
    from autoslay.archive.repository import for_legacy
    repo=for_legacy(directory)
    for entry in repo.entries(kind='decision',limit=10000 if limit is None else limit,descending=True):
        try:
            data=repo.load_event(entry['event_id']);r=data['report']
            result.append(dict(record_id=entry['event_id'],origin='live',recorded_at=entry['recorded_at'],phase=r.get('phase'),
                               location=entry['location'],source=r.get('source'),analysis_ms=r.get('request_elapsed_ms')))
        except (OSError,ValueError,KeyError):continue
    result.sort(key=lambda r:r['recorded_at'],reverse=True)
    unique=[];seen=set()
    for row in result:
        key=(row['record_id'],row['origin'])
        if key not in seen:unique.append(row);seen.add(key)
    return unique if limit is None else unique[:limit]

def load(identity,origin='live',directory=None):
    if origin not in {'live','replay'}: raise ValueError('Invalid origin')
    if origin=='live':
        from autoslay.archive.repository import for_legacy
        try:return for_legacy(directory).load_event(safe_id(identity))
        except FileNotFoundError:pass
    return json.loads((Path(directory or DIRECTORY)/origin/(safe_id(identity)+'.json')).read_text(encoding='utf-8-sig'))


def archive_tool_result(result,snapshot,kind,options=None):
    """Normalize legacy combat and explicit comparison tools for the observer only."""
    from uuid import uuid4
    source=dict(session_epoch=snapshot.get('session_epoch'),state_version=snapshot.get('state_version'),
                run_id=(snapshot.get('state') or {}).get('run_id'),game_version=snapshot.get('game_version'))
    report=dict(analysis_id=result.get('analysis_id') or uuid4().hex,source=result.get('source') or source,
                phase=(snapshot.get('state') or {}).get('phase'),tool_kind=kind,explicit_options=options,
                limitations=result.get('limitations',[]),module_versions=result.get('module_versions'))
    if kind=='combat': report.update(combat=result,shortlist=result.get('candidates',[]),observations=result.get('observation_boundaries',[]))
    else: report[{'add':'choices','remove':'choices','upgrade':'choices'}.get(kind,kind)]=result
    return archive(report,snapshot)

def packet(report):
    knowledge = current_snapshot()
    return dict(source=report['source'],phase=report['phase'],units='heuristic scores and conditional forecasts; not win probability',
                knowledge_revision=report.get('knowledge_revision') or (knowledge.revision if knowledge else None),
                candidates={k:event_sequence_packet(report[k]) if k=='event_sequence' else strip_details(report[k]) for k in ('shortlist','observations','choices','shop','event','event_sequence','rest','route') if k in report},
                coverage=(report.get('combat') or {}).get('coverage'),combat_forecast=forecast_packet((report.get('combat') or {}).get('lookahead')),
                autonomy=report.get('autonomy'),mechanism_audit=report.get('mechanism_audit'),
                combat_strategy=strip_details((report.get('combat') or {}).get('strategy')),
                combat_batches=strip_details((report.get('combat') or {}).get('batches')),
                combat_diagnostics=report.get('combat_diagnostics'),
                limitations=report.get('limitations'),knowledge=report.get('knowledge_compatibility'))


def forecast_packet(value):
    if value is None:return None
    result=strip_details(value)
    for candidate in result.get('candidates',[]):
        for scenario in candidate.get('scenarios',[]):
            turns=scenario.get('enemy_turns',[])
            scenario['enemy_turns']=turns[:8]
            scenario['additional_turn_records_local']=max(0,len(turns)-8)
    return result


def event_sequence_packet(value):
    """Current branches plus continuation summaries; full tree remains in local archive."""
    result=strip_details(value)
    for candidate in result.get('candidates',[]):
        for branch in candidate.get('branches',[]):
            continuation=branch.get('continuation')
            if continuation:
                branch['continuation']={k:v for k,v in continuation.items() if k!='candidates'}
                branch['continuation']['full_tree_available_locally']=True
    return result

def strip_details(value):
    if isinstance(value,dict):
        return {k:strip_details(v) for k,v in value.items() if k not in {'state_trace','normalized_input','before','after','deck_after'}}
    if isinstance(value,list): return [strip_details(v) for v in value]
    return value

def compact(report):
    result=strip_details(report)
    result.pop('ai_packet',None)
    if 'event_sequence' in report: result['event_sequence']=event_sequence_packet(report['event_sequence'])
    # Shortlists are already present; avoid duplicating them inside the combat envelope.
    combat=result.get('combat')
    if combat is not None:
        combat.pop('candidates',None);combat.pop('observation_boundaries',None)
    result['detailed_record_available']=True
    return result
