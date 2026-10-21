"""Native pet mechanics share player damage, cards and turn forecasts."""
from dataclasses import replace
import pytest
from autoslay.decision.contracts import OstyState,Operation,Transition
from autoslay.rules.registry import KnowledgeRules
from autoslay.rules.statuses import add_stack,stack
from autoslay.rules.scheduled_relics import read_scheduled,before_hand_draw,after_hand_draw
from autoslay.simulation.atomic_effects import apply_atomic,damage_player,enemy_attack_hit
from autoslay.simulation.engine import CombatSimulator
from autoslay.evaluation.combat import CombatEvaluator
from test_persistent_mechanics import card,state,play


def test_summon_raises_max_hp_and_heals_existing_pet_and_revives_with_new_maximum():
    c=card('BODYGUARD');s=state(c)
    first=play(s,c)[1].state
    assert first.osty==OstyState(5,5)
    wounded=replace(s,osty=OstyState(2,8))
    assert play(wounded,c)[1].state.osty==OstyState(7,13)
    dead=replace(s,osty=OstyState(0,100))
    assert play(dead,c)[1].state.osty==OstyState(5,5)
    assert apply_atomic(s,'summon',0).osty is None


def test_spur_summons_before_healing_and_healing_never_revives_by_itself():
    c=card('SPUR');s=state(c,osty=OstyState(2,15))
    assert play(s,c)[1].state.osty==OstyState(10,18)
    dead=state(osty=OstyState(0,10))
    assert apply_atomic(dead,'heal_osty',100).osty==dead.osty


def test_block_then_pet_absorption_then_owner_hp_modifiers():
    s=state(block=3,osty=OstyState(5,5),hp_loss_reduction=1)
    after=enemy_attack_hit(s,'e',12)
    assert (after.block,after.osty.hp,after.hp)==(0,0,57)
    # Buffer belongs to the owner and is not consumed by the pet's HP loss.
    s=add_stack(state(osty=OstyState(20,20)),'buffer',1)
    after=enemy_attack_hit(s,'e',12)
    assert after.hp==60 and after.osty.hp==8 and stack(after,'buffer')==1
    after=enemy_attack_hit(after,'e',12)
    assert after.hp==60 and after.osty.hp==0 and stack(after,'buffer')==0


def test_intangible_caps_original_hit_before_pet_and_unpowered_damage_bypasses_pet():
    s=add_stack(state(osty=OstyState(8,8)),'intangible',1)
    after=enemy_attack_hit(s,'e',100)
    assert after.hp==60 and after.osty.hp==7
    after=damage_player(state(osty=OstyState(8,8)),9,self_inflicted=True)
    assert after.hp==51 and after.osty.hp==8


def test_pet_attack_uses_calcify_but_not_owner_strength_weak_vigor_or_envenom():
    c=card('POKE');s=state(c,osty=OstyState(10,10),base_strength=99,player_weak=True)
    for key,value in [('calcify',4),('vigor',5),('envenom',3)]:s=add_stack(s,key,value)
    after=play(s,c)[1].state
    assert after.enemies[0].hp==990 and after.enemies[0].poison==0
    assert stack(after,'vigor')==5


def test_pet_attacks_use_owner_cruelty_and_gigantification():
    c=card('POKE');s=state(c,osty=OstyState(10,10))
    s=replace(s,enemies=(replace(s.enemies[0],vulnerable=1),))
    s=add_stack(add_stack(s,'cruelty',25),'gigantification',1)
    after=play(s,c)[1].state
    assert after.enemies[0].hp==969 and stack(after,'gigantification')==0


def test_missing_pet_attack_is_playable_and_skips_attack_command_hooks():
    c=card('POKE');s=add_stack(state(c,osty_attack_block=2),'gigantification',1)
    after=play(s,c)[1].state
    assert after.enemies[0].hp==1000 and after.energy==5
    assert after.cards_played==1 and after.block==0 and stack(after,'gigantification')==1


def test_pet_damage_from_enemy_thorns_spends_shared_block_and_does_not_hurt_owner():
    c=card('POKE');s=state(c,block=2,osty=OstyState(1,1),osty_attack_block=2)
    s=replace(s,enemies=(add_stack(s.enemies[0],'thorns',9),))
    after=play(s,c)[1].state
    assert after.osty.hp==0 and after.hp==60 and after.block==2
    assert after.enemies[0].hp==994


def test_pet_hp_based_damage_uses_current_hp_and_max_hp():
    for identity,damage in [('UNLEASH',9),('PROTECTOR',22)]:
        c=card(identity);s=state(c,osty=OstyState(3,12))
        assert play(s,c)[1].state.enemies[0].hp==1000-damage


@pytest.mark.parametrize('energy',[0,1,3])
def test_dirge_x_repeats_summon_and_generates_matching_upgraded_souls(energy):
    c=card('DIRGE',1);after=play(state(c,energy=energy),c)[1].state
    assert after.energy==0
    if energy:
        assert after.osty==OstyState(energy*4,energy*4)
        assert sum(n for _,n in after.draw_composition)==energy
        assert after.generated_templates[0].upgrade_level==1
    else:assert after.osty is None and not after.draw_composition


def test_bound_phylactery_summons_before_draw_and_unbound_after_draw():
    s=state(turn=1,scheduled_relics=read_scheduled(dict(entity_id='RELIC.BOUND_PHYLACTERY'),1))
    assert before_hand_draw(s).osty is None  # observed opening is not replayed
    next_turn=before_hand_draw(replace(s,turn=2))
    assert next_turn.osty==OstyState(1,1)
    assert before_hand_draw(next_turn)==next_turn
    s=state(turn=2,scheduled_relics=read_scheduled(dict(entity_id='RELIC.PHYLACTERY_UNBOUND'),1))
    assert before_hand_draw(s).osty is None
    assert after_hand_draw(s).osty==OstyState(2,2)


def test_pet_state_affects_basic_combat_evaluation():
    s=state(osty=OstyState(20,20))
    assert CombatEvaluator().evaluate(s,Transition(s),())['projected_attack_hp_loss']==4


def test_observation_reads_pet_and_rejects_unmodeled_pet_powers():
    from test_deterministic import live
    from autoslay.decision.normalize import normalize
    snapshot=live();p=snapshot['state']['player']
    p['pets']=[dict(entity_id='MONSTER.OSTY',hp=3,max_hp=9,
                    powers=[dict(entity_id='POWER.DIE_FOR_YOU_POWER',amount=1)])]
    s=normalize(snapshot)
    assert s.osty==OstyState(3,9) and s.pets_known and not s.warnings
    p['pets'][0]['powers'].append(dict(entity_id='POWER.UNKNOWN_PET_BUFF',amount=1))
    assert 'UNMODELED_PET_POWER:POWER.UNKNOWN_PET_BUFF' in normalize(snapshot).warnings
    c=card('BODYGUARD');sim=CombatSimulator(state(c,pets_known=False))
    assert 'observed pets' in sim.unsupported[c.instance_id]


def test_devour_life_waits_for_soul_draw_to_finish_and_summons_once():
    c=card('SOUL');s=add_stack(state(c,draw_composition=(('CARD.SHIV',1),)),'devour_life',2)
    sim,t=play(s,c)
    assert t.boundary=='draw_requires_observation' and t.state.osty is None
    after=sim.resume_draw(t,t.state).state
    assert after.osty==OstyState(2,2) and after.cards_played==1
