"""Catalog differences, compatibility gates, explicit rollback and menu-only sync."""
import asyncio
import json
import time
from pathlib import Path
from threading import RLock
from autoslay.decision.cache import digest
from autoslay.knowledge.rulebook import entry_fingerprint

LOCK=RLock()


def compact_update(result):
    """Keep large per-entity diffs in the catalog, outside default AI responses."""
    from collections import Counter
    report=result.get('update_report')
    if not report or 'changes' not in report: return result
    changes=report['changes']
    summary={k:v for k,v in report.items() if k!='changes'}
    summary.update(changed_entity_count=len(changes),change_counts=dict(Counter(r['kind'] for r in changes)),
                   full_diff=dict(path=result.get('path'),json_pointer='/update_report'))
    return result|dict(update_report=summary)

def content_fingerprint(data):
    return digest(dict(game_version=data['game_version'],game_assembly_sha256=data.get('game_assembly_sha256'),language=data['language'],scope=data['scope'],
                       entries=sorted(data['entries'],key=lambda e:e['entity_id'])))

def compare(old,new):
    before={e['entity_id']:e for e in (old or {}).get('entries',[])}
    after={e['entity_id']:e for e in new['entries']}
    changes=[]
    for identity in sorted(before.keys()|after.keys()):
        a,b=before.get(identity),after.get(identity)
        if a is None: kind='added'
        elif b is None: kind='removed'
        elif entry_fingerprint(a)!=entry_fingerprint(b): kind='semantic_changed'
        elif a!=b: kind='cosmetic_changed'
        else: continue
        changes.append(dict(entity_id=identity,category=(b or a)['category'],kind=kind,
                            changed_fields=sorted(k for k in (a or {}).keys()|(b or {}).keys() if (a or {}).get(k)!=(b or {}).get(k)),
                            rules_require_review=kind in {'added','removed','semantic_changed'}))
    version_changed=bool(old and old['game_version']!=new['game_version'])
    implementation_changed=bool(old and old.get('game_assembly_sha256')!=new.get('game_assembly_sha256'))
    return dict(previous_game_version=(old or {}).get('game_version'),game_version=new['game_version'],
                version_changed=version_changed,changes=changes,content_sha256=content_fingerprint(new),
                implementation_changed=implementation_changed,all_rules_require_review=version_changed or implementation_changed,
                note='Catalog changes do not approve executable rules; rule fingerprints are checked on every load')

def rollback(filename,path=None):
    from autoslay.knowledge.catalog import CATALOG_PATH,save_catalog
    path=Path(path or CATALOG_PATH)
    if not isinstance(filename,str) or Path(filename).name!=filename or not filename.endswith('.json'):
        raise ValueError('A history filename is required')
    data=json.loads((path.parent/'history'/filename).read_text(encoding='utf-8-sig'))
    return save_catalog(data,path,allow_rollback=True)


def rebind_added_export_metadata(rulebook,old,new):
    """Explicit one-time exporter schema migration, never game-version approval.

    The already reviewed rules must pin this exact game binary. All previously
    exported semantic fields must remain byte-equivalent JSON values. Only newly
    added model_type/dynamic_variables fields are permitted; no changed, removed,
    or new entities get an executable rule.
    """
    from copy import deepcopy
    if rulebook['game_version']!=new['game_version'] or old['game_version']!=new['game_version']:
        raise ValueError('Metadata migration cannot approve a game version change')
    if not new.get('game_assembly_sha256') or new['game_assembly_sha256']!=rulebook.get('game_assembly_sha256'):
        raise ValueError('Metadata migration requires the previously pinned game assembly')
    if old.get('game_assembly_sha256') and old['game_assembly_sha256']!=new['game_assembly_sha256']:
        raise ValueError('Previous assembly differs')
    before={e['entity_id']:e for e in old['entries']}; after={e['entity_id']:e for e in new['entries']}
    result=deepcopy(rulebook); accepted=[]; rejected=[]
    for identity,rule in result['entries'].items():
        a,b=before.get(identity),after.get(identity)
        if a is None or b is None or rule['reference_sha256']!=entry_fingerprint(a):
            rejected.append(identity); continue
        if any(b.get(k)!=v for k,v in a.items() if k!='name') or set(b)-set(a)-{'model_type','dynamic_variables'}:
            rejected.append(identity); continue
        rule['reference_sha256']=entry_fingerprint(b)
        rule['export_metadata_migration']=dict(previous_reference_sha256=entry_fingerprint(a),
            assembly_sha256=new['game_assembly_sha256'],added_fields=sorted(set(b)-set(a)),
            basis='same pinned binary; every previous semantic field unchanged; exporter metadata addition only')
        accepted.append(identity)
    return result,dict(rebound=accepted,unchanged_validation=True,rejected=rejected,full_support=False)

class CatalogSynchronizer:
    """No save loading, menu navigation or gameplay. Game must already be in menu."""
    def __init__(self,path=None,interval=60):
        from autoslay.knowledge.catalog import CATALOG_PATH
        self.path=Path(path or CATALOG_PATH); self.interval=interval; self.last_check=0
        from autoslay.knowledge.installation import version_status
        self.task=None; self.status=dict(status='not_checked',installation=version_status(catalog_path=self.path))

    async def observe(self,snapshot):
        if not snapshot.get('connected') or snapshot.get('error'):
            from autoslay.knowledge.installation import version_status
            self.status=dict(status='disconnected',installation=version_status(catalog_path=self.path)); return self.status
        version=snapshot.get('game_version'); menu=(snapshot.get('state') or {}).get('phase')=='main_menu'
        try: local=json.loads(self.path.read_text(encoding='utf-8-sig')); matches=local['game_version']==version
        except (OSError,ValueError,KeyError): matches=False
        if not menu:
            self.status=dict(status='deferred_until_main_menu',version_matches=matches,game_version=version)
            return self.status
        if self.task and not self.task.done(): return self.status
        if time.monotonic()-self.last_check<self.interval: return self.status
        self.last_check=time.monotonic(); self.status=dict(status='refreshing',game_version=version)
        self.task=asyncio.create_task(self._refresh(version))
        return self.status

    async def _refresh(self,expected_version):
        from autoslay.knowledge.catalog import export_catalog
        try:
            result=await export_catalog(refresh=True,path=self.path,expected_game_version=expected_version)
            self.status=dict(status='failed' if result.get('error') else 'current',result=result)
        except Exception as exc:
            self.status=dict(status='failed',error=type(exc).__name__)

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try: await self.task
            except asyncio.CancelledError: pass
