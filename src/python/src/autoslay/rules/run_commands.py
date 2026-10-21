"""Run commands and ordered model hooks. No UI or source/text interpretation."""
from dataclasses import dataclass, replace
from decimal import Decimal
from autoslay.decision.contracts import UnsupportedEffect
from autoslay.rules.amounts import validate_expression, evaluate_expression

OPERATIONS=frozenset({'heal','heal_percent','gain_max_hp','gain_gold','pay_gold','lose_hp',
    'damage','lose_max_hp','kill',
    'increase_potion_capacity','obtain_relic','lose_relic','obtain_potion','add_to_deck',
    'remove_card','upgrade_card'})
EVENTS=frozenset({'after_obtained','after_combat_victory','after_item_purchased',
    'after_card_changed_piles','before_card_removed',
    'after_rest_heal','after_rest_smith','modify_rest_heal','modify_merchant_price',
    'modify_hp_loss','after_hp_loss',
    'enter_rest','enter_shop','combat_start','combat_reward'})
NUMERIC=OPERATIONS-{'obtain_relic','lose_relic','obtain_potion','add_to_deck','remove_card','upgrade_card'}


def validate_hooks(hooks,references):
    if not isinstance(hooks,list):raise ValueError('run_hooks: array required')
    for i,hook in enumerate(hooks):
        if not isinstance(hook,dict) or set(hook)!={'event','effects'} or hook.get('event') not in EVENTS:
            raise ValueError(f'run_hooks[{i}]: unsupported hook or fields')
        if not isinstance(hook['effects'],list):raise ValueError(f'run_hooks[{i}].effects: array required')
        for j,effect in enumerate(hook['effects']):
            allowed=({'price_percent'} if hook['event']=='modify_merchant_price' else
                     {'reduce_hp_loss'} if hook['event']=='modify_hp_loss' else NUMERIC)
            if not isinstance(effect,dict) or set(effect)!={'operation','amount'} or effect.get('operation') not in allowed:
                raise ValueError(f'run_hooks[{i}].effects[{j}]: unsupported resource command')
            validate_expression(effect['amount'])
            if hook['event']=='modify_rest_heal' and effect['operation']!='heal':
                raise ValueError('modify_rest_heal: additive heal modifier required')


@dataclass(frozen=True)
class RunTransition:
    state: object
    trace: tuple[dict, ...] = ()
    gaps: tuple[str, ...] = ()


