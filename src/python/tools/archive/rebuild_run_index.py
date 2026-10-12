"""Recover the derived index while preserving original archive evidence."""
import argparse,json,sqlite3
from autoslay.archive.repository import for_legacy,now
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--quarantine-corrupt-index',action='store_true');args=parser.parse_args();repo=for_legacy()
    try:result=repo.rebuild()
    except sqlite3.DatabaseError:
        if not args.quarantine_corrupt_index:raise
        stamp=now().replace(':','-');folder=repo.index.parent/('recovery-'+stamp);folder.mkdir()
        for suffix in ('','-wal','-shm'):
            path=repo.index.with_name(repo.index.name+suffix)
            if path.exists():path.replace(folder/path.name)
        result=repo.rebuild();result['quarantined_index']=str(folder)
    print(json.dumps(result,ensure_ascii=False,indent=2))
