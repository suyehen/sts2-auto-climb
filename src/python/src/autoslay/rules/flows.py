"""Declared page/state machines, bound to public observations and common commands."""
from dataclasses import dataclass,replace
from decimal import Decimal,InvalidOperation
import re
from autoslay.knowledge_base.repository import freeze
from autoslay.rules.amounts import validate_expression,evaluate_expression
from autoslay.rules.run_commands import RunCommands,NUMERIC
from autoslay.decision.contracts import UnsupportedEffect


def validate_flows(rows,references):
    if not isinstance(rows,list):raise ValueError('flows: array required')
    result={}
    for row in rows:
        identity=row.get('entity_id') if isinstance(row,dict) else None
        try:
            if not isinstance(identity,str) or not re.fullmatch(r'EVENT\.[A-Z0-9_]+',identity) or identity in result:
                raise ValueError('unique event model ID required')
            if set(row)-{'entity_id','category','provenance','verification','variables','selectors','states'}:
                raise ValueError('unknown flow fields')
            if row.get('category')!='event':raise ValueError('unsupported flow category')
            states=row.get('states');variables=row.get('variables')
            if not isinstance(states,dict) or not states or not isinstance(variables,dict):
                raise ValueError('states and variables required')
            def expression(value):
                validate_expression(value)
                if isinstance(value,dict):
                    if 'state' in value and value['state'] not in {'hp','max_hp'}:
                        raise ValueError('state field outside run flow scope: '+value['state'])
                    if 'variable' in value and value['variable'] not in variables:
                        raise ValueError('undefined variable: '+value['variable'])
                    for operands in value.values():
                        if isinstance(operands,list):
                            for operand in operands:expression(operand)
            def commands(values,depth=0):
                if depth>8 or not isinstance(values,list):raise ValueError('invalid command nesting')
                has_boundary=False
                for index,command in enumerate(values):
                    if not isinstance(command,dict):raise ValueError('command object required')
                    op=command.get('operation')
                    if op in NUMERIC:
                        if set(command)!={'operation','amount'}:raise ValueError('invalid resource command fields')
                        expression(command['amount'])
                    elif op=='set_variable':
                        if set(command)!={'operation','name','amount'} or command['name'] not in variables:
                            raise ValueError('invalid scoped variable update')
                        expression(command['amount'])
                    elif op=='branch':
                        if set(command)!={'operation','condition','then','else'}:raise ValueError('invalid conditional command fields')
                        expression(command['condition'])
                        left=commands(command['then'],depth+1);right=commands(command['else'],depth+1)
                        has_boundary=left or right
                    elif op=='upgrade_deck':
                        if set(command)!={'operation','mode'} or command['mode'] not in {'random','all'}:
                            raise ValueError('invalid upgrade selection')
                    elif op=='obtain_relic':
                        if (set(command)!={'operation','entity_id'} or command['entity_id'] not in references
                                or not command['entity_id'].startswith('RELIC.')):
                            raise ValueError('missing obtain model reference')
                    elif op=='rest_heal':
                        if set(command)!={'operation','amount','is_mimicked'} or type(command['is_mimicked']) is not bool:
                            raise ValueError('invalid rest command')
                        expression(command['amount'])
                    elif op=='enter_combat':
                        has_boundary=True
                        if set(command)!={'operation','encounter_id','extra_rewards','resume_after_combat'}:
                            raise ValueError('invalid combat boundary fields')
                        if (command['encounter_id'] not in references or not command['encounter_id'].startswith('ENCOUNTER.')
                                or command['resume_after_combat'] is not False):
                            raise ValueError('missing encounter or unsupported combat resumption')
                        if index!=len(values)-1:raise ValueError('observation boundary must terminate command sequence')
                        if not isinstance(command['extra_rewards'],list):raise ValueError('extra reward array required')
                        for reward in command['extra_rewards']:
                            if (set(reward)!={'reward_type','card_id','count'} or reward['reward_type']!='special_card'
                                    or reward['card_id'] not in references or not reward['card_id'].startswith('CARD.')
                                    or type(reward['count']) is not int or reward['count']<1):
                                raise ValueError('unsupported extra reward descriptor')
                    else:raise ValueError('unimplemented flow command: '+str(op))
                    if has_boundary and index!=len(values)-1:
                        raise ValueError('observation boundary must terminate enclosing command sequence')
                return has_boundary
            for name,binding in variables.items():
                if not isinstance(name,str) or not name.isascii() or not name.isidentifier() or not isinstance(binding,dict):
                    raise ValueError('invalid variable declaration')
                if set(binding)-{'default','observed_variable','observed_fields','required','required_in','numeric_type'}:
                    raise ValueError('unknown variable declaration field')
                if binding.get('numeric_type','integer') not in {'integer','decimal'}:raise ValueError('unsupported variable type')
                if 'default' in binding:
                    if type(binding['default']) is not int:raise ValueError('integral canonical default required')
                    validate_expression(binding['default'])
                if 'required' in binding and type(binding['required']) is not bool:raise ValueError('boolean required flag expected')
                if 'required_in' in binding and not isinstance(binding['required_in'],list):raise ValueError('state scope array required')
                if binding.get('required_in') and not set(binding['required_in'])<=set(states):
                    raise ValueError('variable scope references missing state')
                for field in binding.get('observed_fields',[]):
                    if not isinstance(field,str) or not field.isascii() or not field.isidentifier():
                        raise ValueError('invalid public field binding')
            for name,node in states.items():
                if not isinstance(name,str) or not isinstance(node,dict) or set(node)!={'options'}:
                    raise ValueError('invalid state node')
                options=node['options']
                if not isinstance(options,list) or not options or len({o['id'] for o in options})!=len(options):
                    raise ValueError('unique state options required')
                for option in options:
                    if set(option)!={'id','callback','keys','commands','next'}:
                        raise ValueError('invalid option fields')
                    if not isinstance(option['callback'],str) or not isinstance(option['keys'],list) or not option['keys']:
                        raise ValueError('callback and stable option keys required')
                    if (not isinstance(option['id'],str) or not option['id'] or not option['callback'] or
                            any(not isinstance(key,str) or not key for key in option['keys']) or
                            len(set(option['keys']))!=len(option['keys'])):
                        raise ValueError('unique stable option identity/key required')
                    if option['next'] is not None and option['next'] not in states:raise ValueError('missing successor state')
                    commands(option['commands'])
            selectors=row.get('selectors')
            if not isinstance(selectors,list) or not selectors:raise ValueError('entry selectors required')
            for selector in selectors:
                if set(selector)-{'state','option_keys','fields'} or selector.get('state') not in states:
                    raise ValueError('invalid entry selector')
                if not set(selector.get('fields',{}))<=set(variables):raise ValueError('selector references undeclared state')
                if any(type(v) is not int for v in selector.get('fields',{}).values()):raise ValueError('integral selector state required')
            provenance=row.get('provenance') or {}
            if not provenance.get('source_path') or not re.fullmatch('[0-9a-f]{64}',provenance.get('source_sha256','')):
                raise ValueError('source provenance required')
            if row.get('verification') not in {'source_reviewed','interaction_verified'}:raise ValueError('explicit review status required')
            result[identity]=freeze(row)
        except (ValueError,TypeError,KeyError) as exc:raise ValueError(str(identity)+': '+str(exc)) from exc
    return result


