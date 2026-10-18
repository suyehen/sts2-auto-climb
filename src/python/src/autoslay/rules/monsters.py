"""Restricted adapters for local behavior graphs. Unknown mechanics stop coverage.

No game RNG, hidden next move, source execution, or unrestricted eval is used.
"""
import ast
import operator
import re
from math import isfinite
from dataclasses import replace
from autoslay.decision.contracts import UnsupportedEffect
from autoslay.knowledge_base.behavior import load
from autoslay.knowledge_base.monster_values import arguments, call_end, resolve

VERSION = '1.3.0'
KNOWN_POWERS = {'POWER.STRENGTH_POWER','POWER.WEAK_POWER','POWER.VULNERABLE_POWER',
                'POWER.HARD_TO_KILL_POWER','POWER.MINION_POWER'}
ALLOWED_CALLS = {'DamageCmd.Attack','PowerCmd.Apply','CreatureCmd.GainBlock',
                 'CardPileCmd.AddToCombatAndPreview','CreatureCmd.TriggerAnim',
                 'SfxCmd.Play','VfxCmd.PlayOnCreatureCenters','Cmd.Wait'}


def unmodeled_special_hooks(entry):
    from autoslay.rules.mechanism_scope import reviewed_display_hooks
    hooks=set(entry.get('mechanics',{}))-{'AfterAddedToRoom'}
    if not hooks:
        return set()
    return hooks-reviewed_display_hooks(entry)


def expression(text, values, seen=()):
    """Scalar condition subset with C# integer division and short circuit logic."""
    text=str(text).removeprefix('() => ').replace('base.Creature.CurrentHp','hp').replace('base.Creature.MaxHp','max_hp')
    text=text.replace('&&',' and ').replace('||',' or ')
    text=re.sub(r'!(?!=)', ' not ', text).strip()
    text=re.sub(r'\btrue\b','True',text);text=re.sub(r'\bfalse\b','False',text)
    text=re.sub(r'(?<=\d)[mf]\b','',text)
    try:tree=ast.parse(text,mode='eval')
    except SyntaxError as exc:raise UnsupportedEffect('unknown_condition:'+text) from exc
    def walk(n):
        if isinstance(n,ast.Constant) and type(n.value) in {bool,int,float}:return n.value
        if isinstance(n,ast.Name) and n.id in values and n.id not in seen:
            value=values[n.id]
            return value if type(value) in {bool,int,float} else expression(value,values,seen+(n.id,))
        if isinstance(n,ast.UnaryOp) and isinstance(n.op,(ast.Not,ast.USub)):
            value=walk(n.operand);return not value if isinstance(n.op,ast.Not) else -value
        if isinstance(n,ast.BoolOp):
            for child in n.values:
                value=walk(child)
                if isinstance(n.op,ast.And) and not value:return False
                if isinstance(n.op,ast.Or) and value:return True
            return isinstance(n.op,ast.And)
        if isinstance(n,ast.BinOp) and type(n.op) in {ast.Add,ast.Sub,ast.Mult,ast.Div}:
            a,b=walk(n.left),walk(n.right)
            if isinstance(n.op,ast.Div):return int(a/b) if type(a) is int and type(b) is int else a/b
            return {ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul}[type(n.op)](a,b)
        if isinstance(n,ast.Compare) and len(n.ops)==1 and type(n.ops[0]) in {ast.Eq,ast.NotEq,ast.Lt,ast.LtE,ast.Gt,ast.GtE}:
            return {ast.Eq:operator.eq,ast.NotEq:operator.ne,ast.Lt:operator.lt,ast.LtE:operator.le,ast.Gt:operator.gt,ast.GtE:operator.ge}[type(n.ops[0])](walk(n.left),walk(n.comparators[0]))
        raise UnsupportedEffect('unknown_condition:'+text)
    try:return walk(tree.body)
    except (SyntaxError,ZeroDivisionError,RecursionError,TypeError) as exc:raise UnsupportedEffect('unknown_condition:'+text) from exc


