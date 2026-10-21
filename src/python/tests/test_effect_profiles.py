from dataclasses import asdict
import json
import pytest
from autoslay.knowledge_base.effect_profiles import (compile_entry,compile_catalog,profile_for,
    selected_variant,normalize_text,interaction_edges)
from autoslay.evaluation.entity_effects import entity_reference
from autoslay.evaluation.run import RunContext,RunPolicy,card_features
from autoslay.evaluation.strategic import RunResources


def entry(identity,text,category='relic',**kwargs):
    row=dict(entity_id=identity,category=category,name=identity,description=text,**kwargs)
    if category=='card':
        row.pop('description')
        row['variants']=[dict(upgrade_level=0,description=text,energy_cost=1,costs_x=False,
            star_cost=-1,costs_star_x=False,type='Skill',target_type='Self',keywords=[])]
    return row


def evaluate(row,hp=40):
    return entity_reference(dict(entity_id=row['entity_id']),{row['entity_id']:row})


def test_registry_covers_every_entity_and_every_variant_without_an_id_allowlist():
    card=entry('CARD.NEVER_SEEN','获得7点格挡。','card')
    card['variants'].append(dict(card['variants'][0],upgrade_level=1,description='获得11点格挡。'))
    rows=[card,entry('RELIC.NEVER_SEEN','在战斗结束时，回复9点生命。'),
          entry('POTION.NEVER_SEEN','获得2点力量。','potion')]
    model=compile_catalog(rows)
    assert set(model['profiles'])=={r['entity_id'] for r in rows}
    assert model['counts']['card']['variants']==2
    assert model['data_coverage_complete'] and not model['full_simulation_support']
    assert not any(p['mechanism_defined'] for p in model['profiles'].values())
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
        assert result['utility'] is None and result['reference_only']
    assert 'png' not in normalize_text('获得'+icon)


def test_negative_and_positive_lifetime_effects_are_not_separated_into_a_free_bonus():
    row=entry('RELIC.TRADEOFF','在每回合开始时获得1{ENERGY}。你无法再获得药水。')
    result=evaluate(row)
    assert result['utility'] is None and result['known_component_utility'] is None
    assert result['restrictions'][0]['kind']=='no_potion_gain'
    assert result['reference_only'] and result['unpriced']


def test_description_reference_never_supplies_trigger_counts_or_resource_points():
    row=entry('RELIC.HEAL_ALIAS','在战斗结束时，回复6点生命。')
    unknown=evaluate(row)
    assert unknown['utility'] is None
    assert unknown['components'][0]['occurrences'] is None
    assert unknown['components'][0]['per_trigger_proxy'] is None


def test_description_only_relics_do_not_execute_resource_changes():
    from autoslay.rules.run_commands import RunCommands
    rows=[entry('RELIC.HEAL_ALIAS','在战斗结束时，回复12点生命。'),
          entry('RELIC.CASH_ALIAS','拾起时，获得150金币。')]
    initial=RunResources(70,80,0,relics=tuple(dict(entity_id=r['entity_id']) for r in rows))
    result=RunCommands(references={r['entity_id']:r for r in rows}).dispatch(initial,'after_combat_victory')
    assert result.state==initial and len(result.gaps)==2


def test_display_restrictions_are_not_run_command_definitions():
    from autoslay.rules.run_commands import RunCommands
    row=entry('RELIC.NO_GOLD','你不能再获得任何金币。')
    initial=RunResources(40,80,0,relics=(dict(entity_id=row['entity_id']),))
    result=RunCommands(references={row['entity_id']:row}).dispatch(initial,'after_obtained')
    assert result.state==initial and result.gaps


def test_conditional_heal_requires_a_structured_definition():
    from autoslay.rules.run_commands import RunCommands
    row=entry('RELIC.CONDITIONAL','如果你在战斗结束时生命值等于或低于50%，回复12点生命。')
    initial=RunResources(40,80,0,relics=(dict(entity_id=row['entity_id']),))
    result=RunCommands(references={row['entity_id']:row}).dispatch(initial,'after_combat_victory')
    assert result.state.hp==40 and result.gaps


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
    assert features['costs_x'] and features['block']==0
    assert any('UNMODELED' in w for w in features['warnings'])


def test_reference_numeric_profile_cannot_enable_default_resource_scoring():
    from autoslay.evaluation.run import unmodeled,intrinsic_value
    row=entry('CARD.NO_MANUAL_RULE','造成17点伤害。','card')
    features=card_features(dict(entity_id=row['entity_id']),{row['entity_id']:row})
    assert features['damage']==0 and unmodeled(features)
    assert intrinsic_value(dict(entity_id=row['entity_id']),{row['entity_id']:row})['utility'] is None
    row['variants'][0]['description']='造成999点伤害。'
    assert card_features(dict(entity_id=row['entity_id']),{row['entity_id']:row})['damage']==0
    assert not compile_entry(row)['mechanism_defined']


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
    assert all(r['reference_only'] and r['utility'] is None and r['known_component_utility'] is None for r in report['entities'])
    assert len(card['origins'])==2
    packet=entity_models_packet(report)
    assert len(packet['entities'])==3 and all('clauses' not in r for r in packet['entities'])
    assert next(r for r in packet['entities'] if r['entity_id']=='RELIC.NEW')['utility'] is None
    json.dumps(packet,allow_nan=False)


def test_draw_restrictions_and_victory_healing_require_model_definitions():
    from autoslay.decision.unified import context_for
    from autoslay.evaluation.combat_resources import capabilities
    draw=entry('RELIC.DRAW_BAN_ALIAS','在每个回合开始时，额外抽2张牌。你在回合进行中不再能抽任何牌。')
    heal=entry('RELIC.BLOOD_ALIAS','在战斗结束时，回复12点生命。')
    entries={r['entity_id']:r for r in (draw,heal)}
    snapshot={'state':{'phase':'map','player':{'creature':dict(hp=40,max_hp=80),
        'relics':[dict(entity_id=draw['entity_id']),dict(entity_id=heal['entity_id'])]}}}
    assert not context_for(snapshot,dict(draw_blocked=False),entries).draw_blocked
    assert capabilities(snapshot,entries)['victory_heal']==0
    assert capabilities(snapshot,entries)['victory_heal_gaps']
    snapshot['state']['player']['deck']=[dict(entity_id='CARD.UNKNOWN',description='Heal 999 HP.')]
    assert capabilities(snapshot,entries)['healing_cards']==[]


@pytest.mark.local_knowledge
def test_every_local_catalog_variant_can_compile_and_evaluate_without_omission():
    from autoslay.knowledge_base.templates import index
    from autoslay.decision.entity_models import registry_summary
    entries=index()[0];summary=registry_summary()
    assert summary['entity_count']==len(entries)
    evaluations=0
    for identity,row in entries.items():
        profile=profile_for(dict(entity_id=identity),entries)
        assert profile and profile['entity_id']==identity
        expected=row.get('variants',[]) if row['category']=='card' else [row]
        assert len(profile['variants'])==len(expected)
        for variant in expected:
            result=entity_reference(dict(entity_id=identity,upgrade_level=variant.get('upgrade_level',0)),entries)
            assert result['coverage'] not in {'reference_missing','variant_missing'}
            json.dumps(result,ensure_ascii=False,allow_nan=False)
            evaluations+=1
    assert evaluations==sum(c['variants'] for c in summary['counts'].values())
    assert all(c['entities']==c['profiles'] for c in summary['counts'].values())
