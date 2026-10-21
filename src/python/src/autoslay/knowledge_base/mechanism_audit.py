"""Separate catalog, declaration, implementation and verification coverage."""
from collections import Counter
from dataclasses import asdict
from autoslay.rules.registry import VERSION, IMPLEMENTATION_SHA256
from autoslay.knowledge_base.templates import index
from autoslay.rules.model_store import load_models


def build_inventory(inventory=None,behavior=None):
    entries=index()[0]
    models=load_models()
    sources={r['entity_id']:r for r in (inventory or {}).get('entries',[])}
    behaviors={r['entity_id']:r for r in (behavior or {}).get('entries',[])}
    rows=[]
    def nodes(value):
        if isinstance(value,dict):
            yield value
            for child in value.values():yield from nodes(child)
        elif isinstance(value,(list,tuple)):
            for child in value:yield from nodes(child)
    for identity in sorted(set(entries)|set(sources)|set(models.records)|set(behaviors)|set(models.flows)|set(models.monsters)|models.model_references):
        entry=(entries.get(identity) or sources.get(identity) or models.records.get(identity) or
            behaviors.get(identity) or models.flows.get(identity) or dict(entity_id=identity,category=identity.split('.')[0].lower()))
        mechanic=models.definitions.get(identity)
        flow=models.flows.get(identity)
        monster=models.monsters.get(identity)
        defined=mechanic is not None or flow is not None or monster is not None
        record=models.records.get(identity) or flow or monster or {}
        declarations=list(nodes(record))
        gaps=sorted({gap for n in declarations for gap in n.get('gaps',[])}|
            set(record.get('unmodeled_hooks',[]))|{n['unknown'] for n in declarations if 'unknown' in n})
        references=sorted({n[k] for n in declarations for k in ('effect_model','entity_id','encounter_id','card_id')
            if k in n and n[k]!=identity}|{n['generation'][0] for n in declarations if n.get('generation')})
        declarations_by_id=dict(models.records)|dict(models.flows)|dict(models.monsters)
        missing_references=[r for r in references if r not in declarations_by_id]
        pending_references=[r for r in references if r in declarations_by_id and
            declarations_by_id[r].get('verification') not in {'source_reviewed','interaction_verified'}]
        reviewed=record.get('verification') in {'source_reviewed','interaction_verified'}
        rows.append(dict(entity_id=identity,category=entry['category'],name=entry.get('name'),
                         status='defined' if defined else 'missing_mechanic',
                         catalog_present=identity in entries,source_present=identity in sources,
                         behavior_present=identity in behaviors,
                         definition_present=defined,
                         source_reviewed=reviewed,
                         effect_definition_complete=reviewed and not gaps and not missing_references and not pending_references,
                         referenced_models_missing_definition=missing_references,
                         referenced_models_pending_review=pending_references,
                         interaction_verified=record.get('verification')=='interaction_verified',
                         semantic_review=record.get('verification','not_defined'),
                         variables=[v.get('dynamic_variables',[]) for v in entries.get(identity,{}).get('variants',[entries.get(identity,{})])],
                         references=references,
                         mechanism_dependencies=sorted({n['operation'] for n in declarations if 'operation' in n}),
                         declared_operations_supported=defined and not gaps,
                         declaration_gaps=gaps,
                         run_hooks_reviewed='run_hooks' in record,
                         definition=asdict(mechanic) if mechanic else flow or monster,
                         source_hooks=sources.get(identity,{}).get('hooks',[]),
                         missing=(['structured_definition_missing'] if not defined else
                             ([] if record.get('verification') in {'source_reviewed','interaction_verified'} else ['semantic_review_pending']))
                             + gaps+([] if record.get('verification')=='interaction_verified' else ['interaction_verification_pending'])
                             + (['run_hooks_not_reviewed'] if entry['category']=='relic' and 'run_hooks' not in record else [])))
    return dict(schema_version='game-model-coverage/1',module_version=VERSION,rows=rows,
                game_version=models.game_version,game_assembly_sha256=IMPLEMENTATION_SHA256,
                complete_inventory=False,inventory_scope='catalog + source_inventory + behavior + declared model references; missing model categories remain',
                counts=dict(Counter(r['status'] for r in rows)),entity_count=len(rows),
                categories={category:dict(total=sum(r['category']==category for r in rows),
                    catalog=sum(r['category']==category and r['catalog_present'] for r in rows),
                    declared=sum(r['category']==category and r['definition_present'] for r in rows),
                    source_reviewed=sum(r['category']==category and r['source_reviewed'] for r in rows),
                    effects_complete=sum(r['category']==category and r['effect_definition_complete'] for r in rows),
                    interaction_verified=sum(r['category']==category and r['interaction_verified'] for r in rows))
                    for category in sorted({r['category'] for r in rows})},
                rule_fingerprint=models.fingerprint,execution_source='models_json_and_core_commands',full_support=False,
                selection_contract_count=len(models.selections),
                selection_contracts={i:dict(source=dict(r['source']),semantic_review=r['verification'],
                    interaction_verified=r['verification']=='interaction_verified',scope='observed selector continuation only; not a complete source entity definition')
                    for i,r in models.selections.items()},
                limitations=['source inventory IDs are provisional until ModelDb export',
                    'current inventory omits some model categories; runtime ModelDb model_inventory export is pending',
                    'source-reviewed declaration is not proof of all hook interactions',
                    'migrated definitions require semantic review; no unreachable/deprecated classification inferred'])
