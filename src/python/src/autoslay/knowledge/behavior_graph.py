"""Version-bound state graphs and mechanics evidence, without executing game code."""
import re
from .monster_values import arguments, call_end
from .source_audit import body


def registration_conditions(code,position):
    """Keep construction-time guards separate from runtime branch predicates."""
    conditions=[]
    def closing(start):
        depth=0
        for i in range(start,len(code)):
            if code[i]=='{':depth+=1
            elif code[i]=='}':
                depth-=1
                if depth==0:return i
        return len(code)
    for m in re.finditer(r'\bif\s*\(',code):
        end=call_end(code,m.end()-1);condition=code[m.end():end]
        start=end+1
        while start<len(code) and code[start].isspace():start+=1
        if start>=len(code) or code[start]!='{':continue
        finish=closing(start)
        if start<position<finish:conditions.append(condition)
        following=re.match(r'\s*else\s*\{',code[finish+1:])
        if following:
            other=finish+following.end()
            if other<position<closing(other):conditions.append('!('+condition+')')
    return conditions


def methods(text):
    result = {}
    for m in re.finditer(r'(?:public|private|protected|internal)\s+(?:(?:static|override|virtual|async|sealed)\s+)*[\w<>?,\[\] ]+?\s+(\w+)\([^\n]*\)\s*\{', text):
        code = body(text[m.start():], m[1])
        if code is not None and m[1] not in result:
            result[m[1]] = code.strip()
    return result


def properties(text):
    result = {m[1]: m[2].strip() for m in re.finditer(r'\b(?:bool|int|decimal|float|MonsterState|MoveState)\s+(\w+)\s*=>\s*([^;]+);', text)}
    for m in re.finditer(r'\b(?:bool|int|decimal|float)\s+(\w+)\s*\{\s*get\s*\{\s*return\s+([^;]+);', text):
        result.setdefault(m[1], m[2].strip())
    return result


