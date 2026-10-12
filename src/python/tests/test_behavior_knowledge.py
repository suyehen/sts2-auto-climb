from copy import deepcopy
import pytest
from autoslay.knowledge.behavior import save,load,query,relevant


def catalog():
    return dict(schema_version='behavior/1.0',game_version='test',game_assembly_sha256='current',entries=[
        dict(entity_id='MONSTER.A',category='monster',name='测试怪物',calculation_status='source_extracted_unreviewed',
            provenance={'kind':'game'},method_evidence={'Attack':'damage'},localization={'name':'测试怪物'}),
        dict(entity_id='EVENT.B',category='event',name='测试事件',calculation_status='reference_only',provenance={'kind':'game'})])


def test_scoped_version_bound_evidence_and_budget(tmp_path):
    path=tmp_path/'catalog.json';save(catalog(),path)
    snapshot=dict(game_version='test',game_assembly_sha256='current',state={'enemies':[{'entity_id':'MONSTER.A'}]})
    result=relevant(snapshot,path=path)
    assert len(result['entries'])==1 and result['version_matches'] and not result['rules_enabled']
    assert 'method_evidence' not in result['entries'][0]
    assert 'method_evidence' in query(category='monster',path=path,include_evidence=True)['entries'][0]
    snapshot['game_assembly_sha256']='changed'
    assert relevant(snapshot,path=path)['entries'][0]['calculation_status']=='reference_only'
    assert relevant(snapshot,max_bytes=1,path=path)['omitted_ids']==['MONSTER.A']


def test_invalid_candidate_preserves_catalog_and_history(tmp_path):
    path=tmp_path/'catalog.json';save(catalog(),path);before=path.read_bytes()
    bad=catalog();bad['entries'].append(deepcopy(bad['entries'][0]))
    with pytest.raises(ValueError):save(bad,path)
    assert path.read_bytes()==before
    changed=catalog();changed['entries'][0]['name']='新名字';save(changed,path)
    assert len(list((tmp_path/'history').glob('*.json')))==1


def test_query_validation_and_nonmutating_projection(tmp_path):
    path=tmp_path/'catalog.json';save(catalog(),path);before=path.read_bytes()
    assert query('测试',limit=1,page=2,path=path)['entries'][0]['entity_id']=='EVENT.B'
    assert not query(game_version='old',path=path)['version_matches']
    for args in [dict(category='card'),dict(limit=51),dict(page=0)]:
        with pytest.raises(ValueError):query(path=path,**args)
    assert path.read_bytes()==before
