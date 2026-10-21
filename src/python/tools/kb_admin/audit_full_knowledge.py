"""Audit source availability and native definitions without generating rules."""
import json
from autoslay.paths import ROOT
from autoslay.knowledge_base.repository import read_json,dataset_path
from autoslay.knowledge_base.source_audit import audit

if __name__=='__main__':
    report=audit(read_json(dataset_path('catalog')))
    path=ROOT/'data/knowledge/coverage/catalog-source-audit.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(counts=report['counts'],full_support=False,path=str(path)),ensure_ascii=True))
