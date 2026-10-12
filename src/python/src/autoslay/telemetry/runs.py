"""Public run observations and action receipts. Never infer unseen map contents."""
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from autoslay.telemetry import decisions
_TREE_CACHE={}


def location(snapshot, origin='live'):
    s=snapshot.get('state') or {}; m=s.get('map') or {}
    stable=s.get('run_history_id'); runtime=s.get('run_id')
    # Legacy bridges cannot reliably join a save across process restarts.
    key=stable or (f"{snapshot.get('session_epoch')}:{runtime}" if runtime else None)
    run_key=hashlib.sha256(f'{origin}:{key}'.encode()).hexdigest()[:32] if key else None
    coord=m.get('current_position')
    return dict(run_key=run_key,identity_scope='saved_run' if stable else 'session',
                act_index=m.get('act_index'),act_count=s.get('act_count'),floor=s.get('floor'),
                coordinate=coord,node_key=f"{coord.get('column')}:{coord.get('row')}" if coord else None,
                generation=m.get('generation'),room_type=s.get('room_type'),phase=s.get('phase'),
                turn=(s.get('player') or {}).get('turn'))


def _base(directory): return Path(directory or decisions.DIRECTORY)/'observations'


def _save(payload, identity, directory):
    from autoslay.archive.collector import store
    return store(dict(payload,event_id=identity),directory)


def observe(snapshot,directory=None):
    if not snapshot.get('connected') or snapshot.get('error'): return
    loc=location(snapshot)
    if not loc['run_key']: return
    identity='state_'+hashlib.sha256(json.dumps([snapshot.get('session_epoch'),snapshot.get('state_version'),loc],sort_keys=True).encode()).hexdigest()[:32]
    if (_base(directory)/(identity+'.json')).exists(): return
    _save(dict(event_id=identity,kind='snapshot',recorded_at=datetime.now(timezone.utc).isoformat(),
               location=loc,snapshot=decisions.public_snapshot(snapshot)),identity,directory)


def action(kind,request,result=None,directory=None):
    """Every action is recorded even when no analyzer was called; no AI reason is invented."""
    identity=uuid4().hex
    snap=(result or {}).get('snapshot') or {}
    if not isinstance(snap,dict): snap={}
    loc=location(snap)
    if not loc['run_key']:
        fallback=location(dict(session_epoch=request.get('session_epoch'),state=dict(run_id=request.get('run_id'))))
        loc['run_key']=fallback['run_key']
        if str(request.get('run_id','')).startswith('menu_'): loc['identity_scope']='menu'
    receipt={k:v for k,v in (result or {}).items() if k in {'request_id','status','error','message','dispatched','wait_expired'}}
    _save(dict(event_id=identity,kind=kind,recorded_at=datetime.now(timezone.utc).isoformat(),location=loc,
               request={k:v for k,v in request.items() if k in {'request_id','run_id','session_epoch','expected_state_version','action_id','target_id'}},
               receipt=receipt,snapshot=decisions.public_snapshot(snap) if snap.get('state') else None),identity,directory)
    if snap.get('state'): observe(dict(snap,connected=True),directory)


def safe_observe(snapshot):
    try: observe(snapshot)
    except (OSError,ValueError): logging.exception('Run observation could not be persisted')


def safe_action(kind,request,result=None):
    try: action(kind,request,result)
    except (OSError,ValueError): logging.exception('Action observation could not be persisted')


def stage(kind,snapshot,details=None):
    identity=uuid4().hex
    try:
        _save(dict(event_id=identity,kind=kind,recorded_at=datetime.now(timezone.utc).isoformat(),
                   location=location(snapshot),details=details or {},snapshot=decisions.public_snapshot(snapshot)),identity,None)
    except (OSError,ValueError): logging.exception('Decision stage could not be persisted')


def events(directory=None):
    result=[]
    for path in _base(directory).glob('*.json'):
        try: result.append(json.loads(path.read_text(encoding='utf-8')))
        except (OSError,ValueError): continue
    from autoslay.archive.repository import for_legacy
    repo=for_legacy(directory)
    for e in repo.entries(limit=10000,skip_heartbeat=True):
        if e['kind']!='decision':result.append(repo.load_event(e['event_id']))
    unique={e['event_id']:e for e in result}
    return sorted(unique.values(),key=lambda e:e['recorded_at'])


