"""Shared native mechanics through real cards, forecasts and continuations."""
from dataclasses import replace
import pytest
from autoslay.decision.contracts import CombatState,Enemy,Operation,Transition,Potion
from autoslay.knowledge_base.templates import canonical,index
from autoslay.rules.registry import KnowledgeRules,ENTITY_MECHANICS
from autoslay.rules.statuses import stack,add_stack,apply_player_phase,enemy_poison_start,after_enemy_side,preserve_block
from autoslay.simulation.engine import CombatSimulator
from autoslay.simulation.card_flow import flush_hand
from autoslay.simulation.atomic_effects import damage_player,lose_player_hp,visible_enemy_attacks
from autoslay.evaluation.combat import CombatEvaluator


ENTRIES=index()[0]


def card(identity,level=0,instance=None):
    return replace(canonical(dict(entity_id='CARD.'+identity,upgrade_level=level),ENTRIES)[0],
                   instance_id=instance or identity,canonical=True)


def state(*cards,**kwargs):
    return replace(CombatState(60,80,0,5,tuple(cards),
        (Enemy('e','MONSTER.TEST',1000,0,0,None,(12,12)),),(),discard_known=True,turn=1,stars=10),**kwargs)


def play(s,c,target='e'):
    sim=CombatSimulator(s)
    op=Operation('play_'+c.instance_id,target if sim.target_type(c,s)=='AnyEnemy' else None)
    return sim,sim.apply(s,op)


def test_every_native_variant_binds_against_the_actual_catalog():
    rules=KnowledgeRules();s=state(x_value=3,star_x_value=2)
    for identity,mechanic in ENTITY_MECHANICS.items():
        if mechanic.category=='card':
            for variant in ENTRIES[identity]['variants']:
                rules.card(card(identity.removeprefix('CARD.'),variant['upgrade_level']),s)
        elif mechanic.category=='potion':
            rules.potion(Potion('p',identity,ENTRIES[identity]['description'],'use_p',(),True))


def test_vigor_applies_to_each_hit_and_is_spent_once():
    c=card('DAGGER_SPRAY');s=add_stack(state(c),'vigor',4)
    after=play(s,c)[1].state
    assert after.enemies[0].hp==984 and stack(after,'vigor')==0


def test_flat_attack_modifiers_combine_before_clamping_negative_strength():
    c=card('SHIV');s=add_stack(state(c,base_strength=-5),'vigor',2)
    assert play(s,c)[1].state.enemies[0].hp==999
    s=add_stack(state(c,base_strength=-5),'accuracy',2)
    assert play(s,c)[1].state.enemies[0].hp==999


@pytest.mark.parametrize('energy',[0,1,4])
def test_x_uses_all_energy_and_zero_x_still_finishes_the_card(energy):
    c=card('WHIRLWIND');s=state(c,energy=energy)
    after=play(s,c)[1].state
    assert after.energy==0 and after.enemies[0].hp==1000-5*energy
    assert dict(after.discard_composition)['CARD.WHIRLWIND']==1


def test_afterimage_uses_before_play_amount_and_continuation_fires_once():
    power=card('AFTERIMAGE');s=state(power)
    assert play(s,power)[1].state.block==0
    assert play(add_stack(s,'afterimage',2),power)[1].state.block==2
    draw=card('SOUL');s=add_stack(state(draw,draw_composition=(('CARD.SHIV',1),)),'afterimage',3)
    sim,t=play(s,draw)
    assert t.boundary=='draw_requires_observation' and t.state.block==0
    after=sim.resume_draw(t,t.state).state
    assert after.block==3 and after.cards_played==1


def test_artifact_blocks_each_negative_power_in_source_order():
    c=card('COMET');enemy=add_stack(state().enemies[0],'artifact',1)
    after=play(state(c,enemies=(enemy,)),c)[1].state.enemies[0]
    assert stack(after,'artifact')==0 and after.weak_turns==0 and after.vulnerable>0


