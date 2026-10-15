"""Shared command definitions, entity capabilities and observed action bindings.

Definitions describe potential capabilities; only fresh game bindings authorize
submission. Effect/trigger references never become agent commands.
"""
from copy import deepcopy
from autoslay.knowledge_base.repository import dataset_path, read_json, read_view, document_sha256, scoped

VERSION='1.0.0'


def _index(data):
    if data.get('schema_version')!='action-catalog/1.0' or data.get('version')!='1.0.0':
        raise ValueError('UNSUPPORTED_ACTION_CATALOG')
    rows=data.get('actions');targets=data.get('target_rules') or {}
    if not isinstance(rows,list) or not rows:
        raise ValueError('INVALID_ACTION_CATALOG')
    result={}
    for row in rows:
        kind=row.get('type')
        if not isinstance(kind,str) or not kind or kind in result or row.get('definition_id')!='ACTION.'+kind:
            raise ValueError('INVALID_ACTION_DEFINITION')
        if row.get('target_rule') not in targets or type(row.get('agent_allowed')) is not bool:
            raise ValueError('INVALID_ACTION_TARGET_RULE')
        for key in ('source_kinds','phases'):
            if not isinstance(row.get(key),list) or not row[key] or any(not isinstance(x,str) or not x for x in row[key]):
                raise ValueError('INVALID_ACTION_DEFINITION')
        if not isinstance(row.get('source_entity_categories'),list) or any(not isinstance(x,str) for x in row['source_entity_categories']):
            raise ValueError('INVALID_ACTION_DEFINITION')
        for key in ('group','preconditions_ref','cost_rule_ref','effects_ref','completion_boundary','execution_adapter','simulation'):
            if not isinstance(row.get(key),str) or not row[key]:
                raise ValueError('INVALID_ACTION_DEFINITION')
        result[kind]=row
    return result


def definitions():
    return read_view(dataset_path('actions'),'action-index/1',_index)


@scoped
def catalog():
    path=dataset_path('actions');data=read_json(path)
    definitions()  # Validate before exposing a registry to other modules.
    return dict(data,sha256=document_sha256(path))


@scoped
def definition(kind):
    row=definitions().get(kind)
    if row is None:raise ValueError('UNKNOWN_ACTION_TYPE')
    return row


def action_types(*,group=None,agent_allowed=None):
    return frozenset(k for k,v in definitions().items()
                     if (group is None or v['group']==group)
                     and (agent_allowed is None or v['agent_allowed']==agent_allowed))


@scoped
def operation_error(action,target_id,*,allow_admin=False,combat_only=False):
    """Check a real binding. Missing type is tolerated for legacy records only."""
    if not action:return 'ILLEGAL_ACTION'
    kind=action.get('type');known=definitions()
    row=known.get(kind)
    if kind is not None and row is None:return 'UNKNOWN_ACTION_TYPE'
    if row and action.get('definition_id',row['definition_id'])!=row['definition_id']:
        return 'ACTION_DEFINITION_MISMATCH'
    if not allow_admin and (row and not row['agent_allowed'] or action.get('action_id') in action_types(agent_allowed=False)):
        return 'ILLEGAL_ACTION'
    if combat_only and (not row or row['group']!='combat'):return 'ILLEGAL_ACTION'
    if row and row['target_rule']=='none' and target_id is not None:return 'ILLEGAL_TARGET'
    if target_id is None:
        return None if action.get('allows_no_target') else 'TARGET_REQUIRED'
    return None if target_id in (action.get('allowed_target_ids') or []) else 'ILLEGAL_TARGET'


def operations(actions):
    """Expand live bindings; this does not infer permission from the catalog."""
    result=[]
    for action in actions:
        if action.get('allows_no_target'):
            result.append(dict(action_id=action['action_id'],target_id=None))
        result.extend(dict(action_id=action['action_id'],target_id=t) for t in action.get('allowed_target_ids') or [])
    return result


def _knowledge():
    rules=read_json(dataset_path('rules'),default={})
    behavior=read_json(dataset_path('behavior'),default={})
    entities=read_json(dataset_path('catalog'),default={})
    scope=read_json(dataset_path('mechanism_scope'),default={})
    return dict(rules=rules,behavior={e['entity_id']:e for e in behavior.get('entries',[])},
                catalog={e['entity_id']:e for e in entities.get('entries',[])},triggers=scope.get('entries',[]))


