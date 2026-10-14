"""Refresh event source-reference audit; does not certify execution rules."""
import json
from autoslay.paths import ROOT
from autoslay.knowledge_base.behavior import load


def audit():
    data=load();rows=[]
    for e in data['entries']:
        if e['category']!='event':continue
        model=e.get('event_model') or {}
        rows.append(dict(entity_id=e['entity_id'],name=e['name'],options=len(e['options']),
                         bound_options=sum(bool(o.get('bindings')) for o in e['options']),
                         localization_only_options=[o['text_key'] for o in e['options'] if o.get('source_status')=='localization_only'],
                         variables=len(model.get('variables',{})),methods=len(model.get('methods',{})),
                         symbolic_variables=[name for name,v in model.get('variables',{}).items() if v['status']!='fixed'],
                         unresolved=model.get('unresolved',[]),custom_layout=not e['options'],
                         compiled_rule=bool(e.get('existing_rule')),source_sha256=e['provenance']['source_sha256']))
    return dict(schema_version='event-audit/1.0',game_version=data.get('game_version'),game_assembly_sha256=data.get('game_assembly_sha256'),
                count=len(rows),entries=rows,unresolved_count=sum(len(r['unresolved']) for r in rows),
                semantics='Binding and variable-reference audit, not proof of complete simulation or universal probability coverage.')


if __name__=='__main__':
    result=audit();path=ROOT/'data/knowledge/coverage/event-audit.json';temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
    print(json.dumps(dict(count=result['count'],unresolved=result['unresolved_count'],path=str(path)),ensure_ascii=False))
