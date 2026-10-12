from copy import deepcopy
from autoslay.knowledge.behavior import load,relevant,query
from autoslay.agent.context import build
from autoslay.knowledge.behavior_graph import graph


def entry(identity):
    return next(e for e in load()['entries'] if e['entity_id']==identity)


def test_frog_queen_branches_state_updates_and_ascension():
    frog=entry('MONSTER.FROG_KNIGHT')
    branch=next(n for n in frog['state_graph']['nodes'] if n['kind']=='ConditionalBranchState')
    assert [b['target'] for b in branch['branches']]==['moveState3','moveState4']
    assert branch['branches'][1]['condition_expression']=='!HasBeetleCharged && base.Creature.CurrentHp < base.Creature.MaxHp / 2'
    charge=next(m for m in frog['moves'] if m['state_id']=='BEETLE_CHARGE')
    assert {'variable':'HasBeetleCharged','value_expression':'true'} in charge['state_updates']
    assert [b['effects'][0]['amount'] for b in frog['initial_effects']]==[15,19]
    assert all(len(b['effects'])==1 for b in frog['initial_effects'])
    assert [b['min_hp'] for b in frog['health_variants']]==[191,199]


def test_chained_random_followups_and_repeat_overloads():
    fossil=entry('MONSTER.FOSSIL_STALKER')
    assert len(fossil['transitions'])==3
    node=next(n for n in fossil['state_graph']['nodes'] if n['kind']=='RandomBranchState')
    assert len(node['branches'])==3
    assert all(b['max_consecutive']==2 and b['weight_expression']=='1' for b in node['branches'])
    rat=entry('MONSTER.TWO_TAILED_RAT')
    random=next(n for n in rat['state_graph']['nodes'] if n['kind']=='RandomBranchState')
    assert any(b['cooldown']==3 for b in random['branches'])
    assert any(b['repeat_type']=='UseOnlyOnce' and 'CanSummon()' in b['weight_expression'] for b in random['branches'])


def test_initial_hp_uses_nullable_field_defaults_and_keeps_respawn_formula():
    axebot=entry('MONSTER.AXEBOT')
    assert [(b['min_hp'],b['max_hp']) for b in axebot['health_variants']]==[(70,78),(76,86)]
    assert axebot['health_parameter_expressions']['RespawnMaxHpBonus']=='RespawnCount * 10'
    assert all(f['name']!='RespawnMaxHpBonus' for f in axebot['state_graph']['state_fields'])


def test_construction_guards_and_inherited_graphs():
    nibbit=entry('MONSTER.NIBBIT')
    node=next(n for n in nibbit['state_graph']['nodes'] if n['kind']=='ConditionalBranchState')
    assert node['branches'][0]['registration_conditions']==['_isAlone']
    assert node['branches'][1]['registration_conditions']==['!(_isAlone)']
    for e in load()['entries']:
        if e['category']=='monster':assert not e['state_graph']['unresolved_targets']


def test_event_card_resolution_and_narrative_free_nonmutating_context():
    data=load();event=entry('EVENT.AMALGAMATOR')
    assert event['placeholder_entities']['Card1']['entity_id']=='CARD.ULTIMATE_STRIKE'
    assert all('{Card' not in o['description'] and o.get('execution_evidence') for o in event['options'])
    snap=dict(game_version=data['game_version'],game_assembly_sha256=data['game_assembly_sha256'],
              state={'phase':'event','event_screen':{'entity_id':event['entity_id'],'description':'剧情内容','options':[]}})
    before=deepcopy(snap)
    context=build(snap,None)
    assert snap==before
    assert 'description' not in context['visible_state']['state']['event_screen']
    assert 'description' not in context['behavior_references']['entries'][0]
    assert {'CARD.ULTIMATE_STRIKE','CARD.ULTIMATE_DEFEND'} <= {e['entity_id'] for e in context['reference_entries']}
    assert 'description' not in query('AMALGAMATOR',category='event',for_agent=True)['entries'][0]


def test_multipart_enemy_context_deduplicates_power_rules_without_omitting_parts():
    data=load();ids=[e['entity_id'] for e in data['entries'] if 'DECIMILLIPEDE_' in e['entity_id']]
    result=relevant(dict(game_version=data['game_version'],game_assembly_sha256=data['game_assembly_sha256'],state={'enemies':[dict(entity_id=i) for i in ids]}))
    assert len(result['entries'])==3 and not result['omitted_ids']
    assert result['power_references'] and all(e['power_ids'] for e in result['entries'])
    assert all('powers' not in e for e in result['entries'])
