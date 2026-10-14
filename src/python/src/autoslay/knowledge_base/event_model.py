"""Source-bound event dossiers. Symbolic rules are references, never executable C#."""
import re
import ast
from copy import deepcopy
from .behavior_graph import methods, properties, registration_conditions
from .monster_values import arguments, call_end, parameters, resolve

VERSION='1.0.0'
SCALAR=r'(?:\([^()]*\)\s*)?(?:base\.)?DynamicVars(?:\.([\w]+)|\["([^"\n]+)"\])(?:\.(?:BaseValue|IntValue|StringValue))?'
COMMANDS={'CreatureCmd.Damage':('lose_hp',2),'CreatureCmd.Heal':('heal',1),
          'CreatureCmd.GainMaxHp':('gain_max_hp',1),'CreatureCmd.LoseMaxHp':('lose_max_hp',2),
          'PlayerCmd.GainGold':('gain_gold',0),'PlayerCmd.LoseGold':('lose_gold',0)}


def source_mask(text):
    """Keep offsets but hide strings/comments from syntax matching and brace balancing."""
    return re.sub(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                  lambda m:''.join('\n' if c=='\n' else ' ' for c in m[0]),text)


def event_methods(text):
    result=methods(text);masked=source_mask(text)
    pattern=r'(?m)^\s*(?:(?:public|private|protected|internal)\s+(?:(?:async|static|override|virtual)\s+)*[\w<>?,\[\].]+|(?:async\s+)?(?:void|Task(?:<[^>]+>)?))\s+(\w+)(?:<[^>]+>)?\('
    local=r'(?<![\w.])(?:async\s+)?(?:void|Task(?:<[^>]+>)?)\s+(\w+)(?:<[^>]+>)?\('
    for m in [*re.finditer(pattern,masked),*re.finditer(local,masked)]:
        end=call_end(text,m.end()-1);start=masked.find('{',end)
        if start<0 or ';' in masked[end:start]:continue
        depth=1;i=start+1
        while i<len(masked) and depth:
            depth+=(masked[i]=='{')-(masked[i]=='}');i+=1
        result[m[1]]=text[start+1:i-1].strip()
    return result


def clean(expr):
    expr=re.sub(SCALAR,lambda m:(m[1] or m[2]),expr.strip())
    return re.sub(r'\((?:decimal|int|float|double|DamageVar|HpLossVar)\)\s*','',expr)


def calls(code):
    """Offsets and balanced arguments, including nested calls; no line-based truncation."""
    for m in re.finditer(r'(?<![\w])([\w.]+)(?:<([^<>]+)>)?\(',source_mask(code)):
        if m[1].split('.')[-1] in {'if','for','foreach','while','switch','nameof','typeof'}:continue
        end=call_end(code,m.end()-1)
        yield dict(name=m[1],generic=m[2],args=arguments(code[m.end():end]),offset=m.start(),end=end+1)


def arrays(text):
    result={}
    for m in re.finditer(r'\b(?:readonly\s+)?(?:int|string|decimal)\[\]\s+(\w+)\s*=\s*new\s+(?:int|string|decimal)\[\d*\]\s*\{([^}]+)\}',text):
        result[m[1]]=arguments(m[2])
    return result


def static(expr,values,levels):
    expr=clean(expr)
    for name,items in values.get('__arrays',{}).items():
        for i,item in enumerate(items):expr=expr.replace(f'{name}[{i}]',item)
    return resolve(expr,{k:v for k,v in values.items() if k!='__arrays'},0,levels)


