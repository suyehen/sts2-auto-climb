"""Arithmetic and lifecycle checks for source-backed resource adapters."""
from dataclasses import replace
from types import SimpleNamespace
import pytest
from autoslay.decision.contracts import Card,CombatState,Enemy,Operation,Potion,UnsupportedEffect
from autoslay.rules.registry import KnowledgeRules,Effect,CardRule
from autoslay.simulation.engine import CombatSimulator
from autoslay.planning.lookahead import ScenarioRules
from autoslay.knowledge_base.rulebook import validate_rulebook
from autoslay.knowledge_base.source_audit import recognize_card,recognize_potion


def state(**changes):
    return replace(CombatState(40,79,0,3,(),(Enemy('e','MONSTER.TEST',100,0,0,None,(12,)),),(),
                               potions=(Potion('p','POTION.TEST','','use_p',('player',)),)),**changes)


def simulator(initial,effects):
    class Rules:
        def potion(self,p):return SimpleNamespace(effects=tuple(effects),target_type='AnyPlayer')
        def card(self,c,s):return CardRule((Effect('block',5),))
    return CombatSimulator(initial,Rules())


def test_heal_percentage_rounding_capping_and_trajectory_counter():
    s=state(hp=40,healing=2)
    result=simulator(s,[Effect('heal_percent',20)]).apply(s,Operation('use_p','player'))
    assert result.state.hp==55 and result.state.healing==17 and not result.state.potions
    full=replace(s,hp=78)
    result=simulator(full,[Effect('heal_percent',20)]).apply(full,Operation('use_p','player'))
    assert result.state.hp==79 and result.state.healing==3


def test_max_hp_gain_heals_only_actual_increase_and_respects_game_cap():
    s=state()
    result=simulator(s,[Effect('gain_max_hp',5)]).apply(s,Operation('use_p','player'))
    assert (result.state.max_hp,result.state.hp,result.state.healing)==(84,45,5)
    capped=replace(s,max_hp=999999998)
    result=simulator(capped,[Effect('gain_max_hp',5)]).apply(capped,Operation('use_p','player'))
    assert (result.state.max_hp,result.state.hp,result.state.healing)==(999999999,41,1)


def test_stars_enable_a_real_card_and_are_spent_once():
    card=Card('c','CARD.TEST',1,'Self','',star_cost=5)
    s=state(hand=(card,),stars=2);sim=simulator(s,[Effect('stars',3)])
    assert Operation('play_c') not in sim.actions(s)
    after=sim.apply(s,Operation('use_p','player')).state
    assert after.stars==5 and Operation('play_c') in sim.actions(after)
    after=sim.apply(after,Operation('play_c')).state
    assert after.stars==0 and after.energy==2 and not after.potions


def test_dexterity_changes_powered_card_block_and_never_unpowered_block():
    card=Card('c','CARD.TEST',1,'Self','')
    s=state(hand=(card,));sim=simulator(s,[Effect('dexterity',2),Effect('unpowered_block',12)])
    after=sim.apply(s,Operation('use_p','player')).state
    assert after.block==12 and after.dexterity_delta==2
    assert sim.apply(after,Operation('play_c')).state.block==19


def test_observed_frail_block_does_not_guess_pre_rounding_base():
    card=Card('c','CARD.TEST',1,'Self','')
    s=state(hand=(card,),player_frail=True,dexterity_delta=2)
    result=simulator(s,[]).apply(s,Operation('play_c'))
    assert result.boundary=='frail_dexterity_rounding_requires_observation'


def test_canonical_block_adds_base_and_new_dexterity_before_frail_rounding():
    raw=Card('c','CARD.TEST',1,'Self','Gain 5 Block.',canonical=True)
    book=SimpleNamespace(references={'CARD.TEST':{'variants':[dict(upgrade_level=0,dynamic_variables=[])]}},
        require=lambda i,c:dict(effects=[dict(operation='block',amount=dict(visible='block'))]))
    s=state(hand=(raw,),base_dexterity=1,dexterity_delta=2,player_frail=True)
    result=CombatSimulator(s,ScenarioRules(book)).apply(s,Operation('play_c'))
    assert result.state.block==6