@dataclass(frozen=True)
class FlowState:
    resources: object
    model_id: str
    page: str | None
    variables: dict


@dataclass(frozen=True)
class FlowOutcome:
    weight: float
    state: FlowState
    trace: tuple[dict,...] = ()
    gaps: tuple[str,...] = ()
    boundary: dict | None = None


class FlowMachine:
    def __init__(self,models=None,references=None):
        self.commands=RunCommands(models,references)
        self.models=self.commands.models

    def bind(self,event,resources):
        identity=event.get('entity_id');record=self.models.flows.get(identity)
        if record is None:raise UnsupportedEffect('Flow definition missing: '+str(identity))
        fields=(event.get('runtime_state') or {}).get('fields') or {}
        observed={}
        for value in event.get('dynamic_variables') or []:
            name=value.get('name')
            if name in observed:raise UnsupportedEffect('Duplicate event variable: '+str(name))
            observed[name]=value
        values={};present=set()
        for name,binding in record['variables'].items():
            possibilities=[]
            if binding.get('observed_variable') in observed:
                raw=observed[binding['observed_variable']]
                value=raw.get('base_value',raw.get('value'))
                if binding.get('numeric_type')=='decimal':
                    exact=raw.get('decimal_base_value',raw.get('decimal_value'))
                    if exact is None and type(value) is int:exact=str(value)
                    if not isinstance(exact,str):raise UnsupportedEffect('Exact decimal observation required: '+name)
                    try:value=Decimal(exact)
                    except InvalidOperation as exc:raise UnsupportedEffect('Invalid decimal observation: '+name) from exc
                    if not value.is_finite() or not 0<=value<=1000000000:raise UnsupportedEffect('Invalid decimal observation: '+name)
                possibilities.append(value)
            for field in binding.get('observed_fields',[]):
                if field not in fields:continue
                value=fields[field]
                if binding.get('numeric_type')=='decimal':
                    # Runtime numeric fields have no exact-decimal companion yet.
                    if type(value) is not int:raise UnsupportedEffect('Exact decimal observation required: '+name)
                    value=Decimal(value)
                possibilities.append(value)
            if possibilities:
                decimal_type=binding.get('numeric_type')=='decimal'
                if any(type(v) not in (int,float,Decimal) or not 0<=v<=1000000000 or not decimal_type and int(v)!=v for v in possibilities) or len(set(possibilities))!=1:
                    raise UnsupportedEffect('Ambiguous or invalid public variable: '+name)
                values[name]=possibilities[0] if decimal_type else int(possibilities[0]);present.add(name)
            elif 'default' in binding:
                if type(binding['default']) is not int:raise UnsupportedEffect('Integral flow default required')
                values[name]=binding['default']
        keys={o.get('text_key') for o in event.get('options',[]) if not o.get('locked')}
        matches=[s['state'] for s in record['selectors']
            if set(s.get('option_keys',[]))<=keys and all(values.get(k)==v for k,v in s.get('fields',{}).items())]
        if len(matches)!=1:raise UnsupportedEffect('Ambiguous or unobserved flow page')
        page=matches[0]
        for name,binding in record['variables'].items():
            if (binding.get('required') or page in binding.get('required_in',[])) and name not in present:
                raise UnsupportedEffect('Public flow variable required: '+name)
        declared=record['states'][page]['options']
        live=[o for o in event.get('options',[]) if o.get('available',True) and not o.get('locked')]
        bindings={}
        for option in live:
            if option.get('source_model_id',identity)!=identity:
                raise UnsupportedEffect('Option belongs to another source model')
            expected_type=record['provenance'].get('model_type')
            if option.get('callback_declaring_type') is not None and option['callback_declaring_type']!=expected_type:
                raise UnsupportedEffect('Option callback declaring type mismatch')
            found=[d for d in declared if option.get('text_key') in d['keys'] and
                (option.get('callback_method') is None or option['callback_method']==d['callback'])]
            if len(found)!=1:raise UnsupportedEffect('Live option does not match declared callback/key')
            if found[0]['id'] in bindings:raise UnsupportedEffect('Duplicate live flow option')
            bindings[found[0]['id']]=option
        if not bindings:raise UnsupportedEffect('No observed flow options')
        return FlowState(resources,identity,page,freeze(values)),bindings

    def _amount(self,value,state):
        def variable(values,name):
            if name not in values:raise UnsupportedEffect('Flow variable unobserved: '+name)
            return values[name]
        return evaluate_expression(value,state.variables,state.resources,variable)

    def _execute(self,states,commands):
        for command in commands:
            next_states=[]
            for outcome in states:
                state=outcome.state;resources=state.resources;op=command['operation']
                if resources.dead or resources.cleared or outcome.gaps or outcome.boundary:
                    next_states.append(outcome);continue
                try:
                    if op=='set_variable':
                        value=self._amount(command['amount'],state)
                        changed=replace(state,variables=freeze(dict(state.variables,**{command['name']:value})))
                        next_states.append(replace(outcome,state=changed,trace=outcome.trace+(
                            dict(operation=op,name=command['name'],value=str(value) if type(value) is Decimal else value),)))
                    elif op=='branch':
                        branch=command['then'] if self._amount(command['condition'],state) else command['else']
                        next_states.extend(self._execute([outcome],branch))
                    elif op=='upgrade_deck':
                        if len(resources.deck)>500:raise UnsupportedEffect('Upgrade branch budget exceeded')
                        for weight,result in self.commands.upgrade_outcomes(resources,command['mode']):
                            next_states.append(FlowOutcome(outcome.weight*weight,replace(state,resources=result.state),
                                outcome.trace+result.trace,outcome.gaps+result.gaps))
                    elif op=='rest_heal':
                        result=self.commands.rest(resources,observed_heal=self._amount(command['amount'],state),
                            is_mimicked=command['is_mimicked'])
                        next_states.append(FlowOutcome(outcome.weight,replace(state,resources=result.state),
                            outcome.trace+result.trace,outcome.gaps+result.gaps))
                    elif op=='enter_combat':
                        boundary=freeze(dict(kind='combat_requires_observation',encounter_id=command['encounter_id'],
                            extra_rewards=command['extra_rewards'],resume_after_combat=False,
                            scope='entry command only; combat outcome and rewards remain unobserved'))
                        next_states.append(replace(outcome,boundary=boundary,
                            trace=outcome.trace+(dict(operation=op,encounter_id=command['encounter_id']),)))
                    else:
                        bound=dict(command)
                        if 'amount' in bound:bound['amount']=self._amount(bound['amount'],state)
                        result=self.commands.execute(resources,bound,source=state.model_id+':'+state.page)
                        next_states.append(FlowOutcome(outcome.weight,replace(state,resources=result.state),
                            outcome.trace+result.trace,outcome.gaps+result.gaps))
                except (UnsupportedEffect,ValueError,KeyError) as exc:
                    next_states.append(replace(outcome,gaps=outcome.gaps+(str(exc),)))
            states=next_states
        return states

    def options(self,state):
        if state.page is None or state.resources.dead or state.resources.cleared:return ()
        return self.models.flows[state.model_id]['states'][state.page]['options']

    def apply(self,state,option_id):
        options=[o for o in self.options(state) if o['id']==option_id]
        if len(options)!=1:raise UnsupportedEffect('Unique legal declared flow option required')
        option=options[0]
        outcomes=self._execute([FlowOutcome(1.0,state)],option['commands'])
        return [replace(o,state=replace(o.state,page=option['next'] if not o.state.resources.dead else None)) for o in outcomes]