def random_interval(expr,values,levels):
    """Conservative arithmetic bounds for source RNG calls; never sample or read RNG state."""
    expr=clean(expr);bounds={}
    matches=list(re.finditer(r'(?:base\.)?Rng\.Next(Int|Float)\(',expr))
    for index,m in reversed(list(enumerate(matches))):
        end=call_end(expr,m.end()-1);nums=[static(a,values,levels) for a in arguments(expr[m.end():end]) if a]
        if len(nums)>2 or any(type(v) not in (int,float) for v in nums):return None
        lo,hi=(0,1) if not nums else (0,nums[0]) if len(nums)==1 else nums
        if hi<=lo:return None
        key='_random'+str(index);bounds[key]=(lo,hi-1 if m[1]=='Int' else hi)
        expr=expr[:m.start()]+key+expr[end+1:]
    if not bounds:return None
    expr=re.sub(r'(?<=\d)[mf]\b','',expr)
    try:tree=ast.parse(expr,mode='eval')
    except SyntaxError:return None
    def walk(node):
        if isinstance(node,ast.Constant) and type(node.value) in (int,float):return node.value,node.value
        if isinstance(node,ast.Name):
            if node.id in bounds:return bounds[node.id]
            n=static(node.id,values,levels);return (n,n) if n is not None else None
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)):
            a=walk(node.operand);return None if a is None else (-a[1],-a[0]) if isinstance(node.op,ast.USub) else a
        if isinstance(node,ast.BinOp):
            a,b=walk(node.left),walk(node.right)
            if a is None or b is None:return None
            if isinstance(node.op,ast.Add):return a[0]+b[0],a[1]+b[1]
            if isinstance(node.op,ast.Sub):return a[0]-b[1],a[1]-b[0]
            if isinstance(node.op,ast.Mult):
                products=[x*y for x in a for y in b];return min(products),max(products)
        return None
    return walk(tree.body)


def guards(code,offset):
    result=registration_conditions(code,offset)
    # Switch labels remain conditions, rather than merging all cases into one outcome.
    for m in re.finditer(r'\bswitch\s*\(',code):
        end=call_end(code,m.end()-1);start=code.find('{',end)
        if start<0:continue
        depth=1;i=start+1
        while i<len(code) and depth:
            depth+=(code[i]=='{')-(code[i]=='}');i+=1
        if not start<offset<i:continue
        labels=list(re.finditer(r'\b(case\s+([^:\n]+)|default)\s*:',code[start+1:offset]))
        if labels:
            last=labels[-1]
            choices=[last[2]] if last[2] else []
            for prev,nxt in reversed(list(zip(labels[:-1],labels[1:]))):
                if code[start+1+prev.end():start+1+nxt.start()].strip():break
                if prev[2]:choices.insert(0,prev[2])
            result.append(' || '.join(code[m.end():end]+' == '+c for c in choices) if choices else 'default('+code[m.end():end]+')')
    for m in re.finditer(r'\b(for|foreach|while)\s*\(',code):
        end=call_end(code,m.end()-1);start=code.find('{',end)
        if start<0:continue
        depth=1;i=start+1
        while i<len(code) and depth:
            depth+=(code[i]=='{')-(code[i]=='}');i+=1
        if start<offset<i:result.append(m[1]+'('+code[m.end():end]+')')
    # Retain preceding early-exit guards. Explicitly labelled; not assumed to be a complete predicate.
    return list(dict.fromkeys(result))


def expression_info(expr,values,levels):
    normalized=clean(expr)
    number=static(normalized,values,levels)
    result=dict(expression=normalized,value=number,status='static' if number is not None else 'symbolic')
    table=re.fullmatch(r'(\w+)\[([^]]+)\]',normalized)
    if table and table[1] in values.get('__arrays',{}):
        result['indexed_values']=values['__arrays'][table[1]];result['index_expression']=table[2]
    return result


def render_template(template,variables,entities):
    """Do not present defaults as actual runtime values. Preserve raw template separately."""
    missing=[]
    def replacement(match):
        token=match[1];name=token.split(':',1)[0].split('.',1)[0]
        if name=='IsMultiplayer':return token.split(':',1)[-1].split('|')[-1] if ':' in token else ''
        if name=='energyPrefix':return '点能量'
        if name in entities:return entities[name]['name']
        var=variables.get(name)
        if not var:
            missing.append(name);return '【'+name+'：需读取当前事件】'
        if var['status']=='fixed' and var.get('value') is not None:return str(var['value'])+('点能量' if 'energyIcons' in token else '')
        if var.get('range'):
            lo,hi=var['range'];return f'【{lo}–{hi}，本次值以游戏为准】'
        if var.get('value') is not None:return f'【初始 {var["value"]}，随后按规则变化】'
        return '【'+var.get('display_rule','由本次事件状态决定')+'】'
    # Current event option localization uses non-nested tokens. Unhandled nested formatters are audited.
    rendered=re.sub(r'\{([^{}]+)\}',replacement,template)
    if '{' in rendered or '}' in rendered:missing.append('nested_formatter')
    return rendered,sorted(set(missing))