def test_weak_reduces_each_visible_hit_once_and_extends_duration_without_reducing_twice():
    s=state(enemies=(replace(state().enemies[0],attacks=(13,8)),))
    class Rules:
        def potion(self,p):return SimpleNamespace(effects=(Effect('weak',3),),target_type='AnyEnemy')
    s=replace(s,potions=(Potion('p','POTION.TEST','','use_p',('e',)),))
    first=CombatSimulator(s,Rules()).apply(s,Operation('use_p','e')).state
    assert first.enemies[0].attacks==(9,6) and first.enemies[0].weak_turns==3
    again=replace(first,potions=s.potions)
    second=CombatSimulator(again,Rules()).apply(again,Operation('use_p','e')).state
    assert second.enemies[0].attacks==(9,6) and second.enemies[0].weak_turns==6


def test_temporary_dexterity_and_no_draw_expire_before_next_hand_without_losing_permanent_ban():
    from autoslay.rules.monsters import MonsterRules
    model=object.__new__(MonsterRules)
    for permanent,expected in [(False,False),(True,True)]:
        s=state(enemies=(),base_dexterity=7,dexterity_delta=3,temporary_dexterity=5,
            effect_draw_blocked=True,temporary_draw_blocked=True,permanent_draw_blocked=permanent)
        _,after,_=model.step(s)[0]
        assert after.base_dexterity+after.dexterity_delta==5
        assert after.temporary_dexterity==0 and after.temporary_draw_blocked is False
        assert after.effect_draw_blocked is expected


def test_variable_bindings_are_integral_unique_and_never_executable_expressions():
    rules=KnowledgeRules(SimpleNamespace())
    rule=dict(effects=[dict(operation='stars',amount=dict(variable='Stars'))])
    assert rules.effects(rule,'',variables=[dict(name='Stars',base_value=3)])[0].amount==3
    for variables in ([],[dict(name='Stars',base_value=.5)],
                      [dict(name='Stars',base_value=2),dict(name='Stars',base_value=2)],
                      [dict(name='Stars',base_value='__import__("os")')]):
        with pytest.raises(UnsupportedEffect):rules.effects(rule,'',variables=variables)
    candidate=dict(schema_version='rules/1.0',game_version='test',entries={'CARD.TEST':dict(
        category='card',reference_sha256='x',validation='pending_review',effects=[
            dict(operation='stars',amount=dict(variable='Stars'))])})
    validate_rulebook(candidate)
    candidate['entries']['CARD.TEST']['effects'][0]['amount']={'variable':'x.__class__'}
    with pytest.raises(ValueError):validate_rulebook(candidate)


def test_observed_variable_values_override_catalog_and_unknown_instance_mechanics_stop():
    book=SimpleNamespace(references={'POTION.TEST':dict(dynamic_variables=[dict(name='Stars',base_value=3)]),
        'CARD.TEST':dict(variants=[dict(upgrade_level=0,dynamic_variables=[])])},
        require=lambda identity,category:dict(effects=[dict(operation='stars',amount=dict(variable='Stars'))],target_type='AnyPlayer'))
    rules=KnowledgeRules(book)
    potion=Potion('p','POTION.TEST','','use_p',('player',),dynamic_variables=(('Stars',7),))
    assert rules.potion(potion).effects[0].amount==7
    for kwargs in [dict(affliction_id='AFFLICTION.UNKNOWN'),dict(enchantment_id='ENCHANTMENT.UNKNOWN')]:
        with pytest.raises(UnsupportedEffect):rules.card(Card('c','CARD.TEST',1,'Self','',**kwargs),state())


def test_source_grammar_rejects_partial_healing_and_wrong_target_or_extra_hook():
    source='''public override TargetType TargetType => TargetType.AnyPlayer;
    protected override async Task OnUse(PlayerChoiceContext choiceContext, Creature? target) {
      PotionModel.AssertValidForTargetedPotion(target);
      await CreatureCmd.Heal(target, (decimal)target.MaxHp * base.DynamicVars["HealPercent"].BaseValue / 100m);
    }'''
    assert recognize_potion(source)[0]['effects'][0]['operation']=='heal_percent'
    assert recognize_potion(source.replace('/ 100m','/ 50m'))[0] is None
    assert recognize_potion(source.replace('TargetType.AnyPlayer','TargetType.AnyEnemy'))[0] is None
    assert recognize_potion(source+' public override Task AfterUse() {}')[0] is None


