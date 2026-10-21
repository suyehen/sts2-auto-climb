"""Finite monster expression data; never parse source code during prediction."""
from decimal import Decimal,InvalidOperation
from autoslay.decision.contracts import UnsupportedEffect
from autoslay.knowledge_base.repository import freeze

BINARY={'add','subtract','multiply','divide','equal','not_equal','less','less_equal','greater','greater_equal'}


def validate_expression(node,depth=0):
    if depth>24:raise ValueError('monster expression depth exceeded')
    if type(node) in {int,bool}:
        if abs(node)>10**9:raise ValueError('monster constant out of bounds')
        return
    if not isinstance(node,dict) or len(node)!=1:raise ValueError('structured monster expression required')
    op,value=next(iter(node.items()))
    if op=='field':
        if not isinstance(value,str) or not value.isascii() or not value.isidentifier():raise ValueError('invalid public monster field')
    elif op=='ascension':
        if value is not True:raise ValueError('invalid ascension expression')
    elif op=='unknown':
        if not isinstance(value,str) or not value:raise ValueError('unknown expression requires diagnostic')
    elif op=='decimal':
        try:number=Decimal(value) if isinstance(value,str) else None
        except InvalidOperation as exc:raise ValueError('invalid decimal constant') from exc
        if number is None or not number.is_finite() or abs(number)>10**9:raise ValueError('invalid decimal constant')
    elif op in BINARY|{'and','or','if','not','negate'}:
        count=3 if op=='if' else 1 if op in {'not','negate'} else 2
        if not isinstance(value,list) or (not 2<=len(value)<=8 if op in {'and','or'} else len(value)!=count):
            raise ValueError('invalid monster operands')
        for child in value:validate_expression(child,depth+1)
    else:raise ValueError('unimplemented monster expression: '+op)


