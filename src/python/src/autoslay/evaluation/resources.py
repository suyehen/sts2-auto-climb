"""Catalog-wide item valuation; no identity whitelist or invented trigger count."""
from autoslay.evaluation.entity_effects import evaluate_entity
from autoslay.knowledge_base.rulebook import Rulebook

VERSION='2.0.0'


def item_value(value,context,deck,entries,policy):
    evaluated=evaluate_entity(value,context,entries,policy)
    return dict(utility=evaluated['utility'],known_component_utility=evaluated['known_component_utility'],
        basis='catalog effect program; known resource proxy only; conditional lifetime remains explicit',
        reference=entries.get(value.get('entity_id')),semantic_analysis=evaluated,
        simulation_compatibility=Rulebook().status(value.get('entity_id')))