class RunCommands:
    def __init__(self, models=None, references=None):
        from autoslay.rules.model_store import load_models
        from autoslay.knowledge_base.templates import index
        from autoslay.knowledge_base.repository import knowledge_scope
        with knowledge_scope():
            self.models=models or load_models()
            self.references=index()[0] if references is None else references

    def _amount(self,effect,state,owner):
        from autoslay.rules.registry import KnowledgeRules
        variables=owner.get('dynamic_variables')
        if variables is None:variables=self.references.get(owner['entity_id'],{}).get('dynamic_variables',[])
        return evaluate_expression(effect['amount'],variables,state,KnowledgeRules.variable)

    def dispatch(self,state,event,*,source='room',only=None):
        if event not in EVENTS:raise UnsupportedEffect('Unknown run lifecycle: '+event)
        trace=[];gaps=[]
        if state.dead or state.cleared:return RunTransition(state)
        # Tuple snapshot preserves inventory/listener order when a hook changes it.
        for owner in tuple(state.relics):
            if only is not None and owner['entity_id']!=only:continue
            record=self.models.records.get(owner['entity_id'])
            if record is None or 'run_hooks' not in record:
                gaps.append(owner['entity_id']+':run_hooks_not_reviewed:'+event)
                continue
            for hook in record['run_hooks']:
                if hook['event']!=event:continue
                for effect in hook['effects']:
                    if event in {'modify_rest_heal','modify_merchant_price','modify_hp_loss'}:continue
                    result=self.execute(state,dict(operation=effect['operation'],amount=self._amount(effect,state,owner)),
                        source=owner['entity_id']+':'+event)
                    state=result.state;trace.extend(result.trace);gaps.extend(result.gaps)
                    if state.dead or state.cleared:break
        return RunTransition(state,tuple(trace),tuple(dict.fromkeys(gaps)))

    def rest(self,state,*,observed_heal=None,is_mimicked=False):
        """An observed command amount includes modifiers; never apply them twice."""
        trace=[];gaps=[]
        if state.dead or state.cleared:return RunTransition(state)
        if observed_heal is None:
            parameters=self.models.parameters
            percent=parameters.get('rest_heal_percent')
            if type(percent) is not int or not 0<=percent<=100:
                raise UnsupportedEffect('Rest heal parameter missing')
            # Keep fractional base healing until modifiers have been applied.
            from fractions import Fraction
            amount=Fraction(state.max_hp*percent,100)
            for owner in state.relics:
                record=self.models.records.get(owner['entity_id'])
                if record is None or 'run_hooks' not in record:
                    gaps.append(owner['entity_id']+':run_hooks_not_reviewed:modify_rest_heal');continue
                for hook in record['run_hooks']:
                    if hook['event']=='modify_rest_heal':
                        amount+=sum(self._amount(e,state,owner) for e in hook['effects'])
            amount=int(amount)
        else:amount=observed_heal
        source='MIMIC_REST.HEAL' if is_mimicked else 'REST.HEAL'
        result=self.execute(state,dict(operation='heal',amount=amount),source=source)
        after=self.dispatch(result.state,'after_rest_heal',source=source)
        return RunTransition(after.state,result.trace+after.trace,tuple(dict.fromkeys(gaps+list(after.gaps))))

    def quote(self,state,item):
        from fractions import Fraction
        raw=item.get('base_price',item['price'])
        if type(raw) is not int or raw<0:raise UnsupportedEffect('Invalid merchant price')
        price=Fraction(raw);gaps=[]
        for owner in state.relics:
            record=self.models.records.get(owner['entity_id'])
            if record is None or 'run_hooks' not in record:
                gaps.append(owner['entity_id']+':merchant_hooks_not_reviewed');continue
            for hook in record['run_hooks']:
                if hook['event']=='modify_merchant_price':
                    if item.get('base_price') is None:
                        gaps.append('merchant_base_price_unobserved');continue
                    for effect in hook['effects']:price*=Fraction(self._amount(effect,state,owner),100)
        return int(price),tuple(dict.fromkeys(gaps))

    def purchase(self,state,item,*,remove_instance=None):
        if state.shop_stock is not None and item['item_id'] not in state.shop_stock:
            raise UnsupportedEffect('Merchant entry out of stock')
        price,gaps=self.quote(state,item)
        if gaps:return RunTransition(state,gaps=gaps)
        if state.gold<price:raise UnsupportedEffect('Insufficient gold')
        category=item.get('category');trace=[]
        if category=='card':obtain=dict(operation='add_to_deck',card=item['card'])
        elif category=='relic':obtain=dict(operation='obtain_relic',entity_id=item['entity_id'],
            **{k:item[k] for k in ('dynamic_variables','runtime_state','counter') if k in item})
        elif category=='potion':obtain=dict(operation='obtain_potion',entity_id=item['entity_id'],instance_id=item['item_id'])
        elif category=='removal':obtain=dict(operation='remove_card',instance_id=remove_instance)
        else:raise UnsupportedEffect('Unknown merchant entry category')
        payment=dict(operation='pay_gold',amount=price)
        # MerchantRelicEntry pays before Obtain; cards/potions procure before payment.
        commands=(payment,obtain) if category=='relic' else (obtain,payment)
        for command in commands:
            result=self.execute(state,command,source='shop:'+item['item_id'])
            state=result.state;trace.extend(result.trace);gaps+=result.gaps
            if gaps or state.dead or state.cleared:
                return RunTransition(state,tuple(trace),tuple(dict.fromkeys(gaps)))
        if state.shop_stock is not None:
            state=replace(state,shop_stock=tuple(i for i in state.shop_stock if i!=item['item_id']))
        result=self.dispatch(state,'after_item_purchased',source='shop:'+item['item_id'])
        return RunTransition(result.state,tuple(trace)+result.trace,tuple(dict.fromkeys(gaps+result.gaps)))

    def execute(self,state,command,*,source):
        operation=command.get('operation')
        if operation not in OPERATIONS:raise UnsupportedEffect('Run command not implemented: '+str(operation))
        if state.dead or state.cleared:return RunTransition(state)
        before=state;trace=[];gaps=[]
        if operation in NUMERIC:
            amount=command.get('amount')
            decimal_ok=(type(amount) is Decimal and amount.is_finite() and
                operation in {'heal','gain_max_hp','lose_max_hp','damage','lose_hp'})
            if (type(amount) is not int and not decimal_ok) or not 0<=amount<=1000000000:
                raise UnsupportedEffect('Run command amount must be a nonnegative integer')
            from autoslay.rules.resources import heal,gain_max_hp
            if operation=='heal':state=heal(state,amount)
            elif operation=='heal_percent':state=heal(state,state.max_hp*amount//100)
            elif operation=='gain_max_hp':state=gain_max_hp(state,amount)
            elif operation=='gain_gold':state=replace(state,gold=min(999999999,state.gold+amount))
            elif operation=='pay_gold':
                # PlayerCmd.LoseGold clamps. Purchase eligibility is checked
                # separately before the obtain/payment sequence begins.
                state=replace(state,gold=max(0,state.gold-amount))
            elif operation in {'lose_hp','damage','lose_max_hp'}:
                def damage(current,requested):
                    for owner in current.relics:
                        record=self.models.records.get(owner['entity_id'])
                        if record is None or 'run_hooks' not in record:
                            gaps.append(owner['entity_id']+':hp_loss_hooks_not_reviewed');continue
                        for hook in record['run_hooks']:
                            if hook['event']=='modify_hp_loss':
                                for effect in hook['effects']:requested=max(0,requested-self._amount(effect,current,owner))
                    hp=max(0,current.hp-int(min(requested,999999999)))
                    damaged=replace(current,hp=hp,dead=hp==0)
                    if hp<current.hp:
                        # LoseMaxHp calls Damage before SetMaxHp. Listeners must
                        # see the old maximum, not the already-clamped state.
                        after=self.dispatch(damaged,'after_hp_loss',source=source)
                        trace.extend(after.trace);gaps.extend(after.gaps)
                        damaged=after.state
                    return damaged
                if operation=='lose_max_hp':
                    from autoslay.rules.resources import lose_max_hp
                    state=lose_max_hp(state,amount,damage)
                else:state=damage(state,amount)
            elif operation=='kill':state=replace(state,hp=0,dead=True)
            elif operation=='increase_potion_capacity':
                if state.potion_capacity is None:raise UnsupportedEffect('Potion capacity unobserved')
                state=replace(state,potion_capacity=state.potion_capacity+amount)
        elif operation=='obtain_relic':
            identity=command['entity_id']
            if identity not in self.references or self.references[identity].get('category')!='relic':
                raise UnsupportedEffect('Relic reference missing: '+identity)
            if any(r['entity_id']==identity for r in state.relics):raise UnsupportedEffect('Duplicate relic acquisition')
            owner=dict(entity_id=identity,instance_id=command.get('instance_id',identity),
                **{k:command[k] for k in ('dynamic_variables','runtime_state','counter') if k in command})
            state=replace(state,relics=state.relics+(owner,))
        elif operation=='lose_relic':
            identity=command['entity_id']
            if not any(r['entity_id']==identity for r in state.relics):raise UnsupportedEffect('Relic not owned')
            state=replace(state,relics=tuple(r for r in state.relics if r['entity_id']!=identity))
            gaps.append(identity+':after_lost_hooks_not_reviewed')
        elif operation=='obtain_potion':
            if state.potion_capacity is None:raise UnsupportedEffect('Potion capacity unobserved')
            if len(state.potions)>=state.potion_capacity:raise UnsupportedEffect('Potion belt full')
            identity=command['entity_id']
            if self.references.get(identity,{}).get('category')!='potion':raise UnsupportedEffect('Potion reference missing')
            state=replace(state,potions=state.potions+(command.get('instance_id',identity),))
        elif operation=='add_to_deck':
            from autoslay.knowledge_base.templates import canonical
            card=command['card'];canonical(card,self.references)
            if not card.get('instance_id') or any(c.get('instance_id')==card['instance_id'] for c in state.deck):
                raise UnsupportedEffect('Unique card instance required')
            state=replace(state,deck=state.deck+(card,))
        else:
            identity=command['instance_id']
            cards=[c for c in state.deck if c.get('instance_id')==identity]
            if len(cards)!=1:raise UnsupportedEffect('Unique deck instance required')
            card=cards[0]
            from autoslay.knowledge_base.templates import canonical
            _,variant=canonical(card,self.references)
            if operation=='remove_card':
                if 'Eternal' in variant.get('keywords',[]):raise UnsupportedEffect('Eternal card cannot be removed')
                pre=self.dispatch(state,'before_card_removed',source=source)
                if pre.gaps or pre.state.dead or pre.state.cleared:return pre
                state=replace(pre.state,deck=tuple(c for c in pre.state.deck if c.get('instance_id')!=identity))
                trace.extend(pre.trace);gaps.extend(pre.gaps)
            else:
                upgraded=dict(card,upgrade_level=card.get('upgrade_level',0)+1)
                canonical(upgraded,self.references)
                state=replace(state,deck=tuple(upgraded if c.get('instance_id')==identity else c for c in state.deck))
        trace.append(dict(operation=operation,source=source,entity_id=command.get('entity_id'),
            hp_delta=state.hp-before.hp,max_hp_delta=state.max_hp-before.max_hp,gold_delta=state.gold-before.gold))
        event={'obtain_relic':'after_obtained','add_to_deck':'after_card_changed_piles'}.get(operation)
        if event:
            result=self.dispatch(state,event,source=source,only=command['entity_id'] if operation=='obtain_relic' else None)
            state=result.state;trace.extend(result.trace);gaps.extend(result.gaps)
        return RunTransition(state,tuple(trace),tuple(dict.fromkeys(gaps)))

    def upgrade_outcomes(self,state,mode):
        """The actual upgradable deck pool; random selection is uniform NextItem."""
        from autoslay.knowledge_base.templates import canonical
        pool=[]
        for card in state.deck:
            entry=self.references.get(card['entity_id'])
            if entry is None or 'max_upgrade_level' not in entry:
                raise UnsupportedEffect('Upgrade eligibility missing: '+card['entity_id'])
            if card.get('upgrade_level',0)<entry['max_upgrade_level']:
                canonical(dict(card,upgrade_level=card.get('upgrade_level',0)+1),self.references)
                pool.append(card)
        if mode not in {'random','all'}:raise UnsupportedEffect('Unsupported upgrade selection mode')
        if not pool:return [(1.0,RunTransition(state))]
        groups=[[card] for card in pool] if mode=='random' else [pool]
        outcomes=[]
        for group in groups:
            current=state;trace=[];gaps=[]
            for card in group:
                result=self.execute(current,dict(operation='upgrade_card',instance_id=card['instance_id']),source='deck_upgrade_selection')
                current=result.state;trace.extend(result.trace);gaps.extend(result.gaps)
            outcomes.append((1/len(groups),RunTransition(current,tuple(trace),tuple(dict.fromkeys(gaps)))))
        return outcomes
