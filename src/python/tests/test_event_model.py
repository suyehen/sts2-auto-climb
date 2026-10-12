from copy import deepcopy
import json
from autoslay.knowledge.behavior import load, relevant, compact
from autoslay.knowledge.event_model import random_interval, event_methods


def entry(identity):return next(e for e in load()['entries'] if e['entity_id']=='EVENT.'+identity)


def test_every_current_event_has_model_and_bound_active_options():
    events=[e for e in load()['entries'] if e['category']=='event']
    assert len(events)==57
    for event in events:
        assert not event['event_model']['unresolved']
        for option in event['options']:
            assert '{' not in option['description'] and '{' not in option['title']
            assert option['bindings'] or option['source_status']=='localization_only'
        assert event['event_model']['execution_authorized'] is False


def test_repeated_damage_is_not_falsely_fixed_and_future_costs_are_explicit():
    bath=entry('ABYSSAL_BATHS')['event_model']
    damage=next(e for e in bath['methods']['OnImmerse']['effects'] if e['kind']=='lose_hp')['amount']
    assert damage['value'] is None and damage['status']=='state_dependent'
    assert bath['variables']['Damage']['value']==3 and bath['variables']['Damage']['updates'][0]['value']['value']==1
    tablet=entry('TABLET_OF_TRUTH')['event_model']
    future=tablet['variables']['DecipherMaxHpLoss']['future_values']
    assert [f['value'] for f in future[:3]]==[6,12,24]
    assert any('MaxHp - 1' in f['expression'] for f in future)
    assert tablet['methods']['Decipher']['updates']
    option=next(o for o in entry('TABLET_OF_TRUTH')['options'] if '.pages.DECIPHER_2.options.' in o['text_key'])
    assert option['stage_context']['DecipherCount']==2 and '12' in option['description']


def test_array_stages_and_random_ranges_use_actual_game_values():
    flower=entry('COLOSSAL_FLOWER')['event_model']
    assert flower['arrays']['_prizeCosts']==['35','75','135']
    assert flower['arrays']['_prizeDamage']==['5','6','7']
    assert entry('SUNKEN_STATUE')['event_model']['variables']['Gold']['range']==[101,121]
    treasury=entry('SUNKEN_TREASURY')['event_model']['variables']
    assert treasury['SmallChestGold']['range'][1]-treasury['SmallChestGold']['range'][0]==15
    assert random_interval('(base.Rng.NextInt(16) - 8)',{}, {})==(-8,7)
    assert random_interval('UnknownState + base.Rng.NextInt(3)',{}, {}) is None


def test_potion_exchange_and_generic_helpers_include_full_mechanics():
    potion=entry('THE_FUTURE_OF_POTIONS')['event_model']
    assert 'PotionToCardType' in potion['property_rules']
    assert all(name in potion['variables'] for name in ('Potion','Rarity','Type'))
    assert 'GetCardRarity' in potion['methods'] and 'UpgradeCardsInReward' in potion['methods']
    assert any('PotionRarity.Rare' in c and 'PotionRarity.Event' in c for r in potion['methods']['GetCardRarity']['returns'] for c in r['conditions'])
    book=entry('SELF_HELP_BOOK')['event_model']
    assert 'SelectAndEnchant' in book['methods'] and 'ApplyEnchantment' in book['methods']
    assert book['methods']['SelectAndEnchant']['type_parameters']==['T']
    assert entry('SYMBIOTE')['related_mechanics']['ENCHANTMENT.CORRUPTED']['description']


def test_weighted_dishes_include_shared_cost_and_repeated_flow():
    event=entry('ENDLESS_CONVEYOR')
    caviar=next(o for o in event['options'] if o['text_key'].endswith('.CAVIAR'))
    assert 'Caviar' in caviar['reachable_methods']
    assert 'GrabSomethingOffTheBelt' in caviar['reachable_methods'] and 'RollDish' in caviar['reachable_methods']
    assert any(b['selection_weight']['value']==6 for b in caviar['bindings'])
    assert any(t['kind'].startswith('EnterCombat') for n in entry('FAKE_MERCHANT')['event_model']['methods'].values() for t in n['transitions'])


def test_all_agent_event_packets_fit_and_live_values_override_without_mutation():
    data=load();before=deepcopy(entry('ABYSSAL_BATHS'))
    for event in [e for e in data['entries'] if e['category']=='event']:
        snapshot=dict(game_version=data['game_version'],game_assembly_sha256=data['game_assembly_sha256'],
                      state={'event_screen':dict(entity_id=event['entity_id'],dynamic_variables=[dict(name='Damage',value=9)],runtime_state={'fields':{'_lingerCount':6}},options=[])})
        result=relevant(snapshot)
        assert not result['omitted_ids'] and len(json.dumps(result,ensure_ascii=False).encode())<=64000
        assert result['entries'][0]['event_model']['observed_variables']['Damage']==9
        assert result['entries'][0]['event_model']['observed_state']['_lingerCount']==6
    assert entry('ABYSSAL_BATHS')==before
    assert 'description' not in compact(before,for_agent=True)


def test_parser_preserves_generic_and_local_functions_and_ignores_literal_calls():
    text='''private async Task SelectAndEnchant<T>(int amount) where T : EnchantmentModel
    { string text="FakeCall(42)"; await Other<T>(amount); void Local() { Done(); } }'''
    defs=event_methods(text)
    assert 'SelectAndEnchant' in defs and 'Local' in defs