@scoped
def entity_capabilities(entity_id,*,_context=None):
    """Query potential commands and referenced effects, without claiming legality."""
    if not isinstance(entity_id,str) or '.' not in entity_id:raise ValueError('INVALID_ENTITY_ID')
    ctx=_context or _knowledge();kind=entity_id.split('.',1)[0].lower()
    rule=(ctx['rules'].get('entries') or {}).get(entity_id)
    behavior=ctx['behavior'].get(entity_id);entry=ctx['catalog'].get(entity_id)
    commands=[];contextual=[]
    for row in definitions().values():
        if kind in row['source_entity_categories']:
            destination=commands if kind in row['source_kinds'] else contextual
            destination.append(dict(definition_id=row['definition_id'],type=row['type'],phases=row['phases'],source_kinds=row['source_kinds'],
                preconditions_ref=row['preconditions_ref'],availability='conditional',
                blocked_by_static_rule=bool(kind=='card' and row['type']=='play_card' and rule and rule.get('unplayable'))))
    effects=dict(rule_ref='rules#/entries/'+entity_id if rule else None,
                 declared_effects=deepcopy((rule or {}).get('effects',[])),
                 periodic=deepcopy((rule or {}).get('periodic')),
                 triggers=deepcopy([t for t in ctx['triggers'] if t['entity_id']==entity_id]),
                 status='declared_rules' if rule else 'unknown',
                 validation=(rule or {}).get('validation'),
                 reference_sha256=(rule or {}).get('reference_sha256'),
                 note='Declared rules and compatibility markers do not certify all triggers or interactions.')
    # A monster move is an autonomous game transition, never an agent command.
    graph=(behavior or {}).get('state_graph')
    return dict(entity_id=entity_id,category=kind,
        knowledge_status='recorded' if rule or behavior or entry else 'unknown',
        commands=commands,contextual_commands=contextual,effects=effects,
        behavior_ref='behavior#/entries/'+entity_id if behavior else None,
        state_graph=deepcopy(graph),moves=deepcopy((behavior or {}).get('moves',[])),
        behavior_status=(behavior or {}).get('calculation_status','unknown'),
        description=(entry or {}).get('description') or (behavior or {}).get('description'),
        reference_variants=[{k:deepcopy(v[k]) for k in ('upgrade_level','description','energy_cost','star_cost','target_type') if k in v}
                            for v in (entry or {}).get('variants',[])],
        unknown_capabilities_possible=True)


@scoped
def entities_for_action(kind):
    """Reverse lookup of known potential providers, not currently usable objects."""
    row=definition(kind);ctx=_knowledge()
    identities=set(ctx['catalog'])|set(ctx['behavior'])|set(ctx['rules'].get('entries') or {})
    return dict(definition_id=row['definition_id'],source_kinds=row['source_kinds'],
        entity_ids=sorted(i for i in identities if i.split('.',1)[0].lower() in row['source_entity_categories']),
        availability='conditional; use fresh legal_actions')


def _source(state,action,row):
    kind=(row or {}).get('source_kinds',['unknown'])[0]
    # Prefer the concrete binding exported by the bridge.
    exported=action.get('source')
    aid=action['action_id'];matches=[]
    def walk(value,path,parents):
        if isinstance(value,dict):
            for key in ('action_id','use_action_id','discard_action_id','proceed_action_id'):
                if value.get(key)==aid:
                    details=value.get('details') or {}
                    obj=value.get('card') or details.get('card') or details or value
                    parent=next((p for p in reversed(parents) if p.get('entity_id') or p.get('screen_id')), {})
                    matches.append(dict(kind=kind,entity_id=obj.get('entity_id') or parent.get('entity_id'),
                        instance_id=value.get('item_id') or next((obj[k] for k in ('instance_id','item_id','node_id','option_id','reward_id','screen_id') if obj.get(k)),parent.get('screen_id')),
                        path=path,binding_status='observed_object'))
            for key,child in value.items():
                if key!='legal_actions':walk(child,path+'/'+key,parents+[value])
        elif isinstance(value,list):
            for i,child in enumerate(value):walk(child,path+'/'+str(i),parents)
    if action.get('type')=='play_card':
        for i,card in enumerate((state.get('player') or {}).get('hand') or []):
            if card.get('instance_id')==action.get('card_instance_id') or aid=='play_'+str(card.get('instance_id')):
                return dict(kind='card',entity_id=card.get('entity_id'),instance_id=card.get('instance_id'),
                            path='/player/hand/'+str(i),binding_status='observed_object')
    walk(state,'',[])
    if matches:
        result=matches[0]
        if isinstance(exported,dict):
            result.update({k:exported[k] for k in ('kind','entity_id','instance_id') if exported.get(k) is not None})
            if exported.get('instance_id'):result['binding_status']='bridge_bound'
        return result
    if isinstance(exported,dict) and exported.get('instance_id'):
        return dict(exported,binding_status='bridge_bound')
    if kind in {'game','player_turn'}:
        return dict(kind=kind,entity_id=None,instance_id=state.get('combat_instance_id') or state.get('command_context_id') or state.get('run_id'),
                    path='',binding_status='context_bound')
    return dict(kind=kind,entity_id=None,instance_id=None,path=None,binding_status='unresolved')


