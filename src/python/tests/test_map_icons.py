import json
from autoslay.archive.map_icons import saved_icon_metadata, apply_metadata


def fixture(tmp_path):
    recorded = dict(act_index=0, nodes=[
        dict(node_id='start', type='Ancient', coordinate=dict(column=3, row=0), children=['boss']),
        dict(node_id='boss', type='Boss', coordinate=dict(column=3, row=16), children=[])])
    saved = dict(start_time=1791714309, acts=[dict(
        rooms=dict(ancient_id='EVENT.NEOW', boss_id='ENCOUNTER.THE_KIN_BOSS'),
        saved_map=dict(points=[], start=dict(coord=dict(col=3, row=0), type='ancient', children=[dict(col=3, row=16)]),
                       boss=dict(coord=dict(col=3, row=16), type='boss')))])
    path = tmp_path / 'current_run.save'
    path.write_text(json.dumps(saved))
    return recorded, saved, path


def test_verified_save_recovers_native_ancient_and_boss_without_rewriting_map(tmp_path):
    recorded, _, path = fixture(tmp_path)
    metadata = saved_icon_metadata(recorded, '2026-10-11T10:25:09+00:00', [path])
    enriched = apply_metadata(recorded, metadata)
    assert [n['icon_asset'] for n in enriched['nodes']] == ['MAP.ANCIENT_NODE_NEOW', 'MAP.THE_KIN_BOSS_ICON']
    assert enriched['nodes'][1]['entity_id'] == 'ENCOUNTER.THE_KIN_BOSS'
    assert 'icon_asset' not in recorded['nodes'][0]


def test_different_run_or_different_map_cannot_supply_artwork(tmp_path):
    recorded, saved, path = fixture(tmp_path)
    assert saved_icon_metadata(recorded, '2026-10-11T10:25:10+00:00', [path]) is None
    saved['acts'][0]['saved_map']['start']['children'] = []
    path.write_text(json.dumps(saved))
    assert saved_icon_metadata(recorded, '2026-10-11T10:25:09+00:00', [path]) is None


def test_sidecar_cannot_cross_maps_or_replace_recorded_artwork(tmp_path):
    recorded, _, path = fixture(tmp_path)
    metadata = saved_icon_metadata(recorded, '2026-10-11T10:25:09+00:00', [path])
    recorded['nodes'][0]['icon_asset'] = 'MAP.ANCIENT_NODE_PAEL'
    assert apply_metadata(recorded, metadata)['nodes'][0]['icon_asset'] == 'MAP.ANCIENT_NODE_PAEL'
    recorded['nodes'][1]['children'] = ['start']
    assert apply_metadata(recorded, metadata) is recorded
