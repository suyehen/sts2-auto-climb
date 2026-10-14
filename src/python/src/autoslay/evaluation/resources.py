"""Explicit reference effects for resource valuation; unknown items remain unvalued."""
from autoslay.rules.registry import amount, clean

VERSION='1.0.0'

def item_value(value, context, deck, entries, policy):
    entity=value.get('entity_id')
    entry=entries.get(entity,{})
    from autoslay.knowledge_base.rulebook import Rulebook
    book=Rulebook()
    status=book.status(entity)
    if status!='compatible': return dict(utility=None,basis='rule compatibility: '+status,reference=entry)
    valuation=book.data['entries'][entity].get('valuation')
    text=clean(value.get('description') or entry.get('description',''))
    if valuation=='block_potion':
        return dict(utility=amount(text,'block')*policy.block_weight*context.urgency*0.7,
                    basis='single-use block resource; assumed 70% effective future use')
    if valuation=='combat_heal':
        return dict(utility=min(6,context.max_hp-context.hp)*policy.hp_weight*context.urgency,
                    basis='one future combat heal; longer horizon not valued')
    if valuation=='opening_energy':
        return dict(utility=4, basis='one opening energy; heuristic conversion 4 points')
    if valuation=='hp_reduction':
        return dict(utility=3*policy.hp_weight*context.urgency, basis='assumed three future HP-loss events')
    if valuation=='rest_heal':
        return dict(utility=min(15,context.max_hp-context.hp)*policy.hp_weight*0.5,
                    basis='assumed 50% chance of resting; explicit proxy')
    if valuation=='power_block':
        powers=sum(c.get('type')=='Power' for c in deck)
        return dict(utility=7*policy.block_weight*min(1,5*powers/max(1,len(deck))),
                    basis='opening power density proxy; once-per-combat trigger')
    return dict(utility=None,basis='unmodeled item; description supplied for Codex', reference=entry)