def test_intangible_caps_block_loss_and_buffer_only_spends_on_positive_hp_loss():
    s=add_stack(add_stack(state(block=2),'intangible',1),'buffer',1)
    first=damage_player(s,100)
    assert first.hp==60 and first.block==1 and stack(first,'buffer')==1
    second=damage_player(damage_player(first,100),100)
    assert second.hp==60 and second.block==0 and stack(second,'buffer')==0
    reduced=add_stack(state(hp_loss_reduction=1),'buffer',1)
    assert stack(lose_player_hp(reduced,1),'buffer')==1


def test_thorns_are_blockable_and_retaliation_stops_later_hits():
    c=card('DAGGER_SPRAY');enemy=add_stack(state().enemies[0],'thorns',3)
    after=play(state(c,block=5,enemies=(enemy,)),c)[1].state
    assert after.block==0 and after.hp==59
    enemy=replace(state().enemies[0],hp=3)
    s=add_stack(state(enemies=(enemy,)),'thorns',3)
    forecast=visible_enemy_attacks(s)
    assert forecast.combat_won and forecast.hp==48
    metrics=CombatEvaluator().evaluate(s,Transition(s),())
    assert metrics['projected_attack_hp_loss']==12


def test_basic_evaluator_respects_buffer_and_intangible():
    s=add_stack(add_stack(state(),'intangible',1),'buffer',1)
    assert CombatEvaluator().evaluate(s,Transition(s),())['projected_attack_hp_loss']==1


def test_accelerant_ticks_decreasing_poison_without_consuming_block():
    enemy=replace(state().enemies[0],poison=4,block=100)
    s=add_stack(state(enemies=(enemy,)),'accelerant',2)
    after=enemy_poison_start(s).enemies[0]
    assert (after.hp,after.block,after.poison)==(991,100,1)


def test_doom_is_checked_before_demise_and_does_not_trigger_early():
    enemy=add_stack(add_stack(replace(state().enemies[0],hp=10),'doom',9),'demise',2)
    after=after_enemy_side(state(enemies=(enemy,)))
    assert after.enemies[0].hp==8 and not after.combat_won
    assert after_enemy_side(after).combat_won


def test_blur_and_global_retain_expire_after_the_correct_phase():
    s=add_stack(add_stack(state(card('DEFEND_IRONCLAD'),block=20),'blur',1),'retain_hand',1)
    held=flush_hand(s,KnowledgeRules())
    assert len(held.hand)==1 and preserve_block(held)
    ended=apply_player_phase(held,'side_turn_end_after')
    assert stack(ended,'retain_hand')==0 and preserve_block(ended)
    started=apply_player_phase(replace(ended,turn=2),'side_turn_start')
    assert not preserve_block(started)


def test_feel_no_pain_fires_for_played_forced_and_ethereal_exhaustion():
    shiv=card('SHIV');s=add_stack(state(shiv),'feel_no_pain',3)
    assert play(s,shiv)[1].state.block==3
    wind=card('SECOND_WIND');held=card('DAZED');s=add_stack(state(wind,held),'feel_no_pain',3)
    after=play(s,wind)[1].state
    assert after.block==8 and after.cards_exhausted==1
    s=add_stack(state(held),'feel_no_pain',3)
    after=flush_hand(s,KnowledgeRules())
    assert after.block==3 and after.cards_exhausted==1
    assert flush_hand(after,KnowledgeRules())==after


def test_new_doubt_debuff_skips_its_first_duration_tick_and_artifact_prevents_it():
    s=state(card('DOUBT'))
    after=flush_hand(s,KnowledgeRules())
    assert after.weak_turns==1 and after.weak_skip_next_tick
    blocked=flush_hand(add_stack(s,'artifact',1),KnowledgeRules())
    assert blocked.weak_turns==0 and stack(blocked,'artifact')==0
    existing=flush_hand(replace(s,weak_turns=2),KnowledgeRules())
    assert existing.weak_turns==3 and not existing.weak_skip_next_tick


