"""Validated, content-addressed game definitions in the existing knowledge snapshot.

This module loads declarations only. Operations are implemented by rules/simulation;
neither descriptions nor source evidence are executable inputs.
"""
from collections.abc import Mapping
from dataclasses import dataclass, fields
from types import MappingProxyType
import re

from autoslay.knowledge_base.repository import dataset_path, read_view, freeze
from autoslay.decision.cache import digest

MODEL_ID = re.compile(r'[A-Z][A-Z0-9_]*\.[A-Z0-9_]+')


@dataclass(frozen=True)
class ModelSet:
    definitions: Mapping
    records: Mapping
    fingerprint: str
    rules_version: str
    game_version: str
    parameters: Mapping
    flows: Mapping
    model_references: frozenset
    monsters: Mapping
    selections: Mapping

    def __deepcopy__(self, memo):
        # All descendants are frozen; sharing avoids copying every entity per lookup.
        return self


def validate_models(data):
    """Compile a complete document atomically, with entity/field diagnostics."""
    from autoslay.rules.registry import (EntityMechanics, Trigger, EffectSpec,
        GAME_VERSION, IMPLEMENTATION_SHA256, STACK_POWERS)
    if data.get('schema_version') != 'game-models/1':
        raise ValueError('models.schema_version: expected game-models/1')
    if data.get('game_version') != GAME_VERSION or data.get('game_assembly_sha256') != IMPLEMENTATION_SHA256:
        raise ValueError('models: game version or assembly hash mismatch')
    if not isinstance(data.get('rules_version'), str) or not data['rules_version']:
        raise ValueError('models.rules_version: nonempty version required')
    allowed_root={'schema_version','game_version','game_assembly_sha256','rules_version','models','model_references','parameters','flows','monsters','selections'}
    if set(data)-allowed_root:
        raise ValueError('models: unknown fields: '+str(sorted(set(data)-allowed_root)))
    parameters=data.get('parameters',{})
    if not isinstance(parameters,dict) or set(parameters)-{'rest_heal_percent','provenance'}:
        raise ValueError('models.parameters: unsupported parameters')
    if 'rest_heal_percent' in parameters and (type(parameters['rest_heal_percent']) is not int or not 0<=parameters['rest_heal_percent']<=100):
        raise ValueError('models.parameters.rest_heal_percent: integer 0..100 required')
    rows=data.get('models')
    if not isinstance(rows,list) or not rows:
        raise ValueError('models: nonempty model array required')
    refs=data.get('model_references',[])
    if not isinstance(refs,list) or any(not isinstance(r,str) or not MODEL_ID.fullmatch(r) for r in refs) or len(refs)!=len(set(refs)):
        raise ValueError('models.model_references: unique stable model IDs required')
    identities=[row.get('entity_id') if isinstance(row,dict) else None for row in rows]
    if any(not isinstance(i,str) or not MODEL_ID.fullmatch(i) for i in identities):
        raise ValueError('models.entity_id: stable model IDs required')
    if len(identities)!=len(set(identities)):
        raise ValueError('models.entity_id: duplicate ID')
    references=set(refs)|set(identities)
    definitions={};records={}
    entity_fields={f.name for f in fields(EntityMechanics)}
    for row in rows:
        identity=row['entity_id']
        try:
            extras=set(row)-entity_fields-{'entity_id','provenance','verification','run_hooks'}
            if extras:raise ValueError('unknown fields: '+str(sorted(extras)))
            if identity.split('.')[0].lower()!=row.get('category'):
                raise ValueError('category disagrees with model ID')
            if row.get('verification') not in {'migrated_requires_semantic_review','source_reviewed','interaction_verified'}:
                raise ValueError('verification: explicit evidence status required')
            provenance=row.get('provenance')
            if not isinstance(provenance,dict) or not provenance.get('source_path') or not re.fullmatch('[0-9a-f]{64}',provenance.get('source_sha256','')):
                raise ValueError('provenance: source path and SHA-256 required')
            if set(provenance)-{'source_path','source_sha256','model_type'}:
                raise ValueError('provenance: unknown fields')
            if row.get('passive') is not None and not isinstance(row['passive'],str):
                raise ValueError('passive: mechanic name required')
            for flag in ('exhaust','unplayable'):
                if flag in row and type(row[flag]) is not bool:raise ValueError(flag+': boolean required')
            if row.get('target_type') not in {None,'Self','AnyPlayer','AnyEnemy','AllEnemies'}:
                raise ValueError('target_type: unsupported target')
            if row.get('x_resource') not in {None,'energy','stars'}:raise ValueError('x_resource: unsupported resource')
            periodic=row.get('periodic')
            if periodic is not None and (not isinstance(periodic,list) or len(periodic)!=3 or
                    any(type(n) is not int or n<0 for n in periodic) or periodic[0]<1):
                raise ValueError('periodic: positive period and nonnegative bonuses required')
            triggers=[]
            if not isinstance(row.get('triggers',[]),list):raise ValueError('triggers: array required')
            for index,t in enumerate(row.get('triggers',[])):
                if not isinstance(t,dict) or set(t)-{'event','effects','turn','counter'}:
                    raise ValueError(f'triggers[{index}]: invalid fields')
                if t.get('turn') is not None and (type(t['turn']) is not int or t['turn']<1):
                    raise ValueError(f'triggers[{index}].turn: positive integer required')
                counter=t.get('counter')
                if counter is not None:
                    if not isinstance(counter,dict) or set(counter)-{'period','card_type','reset','field','once_field'}:
                        raise ValueError(f'triggers[{index}].counter: invalid fields')
                    if counter.get('reset') not in {'combat','turn','never'} or counter.get('card_type') not in {'Attack','Skill','Power'}:
                        raise ValueError(f'triggers[{index}].counter: invalid scope or card type')
                    from autoslay.rules.amounts import validate_expression
                    validate_expression(counter.get('period'))
                    if counter.get('period')==0:raise ValueError('counter.period: positive period required')
                    if isinstance(counter.get('period'),dict) and set(counter['period'])!={'variable'}:
                        raise ValueError('counter.period: constant or dynamic variable required')
                effects=[]
                if not isinstance(t.get('effects'),list):raise ValueError(f'triggers[{index}].effects: array required')
                for j,e in enumerate(t['effects']):
                    if not isinstance(e,dict) or set(e)-{'operation','amount','repeats','generation','effect_model'}:
                        raise ValueError(f'triggers[{index}].effects[{j}]: invalid fields')
                    expected=STACK_POWERS.get(e.get('operation'))
                    if e.get('effect_model')!=expected:
                        raise ValueError(f'triggers[{index}].effects[{j}].effect_model: expected {expected}')
                    if expected and expected not in references:raise ValueError('missing model reference: '+expected)
                    generation=e.get('generation')
                    if generation is not None:
                        if not isinstance(generation,list) or len(generation)!=3 or generation[0] not in references:
                            raise ValueError(f'triggers[{index}].effects[{j}].generation: missing card reference')
                        generation=tuple(generation)
                    effects.append(EffectSpec(e['operation'],freeze(e['amount']),freeze(e.get('repeats',1)),generation))
                triggers.append(Trigger(t['event'],tuple(effects),t.get('turn'),freeze(counter)))
            kwargs={k:v for k,v in row.items() if k in entity_fields-{'triggers','state_fields','periodic'}}
            state_fields=row.get('state_fields',[])
            if not isinstance(state_fields,list) or any(not isinstance(s,list) or len(s)!=2 for s in state_fields):
                raise ValueError('state_fields: name/value pairs required')
            if len({s[0] for s in state_fields})!=len(state_fields):raise ValueError('state_fields: duplicate field')
            definitions[identity]=EntityMechanics(**kwargs,triggers=tuple(triggers),
                state_fields=tuple(tuple(s) for s in state_fields),periodic=tuple(periodic) if periodic else None)
            if 'run_hooks' in row:
                from autoslay.rules.run_commands import validate_hooks
                validate_hooks(row['run_hooks'],references)
            records[identity]=freeze(row)
        except (ValueError,KeyError,TypeError) as exc:
            raise ValueError(identity+': '+str(exc)) from exc
    from autoslay.rules.flows import validate_flows
    flows=validate_flows(data.get('flows',[]),references)
    from autoslay.rules.monster_models import validate_monsters
    monsters=validate_monsters(data.get('monsters',[]),references)
    from autoslay.rules.selections import validate_selections
    selections=validate_selections(data.get('selections',[]),references|set(flows))
    if set(flows)&set(definitions):raise ValueError('Duplicate entity ID across models and flows')
    return ModelSet(MappingProxyType(definitions),MappingProxyType(records),digest(data),
                    data['rules_version'],data['game_version'],freeze(parameters),MappingProxyType(flows),
                    frozenset(references|set(flows)|set(monsters)),MappingProxyType(monsters),MappingProxyType(selections))


