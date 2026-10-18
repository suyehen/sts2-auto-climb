"""Bounded shopping-combination search including removal and keeping gold."""
from time import perf_counter
from autoslay.evaluation.run import deck_value,card_features,unmodeled
from autoslay.evaluation.resources import item_value
from autoslay.knowledge_base.templates import canonical
from autoslay.evaluation.entity_effects import evaluate_entity

VERSION='1.0.0'

def evaluate_shop(deck, items, context, entries, policy):
    base=deck_value(deck,context,entries,policy)
    groups=[]; unvalued=[]
    for item in items:
        if not item.get('available',True) or item.get('price',-1)<0: continue
        price=item['price']; category=item.get('category','')
        alternatives=[]
        if category=='card':
            feature=card_features(item['card'],entries)
            if unmodeled(feature):
                unvalued.append(dict(item=item,reason='card value mechanism unmodeled',
                    semantic_analysis=evaluate_entity(item['card'],context,entries,policy))); continue
            alternatives=[dict(item=item,add=item['card'])]
        elif category=='removal':
            for c in deck:
                try: eternal='Eternal' in canonical(c,entries)[1].get('keywords',[])
                except ValueError: eternal=True
                if not eternal:
                    alternatives.append(dict(item=item,remove=c['instance_id']))
        else:
            if category=='potion' and context.potion_slots_free==0:
                unvalued.append(dict(item=item,reason='full potion belt; replacement needs separate explicit discard decision')); continue
            value=item_value(item,context,deck,entries,policy)
            if value['utility'] is None:
                unvalued.append(dict(item=item,reason=value['basis'],semantic_analysis=value.get('semantic_analysis'))); continue
            alternatives=[dict(item=item,resource=value)]
        if alternatives: groups.append(alternatives)
    stack=[(0,[],list(deck),0,0,0)]; results=[]; nodes=0; started=perf_counter()
    while stack and nodes<policy.shop_nodes and (perf_counter()-started)*1000<policy.shop_time_ms:
        index, chosen, modified, spent, resources, potion_count=stack.pop(); nodes+=1
        evaluated=deck_value(modified,context,entries,policy)
        points=evaluated['utility_points']-base['utility_points']+resources-spent*policy.gold_weight
        results.append(dict(purchases=chosen,spent=spent,gold_remaining=context.gold-spent,
            delta_utility=points,deck_delta=evaluated['utility_points']-base['utility_points'],
            resource_utility=resources,gold_opportunity_cost=spent*policy.gold_weight,
            component_deltas={k:evaluated['components'][k]-v for k,v in base['components'].items()},
            warnings=evaluated['warnings']))
        if index>=len(groups): continue
        for option in groups[index]:
            item=option['item']; new_spent=spent+item['price']
            new_potions=potion_count+(item.get('category')=='potion')
            if new_spent>context.gold or new_potions>context.potion_slots_free: continue
            new_deck=list(modified)
            if 'add' in option: new_deck.append(option['add'])
            if 'remove' in option:
                if not any(c['instance_id']==option['remove'] for c in new_deck): continue
                if unmodeled(card_features(next(c for c in new_deck if c['instance_id']==option['remove']),entries)):
                    continue
                new_deck=[c for c in new_deck if c['instance_id']!=option['remove']]
            if not new_deck: continue
            choice=dict(item_id=item['item_id'],action_id=item.get('action_id'),price=item['price'])
            if 'remove' in option: choice['remove_instance']=option['remove']
            stack.append((index+1,chosen+[choice],new_deck,new_spent,resources+option.get('resource',{}).get('utility',0),new_potions))
        stack.append((index+1,chosen,modified,spent,resources,potion_count))
    unique={tuple((c['item_id'],c.get('remove_instance')) for c in r['purchases']):r for r in results}
    ranked=sorted(unique.values(),key=lambda r:r['delta_utility'],reverse=True)
    return dict(module_version=VERSION,candidates=ranked[:5],unvalued_items=unvalued,nodes=nodes,
        search_truncated=bool(stack),elapsed_ms=(perf_counter()-started)*1000,execution_authorized=False,
        assumptions=['visible fixed prices; no discount/price changes after purchases; re-read after every purchase',
                     'gold opportunity cost uses explicit policy; future shop stock unknown'],
        limitations=['bounded combinations are not proven optimal; unvalued items need Codex assessment'])
