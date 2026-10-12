"""Copy legacy evidence into dated archives; repeatable and never removes sources."""
from pathlib import Path
from autoslay.archive.repository import read,digest

def migrate(repo,legacy,calibration=None,apply=False):
    legacy=Path(legacy);items=[];issues=[]
    for path in list((legacy/'live').glob('*.json'))+list((legacy/'observations').glob('*.json')):
        try:
            p=read(path)
            if 'report' in p:p.update(kind='decision',event_id=p['report']['analysis_id'])
            if not p.get('snapshot') and not (p.get('location') or {}).get('run_key'):
                issues.append(dict(path=str(path),reason='No reliable run identity'));continue
            p['migration_source']=str(path);items.append(p)
        except (ValueError,KeyError,OSError) as exc:issues.append(dict(path=str(path),reason=str(exc)))
    items.sort(key=lambda p:(p.get('recorded_at',''),p.get('kind')!='snapshot'))
    decisions={p['event_id']:p for p in items if p.get('kind')=='decision'}
    if calibration:
        for category,kind in (('choices','ai_choice'),('observations','outcome_observed')):
            for path in (Path(calibration)/category).glob('*.json'):
                try:
                    value=read(path);identity=value.get('analysis_id') or value.get('decision_id');parent=decisions.get(identity)
                    if not parent:continue
                    items.append(dict(event_id='legacy_'+digest([str(path),value])[:32],kind=kind,
                        recorded_at=value.get('recorded_at') or parent['recorded_at'],snapshot=parent.get('snapshot'),
                        location=parent.get('location'),details=value,migration_source=str(path)))
                except (ValueError,OSError) as exc:issues.append(dict(path=str(path),reason=str(exc)))
    stored=[]
    if apply:
        for p in items:
            try:stored.append(repo.store(p))
            except (ValueError,OSError) as exc:issues.append(dict(event_id=p.get('event_id'),reason=str(exc)))
    return dict(mode='copy' if apply else 'preview',eligible=len(items),stored=len(stored),issues=issues,
                replay_policy='Replay records remain separate; no gameplay actions are executed')
