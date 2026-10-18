from dataclasses import asdict
import json
import pytest
from autoslay.knowledge_base.effect_profiles import (compile_entry,compile_catalog,profile_for,
    selected_variant,normalize_text,interaction_edges)
from autoslay.evaluation.entity_effects import evaluate_entity,apply_route_effects
from autoslay.evaluation.run import RunContext,RunPolicy,card_features
from autoslay.evaluation.strategic import RunResources


def entry(identity,text,category='relic',**kwargs):
    row=dict(entity_id=identity,category=category,name=identity,description=text,**kwargs)
    if category=='card':
        row.pop('description')
        row['variants']=[dict(upgrade_level=0,description=text,energy_cost=1,costs_x=False,
            star_cost=-1,costs_star_x=False,type='Skill',target_type='Self',keywords=[])]
    return row


def evaluate(row,hp=40,**kwargs):
    return evaluate_entity(dict(entity_id=row['entity_id']),RunContext(hp,80,100),{row['entity_id']:row},RunPolicy(),**kwargs)


def test_registry_covers_every_entity_and_every_variant_without_an_id_allowlist():
    card=entry('CARD.NEVER_SEEN','获得7点格挡。','card')
    card['variants'].append(dict(card['variants'][0],upgrade_level=1,description='获得11点格挡。'))
    rows=[card,entry('RELIC.NEVER_SEEN','在战斗结束时，回复9点生命。'),
          entry('POTION.NEVER_SEEN','获得2点力量。','potion')]
    model=compile_catalog(rows)
    assert set(model['profiles'])=={r['entity_id'] for r in rows}
    assert model['counts']['card']['variants']==2
    assert model['data_coverage_complete'] and not model['full_simulation_support']
    assert not any(p['simulation_adapter_enabled'] for p in model['profiles'].values())
    with pytest.raises(ValueError,match='Duplicate'):compile_catalog(rows+[rows[0]])


def test_all_clauses_and_dynamic_variables_survive_unknown_semantics():
    row=entry('RELIC.COMPLEX','获得1点力量。每消耗3张牌，就将一张未知牌加入手牌。',
              dynamic_variables=[dict(name='UnusualCounter',base_value=3)],hover_tips=[dict(id='POWER.WHATEVER',description='复杂效果')])
    profile=compile_entry(row);variant=selected_variant(profile)
    assert len(variant['clauses'])==2 and variant['dynamic_variables'][0]['base_value']==3
    assert variant['hover_tips'][0]['id']=='POWER.WHATEVER'
    result=evaluate(row)
    assert result['utility'] is None and result['unpriced']
    assert result['clauses'][1]['text']=='每消耗3张牌，就将一张未知牌加入手牌'


def test_delayed_damage_is_a_timeline_not_an_immediate_52_damage_bonus():
    row=entry('RELIC.CALENDAR_ALIAS','在第7回合结束时，对所有敌人造成52点伤害。')
    result=evaluate(row)
    clause=result['clauses'][0]
    assert clause['trigger']['kind']=='turn_end' and clause['trigger']['turn']==7
    assert clause['target']=='all_enemies' and clause['effects'][0]['amount']==52
    assert result['utility'] is None and result['components'][0]['occurrences'] is None


def test_energy_images_preserve_numeric_multiplier_and_repeated_icons():
    icon='[img]res://images/packed/sprite_fonts/colorless_energy_icon.png[/img]'
    for text,expected in [('获得'+icon+icon+'。',2),('获得[blue]4'+icon+'[/blue]。',4)]:
        result=evaluate(entry('POTION.ENERGY_ALIAS',text,'potion'))
        assert result['components'][0]['effect']['amount']==expected
        assert result['utility']==expected*RunPolicy().energy_weight
    assert 'png' not in normalize_text('获得'+icon)


def test_negative_and_positive_lifetime_effects_are_not_separated_into_a_free_bonus():
    row=entry('RELIC.TRADEOFF','在每回合开始时获得1{ENERGY}。你无法再获得药水。')
    result=evaluate(row,opportunities={'turn_start':4})
    assert result['utility'] is None and result['known_component_utility']==16
    assert result['restrictions'][0]['kind']=='no_potion_gain'
    assert any('药水' in str(gap) for gap in result['unpriced'])


