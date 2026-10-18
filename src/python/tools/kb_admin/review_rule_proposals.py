"""Promote explicit source-reviewed straight-line rules, with source and reference pins.

This command is not the automatic update path. Conditional/trigger proposals must
be implemented and reviewed separately. Review never asserts full game coverage.
"""
import argparse,json,hashlib
from autoslay.paths import ROOT
from autoslay.knowledge_base.rulebook import Rulebook,entry_fingerprint,validate_rulebook
from autoslay.knowledge_base.source_audit import SOURCE,recognize_card,audit
from autoslay.knowledge_base.repository import read_json,dataset_path

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('entity_ids',nargs='+');args=parser.parse_args()
    book=Rulebook(); report,proposals=audit(book.catalog,book.data); active=json.loads(json.dumps(book.data))
    rows={r['entity_id']:r for r in report['rows']}
    inventory=read_json(dataset_path('source_inventory'))
    if (not book.catalog.get('game_assembly_sha256')
            or inventory.get('game_assembly_sha256')!=book.catalog['game_assembly_sha256']
            or inventory.get('game_version')!=book.catalog['game_version']):
        raise ValueError('Source inventory must match the current catalog build')
    sources={e['entity_id']:e for e in inventory['entries']}
    for identity in args.entity_ids:
        row=rows.get(identity)
        if not row or not row.get('recognized_sequence'): raise ValueError('No recognized complete straight-line program: '+identity)
        if sources.get(identity,{}).get('source_sha256')!=row['source_sha256']:
            raise ValueError('Source differs from the pinned inventory: '+identity)
        rule=proposals['entries'][identity]
        # Explicitly reviewed existing adapters may be corrected as the grammar improves.
        if identity in active['entries']:
            namespace,classname=book.references[identity]['model_type'].rsplit('.',1)
            source=SOURCE/namespace/(classname+'.cs')
            rule=dict(active['entries'][identity],category=row['category'],reference_sha256=entry_fingerprint(book.references[identity]),
                      source=str(source.relative_to(SOURCE)),source_sha256=row['source_sha256'],**row['recognized_sequence'])
        source=SOURCE/rule['source']
        if hashlib.sha256(source.read_bytes()).hexdigest()!=rule['source_sha256']: raise ValueError('Source changed during review')
        rule['validation']='verified_adapter'
        rule['review_evidence']='restricted straight-line source grammar checked; arithmetic/flow regression required; live validation tracked separately'
        active['entries'][identity]=rule
    validate_rulebook(active)
    path=ROOT/'data/knowledge/rules/rulebook.json'; temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(active,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(path)
    print(json.dumps(dict(reviewed=args.entity_ids,full_support=False)))
