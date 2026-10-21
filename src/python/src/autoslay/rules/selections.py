"""Observed selector constraints and declared caller continuations.

The API supplies candidate eligibility and cardinality. Only a matching model
declaration supplies effects; neither prompt nor screen name is a rule source.
"""
import re
from autoslay.knowledge_base.repository import freeze
from autoslay.decision.contracts import UnsupportedEffect
from autoslay.rules.amounts import validate_expression,evaluate_expression
from autoslay.rules.run_commands import NUMERIC,RunCommands,RunTransition

SELECTED_OPERATIONS={'upgrade_card':'upgrade','remove_card':'remove','add_to_deck':'add'}
SELECTION_HOOKS={'after_rest_smith','after_rest_heal','after_card_changed_piles'}


def validate_selections(rows,references):
    if not isinstance(rows,list):raise ValueError('selections: array required')
    result={};origins=set()
    for row in rows:
        identity=row.get('entity_id') if isinstance(row,dict) else None
        try:
            if not isinstance(identity,str) or not re.fullmatch(r'SELECTION\.[A-Z0-9_]+',identity) or identity in result:
                raise ValueError('unique selection ID required')
            if set(row)!={'entity_id','category','provenance','verification','source','selection_api','selected_operation',
                    'variables','after_commands','after_hooks','empty_behavior'} or row['category']!='selection':
                raise ValueError('invalid selection fields')
            if row['verification'] not in {'source_reviewed','interaction_verified'}:raise ValueError('selection source review required')
            provenance=row['provenance']
            if (set(provenance)!={'source_path','source_sha256','model_type'} or not isinstance(provenance['source_path'],str)
                    or not provenance['source_path'] or not re.fullmatch('[a-f0-9]{64}',provenance['source_sha256'])):
                raise ValueError('source provenance required')
            source=row['source']
            if set(source)!={'model_id','model_type','callback','option_key'}:raise ValueError('exact source binding required')
            if source['model_type']!=provenance['model_type'] or not isinstance(source['model_type'],str) or not source['model_type']:
                raise ValueError('source model type mismatch')
            if source['model_id'] is not None and source['model_id'] not in references:raise ValueError('source model reference missing')
            if not isinstance(source['callback'],str) or not source['callback']:raise ValueError('callback required')
            if source['option_key'] is not None and not isinstance(source['option_key'],str):raise ValueError('invalid option key')
            if row['selection_api'] not in {'CardSelectCmd.FromDeckForUpgrade','CardSelectCmd.FromDeckForRemoval',
                    'EventModel.SelectCardsToAddToDeckFromGrid'}:raise ValueError('unsupported selection API')
            expected={'CardSelectCmd.FromDeckForUpgrade':'upgrade_card','CardSelectCmd.FromDeckForRemoval':'remove_card',
                'EventModel.SelectCardsToAddToDeckFromGrid':'add_to_deck'}[row['selection_api']]
            if row['selected_operation']!=expected:raise ValueError('selection API/operation mismatch')
            origin=tuple(source[k] for k in ('model_id','model_type','callback','option_key'))+(row['selection_api'],)
            if origin in origins:raise ValueError('ambiguous selection source')
            origins.add(origin)
            if row['empty_behavior'] not in {'skip_continuation','continue'}:raise ValueError('explicit empty selection behavior required')
            variables=row['variables']
            if not isinstance(variables,dict):raise ValueError('variable bindings required')
            for name,binding in variables.items():
                if not isinstance(name,str) or not name.isascii() or not name.isidentifier():raise ValueError('invalid selection variable')
                if (not isinstance(binding,dict) or set(binding)!={'observed_variable','value_kind'} or
                        not isinstance(binding['observed_variable'],str) or binding['value_kind']!='int_value'):
                    raise ValueError('explicit public integral variable binding required')
            def expression(node):
                validate_expression(node)
                if isinstance(node,dict):
                    if 'variable' in node and node['variable'] not in variables:raise ValueError('undefined selection variable')
                    if 'state' in node and node['state'] not in {'hp','max_hp'}:raise ValueError('selection state scope mismatch')
                    for value in node.values():
                        if isinstance(value,list):
                            for child in value:expression(child)
            if not isinstance(row['after_commands'],list):raise ValueError('continuation command array required')
            for command in row['after_commands']:
                if set(command)!={'operation','amount'} or command['operation'] not in NUMERIC:
                    raise ValueError('unsupported selection continuation')
                expression(command['amount'])
            if not isinstance(row['after_hooks'],list) or any(h not in SELECTION_HOOKS for h in row['after_hooks']):
                raise ValueError('unsupported selection lifecycle')
            result[identity]=freeze(row)
        except (ValueError,KeyError,TypeError) as exc:raise ValueError(str(identity)+': '+str(exc)) from exc
    return result


