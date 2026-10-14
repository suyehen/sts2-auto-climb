"""Compare frozen development references without modifying active policy."""
import argparse
import json
from pathlib import Path
from autoslay.archive.repository import atomic
from autoslay.calibration.combat_profiles import compare


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('references',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Profile comparison output already exists')
    data=json.loads(args.references.read_text(encoding='utf-8-sig'))
    result=compare(data['profiles'])
    atomic(args.output,result)
    print(json.dumps(dict(path=str(args.output),suggested_profile=result['suggested_profile'])))


if __name__=='__main__':main()
