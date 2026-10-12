"""Resolve only explicit static arithmetic and game ascension switches.

This is reference extraction, never a C# interpreter or enemy simulator.
Unknown state, calls and branches stay unresolved instead of becoming zero.
"""
import ast
import operator
import re


def arguments(text):
    result=[];depth=0;start=0;quote=None;escaped=False
    for i,char in enumerate(text):
        if quote:
            if escaped:escaped=False
            elif char=='\\':escaped=True
            elif char==quote:quote=None
            continue
        if char in ('"',"'"):quote=char;continue
        if char in '([{':depth+=1
        elif char in ')]}':depth-=1
        elif char==',' and depth==0:result.append(text[start:i].strip());start=i+1
    result.append(text[start:].strip())
    return result


def call_end(text,start):
    depth=0;quote=None;escaped=False
    for i in range(start,len(text)):
        char=text[i]
        if quote:
            if escaped:escaped=False
            elif char=='\\':escaped=True
            elif char==quote:quote=None
            continue
        if char in ('"',"'"):quote=char;continue
        if text[i]=='(':depth+=1
        elif text[i]==')':
            depth-=1
            if depth==0:return i
    raise ValueError('Unbalanced source call')


def thresholds(source):
    content=source.split('{',1)[1].rsplit('}',1)[0]
    result={};value=-1
    for item in content.split(','):
        item=item.strip()
        if not item:continue
        if '=' in item:name,number=item.split('=');value=int(number.strip())
        else:name=item;value+=1
        result[name.strip()]=value
    return result


def parameters(text):
    result={m[1]:m[2].strip() for m in re.finditer(r'\b(?:int|decimal|float)\s+(\w+)\s*=>\s*([^;]+);',text)}
    result.update({m[1]:m[2].strip() for m in re.finditer(r'\bconst\s+(?:int|decimal|float)\s+(\w+)\s*=\s*([^;]+);',text)})
    return result


def resolve(expression,values,ascension,levels,seen=()):
    expression=expression.strip().removeprefix('() => ').strip()
    coalesce=re.fullmatch(r'(\w+)\s*\?\?\s*(.+)',expression)
    if coalesce and values.get(coalesce[1])=='null':
        return resolve(coalesce[2],values,ascension,levels,seen)
    prefix='AscensionHelper.GetValueIfAscension('
    while prefix in expression:
        start=expression.index(prefix);end=call_end(expression,start+len(prefix)-1)
        args=arguments(expression[start+len(prefix):end])
        if len(args)!=3:return None
        threshold=levels.get(args[0].removeprefix('AscensionLevel.'))
        if threshold is None:return None
        value=resolve(args[1] if ascension>=threshold else args[2],values,ascension,levels,seen)
        if value is None:return None
        expression=expression[:start]+str(value)+expression[end+1:]
    expression=re.sub(r'(?<=\d)[mf]\b','',expression)
    try:tree=ast.parse(expression,mode='eval')
    except SyntaxError:return None
    def walk(node):
        if isinstance(node,ast.Constant) and type(node.value) in (int,float):return node.value
        if isinstance(node,ast.Name) and node.id in values and node.id not in seen:
            return resolve(values[node.id],values,ascension,levels,seen+(node.id,))
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)):
            val=walk(node.operand);return None if val is None else -val if isinstance(node.op,ast.USub) else val
        if isinstance(node,ast.BinOp) and type(node.op) in (ast.Add,ast.Sub,ast.Mult):
            a,b=walk(node.left),walk(node.right)
            if a is None or b is None:return None
            return {ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv}[type(node.op)](a,b)
        return None
    result=walk(tree.body)
    return result if result is not None and abs(result)<1_000_000 else None


def numeric_moves(expression,handler,values,levels):
    specs=[]
    for match in re.finditer(r'new (\w+)Intent\(',expression):
        end=call_end(expression,match.end()-1)
        args=arguments(expression[match.end():end])
        kind=match[1]
        if kind in ('SingleAttack','MultiAttack') and args:
            specs.append(dict(kind='attack',damage_expression=args[0],repeats_expression=args[1] if kind=='MultiAttack' and len(args)>1 else '1'))
    for match in re.finditer(r'(?:CreatureCmd\.GainBlock|PowerCmd\.Apply<([^>]+)>)\(',handler or ''):
        end=call_end(handler,match.end()-1);args=arguments(handler[match.end():end])
        # Apply(choiceContext, target, amount, ...); GainBlock(target, amount, ...).
        position=2 if match[1] else 1
        if len(args)>position:
            specs.append(dict(kind='power' if match[1] else 'block',power=match[1],amount_expression=args[position],target_expression=args[position-1]))
    bands=[]
    for level in range(max(levels.values())+1):
        effects=[]
        for spec in specs:
            row=dict(spec)
            for key in ('damage','repeats','amount'):
                if key+'_expression' in row:row[key]=resolve(row[key+'_expression'],values,level,levels)
            if row['kind']=='attack':row['total_damage']=row['damage']*row['repeats'] if row['damage'] is not None and row['repeats'] is not None else None
            effects.append(row)
        if bands and bands[-1]['effects']==effects:bands[-1]['ascension_max']=level
        else:bands.append(dict(ascension_min=level,ascension_max=level,effects=effects))
    return bands