def expression(node,values,ascension=0):
    validate_expression(node)
    def read(n):
        if type(n) in {int,bool}:return n
        op,arg=next(iter(n.items()))
        if op=='unknown':raise UnsupportedEffect('unknown_condition:'+arg)
        if op=='field':
            v=values.get(arg)
            if type(v) not in {int,bool,Decimal}:raise UnsupportedEffect('public_monster_field_missing:'+arg)
            return v
        if op=='decimal':return Decimal(arg)
        if op=='ascension':return ascension
        if op=='if':return read(arg[1] if read(arg[0]) else arg[2])
        if op=='and':return all(read(a) for a in arg)
        if op=='or':return any(read(a) for a in arg)
        a=read(arg[0])
        if op=='not':return not a
        if op=='negate':return -a
        b=read(arg[1])
        if op=='add':v=a+b
        elif op=='subtract':v=a-b
        elif op=='multiply':v=a*b
        elif op=='divide':
            if b==0:raise UnsupportedEffect('monster_division_by_zero')
            # C# integer division truncates toward zero, without a float conversion.
            v=(abs(a)//abs(b))*(-1 if (a<0)!=(b<0) else 1) if type(a) is int and type(b) is int else a/b
        elif op=='equal':return a==b
        elif op=='not_equal':return a!=b
        elif op=='less':return a<b
        elif op=='less_equal':return a<=b
        elif op=='greater':return a>b
        else:return a>=b
        if abs(v)>10**18:raise UnsupportedEffect('monster_arithmetic_bound')
        return v
    return read(node)


def validate_monsters(rows,references):
    if not isinstance(rows,list):raise ValueError('monsters array required')
    result={}
    for row in rows:
        identity=row.get('entity_id') if isinstance(row,dict) else None
        try:
            fields={'entity_id','category','provenance','verification','gaps','unmodeled_hooks','presentation_hooks','moves','state_graph'}
            if set(row)!=fields or row['category']!='monster' or not identity.startswith('MONSTER.') or identity in result:
                raise ValueError('unique structured monster definition required')
            from re import fullmatch
            provenance=row['provenance']
            if (set(provenance)!={'source_path','source_sha256','model_type'} or
                    not provenance['source_path'] or not fullmatch('[a-f0-9]{64}',provenance['source_sha256'])):
                raise ValueError('monster source provenance required')
            if not fullmatch(r'MONSTER\.[A-Z0-9_]+',identity):raise ValueError('invalid monster ID')
            if row['verification'] not in {'migrated_requires_semantic_review','source_reviewed','interaction_verified'}:
                raise ValueError('monster review status required')
            def gaps(value):
                if not isinstance(value,list) or any(not isinstance(v,str) or not v for v in value):raise ValueError('diagnostic array required')
            gaps(row['gaps']);gaps(row['unmodeled_hooks']);gaps(row['presentation_hooks'])
            if set(row['unmodeled_hooks'])&set(row['presentation_hooks']):raise ValueError('hook scope conflict')
            graph=row['state_graph']
            if set(graph)!={'nodes','edges'} or not isinstance(graph['nodes'],list) or not isinstance(graph['edges'],list):
                raise ValueError('structured state graph required')
            nodes={n['variable']:n for n in graph['nodes']}
            moves={m['variable']:m for m in row['moves']}
            if len(nodes)!=len(graph['nodes']) or len(moves)!=len(row['moves']):raise ValueError('duplicate monster node/move')
            for node in nodes.values():
                if set(node)!={'variable','state_id','kind','branches','gaps'}:raise ValueError('invalid monster node fields')
                gaps(node['gaps'])
                if node['kind'] not in {'MoveState','ConditionalBranchState','RandomBranchState'}:raise ValueError('invalid node kind')
                if node['kind']=='MoveState' and node['variable'] not in moves:raise ValueError('missing move definition')
                for branch in node['branches']:
                    if set(branch)!={'target','condition','weight','repeat_type','cooldown','max_consecutive','gaps'}:
                        raise ValueError('invalid branch fields')
                    if branch['target'] not in nodes:raise ValueError('missing branch target')
                    gaps(branch['gaps']);validate_expression(branch['condition']);validate_expression(branch['weight'])
                    if branch['repeat_type'] not in {'CannotRepeat','CanRepeatForever','UseOnlyOnce','CanRepeatXTimes'}:raise ValueError('invalid repeat type')
                    for key in ('cooldown','max_consecutive'):
                        if type(branch[key]) is not int or branch[key]<0:raise ValueError('invalid history bound')
                    if branch['repeat_type']=='CanRepeatXTimes' and branch['max_consecutive']<1:raise ValueError('invalid repeat count')
            for edge in graph['edges']:
                if set(edge)!={'source','target'} or edge['source'] not in nodes or edge['target'] not in nodes:raise ValueError('missing edge target')
            for move in moves.values():
                if set(move)!={'variable','state_id','commands','announced_attacks','intent_types','gaps'} or move['variable'] not in nodes:
                    raise ValueError('invalid structured move fields')
                gaps(move['gaps'])
                if not isinstance(move['intent_types'],list) or any(not isinstance(v,str) for v in move['intent_types']):raise ValueError('intent type array required')
                for attack in move['announced_attacks']:
                    if set(attack)!={'amount','repeats'}:raise ValueError('invalid announced attack')
                    validate_expression(attack['amount']);validate_expression(attack['repeats'])
                for command in move['commands']:
                    op=command.get('operation')
                    if op=='attack':
                        if set(command)!={'operation','amount','repeats'}:raise ValueError('invalid attack')
                        validate_expression(command['repeats'])
                    elif op in {'strength','block','weak','frail'}:
                        if set(command)!={'operation','amount'}:raise ValueError('invalid monster command')
                    elif op in {'draw','discard'}:
                        if (set(command)!={'operation','amount','card_id'} or command['card_id'] not in references
                                or not command['card_id'].startswith('CARD.')):raise ValueError('missing generated card reference')
                    elif op=='set_field':
                        if set(command)!={'operation','field','amount'}:raise ValueError('invalid state update')
                        validate_expression({'field':command['field']})
                    else:raise ValueError('unimplemented monster command: '+str(op))
                    validate_expression(command['amount'])
            result[identity]=freeze(row)
        except (KeyError,TypeError,ValueError,AttributeError) as exc:raise ValueError(str(identity)+': '+str(exc)) from exc
    return result
