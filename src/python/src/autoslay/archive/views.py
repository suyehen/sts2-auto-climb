"""Compact live projection; detailed evidence stays in the archive."""
from datetime import datetime,timezone
from autoslay.telemetry.decisions import compact

def live(repo):
    from autoslay.archive.repository import read
    from autoslay.dashboard.monster_graphs import project as monster_graphs
    status=read(repo.root/'.live-status.json') if (repo.root/'.live-status.json').exists() else None
    candidates=repo.catalog(limit=100)['records']
    candidates=[r for r in candidates if r.get('origin')!='imported']
    if not candidates:
        snap=(status or {}).get('snapshot')
        fresh=bool(status and (datetime.now(timezone.utc)-datetime.fromisoformat(status['observed_at'])).total_seconds()<8)
        return dict(connected=bool(fresh and snap and snap.get('connected')),archive=None,snapshot=snap,node_decisions=[],monster_graphs=monster_graphs(snap or {}),pending=len(list((repo.root/'.pending').glob('*.json'))))
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
    result['node_decisions']=node_decisions(repo,result)
    result['monster_graphs']=monster_graphs(result.get('snapshot') or {})
    return result


def node_decisions(repo,view):
    """All analyses at the visible map node, independent of the recent-event cap."""
    from autoslay.telemetry.runs import location
    snapshot=view.get('snapshot') or {}
    loc=location(snapshot)
    meta=view.get('archive') or {}
    if not loc.get('run_key') or loc['run_key']!=meta.get('archive_id'):
        return []
    if loc.get('act_index') is None or loc.get('node_key') is None:
        return []
    result=[];cursor=0
    while True:
        page=repo.entries(meta['archive_id'],kind='decision',act=loc['act_index'],
                          node=loc['node_key'],after=cursor,limit=1000)
        result.extend(e for e in page if (e.get('location') or {}).get('generation')==loc.get('generation'))
        if len(page)<1000:
            return result
        cursor=page[-1]['cursor']
