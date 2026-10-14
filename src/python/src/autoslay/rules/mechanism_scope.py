"""Reviewed pickup effects, bound to source, reference and live assembly hashes.

This registry does not exempt granted relics or certify arbitrary hook names.
"""
from autoslay.knowledge_base.repository import dataset_path, read_json, read_view
from autoslay.knowledge_base.rulebook import entry_fingerprint
import hashlib

VERSION = '1.2.0'


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


def coverage_for_snapshot(snapshot, warnings):
    """Compile reviewed provenance into immutable action-scope records.

    Unreviewed hooks have wildcard dependencies: a hook name is never evidence
    that it is dormant. Indirect legality/resource changes stay in the scope.
    """
    from autoslay.decision.contracts import MechanismImpact
    from autoslay.knowledge_base.rulebook import Rulebook
    book=Rulebook();state=snapshot['state'];owned={r['entity_id'] for r in state['player'].get('relics') or []}
    records=[]
    for identity,row in reviewed_pickup(snapshot,book).items():
        if identity in owned:
            records.append(MechanismImpact(identity,row['hook'],'observed_in_snapshot',
                tuple(row['reads']),tuple(row['writes']),row['phase'],row['trigger'],row['source_fingerprint']))
    registry=read_json(dataset_path('mechanism_scope'))
    if registry.get('game_assembly_sha256')==snapshot.get('game_assembly_sha256'):
        wanted={r['entity_id'] for r in registry.get('entries',[]) if r.get('phase')=='presentation'}
        try:
            entries=read_view(dataset_path('behavior'),'scope-bodies/'+hashlib.sha256(str(sorted(wanted)).encode()).hexdigest(),
                lambda data:{e['entity_id']:dict(entity_id=e['entity_id'],mechanics=e.get('mechanics'),provenance=e.get('provenance'))
                             for e in data.get('entries',[]) if e['entity_id'] in wanted})
        except FileNotFoundError:entries={}
        active={e['entity_id'] for e in state.get('enemies') or [] if e.get('hp',0)>0}
        for identity in active&wanted:
            reviewed=reviewed_display_hooks(entries.get(identity,{}))
            for row in registry['entries']:
                if row['entity_id']==identity and row.get('hook') in reviewed:
                    records.append(MechanismImpact(identity,row['hook'],'irrelevant_to_combat',
                        tuple(row['reads']),tuple(row['writes']),row['phase'],row['trigger'],row['source_fingerprint']))
    for card in state['player'].get('hand') or []:
        identity=card['entity_id']
        if book.status(identity)=='compatible':
            entry=book.data['entries'][identity]
            records.append(MechanismImpact(identity,'compiled_card_effects','modeled',
                phase='bounded_effects',trigger='play',source_fingerprint=entry.get('source_sha256')))
    for warning in warnings:
        parts=warning.split(':')
        identity=next((p for p in parts[1:] if p.startswith(('RELIC.','POWER.','MONSTER.'))),'UNKNOWN')
        entry=(book.data.get('entries') or {}).get(identity) or {}
        records.append(MechanismImpact(identity,parts[0],'unknown',source_fingerprint=entry.get('source_sha256'),warning=warning))
    return tuple(records)


def reviewed_move_scope(entry,move):
    """Specific reviewed handler scope; never exempts arbitrary visual-looking code."""
    provenance=entry.get('provenance') or {}
    if not provenance.get('source_sha256'):return None
    try:registry=read_json(dataset_path('mechanism_scope'))
    except (FileNotFoundError,KeyError):return None
    if not registry.get('game_assembly_sha256') or registry['game_assembly_sha256']!=provenance.get('game_assembly_sha256'):
        return None
    body=hashlib.sha256(move.get('execution_evidence','').strip().encode()).hexdigest()
    return next((r for r in registry.get('entries',[]) if r.get('phase')=='combat_move'
        and r.get('treatment')=='modeled' and r['entity_id']==entry.get('entity_id')
        and r['hook']==move.get('handler') and r.get('game_version')==registry.get('game_version')
        and r.get('source_fingerprint')==provenance.get('source_sha256')
        and r.get('body_fingerprint')==body),None)


def action_access(operation, effects=()):
    reads={'player.hp','player.energy','legal_actions','enemy.hp','enemy.block'}
    writes=set()
    for effect in effects:
        writes.update({'damage':{'enemy.hp','enemy.block','death','legal_actions'},
                       'strength':{'player.strength','future_damage'},
                       'temporary_strength':{'player.strength','future_damage'},
                       'block':{'player.block'},'lose_hp':{'player.hp','death'},
                       'energy':{'player.energy','legal_actions'},
                       'draw':{'player.hand','player.piles','player.energy','legal_actions','draw_hooks'}}.get(effect.kind,{'*'}))
    if operation.action_id=='end_turn':writes.add('*')
    return dict(reads=sorted(reads),writes=sorted(writes),comparison_scope='all competing first actions and future trigger closure')


def action_blockers(state, operation, effects=()):
    access=action_access(operation,effects)
    blockers=[]
    for row in state.mechanism_scope:
        if row.treatment in {'modeled','observed_in_snapshot','irrelevant_to_combat'}:continue
        # Unknown dependencies include competing alternatives, not just the chosen action.
        if '*' in row.writes or '*' in access['writes'] or set(row.writes)&set(access['reads']+access['writes']):
            blockers.append(row.warning or row.entity_id+':'+row.hook)
    return blockers
