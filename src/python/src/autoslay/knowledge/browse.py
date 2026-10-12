"""Presentation taxonomy, separate from reference and executable rule fingerprints."""
import json
from collections import Counter
from pathlib import Path
from autoslay.paths import ROOT

SCOPES={'ironclad':'铁甲战士','silent':'静默猎手','defect':'故障机器人','necrobinder':'亡灵契约师','regent':'储君',
        'shared':'通用','colorless':'无色','event':'事件来源','curse':'诅咒','status':'状态',
        'token':'生成专用','quest':'任务','deprecated':'已弃用','mock':'测试资料','fallback':'备用','unknown':'归属未记录'}
CHARACTERS=set(('ironclad','silent','defect','necrobinder','regent'))
RARITIES={'Basic':'基础','Common':'普通','Uncommon':'罕见','Rare':'稀有','Ancient':'远古',
          'Starter':'初始遗物','Shop':'商店','Event':'事件','Curse':'诅咒','Status':'状态','Token':'生成专用','Quest':'任务','None':'未记录'}
TYPES={'Attack':'攻击','Skill':'技能','Power':'能力','Curse':'诅咒','Status':'状态','Quest':'任务'}
SIDECAR=ROOT/'data/knowledge/catalog/classification.json'
PORTRAITS=ROOT/'data/knowledge/assets/portraits.json'

def scope(pool):
    value=(pool or '').split('.')[-1].lower()
    return next((key for key in SCOPES if value.startswith(key+'_')), 'unknown')

def project(entry,sidecar):
    pool=entry.get('pool')
    pools=entry.get('pools') or ([pool] if pool else sidecar.get('entries',{}).get(entry['entity_id'],[]))
    provenance='game_encyclopedia'
    if not pool:
        pool=pools[0] if pools else None
        provenance='versioned_game_source' if pool else 'not_recorded'
    scopes=list(dict.fromkeys(scope(p) for p in pools)) or ['unknown']
    return entry|dict(classification=dict(scope=scopes[0],scopes=scopes,scope_label=' / '.join(SCOPES[s] for s in scopes),
                         pool=pool,pools=pools,source=provenance,rarity_label=RARITIES.get(entry.get('rarity'),'其他'),
                         card_type=(entry.get('variants') or [{}])[0].get('type') if entry['category']=='card' else None))

def query_catalog(catalog,*,q='',category='',owner='',rarity='',card_type='',include_shared=False,page=1,limit=100,sidecar=None):
    if category and category not in {'card','potion','relic'}:raise ValueError('Invalid category')
    if owner and owner not in SCOPES:raise ValueError('Invalid scope')
    if page<1 or not 1<=limit<=100:raise ValueError('Invalid pagination')
    if sidecar is None:sidecar=json.loads(SIDECAR.read_text(encoding='utf-8')) if SIDECAR.exists() else {}
    # Old derived memberships cannot silently classify a changed game build.
    if not catalog.get('game_assembly_sha256') or sidecar.get('game_assembly_sha256')!=catalog['game_assembly_sha256']:sidecar={}
    all_rows=[project(e,sidecar) for e in catalog['entries'] if not category or e['category']==category]
    portraits=json.loads(PORTRAITS.read_text(encoding='utf-8')) if PORTRAITS.exists() else {}
    if portraits.get('game_assembly_sha256')==catalog.get('game_assembly_sha256'):
        for e in all_rows:
            image=portraits.get('entries',{}).get(e['entity_id'])
            if image:
                folder={'card':'cards','potion':'potions','relic':'relics'}[e['category']]
                e['portrait_url']='/assets/'+folder+'/'+image['file']
                e['portrait_kind']=image.get('illustration','standard')
    facets=dict(scopes=[dict(value=k,label=SCOPES[k],count=v) for k,v in Counter(s for e in all_rows for s in e['classification']['scopes']).items()],
                rarities=[dict(value=k,label=RARITIES.get(k,'其他'),count=v) for k,v in Counter(e.get('rarity','None') for e in all_rows).items()],
                card_types=[dict(value=k,label=TYPES.get(k,'其他'),count=v) for k,v in Counter(e['classification']['card_type'] for e in all_rows if e['classification']['card_type']).items()])
    owners={owner}
    if owner in CHARACTERS and include_shared:owners.update(('shared','colorless'))
    needle=q.strip().lower()[:200]
    rows=[e for e in all_rows if (not owner or owners.intersection(e['classification']['scopes']))
          and (not rarity or e.get('rarity','None')==rarity)
          and (not card_type or e['classification']['card_type']==card_type)
          and (not needle or needle in json.dumps(e,ensure_ascii=False).lower())]
    return dict(entries=rows[(page-1)*limit:page*limit],total=len(rows),page=page,limit=limit,
                game_version=catalog['game_version'],facets=facets)
