"""Export complete reference-versus-executable coverage without starting gameplay."""
import json
from autoslay.paths import ROOT
from autoslay.decision.mechanisms import inventory

if __name__=='__main__':
    result=inventory()
    path=ROOT/'data/knowledge/coverage/mechanism-audit.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)
    print(json.dumps(dict(path=str(path),count=len(result['entries']),counts=result['counts']),ensure_ascii=False))
