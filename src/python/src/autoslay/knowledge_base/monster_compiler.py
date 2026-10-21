"""OFFLINE migration helper. Runtime prediction never imports this module.

The finite extractor preserves unsupported source as diagnostics, not executable
strings. Its output requires source review; compilation is not semantic approval.
"""
import ast
import re
from autoslay.knowledge_base.monster_values import arguments,call_end


def compile_expression(source,definitions,levels,seen=()):
    text=str(source).strip().removeprefix('() => ').strip()
    text=text.replace('base.Creature.CurrentHp','hp').replace('base.Creature.MaxHp','max_hp')
    text=text.replace('&&',' and ').replace('||',' or ')
    text=re.sub(r'!(?!=)',' not ',text).strip()
    text=re.sub(r'\btrue\b','True',text);text=re.sub(r'\bfalse\b','False',text)
    text=re.sub(r'(\d+(?:\.\d+)?)[mf]\b',r'DECIMAL("\1")',text)
    def walk(n):
        if isinstance(n,ast.Constant) and type(n.value) in {int,bool}:return n.value
        if isinstance(n,ast.Constant) and type(n.value) is float:return {'decimal':str(n.value)}
        if isinstance(n,ast.Name):
            if n.id in seen:raise ValueError('cyclic parameter reference')
            if n.id in definitions:return compile_expression(definitions[n.id],definitions,levels,seen+(n.id,))
            return {'field':n.id}
        if isinstance(n,ast.UnaryOp) and type(n.op) in {ast.Not,ast.USub,ast.UAdd}:
            return walk(n.operand) if isinstance(n.op,ast.UAdd) else {('not' if isinstance(n.op,ast.Not) else 'negate'):[walk(n.operand)]}
        if isinstance(n,ast.BoolOp):return {('and' if isinstance(n.op,ast.And) else 'or'):[walk(c) for c in n.values]}
        if isinstance(n,ast.BinOp) and type(n.op) in {ast.Add,ast.Sub,ast.Mult,ast.Div}:
            return {{ast.Add:'add',ast.Sub:'subtract',ast.Mult:'multiply',ast.Div:'divide'}[type(n.op)]:[walk(n.left),walk(n.right)]}
        if isinstance(n,ast.Compare) and len(n.ops)==1 and type(n.ops[0]) in {ast.Eq,ast.NotEq,ast.Lt,ast.LtE,ast.Gt,ast.GtE}:
            return {{ast.Eq:'equal',ast.NotEq:'not_equal',ast.Lt:'less',ast.LtE:'less_equal',ast.Gt:'greater',ast.GtE:'greater_equal'}[type(n.ops[0])]:[walk(n.left),walk(n.comparators[0])]}
        if isinstance(n,ast.Call) and not n.keywords:
            if isinstance(n.func,ast.Name) and n.func.id=='DECIMAL' and len(n.args)==1 and isinstance(n.args[0],ast.Constant):
                return {'decimal':n.args[0].value}
            if (isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id=='AscensionHelper'
                    and n.func.attr=='GetValueIfAscension' and len(n.args)==3):
                level=n.args[0]
                if not isinstance(level,ast.Attribute) or not isinstance(level.value,ast.Name) or level.value.id!='AscensionLevel' or level.attr not in levels:
                    raise ValueError('missing ascension threshold')
                return {'if':[{'greater_equal':[{'ascension':True},levels[level.attr]]},walk(n.args[1]),walk(n.args[2])]}
        raise ValueError('unsupported source expression')
    try:return walk(ast.parse(text,mode='eval').body)
    except (ValueError,SyntaxError,RecursionError):return {'unknown':'offline_expression_not_compiled'}


