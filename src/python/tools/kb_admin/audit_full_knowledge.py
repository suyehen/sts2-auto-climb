"""Audit every exported model against local implementation; proposals never auto-approved."""
import argparse,json
from autoslay.paths import ROOT
from autoslay.knowledge_base.rulebook import Rulebook
from autoslay.knowledge_base.source_audit import audit

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--write-proposals',action='store_true'); args=parser.parse_args()
    book=Rulebook(); report,proposed=audit(book.catalog,book.data)
    path=ROOT/'data/knowledge/coverage/catalog-source-audit.json';path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.write_proposals:
        (ROOT/'data/knowledge/rules/proposals.json').write_text(json.dumps(proposed,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(counts=report['counts'],full_support=report['full_support'],path=str(path)),ensure_ascii=True))
