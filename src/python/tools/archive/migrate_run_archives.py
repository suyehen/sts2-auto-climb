import argparse,json
from autoslay.archive.repository import for_legacy
from autoslay.archive.migration import migrate
from autoslay.paths import ROOT
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    print(json.dumps(migrate(for_legacy(),ROOT/'data/decisions',ROOT/'data/calibration',args.apply),ensure_ascii=False,indent=2))