def test_source_grammar_preserves_attack_builder_order_and_dynamic_repeat_binding():
    source='''protected override async Task OnPlay(PlayerChoiceContext choiceContext, CardPlay cardPlay) {
      await DamageCmd.Attack(base.DynamicVars.Damage.BaseValue).WithHitCount(base.DynamicVars.Repeat.IntValue).FromCard(this, cardPlay).Targeting(cardPlay.Target).Execute(choiceContext);
    }'''
    recognized=recognize_card(source)[0]
    assert recognized['effects'][0]['repeats']=={'variable':'Repeat'}
    assert recognize_card(source.replace('.FromCard(this, cardPlay)','.FromCard(other, cardPlay)'))[0] is None
    assert recognize_card(source.replace('.Execute(choiceContext)','.WithUnknownModifier().Execute(choiceContext)'))[0] is None


@pytest.mark.local_knowledge
def test_all_new_source_programs_match_inventory_and_execute_every_exported_variant():
    from autoslay.knowledge_base.rulebook import Rulebook
    from autoslay.knowledge_base.source_audit import audit,SOURCE
    from autoslay.knowledge_base.repository import read_json,dataset_path
    from autoslay.knowledge_base.templates import canonical
    import hashlib
    book=Rulebook();report,_=audit(book.catalog,book.data)
    inventory=read_json(dataset_path('source_inventory'))
    assert inventory['game_assembly_sha256']==book.catalog['game_assembly_sha256']
    sources={e['entity_id']:e for e in inventory['entries']}
    modeled={r['entity_id']:r['recognized_sequence'] for r in report['rows'] if r.get('recognized_sequence')}
    def require(identity,category):
        assert book.references[identity]['category']==category
        return modeled[identity]
    rules=KnowledgeRules(SimpleNamespace(references=book.references,require=require))
    checked=0
    for row in report['rows']:
        if not row.get('recognized_sequence') or row['category']=='relic':continue
        identity=row['entity_id'];entry=book.references[identity]
        namespace,classname=entry['model_type'].rsplit('.',1)
        assert hashlib.sha256((SOURCE/namespace/(classname+'.cs')).read_bytes()).hexdigest()==sources[identity]['source_sha256']
        if entry['category']=='card':
            for variant in entry['variants']:
                card=canonical(dict(entity_id=identity,instance_id='c',upgrade_level=variant['upgrade_level']),book.references)[0]
                initial=state(hp=50,max_hp=80,hand=(card,),stars=10,energy=10,potions=(),
                    draw_composition=(('CARD.STRIKE_IRONCLAD',7),),discard_known=True)
                sim=CombatSimulator(initial,rules)
                assert not sim.unsupported,(identity,variant['upgrade_level'],sim.unsupported)
                action=Operation('play_c','e' if card.target_type=='AnyEnemy' else None)
                result=sim.apply(initial,action)
                assert result.state.energy>=10-card.cost and result.state.stars>=10-max(0,card.star_cost)
                if identity=='CARD.BATTLE_TRANCE':
                    resumed=sim.resume_draw(result,result.state)
                    assert resumed.state.effect_draw_blocked and resumed.state.temporary_draw_blocked
                checked+=1
        else:
            target='e' if modeled[identity]['target_type']=='AnyEnemy' else 'player' if modeled[identity]['target_type']=='AnyPlayer' else None
            initial=state(potions=(Potion('p',identity,entry['description'],'use_p',() if target is None else (target,)),),
                draw_composition=(('CARD.STRIKE_IRONCLAD',7),),discard_known=True)
            result=CombatSimulator(initial,rules).apply(initial,Operation('use_p',target))
            assert not result.state.potions
            checked+=1
    assert checked>0