def test_all_future_trigger_counts_require_explicit_opportunities():
    row=entry('RELIC.HEAL_ALIAS','在战斗结束时，回复6点生命。')
    unknown=evaluate(row);known=evaluate(row,opportunities={'combat_end':2})
    assert unknown['utility'] is None
    assert known['components'][0]['occurrences']==2
    with pytest.raises(ValueError):evaluate(row,opportunities={'combat_end':-1})


def test_resource_triggers_work_for_arbitrary_relic_ids_and_do_not_repeat_pickup():
    profiles=[compile_entry(entry('RELIC.HEAL_ALIAS','在战斗结束时，回复12点生命。')),
              compile_entry(entry('RELIC.CASH_ALIAS','拾起时，获得150金币。'))]
    start=RunResources(70,80,0)
    first,notes=apply_route_effects(start,'combat_end',profiles)
    assert first.hp==80 and first.gold==0
    acquired,_=apply_route_effects(first,'pickup',profiles)
    assert acquired.gold==150
    next_fight,_=apply_route_effects(acquired,'combat_end',profiles)
    assert next_fight.gold==150 and next_fight.hp==80


def test_no_gold_restriction_prevents_parsed_gold_gain():
    profiles=[compile_entry(entry('RELIC.NO_GOLD','你不能再获得任何金币。')),
              compile_entry(entry('RELIC.CASH','拾起时，获得150金币。'))]
    result,_=apply_route_effects(RunResources(40,80,0),'pickup',profiles)
    assert result.gold==0
    result,_=apply_route_effects(RunResources(40,80,0),'pickup',[profiles[1]],context_profiles=[profiles[0]])
    assert result.gold==0


def test_conditional_heal_is_not_applied_without_observing_condition():
    profile=compile_entry(entry('RELIC.CONDITIONAL','如果你在战斗结束时生命值等于或低于50%，回复12点生命。'))
    resources,notes=apply_route_effects(RunResources(40,80,0),'combat_end',[profile])
    assert resources.hp==40 and notes[0]['status']=='unmodeled'


def test_observed_description_overrides_default_for_one_upgrade_variant_only():
    row=entry('CARD.ALIAS','造成6点伤害。','card')
    row['variants'].append(dict(row['variants'][0],upgrade_level=1,description='造成9点伤害。'))
    model=profile_for(dict(entity_id='CARD.ALIAS',description='造成15点伤害。',upgrade_level=0),{'CARD.ALIAS':row})
    assert selected_variant(model,0)['clauses'][0]['effects'][0]['amount']==15
    assert selected_variant(model,1)['clauses'][0]['effects'][0]['amount']==9
    assert row['variants'][0]['description']=='造成6点伤害。'


def test_all_upgrade_and_x_cost_metadata_remain_available_without_canonical_simulation():
    row=entry('CARD.X','获得5点格挡。','card');row['variants'][0]['costs_x']=True
    result=evaluate(row)
    assert result['resource_costs']['costs_x'] and result['utility'] is None
    features=card_features(dict(entity_id='CARD.X'),{'CARD.X':row})
    assert features['costs_x'] and features['block']==5
    assert any('UNMODELED' in w for w in features['warnings'])


def test_complete_new_numeric_card_enters_resource_scoring_without_enabling_simulation():
    from autoslay.evaluation.run import unmodeled,intrinsic_value
    row=entry('CARD.NO_MANUAL_RULE','造成17点伤害。','card')
    features=card_features(dict(entity_id=row['entity_id']),{row['entity_id']:row})
    assert features['damage']==17 and not unmodeled(features)
    assert intrinsic_value(dict(entity_id=row['entity_id']),{row['entity_id']:row})['utility']==17
    assert not compile_entry(row)['simulation_adapter_enabled']


def test_empty_placeholder_is_registered_without_fake_zero_utility():
    row=entry('POTION.EMPTY','','potion')
    assert compile_catalog([row])['entity_count']==1
    assert evaluate(row)['utility'] is None
    assert evaluate(row)['known_component_utility'] is None


def test_interactions_describe_real_mechanism_links_instead_of_a_required_archetype():
    a=compile_entry(entry('CARD.A','失去2点生命。','card'))
    b=compile_entry(entry('RELIC.B','你每次失去生命时，减少失去的生命值1点。'))
    c=compile_entry(entry('CARD.C','造成9点伤害。','card'))
    edges=interaction_edges([a,b,c])
    assert any(e['source']=='CARD.A' and e['target']=='RELIC.B' for e in edges)
    assert not any(e['source']=='CARD.C' and e['target']=='RELIC.B' for e in edges)


