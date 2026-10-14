"""Reviewed pickup effects, bound to source, reference and live assembly hashes.

This registry does not exempt granted relics or certify arbitrary hook names.
"""
from autoslay.knowledge_base.repository import dataset_path, read_json, read_view
from autoslay.knowledge_base.rulebook import entry_fingerprint
import hashlib

VERSION = '1.1.0'


def reviewed_pickup(snapshot, book):
    registry = read_json(dataset_path('mechanism_scope'))
    inventory = read_json(dataset_path('source_inventory'))
    if (registry.get('schema_version') != 'mechanism-scope/1.0'
            or not snapshot.get('game_assembly_sha256')
            or any(data.get('game_assembly_sha256') != snapshot['game_assembly_sha256']
                   or data.get('game_version') != snapshot.get('game_version')
                   for data in (registry, inventory, book.catalog, book.data))):
        return {}
    sources = {e['entity_id']: e for e in inventory.get('entries', [])}
    result = {}
    for row in registry.get('entries', []):
        identity = row['entity_id']
        source = sources.get(identity, {})
        reference = book.references.get(identity)
        if (row.get('phase') == 'pickup' and row.get('trigger') == 'obtained'
                and row.get('treatment') == 'observed_in_snapshot'
                and row.get('game_version') == snapshot['game_version']
                and row.get('hooks') == source.get('hooks') == ['AfterObtained']
                and row.get('source_fingerprint') == source.get('source_sha256')
                and reference and row.get('reference_fingerprint') == entry_fingerprint(reference)):
            result[identity] = row
    return result


def reviewed_display_hooks(entry):
    if not entry.get('mechanics'):
        return set()
    try:
        registry=read_view(dataset_path('mechanism_scope'),'display-scope/1',
                           lambda data: {**{k:data.get(k) for k in ('game_version','game_assembly_sha256')},
                                         'entries':[row for row in data.get('entries',[]) if row.get('phase')=='presentation']})
        if not any(row['entity_id']==entry.get('entity_id') and row['hook'] in entry['mechanics']
                   for row in registry['entries']):
            return set()
        wanted={row['entity_id'] for row in registry['entries']}
        inventory=read_view(dataset_path('behavior'),'display-sources/'+hashlib.sha256(str(sorted(wanted)).encode()).hexdigest(),
                            lambda data: {**{k:data.get(k) for k in ('game_version','game_assembly_sha256')},
                                          'entries':{row['entity_id']:dict(source_sha256=row.get('provenance',{}).get('source_sha256'),hooks=list(row.get('mechanics',{})))
                                                     for row in data.get('entries',[]) if row['entity_id'] in wanted}})
    except FileNotFoundError:
        # Missing evidence never certifies a hook as irrelevant to combat.
        return set()
    provenance=entry.get('provenance') or {}
    if (not registry.get('game_assembly_sha256') or registry['game_assembly_sha256']!=inventory.get('game_assembly_sha256')
            or registry['game_assembly_sha256']!=provenance.get('game_assembly_sha256')
            or registry['game_version']!=inventory.get('game_version')):
        return set()
    source=inventory['entries'].get(entry.get('entity_id'),{})
    reviewed=set()
    for row in registry['entries']:
        if row['entity_id']!=entry.get('entity_id'):continue
        body=(entry.get('mechanics') or {}).get(row['hook'])
        if (body is not None and row['treatment']=='irrelevant_to_combat'
                and row['game_version']==registry['game_version']
                and row['source_fingerprint']==source.get('source_sha256')==provenance.get('source_sha256')
                and row['hook'] in source.get('hooks',[])
                and row['body_fingerprint']==hashlib.sha256(body.strip().encode()).hexdigest()):
            reviewed.add(row['hook'])
    return reviewed
