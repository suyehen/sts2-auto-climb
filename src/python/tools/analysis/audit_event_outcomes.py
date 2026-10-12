"""Summarize event action observations without changing decision weights."""
import argparse
import json
from pathlib import Path
from autoslay.paths import ROOT
from autoslay.calibration.events import summarize

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=ROOT/'data/calibration')
    parser.add_argument('--output',type=Path,default=ROOT/'data/knowledge/coverage/event-outcomes.json')
    args=parser.parse_args();rows=[];groups={}
    for path in (args.directory/'observations').glob('*.json'):
        record=json.loads(path.read_text(encoding='utf-8-sig'))
        if record.get('event_comparison'):
            rows.append(record['event_comparison'])
            source=record.get('source') or {}
            key=json.dumps(dict(game_assembly_sha256=source.get('game_assembly_sha256'),event=source.get('entity_id'),
                                model_versions=record.get('model_versions')),sort_keys=True)
            groups.setdefault(key,[]).append(record['event_comparison'])
    result=summarize(rows);result['groups']=[dict(identity=json.loads(key),**summarize(samples)) for key,samples in groups.items()]
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__': main()