def explain(expression,values,seen=()):
    expression=clean(expression)
    for name in set(re.findall(r'\b\w+\b',expression)):
        if name in values and name not in seen and name!='__arrays' and isinstance(values[name],str) and values[name]!=expression:
            expression=re.sub(r'\b'+re.escape(name)+r'\b','('+explain(values[name],values,seen+(name,))+')',expression)
    terms={'NumberOfHoldOns':'已坚持次数','LingerCount':'已停留次数','NumberOfDigs':'已深入次数',
           'DecipherCount':'已解读次数','CurrentHpLoss':'本次失血'}
    for name,label in terms.items():expression=re.sub(r'\b'+name+r'\b',label,expression)
    expression=expression.replace('base.Owner.Creature.MaxHp','当前生命上限').replace('owner.Creature.MaxHp','当前生命上限')
    expression=re.sub(r'(?<=\d)[mf]\b','',expression)
    if 'HealRestSiteOption.GetHealAmount' in expression:return '按休息治疗规则回复：基础为生命上限的30%，再应用当前遗物等修正'
    if '当前生命上限 * 0.33' in expression:return '当前生命上限的33%，实际回复以游戏为准'
    return expression if not re.search(r'[A-Za-z_]',expression) else '由当前事件状态计算，详见数值与后续规则'


