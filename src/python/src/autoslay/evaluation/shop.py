"""Bounded purchase-order search over independent common-command states."""
from dataclasses import asdict,replace
from time import perf_counter
from autoslay.evaluation.run import deck_value,card_features,unmodeled
from autoslay.evaluation.resources import item_value
from autoslay.evaluation.strategic import RunResources
from autoslay.rules.run_commands import RunCommands
from autoslay.decision.contracts import UnsupportedEffect

VERSION='2.0.0'


def evaluate_shop(deck,items,context,entries,policy,*,relics=(),potions=()):
    commands=RunCommands(references=entries)
    stock={i['item_id']:i for i in items if i.get('stocked',i.get('available',True)) and i.get('price',-1)>=0}
    initial=RunResources(context.hp,context.max_hp,context.gold,dead=context.hp==0,deck=tuple(deck),
        relics=tuple(relics),potions=tuple(p.get('instance_id',p['entity_id']) for p in potions),
        potion_capacity=len(potions)+context.potion_slots_free,shop_stock=tuple(stock))
    base=deck_value(deck,context,entries,policy)
    stack=[(initial,[],0,[],[])];results=[];unvalued=[];nodes=0;started=perf_counter()
    while stack and nodes<policy.shop_nodes and (perf_counter()-started)*1000<policy.shop_time_ms:
        state,chosen,inventory_value,trace,gaps=stack.pop();nodes+=1
        changed_context=replace(context,hp=state.hp,max_hp=state.max_hp,gold=state.gold,
            potion_slots_free=state.potion_capacity-len(state.potions))
        evaluated=deck_value(list(state.deck),changed_context,entries,policy)
        spent=context.gold-state.gold
        deck_delta=evaluated['utility_points']-base['utility_points']
        resource_points=(state.hp-context.hp)*policy.hp_weight*context.urgency+(state.max_hp-context.max_hp)*policy.max_hp_weight
        points=deck_delta+resource_points+inventory_value-spent*policy.gold_weight
        results.append(dict(purchases=chosen,spent=spent,gold_remaining=state.gold,
            delta_utility=points if not gaps else None,known_component_utility=points,
            deck_delta=deck_delta,resource_utility=resource_points+inventory_value,
            gold_opportunity_cost=spent*policy.gold_weight,
            component_deltas={k:evaluated['components'][k]-v for k,v in base['components'].items()},
            warnings=evaluated['warnings'],mechanism_gaps=gaps,command_trace=trace,after=asdict(state)))
        if gaps:continue  # Unknown hooks can change the next price/stock/resources.
        for identity in reversed(state.shop_stock):
            item=stock[identity]
            if not chosen and not item.get('available',True):continue
            targets=[c['instance_id'] for c in state.deck] if item.get('category')=='removal' else [None]
            for target in targets:
                try:
                    price,quote_gaps=commands.quote(state,item)
                    transition=commands.purchase(state,item,remove_instance=target)
                except (UnsupportedEffect,ValueError,KeyError) as exc:
                    unvalued.append(dict(item=item,reason=str(exc)));continue
                next_gaps=list(transition.gaps);value=0
                if item.get('category')=='potion':
                    post_context=replace(changed_context,hp=transition.state.hp,max_hp=transition.state.max_hp,
                        gold=transition.state.gold,potion_slots_free=transition.state.potion_capacity-len(transition.state.potions))
                    priced=item_value(item,post_context,list(transition.state.deck),entries,policy,relics=transition.state.relics)
                    if priced['utility'] is None:next_gaps.append('potion_value_unmodeled:'+identity)
                    else:value=priced['utility']
                if item.get('category')=='card' and unmodeled(card_features(item['card'],entries)):
                    next_gaps.append('card_value_unmodeled:'+identity)
                if target is not None and unmodeled(card_features(next(c for c in state.deck if c['instance_id']==target),entries)):
                    next_gaps.append('removed_card_value_unmodeled:'+target)
                if item.get('category')=='relic':
                    record=commands.models.records.get(item.get('entity_id'))
                    if record and (record.get('triggers') or record.get('state_fields') or record.get('passive') not in {None,'pickup'}):
                        next_gaps.append('relic_continuation_value_unmodeled:'+identity)
                choice=dict(item_id=identity,action_id=item.get('action_id') if not chosen else None,price=price)
                if target is not None:choice['remove_instance']=target
                stack.append((transition.state,chosen+[choice],inventory_value+value,
                    trace+list(transition.trace),next_gaps))
    unique={tuple((c['item_id'],c.get('remove_instance')) for c in r['purchases']):r for r in results}
    ranked=sorted(unique.values(),key=lambda r:(r['delta_utility'] is not None,r['known_component_utility']),reverse=True)
    unvalued=list({(r['item']['item_id'],r['reason']):r for r in unvalued}.values())
    return dict(module_version=VERSION,candidates=ranked[:5],unvalued_items=unvalued,nodes=nodes,
        search_truncated=bool(stack),elapsed_ms=(perf_counter()-started)*1000,execution_authorized=False,
        rule_fingerprint=commands.models.fingerprint,
        assumptions=['current public stock only; unknown restock results require observation',
            'future steps are predictions and require fresh legal actions after each purchase'],
        limitations=['bounded purchase orders are not proven optimal; missing hooks and values stay explicit'])
