"""Restricted executable knowledge. No eval, imports or arbitrary code in data."""
import json
from functools import lru_cache
from pathlib import Path
from autoslay.paths import ROOT
from autoslay.decision.cache import digest

VERSION = '1.1.0'
RULEBOOK_PATH = ROOT/'data/knowledge/rules/rulebook.json'
CATALOG_PATH = ROOT/'data/knowledge/catalog/encyclopedia.json'
OPERATIONS = {'damage','dismantle','block','draw','energy','strength','temporary_strength',
              'vulnerable','lose_hp','enemy_strength','colossus','double_vulnerable',
              'weak_boundary','random_exhaust_boundary','upgrade_boundary','exhaust_nonattacks',
              'unpowered_damage','unpowered_block','multiply_block'}
VISIBLE_FIELDS = {'damage','block','draw','energy','strength','vulnerable','lose_hp','weak'}

def entry_fingerprint(entry):
    # Names alone are cosmetic. Descriptions, variants, variables and targeting are semantic.
    return digest({k:v for k,v in entry.items() if k not in {'name'}})

def validate_rulebook(data):
    if data.get('schema_version')!='rules/1.0' or not isinstance(data.get('game_version'),str):
        raise ValueError('Unsupported rulebook schema/version')
    if not isinstance(data.get('entries'),dict): raise ValueError('Rule entries required')
    for identity,rule in data['entries'].items():
        if not isinstance(identity,str) or not isinstance(rule,dict): raise ValueError('Invalid rule entry')
        if rule.get('category') not in {'card','potion','relic'} or not isinstance(rule.get('reference_sha256'),str):
            raise ValueError('Rule provenance required')
        if rule.get('validation') not in {'verified_adapter','pending_review'}: raise ValueError('Invalid validation status')
        if not isinstance(rule.get('effects',[]),list): raise ValueError('Effects must be ordered list')
        for effect in rule.get('effects',[]):
            if effect.get('operation') not in OPERATIONS: raise ValueError('Unsupported rule operation')
            value=effect.get('amount')
            if type(value) is int:
                if value<0: raise ValueError('Negative effect amount')
            elif isinstance(value,dict):
                if set(value)-{'visible','energy_icons','invert_legion'} or value.get('visible') not in VISIBLE_FIELDS:
                    raise ValueError('Unsupported amount expression')
            else: raise ValueError('Integer or restricted visible expression required')
            if type(effect.get('repeats',1)) is not int or not 1<=effect.get('repeats',1)<=100:
                raise ValueError('Invalid repeats')
        periodic=rule.get('periodic')
        if periodic and (set(periodic)!={'period','draw','energy'} or any(type(v) is not int or v<0 for v in periodic.values()) or periodic['period']<1):
            raise ValueError('Invalid periodic rule')

@lru_cache(maxsize=8)
def _read(rule_path,rule_stamp,catalog_path,catalog_stamp):
    rules=json.loads(Path(rule_path).read_text(encoding='utf-8-sig')); validate_rulebook(rules)
    catalog=json.loads(Path(catalog_path).read_text(encoding='utf-8-sig'))
    if catalog.get('status')!='complete': raise ValueError('Incomplete reference catalog')
    return rules,catalog,{e['entity_id']:e for e in catalog['entries']}

class Rulebook:
    def __init__(self,path=RULEBOOK_PATH,catalog_path=CATALOG_PATH):
        path,catalog_path=Path(path),Path(catalog_path)
        a,b=path.stat(),catalog_path.stat()
        self.data,self.catalog,self.references=_read(str(path), (a.st_mtime_ns,a.st_size),str(catalog_path),(b.st_mtime_ns,b.st_size))
        self.fingerprint=digest(self.data)

    def status(self,identity,expected_version=None):
        rule=self.data['entries'].get(identity); entry=self.references.get(identity)
        if not rule: return 'unmodeled'
        if expected_version and expected_version!=self.catalog['game_version']: return 'catalog_version_mismatch'
        if self.data['game_version']!=self.catalog['game_version']: return 'rule_version_mismatch'
        if self.catalog.get('game_assembly_sha256') and self.catalog['game_assembly_sha256']!=self.data.get('game_assembly_sha256'):
            return 'game_implementation_changed'
        if rule['validation']!='verified_adapter': return 'pending_review'
        if not entry: return 'reference_missing'
        if rule['reference_sha256']!=entry_fingerprint(entry): return 'reference_changed'
        return 'compatible'

    def require(self,identity,category):
        status=self.status(identity)
        if status!='compatible': raise ValueError(f'{identity}: {status}')
        rule=self.data['entries'][identity]
        if rule['category']!=category: raise ValueError('Rule category mismatch')
        return rule

    def audit(self,entity_ids=None):
        identities=self.references if entity_ids is None else sorted(set(entity_ids))
        return dict(module_version=VERSION,rulebook_version=self.data['version'],game_version=self.catalog['game_version'],
                    fingerprint=self.fingerprint,entries={i:self.status(i) for i in identities},
                    entry_scope='complete_catalog' if entity_ids is None else 'current_decision_entities_only',
                    counts={c:dict(reference=sum(e['category']==c for e in self.references.values()),
                                   compatible=sum(e['category']==c and self.status(i)=='compatible' for i,e in self.references.items()))
                            for c in ('card','potion','relic')},
                    compatibility_is_not_full_simulation_coverage=True,
                    full_support=False,live_validation='not_certified_by_reference_fingerprint',
                    unmodeled_reference_count=sum(i not in self.data['entries'] for i in self.references))
