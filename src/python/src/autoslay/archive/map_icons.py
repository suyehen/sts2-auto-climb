"""Recover missing map artwork from the exact saved run, without changing evidence."""
import os
import re
from datetime import datetime
from pathlib import Path

from autoslay.paths import ROOT


def coord(value):
    return value.get('column', value.get('col')), value.get('row')


def topology_matches(recorded, saved):
    points = list(saved.get('points', []))
    points += [saved[k] for k in ('start', 'boss', 'second_boss') if saved.get(k)]
    actual = {coord(n['coordinate']): n for n in recorded.get('nodes', [])}
    expected = {coord(n['coord']): n for n in points}
    if not actual or actual.keys() != expected.keys():
        return False
    ids = {n['node_id']: c for c, n in actual.items()}
    for c, n in actual.items():
        p = expected[c]
        if n['type'].replace('_', '').lower() != p['type'].replace('_', '').lower():
            return False
        if {ids.get(i) for i in n.get('children', [])} != {coord(v) for v in p.get('children', [])}:
            return False
    return True


def saved_icon_metadata(recorded, started_at, save_paths=None):
    from autoslay.archive.repository import read
    if not started_at:
        return None
    if save_paths is None:
        base = Path(os.environ.get('APPDATA', '')) / 'SlayTheSpire2/steam'
        save_paths = list(base.glob('*/profile*/saves/current_run.save'))
        save_paths += list(base.glob('*/modded/profile*/saves/current_run.save'))
    stamp = datetime.fromisoformat(started_at).timestamp()
    index = recorded.get('act_index')
    for path in save_paths:
        try:
            save = read(path)
            if save.get('start_time') != stamp or not isinstance(index, int):
                continue
            act = save['acts'][index]
            saved = act.get('saved_map') or {}
            if not topology_matches(recorded, saved):
                continue
            rooms = act['rooms']
            nodes = {}
            for n in recorded['nodes']:
                if n['type'] == 'Ancient':
                    identity = rooms.get('ancient_id')
                    asset = 'MAP.ANCIENT_NODE_' + str(identity).split('.')[-1]
                elif n['type'] == 'Boss':
                    second = saved.get('second_boss')
                    is_second = second and coord(second['coord']) == coord(n['coordinate'])
                    identity = rooms.get('second_boss_id' if is_second else 'boss_id')
                    asset = 'MAP.' + str(identity).split('.')[-1] + '_ICON'
                else:
                    continue
                if identity and re.fullmatch(r'MAP\.[A-Z0-9_]+', asset) and (ROOT / 'data/knowledge/assets/map' / (asset + '.webp')).is_file():
                    nodes[n['node_id']] = dict(icon_asset=asset, entity_id=identity)
            return dict(source='matching_game_save', start_time_unix=stamp,
                        map_digest=map_digest(recorded), nodes=nodes)
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            continue
    return None


def map_digest(recorded):
    from autoslay.archive.repository import digest
    # Artwork enrichment never changes map identity; order is irrelevant.
    return digest(sorted((n['node_id'], n['type'], sorted(n.get('children', [])))
                         for n in recorded.get('nodes', [])))


def apply_metadata(recorded, metadata):
    if not metadata or metadata.get('map_digest') != map_digest(recorded):
        return recorded
    nodes = metadata.get('nodes', {})
    return recorded | dict(nodes=[n | {k: v for k, v in nodes.get(n['node_id'], {}).items()
                                     if k in {'icon_asset', 'entity_id'} and not n.get(k)}
                                  for n in recorded.get('nodes', [])])