def test_max_hp_and_friendly_fire_do_not_masquerade_as_complete_scalar_benefits():
    assert evaluate(entry('POTION.HP','获得5点最大生命值。','potion'))['utility'] is None
    result=evaluate(entry('POTION.ALL','对所有玩家和敌人造成12点伤害。','potion'))
    assert result['utility'] is None and result['components'][0]['per_trigger_proxy'] is None


def test_live_hand_instances_offers_and_relic_options_all_enter_actual_decision_packet(monkeypatch):
    from autoslay.decision import entity_models
    from autoslay.telemetry.decisions import entity_models_packet
    rows=[entry('CARD.NEW','造成6点伤害。','card'),entry('POTION.NEW','抽3张牌。','potion'),
          entry('RELIC.NEW','在战斗结束时，回复9点生命。')]
    entries={row['entity_id']:row for row in rows}
    monkeypatch.setattr(entity_models,'registry_summary',lambda:dict(entity_count=3))
    snapshot={'state':{'player':{'hand':[dict(entity_id='CARD.NEW',instance_id='one',description='造成12点伤害。')],
        'deck':[dict(entity_id='CARD.NEW',instance_id='one',description='造成6点伤害。')],
        'potions':[dict(entity_id='POTION.NEW',instance_id='bottle')]},
        'event_screen':{'options':[dict(relic_id='RELIC.NEW')]}}}
    report=entity_models.analyze_entities(snapshot,RunContext(40,80,0),entries,RunPolicy())
    assert {row['entity_id'] for row in report['entities']}==set(entries)
    card=next(row for row in report['entities'] if row['entity_id']=='CARD.NEW')
    assert card['components'][0]['effect']['amount']==12
    assert len(card['origins'])==2
    packet=entity_models_packet(report)
    assert len(packet['entities'])==3 and all('clauses' not in r for r in packet['entities'])
    assert next(r for r in packet['entities'] if r['entity_id']=='RELIC.NEW')['utility'] is None
    json.dumps(packet,allow_nan=False)


def test_draw_restrictions_and_victory_healing_use_catalog_effects_without_identity_checks():
    from autoslay.decision.unified import context_for
    from autoslay.evaluation.combat_resources import capabilities
    draw=entry('RELIC.DRAW_BAN_ALIAS','在每个回合开始时，额外抽2张牌。你在回合进行中不再能抽任何牌。')
    heal=entry('RELIC.BLOOD_ALIAS','在战斗结束时，回复12点生命。')
    entries={r['entity_id']:r for r in (draw,heal)}
    snapshot={'state':{'phase':'map','player':{'creature':dict(hp=40,max_hp=80),
        'relics':[dict(entity_id=draw['entity_id']),dict(entity_id=heal['entity_id'])]}}}
    assert context_for(snapshot,dict(draw_blocked=False),entries).draw_blocked
    assert capabilities(snapshot,entries)['victory_heal']==12


@pytest.mark.local_knowledge
def test_every_local_catalog_variant_can_compile_and_evaluate_without_omission():
    from autoslay.knowledge_base.templates import index
    from autoslay.knowledge_base.rulebook import Rulebook
    from autoslay.decision.entity_models import registry_summary
    entries=index()[0];book=Rulebook();summary=registry_summary(book)
    assert summary['entity_count']==len(entries)
    evaluations=0
    for identity,row in entries.items():
        profile=profile_for(dict(entity_id=identity),entries)
        assert profile and profile['entity_id']==identity
        expected=row.get('variants',[]) if row['category']=='card' else [row]
        assert len(profile['variants'])==len(expected)
        for variant in expected:
            result=evaluate_entity(dict(entity_id=identity,upgrade_level=variant.get('upgrade_level',0)),
                                   RunContext(40,80,100),entries,RunPolicy())
            assert result['coverage'] not in {'reference_missing','variant_missing'}
            json.dumps(result,ensure_ascii=False,allow_nan=False)
            evaluations+=1
    assert evaluations==sum(c['variants'] for c in summary['counts'].values())
    assert all(c['entities']==c['profiles'] for c in summary['counts'].values())