def compile_monster(entry,levels,card_ids):
    graph=entry.get('state_graph') or {};definitions=dict(entry.get('parameter_expressions',{}))|graph.get('definitions',{})
    def expr(s):return compile_expression(s,definitions,levels)
    gaps=[];nodes=[];moves=[]
    if not graph.get('nodes') or not entry.get('moves'):gaps.append('monster_state_machine_missing')
    known={n['variable'] for n in graph.get('nodes',[])}
    if graph.get('unresolved_targets'):gaps.append('monster_transition_targets_unresolved')
    for node in graph.get('nodes',[]):
        branches=[];problems=[]
        if node.get('settings'):problems.append('monster_transition_settings_unmodeled')
        kind=node['kind']
        if kind not in {'MoveState','ConditionalBranchState','RandomBranchState'}:
            gaps.append('unsupported_node_kind:'+kind);continue
        for b in node.get('branches',[]):
            if b['target'] not in known:problems.append('missing_branch_target');continue
            conditions=[expr(c) for c in b.get('registration_conditions',[])]
            if kind=='ConditionalBranchState':conditions.append(expr(b['condition_expression']))
            condition={'and':conditions} if len(conditions)>1 else conditions[0] if conditions else True
            repeat=b.get('repeat_type','CanRepeatForever');bound=b.get('max_consecutive',0)
            branch_gaps=[]
            if repeat not in {'CannotRepeat','CanRepeatForever','UseOnlyOnce','CanRepeatXTimes'}:
                branch_gaps.append('unknown_repeat_type');repeat='CanRepeatForever'
            cooldown=b.get('cooldown',0)
            if type(cooldown) is not int or cooldown<0:branch_gaps.append('unknown_move_cooldown');cooldown=0
            if repeat!='CanRepeatXTimes':bound=0
            if type(bound) is not int or bound<0 or repeat=='CanRepeatXTimes' and bound<1:
                branch_gaps.append('unknown_repeat_limit');bound=1
            branches.append(dict(target=b['target'],condition=condition,weight=expr(b.get('weight_expression','1')),
                repeat_type=repeat,cooldown=cooldown,max_consecutive=bound,gaps=branch_gaps))
        nodes.append(dict(variable=node['variable'],state_id=node.get('state_id',node['variable']),kind=kind,branches=branches,gaps=problems))
    known={n['variable'] for n in nodes}
    edges=[]
    for edge in graph.get('edges',[]):
        if edge['source'] in known and edge['target'] in known:edges.append(dict(source=edge['source'],target=edge['target']))
        else:gaps.append('missing_edge_target')
    for move in entry.get('moves',[]):
        if move['variable'] not in known:continue
        source=move.get('execution_evidence','');problems=[];commands=[]
        if not source.strip():problems.append('handler_source_missing')
        # Remove visual guards only if they contain no resource commands.
        visual=re.search(r'if \(TestMode\.IsOff(?: && targets\.Any\(\))?\)\s*\{',source)
        if visual:
            start=visual.end()-1;depth=1;end=start+1
            while end<len(source) and depth:
                depth+=(source[end]=='{')-(source[end]=='}');end+=1
            calls=set(re.findall(r'\b(\w*Cmd\.\w+)(?:<[^>]+>)?\(',source[start:end]))
            if depth or calls-{'CreatureCmd.TriggerAnim','SfxCmd.Play','VfxCmd.PlayOnCreatureCenters','Cmd.Wait'}:
                problems.append('mechanics_inside_visual_guard')
            else:source=source[:visual.start()]+source[end:]
        calls=set(re.findall(r'\b(\w*Cmd\.\w+)(?:<[^>]+>)?\(',source))
        unknown=calls-{'DamageCmd.Attack','PowerCmd.Apply','CreatureCmd.GainBlock','CardPileCmd.AddToCombatAndPreview',
            'CreatureCmd.TriggerAnim','SfxCmd.Play','VfxCmd.PlayOnCreatureCenters','Cmd.Wait'}
        if unknown:problems.append('unmodeled_move_call:'+','.join(sorted(unknown)))
        permitted={'DamageCmd.Attack','PowerCmd.Apply','CreatureCmd.GainBlock','CardPileCmd.AddToCombatAndPreview',
            'CreatureCmd.TriggerAnim','SfxCmd.Play','VfxCmd.PlayOnCreatureCenters','Cmd.Wait',
            'FromMonster','WithHitCount','WithAttackerAnim','WithAttackerFx','WithHitFx','Execute',
            'ThrowingPlayerChoiceContext','BlockingPlayerChoiceContext'}
        invoked=set(re.findall(r'\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*(?:<[^<>]+>)?\(',source))
        other=invoked-permitted-{'if','for','while'}
        if other:problems.append('handler_calls_require_review:'+','.join(sorted(other)))
        if re.search(r'\bif\s*\(',source):problems.append('conditional_move_handler')
        if re.search(r'\b(?:for|while)\s*\(',source):problems.append('looped_move_handler')
        # Assignment positions join commands in actual source order.
        pattern=r'(DamageCmd\.Attack|PowerCmd\.Apply<([^>]+)>|CreatureCmd\.GainBlock|CardPileCmd\.AddToCombatAndPreview<([^>]+)>)\('
        ordered=[]
        for match in re.finditer(pattern,source):
            try:
                end=call_end(source,match.end()-1);args=arguments(source[match.end():end])
                if match[1]=='DamageCmd.Attack':
                    tail=source[end:source.find(';',end) if ';' in source[end:] else len(source)]
                    repeat=re.search(r'\.WithHitCount\(([^()]+)\)',tail)
                    command=dict(operation='attack',amount=expr(args[0]),repeats=expr(repeat[1] if repeat else '1'))
                elif match[2]:
                    power=match[2];target=args[1]
                    if power=='StrengthPower' and target=='base.Creature':op='strength'
                    elif power in {'WeakPower','FrailPower'} and target=='targets':op=power.removesuffix('Power').lower()
                    else:raise ValueError('unmodeled_move_power:'+power+':'+target)
                    command=dict(operation=op,amount=expr(args[2]))
                elif match[3]:
                    identity=card_ids.get(match[3]);pile=args[1]
                    if len(args)!=4 or args[3]!='null' or args[0]!='targets' or not identity or pile not in {'PileType.Discard','PileType.Draw'}:
                        raise ValueError('unmodeled_generated_card')
                    command=dict(operation='discard' if pile.endswith('Discard') else 'draw',amount=expr(args[2]),card_id=identity)
                else:
                    if args[0]!='base.Creature':raise ValueError('unmodeled_block_target')
                    command=dict(operation='block',amount=expr(args[1]))
                ordered.append((match.start(),command))
            except (ValueError,IndexError) as exc:problems.append(str(exc))
        for update in move.get('state_updates',[]):
            variable=update['variable'];field=definitions.get(variable,variable)
            assignment=re.search(r'\b'+re.escape(variable)+r'\s*=(?!=)',source)
            if assignment is None:continue  # e.g. an assignment inside a removed visual block.
            if not re.fullmatch(r'[A-Za-z_]\w*',field) or field not in {v['name'] for v in graph.get('state_fields',[])}:
                problems.append('unmodeled_state_update:'+variable);continue
            ordered.append((assignment.start(),dict(operation='set_field',field=field,amount=expr(update['value_expression']))))
        commands=[c for _,c in sorted(ordered,key=lambda item:item[0])]
        announced=[];intents=[]
        for match in re.finditer(r'new (\w+)Intent\(',move.get('intent_expression','')):
            kind=match[1]
            if kind in {'SingleAttack','MultiAttack'}:
                try:
                    end=call_end(move['intent_expression'],match.end()-1);args=arguments(move['intent_expression'][match.end():end])
                    announced.append(dict(amount=expr(args[0]),repeats=expr(args[1] if kind=='MultiAttack' else '1')))
                except (ValueError,IndexError):problems.append('attack_intent_uncompiled')
            else:intents.append({'Status':'StatusCard'}.get(kind,kind))
        moves.append(dict(variable=move['variable'],state_id=move['state_id'],commands=commands,
            announced_attacks=announced,intent_types=intents,gaps=sorted(set(problems))))
    # Display-only hooks need a reviewed declaration; no entity whitelist here.
    hooks=sorted(set(entry.get('mechanics',{}))-{'AfterAddedToRoom'})
    p=entry['provenance']
    return dict(entity_id=entry['entity_id'],category='monster',
        provenance=dict(source_path=p.get('source_file','offline-test.cs'),source_sha256=p.get('source_sha256','0'*64),
            model_type='MegaCrit.Sts2.Core.Models.Monsters.'+p.get('source_file','Test.cs').split('/')[-1].removesuffix('.cs')),
        verification='migrated_requires_semantic_review',gaps=sorted(set(gaps)),unmodeled_hooks=hooks,presentation_hooks=[],moves=moves,
        state_graph=dict(nodes=nodes,edges=edges))
