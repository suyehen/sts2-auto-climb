from copy import deepcopy
from autoslay.dashboard.monster_graphs import project
from autoslay.knowledge_base.behavior import load


def snapshot():
    data=load()
    return dict(game_version=data['game_version'],game_assembly_sha256=data['game_assembly_sha256'],
                state=dict(phase='combat',ascension=10,player={'turn':1},enemies=[
                    dict(instance_id='leaf',entity_id='MONSTER.LEAF_SLIME_S',hp=14,block=0,
                         intents=[dict(type='Attack',attack_damage=4,attack_repeats=1)]),
                    dict(instance_id='twig',entity_id='MONSTER.TWIG_SLIME_M',hp=27,block=0,intents=[dict(type='StatusCard')]),
                    dict(instance_id='small',entity_id='MONSTER.TWIG_SLIME_S',hp=12,block=0,
                         intents=[dict(type='Attack',attack_damage=5,attack_repeats=1)])]))


def test_current_slimes_have_complete_graphs_and_known_next_moves_without_a_decision():
    s=snapshot();before=deepcopy(s);rows=project(s)
    leaf,twig,small=rows
    assert [len(r['nodes']) for r in rows]==[3,3,1]
    assert leaf['next_moves']==[dict(id='moveState2',probability=1)]
    assert twig['next_moves']==[dict(id='moveState',probability=1)]
    assert small['next_moves']==[dict(id='moveState',probability=1)]
    assert small['edges']==[dict(source='moveState',target='moveState',label='行动结束')]
    assert any('不可连续重复' in edge['label'] for edge in leaf['edges'])
    assert any('最多连续 2 次' in edge['label'] for edge in twig['edges'])
    assert s==before


def test_incomplete_history_keeps_all_rules_without_inventing_probabilities():
    s=snapshot();s['state']['player']['turn']=5
    enemy=s['state']['enemies'][1];enemy['current_move_id']='POKEY_POUNCE_MOVE'
    enemy['intents']=[dict(type='Attack',attack_damage=12,attack_repeats=1)]
    row=project(s)[1]
    assert len(row['nodes'])==3 and len(row['edges'])==4
    assert {m['id'] for m in row['next_moves']}=={'moveState','moveState2'}
    assert all(m['probability'] is None for m in row['next_moves'])
    enemy.update(move_history=['moveState','moveState'],history_complete=True)
    assert project(s)[1]['next_moves']==[dict(id='moveState2',probability=1)]


def test_rules_still_render_when_versions_mismatch_or_current_move_cannot_be_identified():
    s=snapshot();s['game_assembly_sha256']='other'
    row=project(s)[0];assert len(row['nodes'])==3 and not row['version_matches']
    assert row['next_moves']==[] and '其他游戏版本' in row['note']
    s=snapshot();s['state']['enemies'][0]['intents']=[]
    row=project(s)[0];assert len(row['nodes'])==3 and row['current'] is None
    assert '唯一定位' in row['note']


def test_missing_monsters_and_noncombat_have_specific_states():
    s=snapshot();s['state']['enemies'][0]['entity_id']='MONSTER.MISSING'
    assert '尚未收录' in project(s)[0]['note']
    s['state']['phase']='map';assert project(s)==[]


def test_all_random_branches_have_correct_conditional_weights():
    s=snapshot();enemy=s['state']['enemies'][1]
    enemy.update(current_move_id='POKEY_POUNCE_MOVE',intents=[dict(type='Attack',attack_damage=12,attack_repeats=1)])
    assert project(s)[1]['next_moves']==[dict(id='moveState',probability=.5),dict(id='moveState2',probability=.5)]


def test_conditional_graph_keeps_both_rules_and_rechecks_live_hp():
    data=deepcopy(load());entry=next(e for e in data['entries'] if e['entity_id']=='MONSTER.LEAF_SLIME_S')
    graph=entry['state_graph'];branch=graph['nodes'][2];branch['kind']='ConditionalBranchState'
    branch['branches']=[dict(target='moveState2',priority=0,condition_expression='base.Creature.CurrentHp <= base.Creature.MaxHp / 2'),
                        dict(target='moveState',priority=1,condition_expression='true')]
    s=snapshot();enemy=s['state']['enemies'][0];enemy.update(max_hp=30,current_move_id='TACKLE_MOVE',hp=20)
    row=project(s,data)[0];assert row['next_moves']==[dict(id='moveState',probability=1)]
    assert any('生命 <= 最大生命' in edge['label'] for edge in row['edges'])
    enemy['hp']=10;row=project(s,data)[0]
    assert row['next_moves']==[dict(id='moveState2',probability=1)]
    assert len(row['edges'])==4


def test_unmodeled_field_mutation_does_not_reuse_the_pre_action_condition():
    data=deepcopy(load());entry=next(e for e in data['entries'] if e['entity_id']=='MONSTER.LEAF_SLIME_S')
    entry['moves'][0]['state_updates']=[dict(variable='charged',value_expression='true')]
    branch=entry['state_graph']['nodes'][2];branch['kind']='ConditionalBranchState'
    branch['branches']=[dict(target='moveState2',priority=0,condition_expression='charged'),dict(target='moveState',priority=1,condition_expression='!charged')]
    s=snapshot();s['state']['enemies'][0].update(current_move_id='TACKLE_MOVE',runtime_state={'fields':{'charged':False}})
    row=project(s,data)[0]
    assert {m['id'] for m in row['next_moves']}=={'moveState','moveState2'}
    assert all(m['probability'] is None for m in row['next_moves'])
