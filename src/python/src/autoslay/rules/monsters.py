"""Declared monster commands and state machines. Source evidence is audit-only."""
from decimal import Decimal
from dataclasses import replace
from autoslay.decision.contracts import UnsupportedEffect
from autoslay.rules.monster_models import expression
from autoslay.rules.statuses import ENEMY_POWER_IDS

VERSION = '2.0.0'
KNOWN_POWERS = ENEMY_POWER_IDS


def load():
    from autoslay.rules.model_store import load_models
    from autoslay.rules.registry import IMPLEMENTATION_SHA256
    models=load_models()
    return dict(game_version=models.game_version,game_assembly_sha256=IMPLEMENTATION_SHA256,
        entries=list(models.monsters.values()))


def move_state_fields(entry,move):
    return dict(ignored_state_updates=[],initialized_fields=[c['field'] for c in move.get('commands',[]) if c['operation']=='set_field'])


def unmodeled_special_hooks(entry):
    return set(entry.get('unmodeled_hooks',[]))


class MonsterRules:
    def __init__(self,snapshot,entries,behavior=None):
        self.data=load() if behavior is None else behavior
        self.entries={e['entity_id']:e for e in self.data.get('entries',[]) if e['category']=='monster'}
        self.ascension=(snapshot.get('state') or {}).get('ascension',0)
        self.compatible=bool(snapshot.get('game_assembly_sha256')) and self.data.get('game_assembly_sha256')==snapshot['game_assembly_sha256'] and self.data.get('game_version')==snapshot.get('game_version')
        self.card_names={i:e.get('name',i) for i,e in entries.items() if e.get('category')=='card'}
        self.notes=set();self.trace=[];self.generated_ids=set()

    def entry(self,enemy):
        entry=self.entries.get(enemy.entity_id)
        if not self.compatible or not entry:raise UnsupportedEffect('monster_rule_version_or_reference_missing:'+enemy.entity_id)
        if entry['gaps']:raise UnsupportedEffect(';'.join(entry['gaps']))
        if any(n['gaps'] for n in entry['state_graph']['nodes']):raise UnsupportedEffect('monster_transition_settings_unmodeled:'+enemy.entity_id)
        return entry

    def values(self,enemy,entry):
        values=dict(enemy.runtime_fields)|{'hp':enemy.hp}
        if enemy.max_hp>0:values['max_hp']=enemy.max_hp
        return values

    def evaluate(self,node,enemy,entry):return expression(node,self.values(enemy,entry),self.ascension)

    def number(self,node,enemy,entry):
        value=self.evaluate(node,enemy,entry)
        if type(value) not in {int,Decimal} or value!=int(value) or not 0<=value<=10000:
            raise UnsupportedEffect('unknown_move_amount:'+str(node))
        return int(value)

    def moves(self,entry):return {m['variable']:m for m in entry.get('moves',[])}

    def commands(self,move):
        if move['gaps']:raise UnsupportedEffect(';'.join(move['gaps']))
        if any(c['operation'] not in {'attack','strength','block','weak','frail','draw','discard','set_field'} for c in move['commands']):
            raise UnsupportedEffect('unimplemented_monster_command')
        return move['commands']

    def attacks(self,enemy,entry,move):
        attacks=[]
        declared=move['announced_attacks']
        actual=[c for c in self.commands(move) if c['operation']=='attack']
        if len(actual)!=len(declared):raise UnsupportedEffect('attack_intent_execution_mismatch:'+move['state_id'])
        for announced,command in zip(declared,actual):
            base=self.number(announced['amount'],enemy,entry);repeats=self.number(announced['repeats'],enemy,entry)
            if (self.number(command['amount'],enemy,entry),self.number(command['repeats'],enemy,entry))!=(base,repeats):
                raise UnsupportedEffect('attack_intent_execution_mismatch:'+move['state_id'])
            if repeats>100:raise UnsupportedEffect('attack_repeat_limit')
            value=max(0,base+enemy.strength)
            if enemy.weak:value=value*3//4
            attacks.extend([value]*repeats)
        return tuple(attacks)

    def identify(self,enemy):
        entry=self.entry(enemy)
        declared=next((m for m in entry.get('moves',[]) if m['state_id']==enemy.move_state),None)
        if declared:
            if self.attacks(enemy,entry,declared)!=enemy.attacks:raise UnsupportedEffect('announced_move_numeric_mismatch:'+enemy.entity_id)
            return replace(enemy,move_state=declared['variable'],move_history=enemy.move_history or (declared['variable'],))
        if enemy.move_state in self.moves(entry):return enemy
        possible=[]
        for variable,move in self.moves(entry).items():
            try:
                if self.attacks(enemy,entry,move)==enemy.attacks and set(move['intent_types'])==set(enemy.uncertain_intents):possible.append(variable)
            except UnsupportedEffect:continue
        if len(possible)!=1:raise UnsupportedEffect('current_move_ambiguous:'+enemy.entity_id)
        return replace(enemy,move_state=possible[0],move_history=enemy.move_history or (possible[0],))

    def prepare(self,state):
        enemies=[]
        for enemy in state.enemies:
            if enemy.hp<=0:enemies.append(enemy);continue
            try:
                enemy=self.identify(enemy);entry=self.entry(enemy)
                for move in entry.get('moves',[]):
                    self.generated_ids.update(c['card_id'] for c in move['commands'] if c['operation'] in {'draw','discard'})
                if state.turn==1:enemy=replace(enemy,history_complete=True)
            except UnsupportedEffect as exc:self.notes.add(str(exc))
            enemies.append(enemy)
        return replace(state,enemies=tuple(enemies))

    def destinations(self,enemy,entry,target,visited=()):
        if target in visited or len(visited)>16:raise UnsupportedEffect('cyclic_branch:'+enemy.entity_id)
        nodes={n['variable']:n for n in entry['state_graph']['nodes']};node=nodes.get(target)
        if node is None:raise UnsupportedEffect('unknown_branch_target:'+str(target))
        if node['gaps']:raise UnsupportedEffect(';'.join(node['gaps']))
        if node['kind']=='MoveState':return [(1.0,target)]
        branches=node['branches'];weights=[]
        for branch in branches:
            if branch['gaps']:raise UnsupportedEffect(';'.join(branch['gaps']))
            if not self.evaluate(branch['condition'],enemy,entry):continue
            dest=branch['target']
            if node['kind']=='ConditionalBranchState':return self.destinations(enemy,entry,dest,visited+(target,))
            if node['kind']!='RandomBranchState':raise UnsupportedEffect('unknown_branch_kind:'+node['kind'])
            repeat=branch['repeat_type'];history=enemy.move_history;cooldown=branch['cooldown']
            if cooldown>len(history) and not enemy.history_complete:raise UnsupportedEffect('missing_cooldown_history')
            if cooldown and dest in history[-cooldown:]:continue
            if repeat=='CannotRepeat' and history and dest==history[-1]:continue
            if repeat=='UseOnlyOnce':
                if dest in history:continue
                if not enemy.history_complete:raise UnsupportedEffect('missing_once_only_history')
            elif repeat=='CanRepeatXTimes':
                count=branch['max_consecutive']
                if len(history)<count and not enemy.history_complete:raise UnsupportedEffect('missing_repeat_history')
                if len(history)>=count and all(v==dest for v in history[-count:]):continue
            weight=self.evaluate(branch['weight'],enemy,entry)
            if type(weight) not in {int,Decimal} or weight<0:raise UnsupportedEffect('unknown_branch_weight')
            if weight:weights.append((weight,dest))
        total=sum(w for w,_ in weights)
        if not total:raise UnsupportedEffect('no_eligible_random_move')
        return [(float(w/total)*p,d) for w,t in weights for p,d in self.destinations(enemy,entry,t,visited+(target,))]

    def ordered_effects(self,enemy,entry,move):
        self.attacks(enemy,entry,move)
        result=[]
        for command in self.commands(move):
            op=command['operation']
            if op=='set_field':
                value=self.evaluate(command['amount'],enemy,entry)
                enemy=replace(enemy,runtime_fields=tuple((dict(enemy.runtime_fields)|{command['field']:value}).items()))
                continue
            result.append((op,self.number(command['amount'],enemy,entry),
                self.number(command['repeats'],enemy,entry) if op=='attack' else command.get('card_id')))
        return result

    def effects(self,enemy,entry,move):return [e for e in self.ordered_effects(enemy,entry,move) if e[0]!='attack']

    def step(self,state,limit=32,select_next=True):
        """Apply the visible enemy turn, then branch to real next intents."""
        if state.warnings:raise UnsupportedEffect('combat_interactions_unmodeled')
        from autoslay.rules.scheduled_relics import apply_phase
        state=apply_phase(state,'side_turn_end')
        if state.combat_won or state.hp<=0:return [(1.0,state,[])]
        if state.turn_hp_loss_cap is not None:raise UnsupportedEffect('remaining_turn_hp_cap_unknown')
        from autoslay.rules.statuses import enemy_poison_start,apply_player_phase,stack,set_stack,prevent_debuff,after_enemy_side
        from autoslay.simulation.atomic_effects import enemy_attack_hit
        state=apply_player_phase(state,'side_turn_end_after')
        state=enemy_poison_start(state)
        if state.combat_won:return [(1.0,state,[])]
        current=replace(state,weak_turns=max(0,state.weak_turns-(not state.weak_skip_next_tick)),frail_turns=max(0,state.frail_turns-(not state.frail_skip_next_tick)),
            weak_skip_next_tick=False,frail_skip_next_tick=False,
            dexterity_delta=state.dexterity_delta-state.temporary_dexterity,temporary_dexterity=0,
            effect_draw_blocked=state.permanent_draw_blocked if state.temporary_draw_blocked else state.effect_draw_blocked,
            temporary_draw_blocked=False)
        results=[(1.0,current,[])]
        for index,initial_enemy in enumerate(state.enemies):
            if initial_enemy.hp<=0 or state.combat_won:continue
            next_results=[]
            for probability,current,trace in results:
                if current.hp<=0 or current.combat_won:next_results.append((probability,current,trace));continue
                enemy=current.enemies[index]
                if enemy.hp<=0:next_results.append((probability,current,trace));continue
                entry=self.entry(enemy)
                hooks=unmodeled_special_hooks(entry)
                if hooks:raise UnsupportedEffect('monster_special_hooks_unmodeled:'+enemy.entity_id+':'+','.join(sorted(hooks)))
                if not enemy.move_state:raise UnsupportedEffect('current_move_unknown:'+enemy.entity_id)
                if set(enemy.powers)-KNOWN_POWERS:raise UnsupportedEffect('enemy_power_hooks_unmodeled:'+enemy.entity_id)
                moves=self.moves(entry);move=moves[enemy.move_state]
                self.attacks(enemy,entry,move)
                effects=[]
                enemy=replace(enemy,block=enemy.block if stack(enemy,'barricade') or stack(enemy,'blur') else 0)
                enemy=set_stack(enemy,'blur',max(0,stack(enemy,'blur')-1))
                from autoslay.simulation.card_flow import add_to_composition
                for command in self.commands(move):
                    kind=command['operation']
                    if kind=='set_field':
                        value=self.evaluate(command['amount'],enemy,entry)
                        enemy=replace(enemy,runtime_fields=tuple(sorted((dict(enemy.runtime_fields)|{command['field']:value}).items())))
                        continue
                    amount=self.number(command['amount'],enemy,entry)
                    identity=self.number(command['repeats'],enemy,entry) if kind=='attack' else command.get('card_id')
                    if kind!='attack':effects.append((kind,amount,identity))
                    if kind=='attack':
                        for _ in range(identity):
                            if enemy.hp<=0 or current.hp<=0:break
                            hit=max(0,amount+enemy.strength)
                            if enemy.weak:hit=hit*3//4
                            current=replace(current,enemies=tuple(enemy if e.instance_id==enemy.instance_id else e for e in current.enemies))
                            current=enemy_attack_hit(current,enemy.instance_id,hit)
                            enemy=next(e for e in current.enemies if e.instance_id==enemy.instance_id)
                    elif kind=='strength':enemy=replace(enemy,strength=enemy.strength+amount)
                    elif kind=='block':enemy=replace(enemy,block=enemy.block+amount)
                    elif kind in {'weak','frail'}:
                        current,blocked=prevent_debuff(current)
                        if not blocked:
                            field='weak_turns' if kind=='weak' else 'frail_turns'
                            current=replace(current,**{field:getattr(current,field)+amount})
                    elif kind=='discard':current=replace(current,discard_composition=add_to_composition(current.discard_composition,[identity]*amount))
                    elif kind=='draw':current=replace(current,draw_composition=add_to_composition(current.draw_composition,[identity]*amount))
                    if current.hp<=0 or current.combat_won or enemy.hp<=0:break
                if current.hp<=0:
                    next_results.append((probability,current,trace));continue
                enemy=replace(enemy,weak_turns=max(0,enemy.weak_turns-1),weak=enemy.weak_turns>1,
                              strength=enemy.strength+enemy.temporary_strength_loss,temporary_strength_loss=0)
                from autoslay.rules.registry import TEMPORARY_ENEMY_STRENGTH
                for operation in TEMPORARY_ENEMY_STRENGTH:enemy=set_stack(enemy,operation,0)
                if select_next and enemy.hp>0 and not current.combat_won:
                    edges=[e['target'] for e in entry['state_graph']['edges'] if e['source']==enemy.move_state]
                    if len(edges)!=1:raise UnsupportedEffect('followup_state_unknown:'+enemy.entity_id)
                    destinations=self.destinations(enemy,entry,edges[0])
                else:destinations=[(1.0,None)]
                for branch,next_move in destinations:
                    next_enemy=replace(enemy,move_state=next_move,move_history=enemy.move_history+(next_move,),
                                       attacks=self.attacks(enemy,entry,moves[next_move]),uncertain_intents=tuple(moves[next_move]['intent_types'])) if next_move is not None else enemy
                    enemies=list(current.enemies);enemies[index]=next_enemy
                    row=dict(instance_id=enemy.instance_id,entity_id=enemy.entity_id,move=move['state_id'],
                             next_move=moves[next_move]['state_id'] if next_move is not None else None,next_attacks=list(next_enemy.attacks) if next_move is not None else None,effects=effects,probability=branch,
                             turn=state.turn,enemy_hp=enemy.hp,name=entry.get('name'),move_title=move.get('title'),next_move_title=moves[next_move].get('title') if next_move is not None else None,
                             generated_cards=[dict(entity_id=i,name=self.card_names.get(i,i),amount=n,pile=k) for k,n,i in effects if k in {'discard','draw'}])
                    if len(self.trace)<64 and row not in self.trace:self.trace.append(row)
                    next_results.append((probability*branch,replace(current,enemies=tuple(enemies)),trace+[row]))
                    if len(next_results)>limit:raise UnsupportedEffect('enemy_branch_budget_exceeded')
            results=next_results
        return [(p,after_enemy_side(s),trace) for p,s,trace in results]

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