class MonsterRules:
    def __init__(self,snapshot,entries,behavior=None):
        self.data=load() if behavior is None else behavior
        self.entries={e['entity_id']:e for e in self.data.get('entries',[]) if e['category']=='monster'}
        self.ascension=(snapshot.get('state') or {}).get('ascension',0)
        self.levels=self.data.get('ascension_thresholds',{})
        self.compatible=bool(snapshot.get('game_assembly_sha256')) and self.data.get('game_assembly_sha256')==snapshot['game_assembly_sha256'] and self.data.get('game_version')==snapshot.get('game_version')
        self.card_ids={e.get('model_type','').split('.')[-1]:i for i,e in entries.items() if e.get('category')=='card'}
        self.card_names={i:e.get('name',i) for i,e in entries.items() if e.get('category')=='card'}
        self.notes=set()
        self.trace=[]
        self.generated_ids=set()

    def entry(self,enemy):
        entry=self.entries.get(enemy.entity_id)
        if not self.compatible or not entry or entry['provenance'].get('game_assembly_sha256')!=self.data.get('game_assembly_sha256'):
            raise UnsupportedEffect('monster_rule_version_or_reference_missing:'+enemy.entity_id)
        graph=entry.get('state_graph') or {}
        if graph.get('unresolved_targets') or any(n.get('settings') for n in graph.get('nodes',[])):
            raise UnsupportedEffect('monster_transition_settings_unmodeled:'+enemy.entity_id)
        return entry

    def values(self,enemy,entry):
        values=dict(entry.get('parameter_expressions',{})) | dict(entry['state_graph'].get('definitions',{})) | dict(enemy.runtime_fields) | {'hp':enemy.hp}
        if enemy.max_hp>0:values['max_hp']=enemy.max_hp
        return values

    def number(self,text,enemy,entry):
        values=self.values(enemy,entry)
        value=resolve(str(text),{k:str(v) for k,v in values.items()},self.ascension,self.levels)
        if value is None:
            value=expression(text,values)
        if type(value) not in {int,float} or value!=int(value) or not 0<=value<=10000:
            raise UnsupportedEffect('unknown_move_amount:'+str(text))
        return int(value)

    def moves(self,entry):return {m['variable']:m for m in entry.get('moves',[])}

    def attacks(self,enemy,entry,move):
        bands=move.get('numeric_variants',[])
        band=next((b for b in bands if b['ascension_min']<=self.ascension<=b['ascension_max']),None)
        if band is None:raise UnsupportedEffect('ascension_band_missing:'+enemy.entity_id)
        hits=[]
        source=move.get('execution_evidence','')
        attack_calls=[]
        for match in re.finditer(r'DamageCmd\.Attack\(',source):
            attack_calls.append(source[match.end():call_end(source,match.end()-1)])
        specs=[e for e in band['effects'] if e['kind']=='attack']
        if len(attack_calls)!=len(specs):raise UnsupportedEffect('attack_intent_execution_mismatch:'+move['handler'])
        hit_count=re.search(r'\.WithHitCount\(([^()]+)\)',source)
        for index,effect in enumerate(specs):
            if effect['kind']=='attack':
                base=self.number(effect['damage_expression'],enemy,entry)
                repeats=self.number(effect['repeats_expression'],enemy,entry)
                if self.number(attack_calls[index],enemy,entry)!=base or (self.number(hit_count[1],enemy,entry) if hit_count else 1)!=repeats:
                    raise UnsupportedEffect('attack_intent_execution_mismatch:'+move['handler'])
                if repeats>100:raise UnsupportedEffect('attack_repeat_limit')
                value=max(0,base+enemy.strength)
                if enemy.weak:value=value*3//4
                hits.extend([value]*repeats)
        return tuple(hits)

    def identify(self,enemy):
        entry=self.entry(enemy)
        declared=next((m for m in entry.get('moves',[]) if m['state_id']==enemy.move_state),None)
        if declared:
            if self.attacks(enemy,entry,declared)!=enemy.attacks:
                raise UnsupportedEffect('announced_move_numeric_mismatch:'+enemy.entity_id)
            return replace(enemy,move_state=declared['variable'],move_history=enemy.move_history or (declared['variable'],))
        if enemy.move_state in self.moves(entry):return enemy
        possible=[]
        for variable,move in self.moves(entry).items():
            if self.attacks(enemy,entry,move)!=enemy.attacks:continue
            types=set(re.findall(r'new (\w+)Intent\(',move.get('intent_expression','')))-{'SingleAttack','MultiAttack'}
            expected={dict(Status='StatusCard',Buff='Buff',Debuff='Debuff',Defend='Defend',Sleep='Sleep',Stun='Stun').get(t,t) for t in types}
            if expected==set(enemy.uncertain_intents):possible.append(variable)
        if len(possible)!=1:raise UnsupportedEffect('current_move_ambiguous:'+enemy.entity_id)
        return replace(enemy,move_state=possible[0],move_history=enemy.move_history or (possible[0],))

    def prepare(self,state):
        enemies=[]
        for enemy in state.enemies:
            if enemy.hp<=0:enemies.append(enemy);continue
            try:
                enemy=self.identify(enemy)
                entry=self.entry(enemy)
                for move in entry.get('moves',[]):
                    for card_class in re.findall(r'CardPileCmd\.AddToCombatAndPreview<([^>]+)>',move.get('execution_evidence','')):
                        if card_class in self.card_ids:self.generated_ids.add(self.card_ids[card_class])
                if state.turn==1:
                    # Initialization hooks may change field defaults before turn 1.
                    # Missing runtime fields remain unknown, even on the first turn.
                    enemy=replace(enemy,history_complete=True)
            except UnsupportedEffect as exc:self.notes.add(str(exc))
            enemies.append(enemy)
        return replace(state,enemies=tuple(enemies))

    def destinations(self,enemy,entry,target,visited=()):
        if target in visited or len(visited)>16:raise UnsupportedEffect('cyclic_branch:'+enemy.entity_id)
        nodes={n['variable']:n for n in entry['state_graph']['nodes']}
        node=nodes.get(target)
        if node is None:raise UnsupportedEffect('unknown_branch_target:'+str(target))
        if node['kind']=='MoveState':return [(1.0,target)]
        branches=node.get('branches',[])
        if node['kind']=='ConditionalBranchState':
            for branch in branches:
                if branch.get('registration_conditions'):raise UnsupportedEffect('branch_registration_condition')
                if expression(branch['condition_expression'],self.values(enemy,entry)):
                    return self.destinations(enemy,entry,branch['target'],visited+(target,))
            raise UnsupportedEffect('no_matching_condition:'+enemy.entity_id)
        if node['kind']!='RandomBranchState':raise UnsupportedEffect('unknown_branch_kind:'+node['kind'])
        weights=[]
        for branch in branches:
            if branch.get('registration_conditions'):raise UnsupportedEffect('branch_registration_condition')
            repeat=branch.get('repeat_type');history=enemy.move_history;dest=branch['target']
            cooldown=branch.get('cooldown',0)
            if type(cooldown) is not int or cooldown<0:raise UnsupportedEffect('unknown_move_cooldown')
            if cooldown>len(history) and not enemy.history_complete:raise UnsupportedEffect('missing_cooldown_history')
            if cooldown and dest in history[-cooldown:]:continue
            if repeat=='CannotRepeat' and history and dest==history[-1]:continue
            if repeat=='UseOnlyOnce':
                if dest in history:continue
                if not enemy.history_complete:raise UnsupportedEffect('missing_once_only_history')
            elif repeat=='CanRepeatXTimes':
                count=branch.get('max_consecutive')
                if type(count) is not int or count<1:raise UnsupportedEffect('unknown_repeat_limit')
                if len(history)<count and not enemy.history_complete:raise UnsupportedEffect('missing_repeat_history')
                if len(history)>=count and all(v==dest for v in history[-count:]):continue
            elif repeat not in {'CannotRepeat','CanRepeatForever','UseOnlyOnce'}:raise UnsupportedEffect('unknown_repeat_type')
            weight=expression(branch.get('weight_expression','1'),self.values(enemy,entry))
            if type(weight) not in {int,float} or not isfinite(weight) or weight<0:raise UnsupportedEffect('unknown_branch_weight')
            if weight:weights.append((weight,branch['target']))
        total=sum(w for w,_ in weights)
        if not total:raise UnsupportedEffect('no_eligible_random_move')
        return [(w/total*p,d) for w,t in weights for p,d in self.destinations(enemy,entry,t,visited+(target,))]

    def effects(self,enemy,entry,move,allow_ordered=False):
        source=move.get('execution_evidence','')
        from autoslay.rules.mechanism_scope import reviewed_move_scope
        scope=reviewed_move_scope(entry,move)
        visual=re.search(r'if \(TestMode\.IsOff(?: && targets\.Any\(\))?\)\s*\{',source)
        if visual:
            start=visual.end()-1;depth=1;end=start+1
            while end<len(source) and depth:
                depth+=(source[end]=='{')-(source[end]=='}');end+=1
            block=source[start:end]
            if '&& targets.Any()' in source[visual.start():start] and not scope:
                raise UnsupportedEffect('unreviewed_visual_guard:'+move['handler'])
            if depth or re.search(r'(?:DamageCmd|PowerCmd|CardPileCmd|CreatureCmd\.GainBlock)',block):
                raise UnsupportedEffect('mechanics_inside_visual_guard')
            source=source[:visual.start()]+source[end:]
        calls=set(re.findall(r'\b(\w*Cmd\.\w+)(?:<[^>]+>)?\(',source))
        unknown=calls-ALLOWED_CALLS
        # Visual-only TestMode guards do not affect mechanics. Other handler
        # branches and loops require dedicated adapters, not flattened effects.
        if unknown:raise UnsupportedEffect('unmodeled_move_call:'+','.join(sorted(unknown)))
        if re.search(r'\bif\s*\((?!TestMode\.IsOff)',source):raise UnsupportedEffect('conditional_move_handler:'+move['handler'])
        if re.search(r'\b(?:for|while)\s*\(',source):raise UnsupportedEffect('looped_move_handler:'+move['handler'])
        attack=source.find('DamageCmd.Attack(')
        if not allow_ordered and attack>=0 and re.search(r'(?:PowerCmd\.Apply|CreatureCmd\.GainBlock|CardPileCmd\.)',source[:attack]):
            raise UnsupportedEffect('pre_attack_effect_order_requires_adapter')
        effects=[]
        for match in re.finditer(r'(PowerCmd\.Apply<([^>]+)>|CreatureCmd\.GainBlock|CardPileCmd\.AddToCombatAndPreview<([^>]+)>)\(',source):
            args=arguments(source[match.end():call_end(source,match.end()-1)])
            if match[2]:
                power=match[2];target=args[1];amount=self.number(args[2],enemy,entry)
                if power=='StrengthPower' and target=='base.Creature':effects.append(('strength',amount,None))
                elif power in {'WeakPower','FrailPower'} and target=='targets':effects.append((power.removesuffix('Power').lower(),amount,None))
                else:raise UnsupportedEffect('unmodeled_move_power:'+power+':'+target)
            elif match[3]:
                identity=self.card_ids.get(match[3]);pile=args[1];amount=self.number(args[2],enemy,entry)
                if len(args)!=4 or args[3]!='null' or args[0]!='targets' or not identity or pile not in {'PileType.Discard','PileType.Draw'}:raise UnsupportedEffect('unmodeled_generated_card')
                effects.append(('discard' if pile.endswith('Discard') else 'draw',amount,identity))
            else:
                if args[0]!='base.Creature':raise UnsupportedEffect('unmodeled_block_target')
                effects.append(('block',self.number(args[1],enemy,entry),None))
        return effects

    def ordered_effects(self,enemy,entry,move):
        """Validate all calls, then retain the source order including attacks."""
        effects=iter(self.effects(enemy,entry,move,allow_ordered=True))
        self.attacks(enemy,entry,move)  # Cross-check numeric intent and execution evidence.
        source=move.get('execution_evidence','');result=[]
        pattern=r'(DamageCmd\.Attack|PowerCmd\.Apply<[^>]+>|CreatureCmd\.GainBlock|CardPileCmd\.AddToCombatAndPreview<[^>]+>)\('
        for match in re.finditer(pattern,source):
            if match[1]=='DamageCmd.Attack':
                end=call_end(source,match.end()-1)
                amount=self.number(source[match.end():end],enemy,entry)
                tail=source[end:source.find(';',end) if ';' in source[end:] else len(source)]
                repeats=re.search(r'\.WithHitCount\(([^()]+)\)',tail)
                count=self.number(repeats[1],enemy,entry) if repeats else 1
                if count>100:raise UnsupportedEffect('attack_repeat_limit')
                result.append(('attack',amount,count))
            else:result.append(next(effects))
        return result

    def step(self,state,limit=32,select_next=True):
        """Apply the visible enemy turn, then branch to real next intents."""
        if state.warnings:raise UnsupportedEffect('combat_interactions_unmodeled')
        from autoslay.rules.scheduled_relics import apply_phase
        state=apply_phase(state,'side_turn_end')
        if state.combat_won or state.hp<=0:return [(1.0,state,[])]
        if state.turn_hp_loss_cap is not None:raise UnsupportedEffect('remaining_turn_hp_cap_unknown')
        current=replace(state,weak_turns=max(0,state.weak_turns-1),frail_turns=max(0,state.frail_turns-1),
            dexterity_delta=state.dexterity_delta-state.temporary_dexterity,temporary_dexterity=0,
            effect_draw_blocked=state.permanent_draw_blocked if state.temporary_draw_blocked else state.effect_draw_blocked,
            temporary_draw_blocked=False)
        results=[(1.0,current,[])]
        for index,initial_enemy in enumerate(state.enemies):
            if initial_enemy.hp<=0 or state.combat_won:continue
            next_results=[]
            for probability,current,trace in results:
                if current.hp<=0:next_results.append((probability,current,trace));continue
                enemy=current.enemies[index];entry=self.entry(enemy)
                hooks=unmodeled_special_hooks(entry)
                if hooks:raise UnsupportedEffect('monster_special_hooks_unmodeled:'+enemy.entity_id+':'+','.join(sorted(hooks)))
                if not enemy.move_state:raise UnsupportedEffect('current_move_unknown:'+enemy.entity_id)
                if set(enemy.powers)-KNOWN_POWERS:raise UnsupportedEffect('enemy_power_hooks_unmodeled:'+enemy.entity_id)
                moves=self.moves(entry);move=moves[enemy.move_state]
                ordered=self.ordered_effects(enemy,entry,move)
                effects=[e for e in ordered if e[0]!='attack']
                enemy=replace(enemy,block=0)
                hp,block=current.hp,current.block
                from autoslay.simulation.card_flow import add_to_composition
                for kind,amount,identity in ordered:
                    if kind=='attack':
                        for _ in range(identity):
                            hit=max(0,amount+enemy.strength)
                            if enemy.weak:hit=hit*3//4
                            hit=hit//2 if current.colossus and enemy.vulnerable else hit
                            absorbed=min(block,hit);block-=absorbed;hp=max(0,hp-max(0,hit-absorbed-current.hp_loss_reduction))
                    elif kind=='strength':enemy=replace(enemy,strength=enemy.strength+amount)
                    elif kind=='block':enemy=replace(enemy,block=enemy.block+amount)
                    elif kind=='weak':current=replace(current,weak_turns=current.weak_turns+amount)
                    elif kind=='frail':current=replace(current,frail_turns=current.frail_turns+amount)
                    elif kind=='discard':current=replace(current,discard_composition=add_to_composition(current.discard_composition,[identity]*amount))
                    elif kind=='draw':current=replace(current,draw_composition=add_to_composition(current.draw_composition,[identity]*amount))
                    if hp<=0:break
                current=replace(current,hp=hp,block=block,enemy_hp_loss=current.enemy_hp_loss+current.hp-hp)
                if hp<=0:
                    next_results.append((probability,current,trace));continue
                values=self.values(enemy,entry);fields=dict(enemy.runtime_fields)
                from autoslay.rules.mechanism_scope import reviewed_move_scope
                scope=reviewed_move_scope(entry,move) or {}
                for update in move.get('state_updates',[]):
                    variable=update['variable']
                    if variable in scope.get('ignored_state_updates',[]):continue
                    field=entry['state_graph'].get('definitions',{}).get(variable,variable)
                    if field not in fields and variable not in scope.get('initialized_fields',[]):raise UnsupportedEffect('unmodeled_state_update:'+variable)
                    fields[field]=expression(update['value_expression'],values)
                enemy=replace(enemy,runtime_fields=tuple(sorted(fields.items())),weak_turns=max(0,enemy.weak_turns-1),weak=enemy.weak_turns>1)
                if select_next:
                    edges=[e['target'] for e in entry['state_graph']['edges'] if e['source']==enemy.move_state]
                    if len(edges)!=1:raise UnsupportedEffect('followup_state_unknown:'+enemy.entity_id)
                    destinations=self.destinations(enemy,entry,edges[0])
                else:destinations=[(1.0,None)]
                for branch,next_move in destinations:
                    next_enemy=replace(enemy,move_state=next_move,move_history=enemy.move_history+(next_move,),
                                       attacks=self.attacks(enemy,entry,moves[next_move]),uncertain_intents=()) if next_move is not None else enemy
                    enemies=list(current.enemies);enemies[index]=next_enemy
                    row=dict(instance_id=enemy.instance_id,entity_id=enemy.entity_id,move=move['state_id'],
                             next_move=moves[next_move]['state_id'] if next_move is not None else None,next_attacks=list(next_enemy.attacks) if next_move is not None else None,effects=effects,probability=branch,
                             turn=state.turn,enemy_hp=enemy.hp,name=entry.get('name'),move_title=move.get('title'),next_move_title=moves[next_move].get('title') if next_move is not None else None,
                             generated_cards=[dict(entity_id=i,name=self.card_names[i],amount=n,pile=k) for k,n,i in effects if k in {'discard','draw'}])
                    if len(self.trace)<64 and row not in self.trace:self.trace.append(row)
                    next_results.append((probability*branch,replace(current,enemies=tuple(enemies)),trace+[row]))
                    if len(next_results)>limit:raise UnsupportedEffect('enemy_branch_budget_exceeded')
            results=next_results
        return results

    def audit(self,state):
        rows=[]
        for enemy in state.enemies:
            supported=[];unknown={}
            try:
                entry=self.entry(enemy)
                for variable,move in self.moves(entry).items():
                    try:self.effects(enemy,entry,move);self.attacks(enemy,entry,move);supported.append(move['state_id'])
                    except (UnsupportedEffect,KeyError) as exc:unknown[move['state_id']]=str(exc)
                hooks=sorted(unmodeled_special_hooks(entry))
            except UnsupportedEffect as exc:unknown['reference']=str(exc);hooks=[]
            rows.append(dict(instance_id=enemy.instance_id,entity_id=enemy.entity_id,current_move=enemy.move_state,
                             supported_move_effects=supported,unmodeled_moves=unknown,unmodeled_special_hooks=hooks))
        return rows
