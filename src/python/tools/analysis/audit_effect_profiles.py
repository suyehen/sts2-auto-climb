"""Compile every local catalog entry; report data, semantic and simulation coverage."""
import argparse
import json
from pathlib import Path
from autoslay.knowledge_base.repository import scoped,read_json,dataset_path
from autoslay.knowledge_base.rulebook import Rulebook
from autoslay.knowledge_base.effect_profiles import compile_catalog


@scoped
def build():
    book=Rulebook()
    registry=compile_catalog(book.catalog['entries'],read_json(dataset_path('source_inventory'),default={}),
                             {i:book.status(i) for i in book.references})
    registry.update(game_version=book.catalog['game_version'],
                    game_assembly_sha256=book.catalog.get('game_assembly_sha256'))
    from collections import Counter
    from autoslay.evaluation.entity_effects import evaluate_entity
    from autoslay.evaluation.run import RunContext,RunPolicy
    context=RunContext(40,80,100);policy=RunPolicy();counts={}
    for identity,profile in registry['profiles'].items():
        counts.setdefault(profile['category'],Counter())
        for variant in profile['variants']:
            valuation=evaluate_entity(dict(entity_id=identity,upgrade_level=variant['upgrade_level']),
                                      context,book.references,policy)
            variant['valuation']=valuation
            counts[profile['category']][valuation['coverage']]+=1
    registry['valuation_counts']={k:dict(v) for k,v in counts.items()}
    registry['valuation_context']=dict(hp=40,max_hp=80,gold=100,opportunities='unknown')
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
