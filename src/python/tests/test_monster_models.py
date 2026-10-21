"""Declarations drive predictions; code/evidence extraction is offline only."""
from copy import deepcopy
from dataclasses import replace
import json
import pytest
from autoslay.paths import ROOT
from autoslay.rules.model_store import load_models,validate_models
from autoslay.rules.monster_models import expression,validate_monsters
from autoslay.rules.monsters import MonsterRules
from autoslay.knowledge_base.monster_compiler import compile_expression,compile_monster
from autoslay.decision.contracts import Enemy,CombatState,UnsupportedEffect
from autoslay.rules.registry import IMPLEMENTATION_SHA256


def fixture():
    snapshot=dict(game_version='0.111.0',game_assembly_sha256=IMPLEMENTATION_SHA256,state={'ascension':0})
    enemy=Enemy('w','MONSTER.FUZZY_WURM_CRAWLER',55,0,0,None,(),uncertain_intents=('Buff',),move_state='INHALE')
    state=CombatState(70,80,0,3,(),(enemy,),(),turn=1,discard_known=True)
    return snapshot,state


def test_default_runtime_does_not_read_or_compile_source_evidence(monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('runtime must consume model data only')
    monkeypatch.setattr('autoslay.knowledge_base.behavior.load',forbidden)
    monkeypatch.setattr('autoslay.knowledge_base.monster_compiler.compile_monster',forbidden)
    snapshot,state=fixture();model=MonsterRules(snapshot,{})
    prepared=model.prepare(state);after=model.step(prepared)[0][1]
    assert after.enemies[0].strength==7 and after.enemies[0].attacks==(11,)
    assert dict(after.enemies[0].runtime_fields)=={'_isPuffed':True}


def test_json_composition_changes_default_monster_predictions_and_pins_existing_models(monkeypatch):
    snapshot,state=fixture();old=MonsterRules(snapshot,{})
    data=json.loads((ROOT/'data/knowledge/models/game-0.111.0.json').read_text('utf-8'))
    move=next(m for r in data['monsters'] if r['entity_id']=='MONSTER.FUZZY_WURM_CRAWLER' for m in r['moves'] if m['state_id']=='INHALE')
    move['commands'][-1]['amount']=9
    move['commands'].append(dict(operation='block',amount=12))
    changed=validate_models(data)
    monkeypatch.setattr('autoslay.rules.model_store.load_models',lambda:changed)
    model=MonsterRules(snapshot,{})
    after=model.step(model.prepare(state))[0][1]
    assert (after.enemies[0].strength,after.enemies[0].block,after.enemies[0].attacks)==(9,12,(13,))
    assert old.step(old.prepare(state))[0][1].enemies[0].strength==7
    assert changed.fingerprint!=load_models().fingerprint
    with pytest.raises(TypeError):changed.monsters['MONSTER.FUZZY_WURM_CRAWLER']['moves'][0]['commands'].clear()


@pytest.mark.parametrize('node,expected',[
    ({'divide':[5,2]},2),({'divide':[{'negate':[5]},2]},-2),
    ({'divide':[5,{'negate':[2]}]},-2),
    ({'if':[{'greater_equal':[{'ascension':True},9]},7,4]},7),
    ({'or':[True,{'field':'unseen'}]},True),
    ({'and':[False,{'unknown':'unimplemented'}]},False),
])
def test_structured_expression_order_and_csharp_integer_arithmetic(node,expected):
    assert expression(node,{},9)==expected


@pytest.mark.parametrize('node',[
    '__import__("os")',{'call':'anything'},{'field':'hp.__class__'},
    {'divide':[1,0]},{'unknown':'missing_rule'},{'field':'not_publicly_observed'},
])
def test_unsafe_or_missing_expression_never_becomes_zero(node):
    with pytest.raises(ValueError):expression(node,{})


def test_offline_compiler_produces_structured_ascension_and_preserves_unknown_calls():
    source='AscensionHelper.GetValueIfAscension(AscensionLevel.DeadlyEnemies, 5, 4)'
    node=compile_expression(source,{},dict(DeadlyEnemies=9))
    assert expression(node,{},8)==4 and expression(node,{},9)==5
    assert compile_expression('Random.Next(5)',{},{}).get('unknown')
    from autoslay.knowledge_base.behavior import load
    data=load();entry=next(e for e in data['entries'] if e['entity_id']=='MONSTER.FUZZY_WURM_CRAWLER')
    entry['moves'][2]['execution_evidence']+='\nawait UnexportedMutation();'
    result=compile_monster(entry,data['ascension_thresholds'],{})
    assert any('UnexportedMutation' in g for g in result['moves'][2]['gaps'])
    assert not any(k in json.dumps(result) for k in ('execution_evidence','condition_expression','value_expression'))


@pytest.mark.parametrize('change,match',[
    (lambda r:r['state_graph']['edges'][0].update(target='missing'),'edge'),
    (lambda r:r['moves'][0]['commands'][0].update(operation='invented'),'unimplemented'),
    (lambda r:r['moves'][0]['commands'][0].update(amount='5 + 7'),'structured'),
    (lambda r:r['moves'].append(deepcopy(r['moves'][0])),'duplicate'),
])
def test_invalid_monster_declarations_fail_atomically(change,match):
    models=load_models();record=deepcopy(models.monsters['MONSTER.FUZZY_WURM_CRAWLER'])
    change(record)
    with pytest.raises(ValueError,match=match):validate_monsters([record],models.model_references)


def test_unsupported_monster_move_is_visible_in_capability_and_inventory():
    from autoslay.actions import entity_capabilities
    from autoslay.knowledge_base.mechanism_audit import build_inventory
    result=entity_capabilities('MONSTER.FUZZY_WURM_CRAWLER')
    assert result['effects']['monster']['moves'][0]['commands']
    report=build_inventory()
    monster=next(r for r in report['rows'] if r['entity_id']=='MONSTER.FUZZY_WURM_CRAWLER')
    assert monster['definition_present'] and not monster['effect_definition_complete']
    assert 'semantic_review_pending' in monster['missing']