def test_fan_of_knives_changes_existing_and_generated_shiv_targets():
    power=card('FAN_OF_KNIVES');shiv=card('SHIV')
    sim,t=play(state(power,shiv,enemies=(state().enemies[0],replace(state().enemies[0],instance_id='e2'))),power)
    after=sim.resume_generated(t).state
    assert Operation('play_SHIV') in sim.actions(after)
    assert Operation('play_SHIV','e') not in sim.actions(after)
    hit=sim.apply(after,Operation('play_SHIV')).state
    assert [e.hp for e in hit.enemies]==[996,996]


def test_generated_souls_keep_upgrade_and_public_draw_composition():
    c=card('REAVE',1);after=play(state(c),c)[1].state
    generated=after.generated_templates[0]
    assert generated.entity_id=='CARD.SOUL' and generated.upgrade_level==1
    assert dict(after.draw_composition)[generated.variant_key]==1
    c=card('SEVERANCE');sim,t=play(state(c),c)
    assert t.boundary=='generated_cards_requires_observation'
    after=sim.resume_generated(t).state
    assert len(after.hand)==1 and sum(n for _,n in after.draw_composition)==1
    assert sum(n for _,n in after.discard_composition)==2


def test_temporary_enemy_strength_preserves_origin_and_artifact_prevents_it():
    c=card('DARK_SHACKLES');s=state(c)
    after=play(s,c)[1].state.enemies[0]
    assert after.strength==-9 and after.attacks==(3,3)
    assert after.temporary_strength_loss==9 and stack(after,'dark_shackles')==9
    blocked=play(state(c,enemies=(add_stack(s.enemies[0],'artifact',1),)),c)[1].state.enemies[0]
    assert blocked.strength==0 and blocked.temporary_strength_loss==0


def test_fiend_fire_counts_removed_hand_and_all_exhaustions_trigger_shared_hooks():
    c=card('FIEND_FIRE');s=add_stack(state(c,card('DAZED'),card('WOUND')),'feel_no_pain',3)
    after=play(s,c)[1].state
    assert after.enemies[0].hp==986 and after.block==9
    assert after.cards_exhausted==3 and not after.hand


def test_history_based_cards_use_observed_counts_and_refuse_unknown_history():
    c=card('FINISHER');s=state(c,attacks_played=3)
    assert play(s,c)[1].state.enemies[0].hp==982
    sim=CombatSimulator(replace(s,history_known=False))
    assert 'observed combat history' in sim.unsupported[c.instance_id]
    c=card('FTL');s=state(c,cards_played=3,draw_composition=(('CARD.SHIV',1),))
    assert play(s,c)[1].boundary is None
    assert play(replace(s,cards_played=2),c)[1].boundary=='draw_requires_observation'


def test_effect_draw_counter_excludes_opening_hand_draw():
    from autoslay.simulation.card_flow import apply_drawn_hooks
    c=card('SHIV');s=state()
    assert apply_drawn_hooks(s,[c],KnowledgeRules()).turn_cards_drawn==1
    assert apply_drawn_hooks(s,[c],KnowledgeRules(),from_hand_draw=True).turn_cards_drawn==0


def test_timed_resource_stacks_extend_duration_instead_of_magnitude():
    from autoslay.rules.statuses import hand_draw_bonus
    s=add_stack(add_stack(state(),'clarity',3),'radiance',3)
    for turn in range(2,5):
        s=replace(s,turn=turn,energy=3)
        assert hand_draw_bonus(s)==1
        s=apply_player_phase(s,'after_energy_reset')
        assert s.energy==4
        s=apply_player_phase(s,'side_turn_start')
    assert hand_draw_bonus(s)==0 and stack(s,'radiance')==0
    assert apply_player_phase(replace(s,turn=5,energy=3),'after_energy_reset').energy==3


def test_gigantification_multiplies_one_command_and_decrements_one_charge():
    c=card('DAGGER_SPRAY');s=add_stack(state(c),'gigantification',2)
    after=play(s,c)[1].state
    assert after.enemies[0].hp==976 and stack(after,'gigantification')==1


