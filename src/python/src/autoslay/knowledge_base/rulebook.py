"""Restricted executable knowledge. No eval, imports or arbitrary code in data."""
from autoslay.knowledge_base.repository import read_json, read_view, dataset_path
from pathlib import Path
from autoslay.decision.cache import digest

VERSION = '1.3.0'
RULEBOOK_PATH = dataset_path('rules')
CATALOG_PATH = dataset_path('catalog')
OPERATIONS = {'damage','dismantle','block','draw','energy','strength','temporary_strength',
              'vulnerable','lose_hp','enemy_strength','colossus','double_vulnerable',
              'weak_boundary','random_exhaust_boundary','upgrade_boundary','exhaust_nonattacks',
              'unpowered_damage','unpowered_block','multiply_block','stars',
              'heal','heal_percent','gain_max_hp','dexterity','temporary_dexterity','weak','no_draw'}
VISIBLE_FIELDS = {'damage','block','draw','energy','strength','vulnerable','lose_hp','weak'}

def entry_fingerprint(entry):
    # Names alone are cosmetic. Descriptions, variants, variables and targeting are semantic.
    return digest({k:v for k,v in entry.items() if k not in {'name'}})


def reference_matches(rule, entry):
    if rule['reference_sha256'] == entry_fingerprint(entry):
        return True
    # The exporter added ownership facets to relics. Prove the *entire* previous
    # entry is unchanged, instead of updating reviewed hashes or ignoring effects.
    # Cards keep pool identity in their rule provenance. Binary/version checks
    # still happen in status() before this narrowly scoped metadata migration.
    if entry.get('category') == 'relic' and isinstance(entry.get('pools'), list) and entry.get('pool') in entry['pools']:
        previous = {k:v for k,v in entry.items() if k not in {'pool', 'pools'}}
        return rule['reference_sha256'] == entry_fingerprint(previous)
    return False

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
                variable=set(value)=={'variable'} and isinstance(value['variable'],str) and value['variable'].isascii() and value['variable'].isidentifier()
                if not variable and (set(value)-{'visible','energy_icons','invert_legion'} or value.get('visible') not in VISIBLE_FIELDS):
                    raise ValueError('Unsupported amount expression')
            else: raise ValueError('Integer or restricted visible expression required')
            repeats=effect.get('repeats',1)
            variable_repeat=(effect['operation']=='damage' and isinstance(repeats,dict) and set(repeats)=={'variable'}
                and isinstance(repeats['variable'],str) and repeats['variable'].isascii() and repeats['variable'].isidentifier())
            if not variable_repeat and (type(repeats) is not int or not 1<=repeats<=100):
                raise ValueError('Invalid repeats')
        periodic=rule.get('periodic')
        if periodic and (set(periodic)!={'period','draw','energy'} or any(type(v) is not int or v<0 for v in periodic.values()) or periodic['period']<1):
            raise ValueError('Invalid periodic rule')
        scheduled=rule.get('scheduled',[])
        if not isinstance(scheduled,list):raise ValueError('Invalid scheduled rules')
        for item in scheduled:
            if (rule['category']!='relic' or set(item)!={'phase','turn','operation','variable'}
                    or item['phase'] not in {'after_block_cleared','side_turn_start','after_hand_draw','side_turn_end'}
                    or item['operation'] not in {'unpowered_damage','unpowered_block','energy'}
                    or not isinstance(item['variable'],str) or not item['variable'].isascii() or not item['variable'].isidentifier()
                    or item['turn'] is not None and (type(item['turn']) is not int or item['turn']<1)):
                raise ValueError('Invalid scheduled rule')

def _read(rule_path,rule_stamp,catalog_path,catalog_stamp):
    def build(data):
        validate_rulebook(data)
        return data, digest(data)
    rules,fingerprint=read_view(rule_path, 'validated-rulebook/1', build)
    catalog=read_json(catalog_path)
    if catalog.get('status')!='complete': raise ValueError('Incomplete reference catalog')
    return rules,catalog,{e['entity_id']:e for e in catalog['entries']},fingerprint

class Rulebook:
    def __init__(self,path=None,catalog_path=None):
        path,catalog_path=Path(path or dataset_path('rules')),Path(catalog_path or dataset_path('catalog'))
        self.data,self.catalog,self.references,self.fingerprint=_read(str(path), None,str(catalog_path),None)

    def status(self,identity,expected_version=None):
        rule=self.data['entries'].get(identity); entry=self.references.get(identity)
        if not rule: return 'unmodeled'
        if expected_version and expected_version!=self.catalog['game_version']: return 'catalog_version_mismatch'
        if self.data['game_version']!=self.catalog['game_version']: return 'rule_version_mismatch'
        if self.catalog.get('game_assembly_sha256') and self.catalog['game_assembly_sha256']!=self.data.get('game_assembly_sha256'):
            return 'game_implementation_changed'
        if rule['validation']!='verified_adapter': return 'pending_review'
        if not entry: return 'reference_missing'
        if not reference_matches(rule, entry): return 'reference_changed'
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
