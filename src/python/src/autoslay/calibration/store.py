"""Persistent public decision evidence; intent, observations and review labels stay separate."""
import json
import re
from datetime import datetime,timezone
from pathlib import Path
from threading import RLock
from autoslay.paths import ROOT
from autoslay.decision.cache import digest

DIRECTORY=ROOT/'data/calibration'
LOCK=RLock()

def safe_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',value): raise ValueError('Invalid calibration ID')
    return value

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp'); temporary.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2),encoding='utf-8')
    temporary.replace(path)

def archive(report,snapshot,directory=None):
    directory=Path(directory or DIRECTORY); analysis=safe_id(report['analysis_id'])
    state=snapshot['state']
    if report['source']['state_sha256']!=digest(state): raise ValueError('Analysis state does not match snapshot')
    # Public state only. Never archive transport/config/token values.
    public={k:state[k] for k in ('phase','floor','ascension','room_type','player','enemies','run_id','command_context_id','map','legal_actions','event_screen') if k in state}
    payload=dict(schema_version='calibration/1.0',recorded_at=datetime.now(timezone.utc).isoformat(),report=report,
                 snapshot=dict(game_version=snapshot['game_version'],schema_version=snapshot['schema_version'],state=public))
    with LOCK: save(directory/'analyses'/(analysis+'.json'),payload)
    return dict(analysis_id=analysis,archived=True,choice_recorded=False)

def load(analysis_id,directory=None):
    return json.loads((Path(directory or DIRECTORY)/'analyses'/(safe_id(analysis_id)+'.json')).read_text(encoding='utf-8-sig'))

def candidates(report,group):
    if group in {'shortlist','observations'}: return report.get(group,[])
    if group in {'choices','shop','event','event_sequence','route','rest'}: return (report.get(group) or {}).get('candidates',[])
    if group in {'combat.candidates','combat.observation_boundaries','combat.wait_candidates'}:
        return (report.get('combat') or {}).get(group.split('.')[1],[])
    raise ValueError('Unsupported candidate group')

def choose(analysis_id,group,index,reason,snapshot,trial_id=None,directory=None):
    directory=Path(directory or DIRECTORY); evidence=load(analysis_id,directory); report=evidence['report']; source=report['source']
    if type(index) is not int or index<0 or index>=len(candidates(report,group)): raise ValueError('Candidate index outside report')
    current=snapshot.get('state') or {}
    if source.get('session_epoch')!=snapshot.get('session_epoch') or source.get('state_version')!=snapshot.get('state_version') or source['state_sha256']!=digest(current):
        raise ValueError('Stale decision evidence; analyze fresh state')
    if snapshot.get('game_version')!=source['game_version']: raise ValueError('Game version changed')
    if not isinstance(reason,str) or not reason.strip() or len(reason)>4000: raise ValueError('Concise choice reason required')
    trial=safe_id(trial_id) if trial_id else digest(dict(epoch=source.get('session_epoch'),run=source.get('run_id')))[:24]
    payload=dict(decision_id=analysis_id,analysis_id=analysis_id,trial_id=trial,group=group,index=index,reason=reason,
        candidate=candidates(report,group)[index],source=source,weights=report.get('weights'),interaction_weights=report.get('interaction_weights'),
        module_versions=report.get('module_versions'),phase=report['phase'],is_intent_only=True,execution_authorized=False)
    payload['policy_hash']=digest(dict(game_version=source['game_version'],weights=payload['weights'],
                                      interactions=payload['interaction_weights'],modules=payload['module_versions'],combat_weights=report.get('combat_weights'),analysis_policy_hash=report.get('analysis_policy_hash')))
    payload['trial_metadata']=dict(character=current.get('player',{}).get('character'),ascension=current.get('ascension'),
                                   seed=None,seed_basis='not exposed in this snapshot')
    path=directory/'choices'/(safe_id(analysis_id)+'.json')
    with LOCK:
        if path.exists():
            old=json.loads(path.read_text(encoding='utf-8-sig'))
            if old!=payload: raise ValueError('Decision ID already records a different choice')
        else: save(path,payload)
    return dict(decision_id=analysis_id,trial_id=trial,recorded=True,execution_authorized=False)

def observe(decision_id,horizon,snapshot,note='',trial_result=None,directory=None):
    directory=Path(directory or DIRECTORY)
    choice=json.loads((directory/'choices'/(safe_id(decision_id)+'.json')).read_text(encoding='utf-8-sig'))
    if horizon not in {'action','turn','combat','run'}: raise ValueError('Unknown outcome horizon')
    if trial_result not in {None,'win','loss','abandoned'} or trial_result and horizon!='run': raise ValueError('Run label only valid at run horizon')
    state=snapshot.get('state') or {}; source=choice['source']
    current_run=state.get('command_context_id') or state.get('run_id')
    if snapshot.get('session_epoch')!=source.get('session_epoch') or current_run!=source.get('run_id') or snapshot.get('game_version')!=source['game_version']:
        raise ValueError('Observation belongs to different session/run/version; explicit reviewed import needed across restart')
    if snapshot.get('state_version',-1)<source['state_version']: raise ValueError('Observation predates choice')
    if not snapshot.get('connected',True) or snapshot.get('error'): raise ValueError('Observation unavailable')
    player=state.get('player') or {}; creature=player.get('creature') or {}
    baseline=load(decision_id,directory)['snapshot']['state'].get('player') or {}
    before_hp=(baseline.get('creature') or {}).get('hp')
    hp=creature.get('hp')
    payload=dict(decision_id=decision_id,trial_id=choice['trial_id'],horizon=horizon,note=note,
        phase=state.get('phase'),state_version=snapshot.get('state_version'),hp=hp,gold=player.get('gold'),energy=player.get('energy'),
        hp_loss=None if hp is None or before_hp is None else before_hp-hp,
        dead=hp is not None and hp<=0,trial_result=trial_result,
        result_label_source='caller_reported' if trial_result else None,
        limits=['HP difference may include healing or multiple actions; not attributable to one card',
                'chosen outcomes provide no unchosen counterfactual; caller win labels are not auto-verified'])
    if choice['phase']=='event' and horizon=='action':
        from autoslay.calibration.events import compare
        payload['event_comparison']=compare(choice['candidate'],baseline,player)
    key=digest(dict(decision_id=decision_id,horizon=horizon,state_version=snapshot.get('state_version'),result=trial_result))
    path=directory/'observations'/(key+'.json')
    with LOCK:
        if path.exists():
            old=json.loads(path.read_text(encoding='utf-8-sig'))
            if old!=payload: raise ValueError('Observation ID conflict')
        else: save(path,payload)
    return dict(observation_id=key,recorded=True,execution_authorized=False)