def test_dynamic_scheduled_relic_binds_at_the_phase_and_fires_once():
    from autoslay.rules.scheduled_relics import read_scheduled,apply_phase
    tracked=read_scheduled(dict(entity_id='RELIC.CLOAK_CLASP'),1)
    s=state(card('SHIV'),card('DAZED'),scheduled_relics=tracked)
    assert tracked[0].amount is None
    after=apply_phase(s,'side_turn_end')
    assert after.block==2 and apply_phase(after,'side_turn_end')==after
    tracked=read_scheduled(dict(entity_id='RELIC.MR_STRUGGLES'),1)
    assert apply_phase(state(turn=3,scheduled_relics=tracked),'after_hand_draw').enemies[0].hp==997


def test_discard_exhaust_and_shuffle_relics_share_card_flow():
    c=card('CALCULATED_GAMBLE');s=state(c,card('SHIV'),card('DAZED'),discard_block=3)
    t=play(s,c)[1]
    assert t.state.block==6 and t.boundary=='draw_requires_observation'
    # Ordinary end-turn flush does not emit CardCmd.Discard hooks.
    assert flush_hand(state(card('SHIV'),discard_block=3),KnowledgeRules()).block==0
    c=card('FIEND_FIRE');s=state(c,card('SHIV'),exhaust_damage=3)
    assert play(s,c)[1].state.enemies[0].hp==987
    c=card('REBOOT');s=state(c,card('SHIV'),shuffle_block=6)
    assert play(s,c)[1].state.block==6


def test_normalization_reads_public_history_and_new_relic_mechanics():
    from test_deterministic import live
    from autoslay.decision.normalize import normalize
    snapshot=live();p=snapshot['state']['player']
    fields=dict(cards_played=4,attacks_played=2,skills_played=1,combat_cards_played=9,
                cards_exhausted=5,turn_cards_exhausted=1,turn_cards_drawn=3)
    p['combat_counters']=fields
    p['relics']=[dict(entity_id=i) for i in ('RELIC.RUNIC_PYRAMID','RELIC.ICE_CREAM','RELIC.PAELS_BLOOD','RELIC.TOUGH_BANDAGES')]
    s=normalize(snapshot)
    assert s.history_known and s.attacks_played==2 and s.combat_cards_played==9
    assert s.retain_hand_relic and s.retain_energy and s.extra_hand_draw==1 and s.discard_block==3
    del p['combat_counters']
    assert not normalize(snapshot).history_known


def test_native_x_templates_survive_public_pile_reconstruction():
    from autoslay.planning.lookahead import templates_for
    c=card('WHIRLWIND')
    raw=dict(entity_id=c.entity_id,upgrade_level=0,costs_x=True,energy_cost=c.cost,
             base_energy_cost=c.cost,star_cost=c.star_cost)
    templates=templates_for(dict(state=dict(player=dict(deck=[raw]))),ENTRIES)
    assert templates[c.entity_id].costs_x


def test_permafrost_uses_observed_once_per_combat_state_and_never_replays():
    from autoslay.rules.triggered_relics import read_triggered
    from autoslay.decision.contracts import UnsupportedEffect
    relic=dict(entity_id='RELIC.PERMAFROST',runtime_state=dict(fields={'_activatedThisCombat':False}))
    tracked=read_triggered(relic,1)
    a=card('AFTERIMAGE');b=card('BARRICADE');s=state(a,b,triggered_relics=tracked)
    after=play(s,a)[1].state
    assert after.block==7 and after.triggered_relics[0].remaining_triggers==0
    after=play(after,b)[1].state
    assert after.block==8  # only Afterimage now fires
    relic['runtime_state']['fields']['_activatedThisCombat']=True
    assert read_triggered(relic,1)[0].remaining_triggers==0
    with pytest.raises(UnsupportedEffect):read_triggered(dict(entity_id='RELIC.PERMAFROST'),1)


def test_exhaustion_damage_victory_stops_later_card_exhaustions():
    c=card('FIEND_FIRE');a=card('SHIV');b=card('WOUND')
    s=state(c,a,b,exhaust_damage=3)
    s=replace(s,enemies=(replace(s.enemies[0],hp=3),))
    after=play(s,c)[1].state
    assert after.combat_won and after.cards_exhausted==1
    assert tuple(x.entity_id for x in after.hand)==('CARD.WOUND',)