def load_event(identity,directory=None):
    from autoslay.archive.repository import for_legacy
    try:return for_legacy(directory).load_event(decisions.safe_id(identity))
    except FileNotFoundError:pass
    return json.loads((_base(directory)/(decisions.safe_id(identity)+'.json')).read_text(encoding='utf-8'))


def tree(directory=None):
    base=Path(directory or decisions.DIRECTORY)
    signature=tuple(sorted((str(p),p.stat().st_mtime_ns,p.stat().st_size)
                           for folder in ('live','observations') for p in (base/folder).glob('*.json')))
    from autoslay.archive.repository import for_legacy
    signature+=(('archive_cursor',for_legacy(directory).latest_cursor(),0),)
    cache_key=str(base.resolve())
    cached=_TREE_CACHE.get(cache_key)
    if cached and cached[0]==signature: return cached[1]
    groups={}
    # Full history, including old records. Replay evidence never merges into a live run.
    entries=[dict(kind='decision',record_id=r['record_id'],origin=r['origin'],recorded_at=r['recorded_at'],location=r['location'])
             for r in decisions.list_records(directory,limit=None) if r['origin']=='live']+events(directory)
    entries.sort(key=lambda e:e['recorded_at'])
    runtime_aliases={}
    for e in entries:
        snap=e.get('snapshot') or {}; s=snap.get('state') or {}
        if s.get('run_history_id'):
            runtime_aliases[(snap.get('session_epoch'),s.get('run_id'))]=e['location']['run_key']
    submitted={e['request']['request_id']:e['location'] for e in entries
               if e['kind']=='action_submitted' and (e.get('request') or {}).get('request_id')}
    for e in entries:
        loc=dict(e.get('location') or {}); key=loc.get('run_key')
        request=e.get('request') or {}
        if e['kind'] in {'action_receipt','action_result'} and request.get('request_id') in submitted:
            loc=dict(submitted[request['request_id']]); key=loc.get('run_key')
        if request:
            key=runtime_aliases.get((request.get('session_epoch'),request.get('run_id')),key)
        if not key: continue
        run=groups.setdefault(key,dict(run_key=key,identity_scope=loc.get('identity_scope'),acts={},unlocated=[],latest_at=e['recorded_at']))
        run['latest_at']=e['recorded_at']
        if loc.get('act_index') is not None: run['latest_location']=loc
        summary={k:v for k,v in e.items() if k not in {'snapshot'}}; summary['location']=loc
        idx=loc.get('act_index')
        if idx is None:
            run['unlocated'].append(summary); continue
        act=run['acts'].setdefault(str(idx),dict(act_index=idx,maps=[],timeline=[]))
        act['timeline'].append(summary)
        m=(e.get('snapshot') or {}).get('state',{}).get('map') or {}
        # A travel receipt belongs to its submission node; its after-state can be
        # in the next act and is separately recorded as an observed snapshot.
        if m.get('act_index') != idx: m={}
        if m.get('nodes'):
            version=next((v for v in act['maps'] if v['generation']==m.get('generation')),None)
            value=dict(generation=m.get('generation'),recorded_at=e['recorded_at'],map=m)
            if version: version.update(value)
            else: act['maps'].append(value)
        if m.get('current_position') and e['kind']=='snapshot': act['current_position']=m['current_position']
        if m.get('visited') and e['kind']=='snapshot': act['visited']=m['visited']
    # Attach request receipts to the exact submission context, never by time proximity.
    by_request={}
    for run in groups.values():
        for act in run['acts'].values():
            for entry in act['timeline']:
                req=entry.get('request') or {}
                if req.get('request_id') and entry['kind']=='action_submitted': by_request[req['request_id']]=(run,act,entry['location'])
    for run in list(groups.values()):
        pending=[]
        for entry in run['unlocated']:
            req=entry.get('request') or {}; match=by_request.get(req.get('request_id'))
            if match:
                target,act,loc=match; entry['location']=loc; act['timeline'].append(entry)
            else: pending.append(entry)
        run['unlocated']=pending
        for act in run['acts'].values():
            act['timeline'].sort(key=lambda e:e['recorded_at'])
            act['maps'].sort(key=lambda m:m['recorded_at'])
    result=sorted((r for r in groups.values() if r['acts'] or r['unlocated']),key=lambda r:r['latest_at'],reverse=True)
    _TREE_CACHE[cache_key]=(signature,result)
    while len(_TREE_CACHE)>8: _TREE_CACHE.pop(next(iter(_TREE_CACHE)))
    return result
