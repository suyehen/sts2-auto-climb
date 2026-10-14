"""Replay scope/search ablations against an explicit local Git baseline; never play."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import types
from autoslay.archive.repository import atomic
from autoslay.decision import normalize as adapter, local_combat, deterministic, coverage
from autoslay.planning import search as planner
from autoslay.rules import monsters


def baseline_module(ref, name, relative):
    raw=subprocess.check_output(['git','show',f'{ref}:{relative}'])
    module=types.ModuleType('combat_baseline_'+name)
    sys.modules[module.__name__]=module
    exec(compile(raw.decode('utf-8'),relative,'exec'),module.__dict__)
    return module,hashlib.sha256(raw).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_id');parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--baseline-ref',required=True);parser.add_argument('--output-prefix',type=Path,required=True)
    args=parser.parse_args()
    ref=subprocess.check_output(['git','rev-parse',args.baseline_ref]).decode().strip()
    legacy_search,search_hash=baseline_module(ref,'search','src/python/src/autoslay/planning/search.py')
    legacy_normalize,normalize_hash=baseline_module(ref,'normalize','src/python/src/autoslay/decision/normalize.py')
    legacy_monsters,monster_hash=baseline_module(ref,'monsters','src/python/src/autoslay/rules/monsters.py')
    spec=importlib.util.spec_from_file_location('combat_audit',Path(__file__).with_name('audit_local_decisions.py'))
    audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
    original_search=planner.search;original_normalize=adapter.normalize;original_hooks=monsters.unmodeled_special_hooks
    argv=sys.argv
    try:
        for variant in ('baseline','scope','pool','combined'):
            planner.search=legacy_search.search if variant in ('baseline','scope') else original_search
            adapter.normalize=legacy_normalize.normalize if variant in ('baseline','pool') else original_normalize
            monsters.unmodeled_special_hooks=legacy_monsters.unmodeled_special_hooks if variant in ('baseline','pool') else original_hooks
            audit.normalize=coverage.normalize=local_combat.normalize=deterministic.normalize=adapter.normalize
            target=Path(str(args.output_prefix)+'-'+variant+'.json')
            sys.argv=['audit',args.run_id,'--manifest',str(args.manifest),'--output',str(target)]
            audit.main()
            result=json.loads(target.read_text(encoding='utf-8'))
            result['ablation']=dict(variant=variant,baseline_commit=ref,
                                    legacy_source_hashes=dict(search=search_hash,normalize=normalize_hash,monsters=monster_hash),
                                    switched_modules=['search'] if variant=='scope' else ['normalize','monster_hooks'] if variant=='pool' else
                                                     ['search','normalize','monster_hooks'] if variant=='baseline' else [],
                                    note='code_fingerprint identifies current disk files; switched modules use the recorded legacy source hashes')
            atomic(target,result)
    finally:
        planner.search=original_search;adapter.normalize=original_normalize;monsters.unmodeled_special_hooks=original_hooks
        coverage.normalize=local_combat.normalize=deterministic.normalize=original_normalize
        sys.argv=argv


if __name__=='__main__':main()
