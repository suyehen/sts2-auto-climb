"""Add verified artwork metadata alongside old map records, leaving evidence intact."""
from autoslay.archive.repository import Repository, read, atomic
from autoslay.archive.map_icons import saved_icon_metadata


def backfill():
    repo = Repository()
    count = 0
    for path in repo.root.glob('*/run_*/maps/act_*/*.json'):
        folder = path.parents[2]
        metadata = saved_icon_metadata(read(path)['map'], read(folder / 'manifest.json').get('started_at'))
        if metadata and metadata['nodes']:
            atomic(folder / 'map_artwork' / path.parent.name / path.name, metadata)
            count += len(metadata['nodes'])
    print(f'Recovered {count} verified node icons')


if __name__ == '__main__':
    backfill()
