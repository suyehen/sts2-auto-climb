"""Offline, explicit migration of local source evidence to model declarations."""
import argparse,json
from pathlib import Path
from autoslay.knowledge_base.monster_compiler import compile_monster


def migrate(data,behavior,catalog):
    if any(data[k]!=behavior[k] for k in ('game_version','game_assembly_sha256')):
        raise ValueError('Source evidence and model versions differ')
    cards={e.get('model_type','').split('.')[-1]:e['entity_id'] for e in catalog['entries'] if e['category']=='card'}
    previous={m['entity_id']:m for m in data.get('monsters',[])}
    declarations=[]
    for entry in behavior['entries']:
        if entry['category']!='monster':continue
        if entry['provenance']['game_assembly_sha256']!=data['game_assembly_sha256']:raise ValueError('Entry version mismatch')
        row=compile_monster(entry,behavior['ascension_thresholds'],cards)
        old=previous.get(row['entity_id'])
        if old and old['provenance']==row['provenance']:
            row['presentation_hooks']=old['presentation_hooks']
            row['unmodeled_hooks']=[h for h in row['unmodeled_hooks'] if h not in row['presentation_hooks']]
        declarations.append(row)
    data['monsters']=declarations
    data['model_references']=sorted(set(data['model_references'])|{
        c['card_id'] for r in declarations for m in r['moves'] for c in m['commands'] if 'card_id' in c})
    from autoslay.rules.model_store import validate_models
    validate_models(data)
    return data


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--models',type=Path,required=True)
    parser.add_argument('--behavior',type=Path,required=True)
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    read=lambda p:json.loads(p.read_text('utf-8'))
    data=migrate(read(args.models),read(args.behavior),read(args.catalog))
    args.output.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(monsters=len(data['monsters']),semantic_completeness_certified=False)))