def load_models(path=None):
    models=read_view(path or dataset_path('models'),'game-models/1',validate_models)
    from autoslay.rules.registry import GAME_VERSION, IMPLEMENTATION_SHA256
    def bindings(data):
        if data.get('status')!='complete':raise ValueError('models.catalog: incomplete catalog')
        entries={e['entity_id']:e for e in data.get('entries',[])}
        def variables(node):
            if isinstance(node,dict):
                if 'variable' in node:yield node['variable']
                for value in node.values():yield from variables(value)
            elif isinstance(node,(list,tuple)):
                for value in node:yield from variables(value)
        from autoslay.rules.registry import KnowledgeRules
        for identity,record in models.records.items():
            entry=entries.get(identity)
            if not entry or entry.get('category')!=record['category']:
                raise ValueError(identity+': catalog reference or category missing')
            names=set(variables(record.get('triggers',[])))|set(variables(record.get('run_hooks',[])))
            for trigger in record.get('triggers',[]):
                for effect in trigger['effects']:
                    generation=effect.get('generation')
                    if generation and entries.get(generation[0],{}).get('category')!='card':
                        raise ValueError(identity+': generation -> '+generation[0]+': catalog card reference missing')
            for variant in entry.get('variants',[entry]):
                for name in sorted(names):
                    try:KnowledgeRules.variable(variant.get('dynamic_variables'),name)
                    except ValueError as exc:
                        raise ValueError(identity+': variants['+str(variant.get('upgrade_level',0))+'].'+name+': '+str(exc)) from exc
        for identity,record in models.monsters.items():
            for move in record['moves']:
                for command in move['commands']:
                    if command['operation'] in {'draw','discard'} and entries.get(command['card_id'],{}).get('category')!='card':
                        raise ValueError(identity+': generated card catalog reference missing: '+command['card_id'])
        return data.get('game_version'),data.get('game_assembly_sha256')
    metadata=read_view(dataset_path('catalog'),'model-catalog-bindings/1:'+models.fingerprint,bindings)
    if metadata!=(GAME_VERSION,IMPLEMENTATION_SHA256):
        raise ValueError('models.catalog: game version or assembly hash mismatch')
    return models


class ModelRegistry(Mapping):
    """Read-only live view; enclosing knowledge_scope pins a decision's revision."""
    def __getitem__(self,key):return load_models().definitions[key]
    def __iter__(self):return iter(load_models().definitions)
    def __len__(self):return len(load_models().definitions)
    def items(self):return load_models().definitions.items()
    def values(self):return load_models().definitions.values()