def graph(text):
    code = body(text, 'GenerateMoveStateMachine') or ''
    nodes = []
    # Include assignments nested inside casts and chained FollowUpState assignments.
    for m in re.finditer(r'new (MoveState|ConditionalBranchState|RandomBranchState)\(', code):
        end = call_end(code, m.end()-1)
        args = arguments(code[m.end():end])
        prefix = code[code.rfind(';', 0, m.start())+1:m.start()]
        lhs = re.findall(r'(\w+(?:\.FollowUpState)?)\s*=', prefix)
        if not lhs or not args:
            continue
        variable = next((v for v in lhs if '.' not in v), lhs[-1])
        node = dict(variable=variable, state_id=args[0].strip('"'), kind=m[1], branches=[])
        if m[1]=='MoveState':
            node.update(handler=args[1] if len(args)>1 else '', intent_expression=','.join(args[2:]))
        tail=code[end+1:code.find(';',end)+1]
        node['settings']={a[1]:a[2].strip() for a in re.finditer(r'(\w+)\s*=\s*([^,}\n]+)',tail)}
        nodes.append(node)
    by_variable = {n['variable']: n for n in nodes}
    edges = []
    for statement in code.split(';'):
        owners = re.findall(r'(\w+)\.FollowUpState\s*=', statement)
        if not owners:
            continue
        created = re.search(r'new (?:MoveState|ConditionalBranchState|RandomBranchState)\(', statement)
        if created:
            declared = re.search(r'(?:MoveState|ConditionalBranchState|RandomBranchState)\s+(\w+)\s*=', statement)
            target = declared[1] if declared else None
        else:
            target_match = re.search(r'=\s*(\w+)\s*\)*\s*$', statement)
            target = target_match[1] if target_match else None
        if target:
            edges.extend(dict(source=owner, target=target) for owner in owners)
    for m in re.finditer(r'(\w+)\.(AddState|AddBranch)\(', code):
        args = arguments(code[m.end():call_end(code,m.end()-1)])
        node = by_variable.get(m[1])
        if node is None:
            continue
        branch = dict(target=args[0], priority=len(node['branches']))
        branch['registration_conditions']=registration_conditions(code,m.start())
        if m[2]=='AddState':
            branch['condition_expression'] = args[1].removeprefix('() => ').strip()
        else:
            remaining = args[1:]; cooldown=0; weight='1'; maximum=None
            if remaining and remaining[0].startswith('MoveRepeatType.'):
                repeat=remaining.pop(0).split('.')[-1]
            elif len(remaining)>1 and remaining[1].startswith('MoveRepeatType.'):
                cooldown=int(remaining.pop(0)); repeat=remaining.pop(0).split('.')[-1]
            else:
                repeat='CanRepeatXTimes'; maximum=int(remaining.pop(0))
                if len(remaining)>1 and re.fullmatch(r'\d+',remaining[0]):
                    cooldown=maximum; maximum=int(remaining.pop(0))
            if remaining:weight=remaining[0].removeprefix('() => ').strip()
            branch.update(weight_expression=weight,cooldown=cooldown,repeat_type=repeat,max_consecutive=maximum)
        node['branches'].append(branch)
    initial=[];initial_choices=[]
    for m in re.finditer(r'new MonsterMoveStateMachine\(',code):
        args=arguments(code[m.end():call_end(code,m.end()-1)])
        if len(args)>1:
            expression=args[1];initial.append(expression)
            assigned=re.search(r'\bMonsterState\s+'+re.escape(expression)+r'\s*=\s*([^;]+);',code) if re.fullmatch(r'\w+',expression) else None
            expanded=assigned[1] if assigned else expression
            normalized=re.sub(r'\(\(?(?:MonsterState|MoveState)\)','',expanded).strip()
            ternary=re.search(r'^\(?([\w.]+)\s*\?\s*\(*\s*(\w+)\s*\)*\s*:\s*\(*\s*(\w+)\s*\)*$',normalized)
            guards=registration_conditions(code,m.start())
            if ternary:
                initial_choices.extend([dict(target=ternary[2],conditions=guards+[ternary[1]]),dict(target=ternary[3],conditions=guards+['!('+ternary[1]+')'])])
            elif expression in by_variable:initial_choices.append(dict(target=expression,conditions=guards))
    last_add=list(re.finditer(r'list\.Add\([^;]+;',code))
    initial_evidence=code[last_add[-1].end():].strip() if last_add else code
    fields=[dict(name=m[2],type=m[1],initial_expression=m[3].strip() if m[3] else ('null' if m[1].endswith('?') else 'false' if m[1]=='bool' else '0'))
            for m in re.finditer(r'\bprivate\s+(bool\??|int\??|decimal\??|float\??)\s+(\w+)\s*(?:=(?!>)\s*([^;]+))?;',text)]
    return dict(nodes=nodes,edges=edges,initial_expressions=initial,initial_choices=initial_choices,initial_selection_evidence=initial_evidence,state_fields=fields,
                definitions=properties(text),construction_evidence=code.strip(),
                conditional_selection='first_true_in_declared_order',
                random_selection='eligible_weight / sum_eligible_weights; repeat and cooldown exclusions apply',
                unresolved_targets=sorted({e['target'] for e in edges if e['target'] not in by_variable}))


def card_links(text, identity, catalog):
    by_id={e['entity_id']:e for e in catalog['entries']}
    links={}
    for m in re.finditer(r'new StringVar\("([^"\n]+)",\s*ModelDb\.(Card|Potion|Relic)<([\w.]+)>\(\)\.(Title|DynamicDescription)',text):
        key=m[2].upper()+'.'+identity(m[3].split('.')[-1]);card=by_id.get(key)
        if card:links[m[1]]=dict(entity_id=key,name=card['name'])
    ids=sorted({'CARD.'+identity(m[1]) for m in re.finditer(r'(?:Card|CreateCard|FromCardWithCardHoverTips)<(\w+)>',text)} & by_id.keys())
    return links, [dict(entity_id=i,name=by_id[i]['name']) for i in ids]


def dynamic_vars(text):
    result={}
    for m in re.finditer(r'new (\w+Var)(?:<(\w+)>)?\(',text):
        args=arguments(text[m.end():call_end(text,m.end()-1)])
        if not args:continue
        explicit=re.fullmatch(r'"([^\"]+)"',args[0])
        if explicit and len(args)>1:name=explicit[1];value=args[1]
        elif explicit:continue # Runtime-only StringVar.
        else:name=m[2] or m[1].removesuffix('Var');value=args[0]
        result[name]=value
    return result


def expand_helpers(code,definitions,seen=()):
    """Retain source control flow while following named local helper calls."""
    if len(seen)>5:return code
    for m in reversed(list(re.finditer(r'(?<![\w.])(?:await\s+)?(\w+)\(\)\s*;',code))):
        name=m[1]
        if name in definitions and name not in seen:
            code=code[:m.start()]+'{ /* '+name+' */ '+expand_helpers(definitions[name],definitions,seen+(name,))+' }'+code[m.end():]
    return code
