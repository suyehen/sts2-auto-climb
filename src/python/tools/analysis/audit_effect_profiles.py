"""Compile catalog description references, separate from declared rule coverage."""
import argparse
import json
from pathlib import Path
from autoslay.knowledge_base.repository import scoped,read_json,dataset_path
from autoslay.knowledge_base.templates import index
from autoslay.rules.registry import mechanism_status
from autoslay.knowledge_base.effect_profiles import compile_catalog


@scoped
def build():
    catalog=read_json(dataset_path('catalog')); references=index()[0]
    registry=compile_catalog(catalog['entries'],read_json(dataset_path('source_inventory'),default={}),
                             {i:mechanism_status(i) for i in references})
    registry.update(game_version=catalog['game_version'],
                    game_assembly_sha256=catalog.get('game_assembly_sha256'))
    from collections import Counter
    from autoslay.evaluation.entity_effects import entity_reference
    counts={}
    for identity,profile in registry['profiles'].items():
        counts.setdefault(profile['category'],Counter())
        for variant in profile['variants']:
            reference=entity_reference(dict(entity_id=identity,upgrade_level=variant['upgrade_level']),references)
            variant['reference_analysis']=reference
            counts[profile['category']][reference['coverage']]+=1
    registry['reference_counts']={k:dict(v) for k,v in counts.items()}
    registry['reference_only']=True
    return registry


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();report=build()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    summary={k:v for k,v in report.items() if k!='profiles'}
    summary_path=args.output.with_name(args.output.stem+'-summary.json')
    summary_path.write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=True))