@scoped
def describe(snapshot):
    """Version-bound observation record, archived with the decision report."""
    state=snapshot.get('state') or {};rows=definitions();bindings=[];issues=[];seen=set();ids=set();objects=[]
    identity=state.get('action_catalog')
    if identity and (identity.get('version')!='1.0.0' or identity.get('sha256')!=document_sha256(dataset_path('actions'))):
        issues.append(dict(code='ACTION_CATALOG_MISMATCH',bridge=deepcopy(identity)))
    def collect(value,path=''):
        if isinstance(value,dict):
            for key in ('enchantment_id','affliction_id'):
                if isinstance(value.get(key),str):ids.add(value[key])
            ids.update(i for i in value.get('hidden_power_ids',[]) or [] if isinstance(i,str))
            if isinstance(value.get('entity_id'),str):
                ids.add(value['entity_id'])
                if value.get('instance_id'):
                    objects.append(dict(entity_id=value['entity_id'],instance_id=value['instance_id'],path=path,
                        observed_flags={k:value[k] for k in ('usable','discardable','queued','available','selected','target_type') if k in value},
                        observed_state={k:deepcopy(value[k]) for k in ('description','type','target_type','upgrade_level','energy_cost','star_cost',
                            'costs_x','costs_star_x','enchantment_id','enchantment_amount','affliction_id','variant_key','counter','status') if k in value}))
            for k,v in value.items():
                if k!='legal_actions':collect(v,path+'/'+k)
        elif isinstance(value,list):
            for i,v in enumerate(value):collect(v,path+'/'+str(i))
    collect(state)
    for action in state.get('legal_actions') or []:
        aid=action.get('action_id');kind=action.get('type');row=rows.get(kind)
        if not isinstance(aid,str) or not aid:
            issues.append(dict(code='INVALID_ACTION_ID'));continue
        if aid in seen:issues.append(dict(code='DUPLICATE_ACTION_ID',action_id=aid))
        seen.add(aid)
        if row is None:issues.append(dict(code='UNKNOWN_ACTION_TYPE' if kind else 'LEGACY_ACTION_TYPE_MISSING',action_id=aid,type=kind))
        if row and action.get('definition_id',row['definition_id'])!=row['definition_id']:
            issues.append(dict(code='ACTION_DEFINITION_MISMATCH',action_id=aid))
        if row and row['target_rule']=='none' and action.get('allowed_target_ids'):
            issues.append(dict(code='ACTION_TARGET_RULE_MISMATCH',action_id=aid))
        source=_source(state,action,row)
        bindings.append(dict(action_id=aid,type=kind,definition_id=(row or {}).get('definition_id'),
            source=source,capability_ref=(str(source['entity_id'])+'/' if source.get('entity_id') else '')+(row or {}).get('definition_id','unknown'),
            allowed_target_ids=deepcopy(action.get('allowed_target_ids') or []),allows_no_target=action.get('allows_no_target') is True,
            availability='observed_legal' if row else 'observed_unregistered',
            execution_support='registered' if row else 'unknown',simulation_support=(row or {}).get('simulation','unknown')))
    for binding in bindings:
        source=binding['source']
        if source.get('entity_id'):ids.add(source['entity_id'])
        if source.get('instance_id') and not any(o['instance_id']==source['instance_id'] for o in objects):
            objects.append(dict(entity_id=source.get('entity_id'),instance_id=source['instance_id'],kind=source['kind'],
                                path=source.get('path'),observed_flags={},observed_state={}))
    ctx=_knowledge()
    for obj in objects:
        obj['action_ids']=[b['action_id'] for b in bindings if b['source'].get('instance_id')==obj['instance_id']]
        obj['availability']='has_live_bindings' if obj['action_ids'] else 'not_exposed_currently'
    return dict(schema_version='action-space/1.0',catalog_version='1.0.0',catalog_sha256=document_sha256(dataset_path('actions')),
        source=dict(session_epoch=snapshot.get('session_epoch'),state_version=snapshot.get('state_version'),
                    run_id=state.get('command_context_id') or state.get('run_id')),
        bindings=bindings,objects=objects,entities=[entity_capabilities(i,_context=ctx) for i in sorted(ids)],issues=issues,
        limitations=['Observed legality is not simulation coverage.',
                     'Absent live commands do not establish an unavailability reason.',
                     'Potential entity commands never grant instance execution permission.'])


def validate_catalog_identity(snapshot):
    """Old bridges omit metadata; declared but different registries cannot mix."""
    identity=(snapshot.get('state') or {}).get('action_catalog')
    if identity and (identity.get('version')!='1.0.0' or identity.get('sha256')!=document_sha256(dataset_path('actions'))):
        raise ValueError('ACTION_CATALOG_MISMATCH')
