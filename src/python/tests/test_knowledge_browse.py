from copy import deepcopy
import pytest
from autoslay.knowledge.browse import query_catalog

def catalog():
    return dict(game_version='test',game_assembly_sha256='same',entries=[
        dict(entity_id='CARD.A',category='card',name='测试牌',pool='CARD_POOL.IRONCLAD_CARD_POOL',rarity='Common',variants=[dict(upgrade_level=0,type='Attack',description='基础伤害'),dict(upgrade_level=1,type='Attack',description='升级伤害')]),
        dict(entity_id='CARD.B',category='card',name='无色牌',pool='CARD_POOL.COLORLESS_CARD_POOL',rarity='Rare',variants=[dict(type='Skill',description='抽牌')]),
        dict(entity_id='POTION.A',category='potion',name='职业药水',pool='POTION_POOL.SILENT_POTION_POOL',rarity='Rare'),
        dict(entity_id='RELIC.A',category='relic',name='职业遗物',rarity='Starter'),
        dict(entity_id='RELIC.B',category='relic',name='未记录遗物',rarity='Common')])

def test_character_common_category_and_upgrade_preserved():
    data=catalog();before=deepcopy(data)
    result=query_catalog(data,category='card',owner='ironclad')
    assert result['total']==1 and len(result['entries'][0]['variants'])==2
    assert query_catalog(data,category='card',owner='ironclad',include_shared=True)['total']==2
    assert query_catalog(data,category='potion',owner='silent')['total']==1
    assert query_catalog(data,card_type='Skill',rarity='Rare')['total']==1
    assert data==before

def test_relic_sidecar_is_build_gated_and_missing_never_means_shared():
    sidecar=dict(game_assembly_sha256='same',entries={'RELIC.A':['RELIC_POOL.REGENT_RELIC_POOL']})
    assert query_catalog(catalog(),category='relic',owner='regent',sidecar=sidecar)['total']==1
    sidecar['game_assembly_sha256']='old'
    assert query_catalog(catalog(),category='relic',owner='regent',sidecar=sidecar)['total']==0
    assert query_catalog(catalog(),category='relic',owner='shared',sidecar={})['total']==0

def test_pagination_search_and_invalid_queries():
    assert query_catalog(catalog(),limit=2,page=2)['entries'][0]['entity_id']=='POTION.A'
    assert query_catalog(catalog(),q='升级伤害')['total']==1
    for kwargs in [dict(category='../'),dict(owner='guessed'),dict(page=0),dict(limit=1000)]:
        with pytest.raises(ValueError):query_catalog(catalog(),**kwargs)

def test_multiple_relic_pools_preserve_every_membership():
    sidecar=dict(game_assembly_sha256='same',entries={'RELIC.A':['RELIC_POOL.EVENT_RELIC_POOL','RELIC_POOL.SHARED_RELIC_POOL']})
    for owner in ['event','shared']:
        result=query_catalog(catalog(),category='relic',owner=owner,sidecar=sidecar)
        assert result['total']==1
        assert result['entries'][0]['classification']['scopes']==['event','shared']
