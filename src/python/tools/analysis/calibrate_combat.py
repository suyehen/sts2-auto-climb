"""Build a new immutable combat artifact from independent full-action references."""
import argparse
import json
from pathlib import Path
from autoslay.calibration.combat import build
from autoslay.archive.repository import atomic


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('references',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Calibration output already exists')
    data=json.loads(args.references.read_text(encoding='utf-8-sig'))
    result=build(data['records'],data['identity'])
    atomic(args.output,result)
    print(json.dumps(dict(path=str(args.output),fingerprint=result['fingerprint'],
                          cohorts=len(result['cohorts']),authorized=sum(v['authorized'] for v in result['cohorts'].values()))))


if __name__=='__main__':main()
