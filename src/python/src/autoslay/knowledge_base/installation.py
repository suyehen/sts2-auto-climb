"""Read local release metadata even when an incompatible bridge cannot connect."""
import json
from pathlib import Path
from autoslay.paths import ROOT
from autoslay.knowledge_base.repository import read_json, dataset_path

def version_status(installation_path=None,catalog_path=None):
    installation=Path(installation_path or ROOT/'.runtime/local/installation.json')
    catalog=Path(catalog_path or dataset_path('catalog'))
    try:
        info=json.loads(installation.read_text(encoding='utf-8-sig'))
        release=json.loads((Path(info['game_path'])/'release_info.json').read_text(encoding='utf-8-sig'))
        actual=release['version'].removeprefix('v')
        current=read_json(catalog)['game_version']
        return dict(status='current' if actual==current else 'game_updated',installed_game_version=actual,
                    catalog_game_version=current,version_matches=actual==current,
                    action='none' if actual==current else 'update_bridge_catalog_and_native_mechanics')
    except (OSError,ValueError,KeyError,TypeError):
        return dict(status='unavailable',version_matches=None)
