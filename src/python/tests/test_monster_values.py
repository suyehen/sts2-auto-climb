from autoslay.knowledge.monster_values import parameters,resolve,numeric_moves,thresholds
from autoslay.knowledge.behavior import load,relevant


LEVELS=thresholds('enum AscensionLevel { None, ToughEnemies=8, DeadlyEnemies, DoubleBoss }')


def test_threshold_is_inclusive_and_static_constants_are_kept():
    values=parameters('private const int Repeat=3; private static int Damage => AscensionHelper.GetValueIfAscension(AscensionLevel.DeadlyEnemies, 4, 3);')
    assert resolve('Damage * Repeat',values,8,LEVELS)==9
    assert resolve('Damage * Repeat',values,9,LEVELS)==12
    assert resolve('Damage',values,10,LEVELS)==4
    assert resolve('AscensionHelper.GetValueIfAscension(AscensionLevel.ToughEnemies, 90, 81) + 2',values,8,LEVELS)==92


def test_dynamic_state_and_unsupported_code_never_become_zero():
    for expression in ['Creature.Hp','Random.Next(5)','unknown','__import__("os")','5 / 2']:
        assert resolve(expression,{},9,LEVELS) is None
    assert resolve('A',{'A':'B','B':'A'},9,LEVELS) is None


def test_attack_block_power_and_ascension_bands():
    values={'Damage':'AscensionHelper.GetValueIfAscension(AscensionLevel.DeadlyEnemies, 4, 3)','Repeat':'3','Block':'10','Strength':'2'}
    handler='await CreatureCmd.GainBlock(base.Creature, Block, ValueProp.Move); await PowerCmd.Apply<StrengthPower>(context, base.Creature, Strength, base.Creature, this);'
    bands=numeric_moves('new MultiAttackIntent(Damage, Repeat), new DefendIntent()',handler,values,LEVELS)
    assert [(b['ascension_min'],b['ascension_max']) for b in bands]==[(0,8),(9,10)]
    assert bands[0]['effects'][0]['total_damage']==9 and bands[1]['effects'][0]['total_damage']==12
    assert bands[0]['effects'][1]['amount']==10 and bands[0]['effects'][2]['amount']==2


def test_shipped_byrdonis_numbers_and_local_only_policy():
    data=load();bird=next(e for e in data['entries'] if e['entity_id']=='MONSTER.BYRDONIS')
    peck=next(m for m in bird['moves'] if m['state_id']=='PECK_MOVE')
    assert peck['numeric_variants'][0]['effects'][0]['damage']==3
    assert peck['numeric_variants'][1]['effects'][0]['damage']==4
    assert bird['health_variants'][0]['min_hp']==81 and bird['health_variants'][1]['min_hp']==90
    assert data['ascension_thresholds']['DeadlyEnemies']==9
    assert data['source_policy']=='local_game_only' and not any('wiki_supplement' in e for e in data['entries'])
    result=relevant(dict(game_version=data['game_version'],game_assembly_sha256=data['game_assembly_sha256'],state={'enemies':[{'entity_id':bird['entity_id']}]}))
    assert result['entries'][0]['moves'][0]['numeric_variants'] and not result['execution_authorized']
