"""Validate semantic definitions and their catalog bindings without contacting a game."""
import argparse
import json
from pathlib import Path
from autoslay.knowledge_base.repository import knowledge_scope,dataset_path,read_json
from autoslay.knowledge_base.templates import index
from autoslay.rules.model_store import load_models
from autoslay.rules.registry import KnowledgeRules
from autoslay.paths import ROOT


def validate_bindings(models,references):
    errors=[]
    for identity,rule in models.definitions.items():
        entry=references.get(identity)
        if entry is None:
            errors.append(dict(entity_id=identity,field='catalog',message='reference missing'));continue
        variants=entry.get('variants',[entry])
        def expressions(node):
            if isinstance(node,dict):
                if 'variable' in node:yield node['variable']
                for v in node.values():yield from expressions(v)
            elif isinstance(node,(list,tuple)):
                for v in node:yield from expressions(v)
        names=set(expressions(models.records[identity].get('triggers',[])))
        names.update(expressions(models.records[identity].get('run_hooks',[])))
        for variant in variants:
            for name in sorted(names):
                try:KnowledgeRules.variable(variant.get('dynamic_variables'),name)
                except ValueError as exc:errors.append(dict(entity_id=identity,field=name,message=str(exc)))
    return errors


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with knowledge_scope():
        from jsonschema import Draft202012Validator
        schema=json.loads((ROOT/'contracts/game-models.schema.json').read_text('utf-8'))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(read_json(dataset_path('models')))
        models=load_models();entries,_=index()
        from autoslay.decision.contracts import UnsupportedEffect
        try:errors=validate_bindings(models,entries)
        except UnsupportedEffect as exc:errors=[dict(message=str(exc))]
        report=dict(rule_fingerprint=models.fingerprint,declared_models=len(models.definitions),
            declared_flows=len(models.flows),declared_monsters=len(models.monsters),
            declared_selections=len(models.selections),
            schema_validated=True,errors=errors,passed=not errors,semantic_completeness_certified=False)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report,ensure_ascii=True))
        raise SystemExit(bool(errors))