class SelectionRules:
    def __init__(self,models=None,references=None):
        self.commands=RunCommands(models,references);self.models=self.commands.models

    def bind(self,selection):
        observed=selection.get('semantics') or {}
        if observed.get('schema_version')!='observed-selection/1':raise UnsupportedEffect('Observed selection provenance required')
        if not isinstance(observed.get('source_instance_id'),str) or not observed['source_instance_id']:
            raise UnsupportedEffect('Selection source instance unobserved')
        path=observed.get('api_path')
        if not isinstance(path,list) or not 1<=len(path)<=24 or any(not isinstance(p,str) or not p for p in path):
            raise UnsupportedEffect('Observed selection API path required')
        matches=[]
        for record in self.models.selections.values():
            source=record['source']
            if (all(observed.get('source_'+field)==value for field,value in source.items()) and
                    record['selection_api'] in (observed.get('api_path') or [])):
                matches.append(record)
        if len(matches)!=1:raise UnsupportedEffect('Unique declared selection continuation required')
        record=matches[0]
        if observed.get('operation')!=SELECTED_OPERATIONS[record['selected_operation']]:
            raise UnsupportedEffect('Observed selection operation mismatch')
        raw={}
        for variable in observed.get('source_dynamic_variables') or []:
            if variable['name'] in raw:raise UnsupportedEffect('Duplicate selection source variable')
            raw[variable['name']]=variable
        values={}
        for name,binding in record['variables'].items():
            value=raw.get(binding['observed_variable'],{}).get(binding['value_kind'])
            if type(value) is not int or not 0<=value<=10**9:raise UnsupportedEffect('Public selection variable required: '+name)
            values[name]=value
        return record,freeze(values)

    def apply(self,state,record,variables,cards):
        if not cards and record['empty_behavior']=='skip_continuation':return RunTransition(state)
        trace=[];gaps=[];source=record['entity_id']
        for card in cards:
            command=dict(operation=record['selected_operation'])
            if record['selected_operation']=='add_to_deck':command['card']=card
            else:command['instance_id']=card['instance_id']
            result=self.commands.execute(state,command,source=source)
            state=result.state;trace.extend(result.trace);gaps.extend(result.gaps)
            if gaps or state.dead or state.cleared:return RunTransition(state,tuple(trace),tuple(dict.fromkeys(gaps)))
        for command in record['after_commands']:
            amount=evaluate_expression(command['amount'],variables,state,lambda v,n:v[n])
            result=self.commands.execute(state,dict(operation=command['operation'],amount=amount),source=source)
            state=result.state;trace.extend(result.trace);gaps.extend(result.gaps)
            if gaps or state.dead or state.cleared:return RunTransition(state,tuple(trace),tuple(dict.fromkeys(gaps)))
        for event in record['after_hooks']:
            result=self.commands.dispatch(state,event,source=source)
            state=result.state;trace.extend(result.trace);gaps.extend(result.gaps)
            if gaps or state.dead or state.cleared:break
        return RunTransition(state,tuple(trace),tuple(dict.fromkeys(gaps)))
