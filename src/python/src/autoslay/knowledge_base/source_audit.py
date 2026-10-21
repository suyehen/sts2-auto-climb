"""Read-only source inventory. Never generates or approves executable mechanics."""
import hashlib
import re
from pathlib import Path
from autoslay.paths import ROOT
from autoslay.rules.registry import mechanism_status

SOURCE=ROOT/'.runtime/cache/game-source'


def body(text,method):
    match=re.search(r'\b'+re.escape(method)+r'\([^\n]*\)\s*\{',text)
    if not match: return None
    start=match.end(); depth=1
    for index in range(start,len(text)):
        if text[index]=='{': depth+=1
        elif text[index]=='}':
            depth-=1
            if depth==0: return text[start:index]
    raise ValueError('Unbalanced source method')


def audit(catalog,source=SOURCE):
    rows=[]
    for entry in catalog['entries']:
        model=entry.get('model_type')
        path=None
        if model and '.' in model:
            namespace,classname=model.rsplit('.',1)
            path=Path(source)/namespace/(classname+'.cs')
        row=dict(entity_id=entry['entity_id'],category=entry['category'],name=entry.get('name'),
                 data_present=True,source_found=bool(path and path.is_file()),
                 mechanism_status=mechanism_status(entry['entity_id']))
        if row['source_found']:
            text=path.read_text(encoding='utf-8-sig')
            row.update(source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                       hooks=sorted(set(re.findall(r'override\s+(?:async\s+)?[\w<>?]+\s+(\w+)\s*\(',text))))
        rows.append(row)
    return dict(schema_version='source-inventory-audit/2',game_version=catalog['game_version'],rows=rows,
                counts={c:dict(total=sum(r['category']==c for r in rows),
                               source_found=sum(r['category']==c and r['source_found'] for r in rows),
                               mechanisms_defined=sum(r['category']==c and r['mechanism_status']=='defined' for r in rows))
                        for c in ('card','potion','relic')},
                full_support=False,execution_source='native_code')
