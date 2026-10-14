"""Explicit exporter schema migration; game changes and semantic edits are refused."""
import argparse,json
from autoslay.knowledge_base.rulebook import Rulebook,validate_rulebook
from autoslay.knowledge_base.updates import rebind_added_export_metadata
from autoslay.paths import ROOT
from autoslay.knowledge_base.repository import read_json

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--previous',required=True); args=parser.parse_args()
    book=Rulebook(); previous=read_json(args.previous, fresh=True)
    rules,report=rebind_added_export_metadata(book.data,previous,book.catalog); validate_rulebook(rules)
    path=ROOT/'data/knowledge/rules/rulebook.json'; temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(rules,ensure_ascii=False,indent=2),encoding='utf-8'); temp.replace(path)
    (ROOT/'data/knowledge/coverage/export-metadata-migration.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(dict(rebound_count=len(report['rebound']),rejected=report['rejected'],full_support=False)))
