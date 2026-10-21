"""Offline event, monster and intent references; no implicit rule execution."""
import hashlib
import json
import os
from datetime import datetime,timezone
from pathlib import Path
from copy import deepcopy
from autoslay.knowledge_base.repository import read_json, dataset_path

PATH=dataset_path('behavior')
CATEGORIES={'event','monster','intent'}


def load(path=None):
    path=Path(path or dataset_path('behavior'))
    data=read_json(path, default=dict(schema_version='behavior/1.0',entries=[]))
    validate(data)
    return data


def validate(data):
    if data.get('schema_version')!='behavior/1.0' or not isinstance(data.get('entries'),list):raise ValueError('Invalid behavior catalog')
    seen=set()
    for entry in data['entries']:
        identity=entry.get('entity_id','')
        if entry.get('category') not in CATEGORIES or not identity.startswith(entry['category'].upper()+'.') or identity in seen:raise ValueError('Invalid or duplicate behavior ID')
        if not entry.get('name') or not isinstance(entry.get('provenance'),dict):raise ValueError('Missing behavior provenance')
        if entry.get('calculation_status') not in {'reference_only','source_extracted_unreviewed','reviewed_existing_rule'}:raise ValueError('Unsupported calculation status')
        seen.add(identity)


def save(data,path=PATH):
    """Validate the complete candidate before replacing; preserve previous revision."""
    validate(data);path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    encoded=json.dumps(data,ensure_ascii=False,indent=2).encode('utf-8')
    if path.exists():
        previous=path.read_bytes()
        if previous==encoded:return dict(changed=False,count=len(data['entries']))
        history=path.parent/'history';history.mkdir(exist_ok=True)
        (history/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json')).write_bytes(previous)
    temp=path.with_suffix('.tmp');temp.write_bytes(encoded);os.replace(temp,path)
    return dict(changed=True,count=len(data['entries']),sha256=hashlib.sha256(encoded).hexdigest())


def compact(entry,include_evidence=False,for_agent=False):
    row=deepcopy(entry)
    if not include_evidence:
        row.pop('method_evidence',None)
        row.pop('localization',None)
        if 'state_graph' in row:
            row['state_graph']={k:v for k,v in row['state_graph'].items() if k!='construction_evidence'}
    if for_agent and entry.get('category')=='event':
        row.pop('description',None)
        if entry.get('event_model'):
            row['event_model']=deepcopy(entry['event_model'])
            for method in row['event_model']['methods'].values():
                evidence=method.pop('evidence',None)
                if evidence:method['control_flow']=' '.join(evidence.split())
            row['options']=[o for o in row.get('options',[]) if o.get('source_status')!='localization_only']
            row['event_model']['runtime_priority']='Source excerpts are audit references only. State predictions use the models dataset and common commands; missing declarations and unobserved future outcomes remain unknown. Public instance values take priority over canonical defaults.'
            row.pop('dynamic_variable_expressions',None)
        else:row['option_mechanics']=entry.get('method_evidence',{})
    if for_agent:
        for key in ('numeric_note','behavior_summary','limitations'):row.pop(key,None)
    return row


def query(query='',category='',limit=10,page=1,game_version=None,path=None,include_evidence=False,for_agent=False):
    if category and category not in CATEGORIES or not 1<=limit<=50 or page<1:raise ValueError('Invalid behavior query')
    data=load(path);needle=query.strip().casefold()[:200]
    rows=[e for e in data['entries'] if (not category or e['category']==category) and (not needle or needle in json.dumps(e,ensure_ascii=False).casefold())]
    return dict(entries=[compact(e,include_evidence,for_agent) for e in rows[(page-1)*limit:page*limit]],total=len(rows),limit=limit,page=page,
                game_version=data.get('game_version'),version_matches=game_version is None or game_version==data.get('game_version'),
                game_assembly_sha256=data.get('game_assembly_sha256'),source_policy='local_game_only',
                execution_authorized=False,limitations=['Reference entries do not enable new simulation rules.','Visible live state takes priority over static reference values.'])


def relevant(snapshot,max_bytes=64_000,path=None):
    data=load(path);state=snapshot.get('state') or {};ids={e.get('entity_id') for e in state.get('enemies') or []}
    ids.add((state.get('event_screen') or {}).get('entity_id'))
    same=data.get('game_version')==snapshot.get('game_version') and bool(data.get('game_assembly_sha256'))
    if snapshot.get('game_assembly_sha256'):same=same and data.get('game_assembly_sha256')==snapshot['game_assembly_sha256']
    rows=[];omitted=[];power_references={}
    for entry in data['entries']:
        if entry['entity_id'] not in ids:continue
        row=compact(entry,for_agent=True);row['version_matches']=same
        if same and entry.get('category')=='event' and row.get('event_model'):
            event=state.get('event_screen') or {}
            row['event_model']['observed_variables']={v['name']:v.get('value') for v in event.get('dynamic_variables') or [] if v.get('name')}
            row['event_model']['observed_state']=(event.get('runtime_state') or {}).get('fields',{})
            row['event_model']['observed_option_keys']=[o.get('text_key') for o in event.get('options') or []]
        powers={p['entity_id']:p for p in row.pop('powers',[])}
        if powers:row['power_ids']=list(powers)
        if not same:row['calculation_status']='reference_only';row['version_warning']='Static reference differs from live build; do not use for exact calculation.'
        candidate_powers=power_references|powers
        if len(json.dumps(dict(entries=rows+[row],power_references=candidate_powers),ensure_ascii=False).encode())>max_bytes:omitted.append(entry['entity_id'])
        else:rows.append(row);power_references=candidate_powers
    return dict(entries=rows,version_matches=same,omitted_ids=omitted,execution_authorized=False,
                power_references=power_references,rules_enabled=False,source_policy='local_game_only')