def extract(text,entry,levels,catalog,identity):
    from .behavior_graph import card_links
    definitions=event_methods(text);values=parameters(text)|properties(text);values['__arrays']=arrays(text)
    # Constant strings name custom variables and option keys.
    strings={m[1]:m[2] for m in re.finditer(r'const\s+string\s+(\w+)\s*=\s*"([^"]*)"',text)}
    variables={}
    canonical=text.split('CanonicalVars',1)[-1].split('public override',1)[0].split('protected override IReadOnlyList',1)[0]
    for c in calls(canonical):
        kind=c['name'];args=c['args']
        if not kind.endswith('Var') or not args:continue
        first=strings.get(args[0],args[0]);name=None;expression=None
        if first.startswith('"') and first.endswith('"'):name=first[1:-1];expression=args[1] if len(args)>1 else None
        elif args[0] in strings:name=strings[args[0]];expression=args[1] if len(args)>1 else None
        elif re.fullmatch(r'(\w+)\[\d+\]',args[0]):
            arr=re.fullmatch(r'(\w+)\[(\d+)\]',args[0]);items=values['__arrays'].get(arr[1],[])
            if int(arr[2])<len(items) and items[int(arr[2])].startswith('"'):name=items[int(arr[2])].strip('"');expression=args[1] if len(args)>1 else None
        if name is None:name=kind.removesuffix('Var');expression=args[0]
        number=static(expression,values,levels) if expression and kind!='StringVar' else None
        row=dict(name=name,kind=kind,expression=clean(expression) if expression else 'runtime',value=number,
                 status='fixed' if number is not None else 'runtime',updates=[])
        variables[name]=row
        if expression is not None and number is not None:values[name]=str(number)
        elif expression:values[name]=clean(expression)
    nodes={};bindings=[];unhandled=[]
    key_aliases={}
    for m in re.finditer(r'\b(?:string|LocString)\s+(\w+)\s*(?:=>|=)\s*([^;]+);',text):
        expression=m[2]
        loc=re.search(r'new LocString\("events",\s*"([^"]+)"\)',expression)
        key_aliases[m[1]]='"'+loc[1].removesuffix('.title').removesuffix('.description')+'"' if loc else expression
    def key_pattern(expr,seen=()):
        expr=expr.strip()
        if expr in strings:return re.escape(strings[expr])
        if expr in key_aliases and expr not in seen:return key_pattern(key_aliases[expr],seen+(expr,))
        initial=re.fullmatch(r'InitialOptionKey\("([^"]+)"\)',expr)
        if initial:return re.escape(entry['entity_id'].split('.',1)[1]+'.pages.INITIAL.options.'+initial[1])
        if expr.startswith('$"'):
            pieces=re.split(r'(\{[^}]+\})',expr[2:-1]);return ''.join('[A-Z0-9_]+' if p.startswith('{') else re.escape(p) for p in pieces)
        if expr.startswith('"'):
            pieces=arguments(expr.replace(' + ',','))
            return ''.join(re.escape(p.strip('"')) if p.startswith('"') and p.endswith('"') else '[A-Z0-9_]+' for p in pieces)
        return None
    for name,code in definitions.items():
        node=dict(method=name,parameters=[],effects=[],updates=[],transitions=[],options=[],local_calls=[],random=[],returns=[],conditions=[],switch_cases=[],operations=[])
        sig=re.search(r'\b'+re.escape(name)+r'(?:<([^>]+)>)?\(([^\n]*)\)\s*(?:where[^\n]+\s*)?\{',text)
        if sig:
            node['type_parameters']=arguments(sig[1]) if sig[1] else []
            node['parameters']=arguments(sig[2]) if sig[2].strip() else []
        for m in re.finditer(r'\bif\s*\(',code):
            end=call_end(code,m.end()-1);node['conditions'].append(code[m.end():end])
        for m in re.finditer(r'\breturn\s+([^;]+);',code):
            node['returns'].append(dict(**expression_info(m[1],values,levels),conditions=guards(code,m.start())))
        for m in re.finditer(r'(\w+(?:\.\w+)*)\s+switch\s*\{',code):
            depth=1;i=m.end()
            while i<len(code) and depth:
                depth+=(code[i]=='{')-(code[i]=='}');i+=1
            for arm in arguments(code[m.end():i-1]):
                if '=>' not in arm:continue
                case,value=arm.split('=>',1)
                node['switch_cases'].append(dict(selector=m[1],case=case.strip(),value=expression_info(value,values,levels)))
        for c in calls(code):
            conditions=guards(code,c['offset']);base=c['name'].removeprefix('base.');args=c['args'];leaf=base.split('.')[-1]
            if leaf=='EventOption' and len(args)>2:
                key=args[4] if len(args)>4 and '.options.' in args[4] else args[2]
                # Resolve fixed InitialOptionKey and const strings without guessing interpolated state.
                initial=re.fullmatch(r'InitialOptionKey\("([^"]+)"\)',key)
                if initial:key='"'+entry['entity_id'].split('.',1)[1]+'.pages.INITIAL.options.'+initial[1]+'"'
                key=strings.get(key,key)
                handler=args[1];invocation=None
                alias=re.search(r'\b'+re.escape(handler)+r'\s*=\s*(\w+)\s*;',code) if re.fullmatch(r'\w+',handler) else None
                if alias and alias[1] in definitions:handler=alias[1]
                if '=>' in handler or 'delegate' in handler:
                    invoked=next((x for x in calls(handler) if x['name'] in definitions),None)
                    if invoked:invocation=dict(method=invoked['name'],arguments=invoked['args']);handler=invoked['name']
                binding=dict(handler=handler,key_expression=key,conditions=conditions,declared_in=name,invocation=invocation,key_pattern=key_pattern(key))
                bindings.append(binding);node['options'].append(binding)
            if leaf=='RelicOption' and c['generic'] and args:
                key=entry['entity_id'].split('.',1)[1]+'.pages.INITIAL.options.'+identity(c['generic'])
                binding=dict(handler=args[0],key_expression='"'+key+'"',conditions=conditions,declared_in=name,key_pattern=re.escape(key))
                bindings.append(binding);node['options'].append(binding)
            if leaf=='Dish' and len(args)>=4 and args[0].startswith('"'):
                key=entry['entity_id'].split('.',1)[1]+'.pages.ALL.options.'+args[0].strip('"')
                binding=dict(handler=args[1],key_expression='"'+key+'"',conditions=conditions,declared_in=name,key_pattern=re.escape(key),selection_weight=expression_info(args[3],values,levels))
                bindings.append(binding);node['options'].append(binding)
            if base in definitions or leaf in definitions and base==leaf:
                node['local_calls'].append(dict(method=leaf,arguments=[clean(a) for a in args],generic_arguments=c['generic'],conditions=conditions))
            if leaf in ('SetEventState','SetEventFinished') or leaf.startswith('EnterCombat'):
                node['transitions'].append(dict(kind=leaf,arguments=args,conditions=conditions))
            if re.search(r'Factory\.|CreationOptions\.|^.*Reward$|TakeRandom|UnstableShuffle|Shuffle|\.Where$|\.Take$|\.WithFlags$',base):
                node['operations'].append(dict(operation=base,generic=c['generic'],arguments=args,conditions=conditions))
            if base in COMMANDS and len(args)>COMMANDS[base][1]:
                kind,idx=COMMANDS[base];node['effects'].append(dict(kind=kind,amount=expression_info(args[idx],values,levels),conditions=conditions,command=base))
            elif re.match(r'(?:Creature|Player|Card|CardPile|CardSelect|Relic|Potion|Rewards)Cmd\.',base):
                if any(s in base for s in ('Vfx','TriggerAnim','Preview')):continue
                kind=base.split('.',1)[-1]
                linked=[]
                _,cards=card_links(c['name']+('<'+c['generic']+'>' if c['generic'] else '')+'('+','.join(args)+')',identity,catalog)
                linked.extend(cards)
                for category,type_name in re.findall(r'ModelDb\.(Card|Relic|Potion)<([\w.]+)>',','.join(args)):
                    target=category.upper()+'.'+identity(type_name.split('.')[-1]);ref=next((e for e in catalog['entries'] if e['entity_id']==target),None)
                    if ref:linked.append(dict(entity_id=target,name=ref['name']))
                if c['generic'] and 'RelicCmd' in base:
                    target='RELIC.'+identity(c['generic'].split('.')[-1]);ref=next((e for e in catalog['entries'] if e['entity_id']==target),None)
                    if ref:linked.append(dict(entity_id=target,name=ref['name']))
                effect=dict(kind=kind,command=base,generic=c['generic'],arguments=args,conditions=conditions,entities=linked)
                if base=='CardCmd.Enchant' and len(args)>1:effect['amount']=expression_info(args[1],values,levels)
                node['effects'].append(effect)
            if re.search(r'(?:Rng|rng)\.',base) and not 'Chaotic' in base:
                random=dict(operation=leaf,arguments=args,conditions=conditions,distribution='unknown')
                if leaf=='NextInt':
                    nums=[static(a,values,levels) for a in args]
                    if all(type(v) is int for v in nums) and len(nums) in (1,2):
                        lo,hi=(0,nums[0]) if len(nums)==1 else nums
                        if hi>lo:random.update(distribution='uniform_integer',min_inclusive=lo,max_exclusive=hi,probability_each=1/(hi-lo))
                elif leaf=='NextBool':random.update(distribution='uniform_boolean',probability_each=.5)
                elif leaf=='NextFloat':
                    nums=[static(a,values,levels) for a in args if a]
                    if len(nums)<=2 and all(type(v) in (int,float) for v in nums):
                        lo,hi=(0,1) if not nums else (0,nums[0]) if len(nums)==1 else nums
                        random.update(distribution='uniform_float',min_bound=lo,max_bound=hi)
                elif leaf=='NextItem':random.update(distribution='runtime_candidate_pool',candidate_pool_expression=args[0] if args else '')
                node['random'].append(random)
            if leaf=='Add' and len(args)>=2 and args[0].startswith('"'):
                token=args[0].strip('"')
                if any('{'+token in o.get('description_template','') for o in entry['options']) and token not in variables:
                    variables[token]=dict(name=token,kind='formatted_runtime_value',expression=args[1],value=None,status='runtime',updates=[])
                if token in variables:variables[token].setdefault('formatted_sources',[]).append(dict(method=name,receiver=base.rsplit('.',1)[0],expression=args[1]))
        # All assignment forms and ++/-- are retained with location and guards.
        pattern=r'((?:base\.)?DynamicVars(?:\.\w+|\["[^"\n]+"\])(?:\.\w+)?|[A-Za-z_]\w*(?:\.\w+)*)\s*(\+=|-=|\*=|=(?!=|>)|\+\+|--)\s*([^;\n]*)'
        for m in re.finditer(pattern,code):
            expression=m[3].strip() if m[2] not in ('++','--') else '1'
            update=dict(target=clean(m[1]),operation=m[2],value=expression_info(expression,values,levels),conditions=guards(code,m.start()))
            node['updates'].append(update)
            target=clean(m[1]);var=variables.get(target)
            if var and 'DynamicVars' in m[1]:
                var['updates'].append(dict(method=name,**update));var['status']='dynamic'
                rng=re.search(r'(?:base\.)?Rng\.NextInt\(([^)]+)\)',expression)
                if name=='CalculateVars' and rng:
                    nums=[static(a,values,levels) for a in arguments(rng[1])]
                    if all(type(v) is int for v in nums) and len(nums) in (1,2):
                        lo,hi=(0,nums[0]) if len(nums)==1 else nums
                        if hi>lo:
                            base_value=var.get('value')
                            if m[2]=='+=' and base_value is not None:lo+=base_value;hi+=base_value
                            elif m[2]=='-=' and base_value is not None:lo,hi=base_value-hi+1,base_value-lo+1
                            # Extra arithmetic stays symbolic; never label an approximate interval exact.
                            outer=clean(expression);simple=re.fullmatch(r'(?:base\.)?Rng\.NextInt\([^)]*\)',outer)
                            if simple:var['range']=[lo,hi-1]
        # Keep mechanically relevant helper definitions, including initialization and cost functions.
        node['evidence']=code
        nodes[name]=node
    # Costs read from mutable variables must never be reported as their canonical initial value.
    dynamic={n for n,v in variables.items() if v['status']!='fixed'}
    def depends(expr,seen=()):
        names=set(re.findall(r'\b\w+\b',expr))
        if names & dynamic:return True
        return any(n not in seen and isinstance(values.get(n),str) and depends(values[n],seen+(n,)) for n in names)
    for node in nodes.values():
        for effect in node['effects']:
            amount=effect.get('amount')
            if amount and depends(amount['expression']):
                amount['initial_value']=amount.pop('value',None);amount['value']=None;amount['status']='state_dependent'
                token=amount['expression']
                if token in variables:amount['variable']=token
    for variable in variables.values():
        calculated=[u for u in variable['updates'] if u['method']=='CalculateVars']
        if calculated:
            update=calculated[-1];interval=random_interval(update['value']['expression'],values,levels)
            if interval:
                lo,hi=interval
                if update['operation']=='+=' and variable['value'] is not None:lo+=variable['value'];hi+=variable['value']
                elif update['operation']=='-=' and variable['value'] is not None:lo,hi=variable['value']-hi,variable['value']-lo
                variable['range']=[lo,hi]
        if calculated and 'range' not in variable:
            variable['canonical_value']=variable['value'];variable['value']=None
            variable['expression']=calculated[-1]['value']['expression'];variable['status']='runtime'
        for update in variable['updates']:
            helper=re.fullmatch(r'(\w+)\(\)',update['value']['expression'])
            if helper and helper[1] in nodes:
                variable['future_values']=[r for r in nodes[helper[1]]['returns'] if r['conditions']]
        runtime_names={'Potion':'本次选中的药水','Relic':'本次选中的遗物','Rarity':'该药水对应的奖励稀有度',
                       'Type':'本次生成的卡牌类型','RandomCard':'本次随机选中的可移除卡牌','DrinkRandomPotion':'本次选中的药水',
                       'TopRelicOwned':'你用于交换的第一件遗物','TopRelicNew':'第一件随机提供的遗物',
                       'MiddleRelicOwned':'你用于交换的第二件遗物','MiddleRelicNew':'第二件随机提供的遗物',
                       'BottomRelicOwned':'你用于交换的第三件遗物','BottomRelicNew':'第三件随机提供的遗物'}
        variable['display_rule']=runtime_names.get(variable['name'],explain(variable['expression'],values))
    def reachable(handler):
        seen=set();todo=[handler]
        while todo:
            name=todo.pop()
            if name in seen or name not in nodes:continue
            seen.add(name);todo.extend(c['method'] for c in nodes[name]['local_calls'])
        return sorted(seen)
    for option in entry['options']:
        option['title_template']=option.get('title_template',option['title'])
        option['title'],title_missing=render_template(option['title_template'],variables,entry.get('placeholder_entities',{}))
        key=option['text_key'];matches=[]
        for b in bindings:
            expr=b['key_expression'].lstrip('$').strip('"')
            regex='^'+re.escape(expr)+'$'
            regex=re.sub(r'\\\{.*?\\\}',r'[A-Z0-9_]+',regex)
            if re.fullmatch(b.get('key_pattern') or regex,key):matches.append(b)
        # Helper-generated keys can be enumerated from their actual return expression.
        if not matches:
            for b in bindings:
                helper=re.match(r'(\w+)\(',b['key_expression'])
                if helper and helper[1] in definitions:
                    returns=re.findall(r'return\s+([^;]+);',definitions[helper[1]])
                    if any(re.fullmatch(key_pattern(r) or '(?!)',key) for r in returns) or '"'+key+'"' in definitions[helper[1]]:matches.append(b)
        option['bindings']=matches
        stage={}
        for binding in matches:
            expression=binding['key_expression']
            if expression.startswith('$"'):
                tokens=re.findall(r'\{([^}]+)\}',expression)
                pattern=re.escape(expression[2:-1])
                for token in tokens:pattern=pattern.replace(re.escape('{'+token+'}'),r'(\d+|[A-Z_]+)',1)
                match=re.fullmatch(pattern,key)
                if match:
                    for token,value in zip(tokens,match.groups()):
                        if re.fullmatch(r'\w+',token) and value.isdigit():stage[token]=int(value)
        option['stage_context']=stage
        option['reachable_methods']=sorted({n for b in matches for n in reachable(b['handler'])})
        # A dish's effect runs inside the common paid/repeating event action.
        if any('selection_weight' in b for b in matches):
            common=next((b['handler'] for b in bindings if b['key_expression']=='_currentDish.optionKey'),None)
            if common:option['reachable_methods']=sorted(set(option['reachable_methods'])|set(reachable(common)))
        option['handler']=next((b['handler'] for b in matches if b['handler']!='null'),option.get('handler'))
        preview=deepcopy(variables)
        for variable in preview.values():
            for future in variable.get('future_values',[]):
                predicates=[re.fullmatch(r'(\w+)\s*==\s*(\d+)',c) for c in future['conditions']]
                if predicates and all(p and stage.get(p[1])==int(p[2]) for p in predicates):
                    if future['value'] is not None:variable.update(value=future['value'],status='fixed')
                    else:variable.update(value=None,status='runtime',display_rule=explain(future['expression'],values))
        option['preview_variables']={n:v for n,v in preview.items() if v!=variables[n]}
        option['description'],missing=render_template(option.get('description_template',option['description']),preview,entry.get('placeholder_entities',{}))
        missing=sorted(set(missing+title_missing));option['unresolved_placeholders']=missing
        option['active_in_source']=bool(matches)
        if not matches:
            # Localization sometimes retains obsolete options. A missing source reference is different from an unresolved binding.
            option['source_status']='localization_only' if key not in text else 'binding_unresolved'
            if option['source_status']=='binding_unresolved':unhandled.append(dict(kind='option_binding',text_key=key))
        for placeholder in missing:unhandled.append(dict(kind='placeholder',text_key=key,name=placeholder))
    # Preserve parameterized cost functions and decision helpers, but exclude renderer-only methods.
    roots={n for o in entry['options'] for n in o['reachable_methods']}|{n for n in ('GenerateInitialOptions','CalculateVars','IsAllowed') if n in nodes}
    roots|={c['method'] for name in list(roots) for c in nodes[name]['local_calls']}
    relevant={name:n for name,n in nodes.items() if not re.search(r'GetAssetPaths|Play.*Vfx|Add.*Vfx|OnRoomEnter',name) or n['effects'] or n['transitions']}
    property_rules={}
    for m in re.finditer(r'(?:public|private|protected)\s+(?:(?:override|static)\s+)*[\w<>?,. ]+\s+(\w+)\s*\{\s*get\s*\{',text):
        if m[1]=='GameInfoOptions':continue
        start=m.end()-1;depth=1;i=start+1
        while i<len(text) and depth:
            depth+=(text[i]=='{')-(text[i]=='}');i+=1
        property_rules[m[1]]=text[start+1:i-1].strip()
    model=dict(schema_version='event-model/1.0',module_version=VERSION,variables=variables,arrays=values['__arrays'],
               constants={k:v for k,v in values.items() if k!='__arrays'},methods=relevant,property_rules=property_rules,
               initial_method='GenerateInitialOptions',bindings=bindings,unresolved=unhandled,
               execution_authorized=False,status='source_reference',
               semantics='Ordered source operations; conditions and helper arguments must be evaluated against live state. Not an executable simulator.')
    from .behavior_graph import graph
    model['state_fields']=graph(text)['state_fields']
    model['state_aliases']={n:v for n,v in properties(text).items() if v in {f['name'] for f in model['state_fields']}}
    return model