def test_spirit_of_ash_fires_before_card_effects_using_existing_power_only():
    c=replace(card('APPARITION'),keywords=('Ethereal','Exhaust'))
    s=add_stack(state(c),'spirit_of_ash',4)
    assert play(s,c)[1].state.block==4
    c=replace(card('SPIRIT_OF_ASH'),keywords=('Ethereal',))
    assert play(state(c),c)[1].state.block==0


def test_fiddle_blocks_effect_draw_but_allows_opening_draw_and_adds_two_cards():
    from test_deterministic import live
    from autoslay.decision.normalize import normalize
    from autoslay.planning.lookahead import draw_branches
    from autoslay.rules.statuses import hand_draw_bonus
    snapshot=live();snapshot['state']['player']['relics']=[dict(entity_id='RELIC.FIDDLE'),dict(entity_id='RELIC.PAELS_BLOOD')]
    observed=normalize(snapshot)
    assert observed.effect_draw_blocked and hand_draw_bonus(observed)==3
    s=state(draw_composition=(('CARD.SHIV',8),),effect_draw_blocked=True)
    effect=draw_branches(s,5,{'CARD.SHIV':card('SHIV')},8,1)[0][0][1]
    assert not effect.hand
    opening=draw_branches(s,5,{'CARD.SHIV':card('SHIV')},8,1,rules=KnowledgeRules(),from_hand_draw=True)[0][0][1]
    assert len(opening.hand)==5 and opening.turn_cards_drawn==0


def test_artifact_can_keep_temporary_stats_per_source_and_in_application_order():
    from autoslay.rules.statuses import expire_temporary_stats
    # Speed was applied before Flex. One Artifact preserves Speed's Dexterity.
    s=state(dexterity_delta=5,temporary_dexterity=5,strength_delta=3,temporary_strength=3)
    s=add_stack(add_stack(add_stack(s,'speed_potion',5),'flex_potion',3),'artifact',1)
    after=expire_temporary_stats(s)
    assert after.dexterity_delta==5 and after.strength_delta==0
    assert after.temporary_dexterity==after.temporary_strength==0
    assert stack(after,'artifact')==stack(after,'speed_potion')==stack(after,'flex_potion')==0
    assert expire_temporary_stats(after)==after
    reverse=state(dexterity_delta=5,temporary_dexterity=5,strength_delta=3,temporary_strength=3)
    reverse=add_stack(add_stack(add_stack(reverse,'flex_potion',3),'speed_potion',5),'artifact',1)
    after=expire_temporary_stats(reverse)
    assert after.strength_delta==3 and after.dexterity_delta==0


def test_setup_strike_exposes_its_real_power_and_expiry_does_not_double_subtract():
    c=card('SETUP_STRIKE');s=state(c)
    after=play(s,c)[1].state
    assert stack(after,'setup_strike')==3 and after.temporary_strength==3
    assert stack(after,'flex_potion')==0
    expired=apply_player_phase(after,'side_turn_end_after')
    assert expired.strength_delta==0 and expired.temporary_strength==0
    assert apply_player_phase(expired,'side_turn_end_after')==expired


def test_molten_fist_vulnerable_increase_is_stopped_by_artifact():
    c=card('MOLTEN_FIST');s=state(c)
    enemy=add_stack(replace(s.enemies[0],vulnerable=2),'artifact',1)
    after=play(replace(s,enemies=(enemy,)),c)[1].state.enemies[0]
    assert after.vulnerable==2 and stack(after,'artifact')==0


def test_max_hp_loss_clamps_hp_and_updates_the_resource_ledger_after_buffer():
    c=card('BRIGHTEST_FLAME');s=add_stack(state(c,hp=80,max_hp=80),'buffer',1)
    after=play(s,c)[1].state
    assert after.max_hp<80 and after.hp==after.max_hp
    assert after.self_hp_loss==80-after.hp and stack(after,'buffer')==0
