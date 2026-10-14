"""Promote explicit source-reviewed straight-line rules, with source and reference pins.

This command is not the automatic update path. Conditional/trigger proposals must
be implemented and reviewed separately. Review never asserts full game coverage.
"""
import argparse,json,hashlib
from autoslay.paths import ROOT
from autoslay.knowledge_base.rulebook import Rulebook,entry_fingerprint,validate_rulebook
from autoslay.knowledge_base.source_audit import SOURCE,recognize_card,audit

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('entity_ids',nargs='+');args=parser.parse_args()
    book=Rulebook(); report,proposals=audit(book.catalog,book.data); active=json.loads(json.dumps(book.data))
    rows={r['entity_id']:r for r in report['rows']}
    for identity in args.entity_ids:
        row=rows.get(identity)
        if not row or not row.get('recognized_sequence'): raise ValueError('No recognized complete straight-line program: '+identity)
        rule=proposals['entries'][identity]
        # Explicitly reviewed existing adapters may be corrected as the grammar improves.
        if identity in active['entries']:
            source=SOURCE/('MegaCrit.Sts2.Core.Models.'+{'card':'Cards','potion':'Potions'}[row['category']])/(''.join(w.title() for w in identity.split('.',1)[1].split('_'))+'.cs')
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
