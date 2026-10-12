"""Compact live projection; detailed evidence stays in the archive."""
from datetime import datetime,timezone
from autoslay.telemetry.decisions import compact

def live(repo):
    from autoslay.archive.repository import read
    status=read(repo.root/'.live-status.json') if (repo.root/'.live-status.json').exists() else None
    candidates=repo.catalog(limit=100)['records']
    candidates=[r for r in candidates if r.get('origin')!='imported']
    if not candidates:
        snap=(status or {}).get('snapshot')
        fresh=bool(status and (datetime.now(timezone.utc)-datetime.fromisoformat(status['observed_at'])).total_seconds()<8)
        return dict(connected=bool(fresh and snap and snap.get('connected')),archive=None,snapshot=snap,pending=len(list((repo.root/'.pending').glob('*.json'))))
    meta=candidates[0];identity=meta['archive_id']
    recent=repo.entries(identity,limit=100,descending=True)
    state=next((r for r in recent if r['kind'] in {'snapshot','heartbeat'}),None)
    decisions=repo.entries(identity,kind='decision',limit=1,descending=True)
    report_record=repo.load_event(decisions[0]['event_id']) if decisions else None
    result=dict(connected=meta.get('connection_fresh',False),archive=meta,
                snapshot=repo.load_event(state['event_id']).get('snapshot') if state else None,
                decision=compact(report_record['report']) if report_record else None,
                decision_id=report_record['event_id'] if report_record else None,
                decision_snapshot=report_record.get('snapshot') if report_record else None,
                latest=next((r for r in recent if r['kind'] not in {'heartbeat','snapshot'}),None),
                recent=[r for r in reversed(recent) if r['kind'] not in {'heartbeat','snapshot'}][-20:],
                pending=len(list((repo.root/'.pending').glob('*.json'))))
    if status:
        fresh=(datetime.now(timezone.utc)-datetime.fromisoformat(status['observed_at'])).total_seconds()<8
        result['connected']=bool(fresh and status['snapshot'].get('connected'))
        result['snapshot']=status['snapshot']
        state=status['snapshot'].get('state') or {}
        if not state.get('run_id'):
            result.update(decision=None,decision_id=None,decision_snapshot=None,latest=None,recent=[],archive=None)
    return result
