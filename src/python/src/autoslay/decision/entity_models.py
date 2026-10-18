"""Materialize the full catalog model and all currently relevant entity instances."""
from collections import Counter
from autoslay.knowledge_base.repository import read_json,read_view,dataset_path,document_sha256
from autoslay.knowledge_base.rulebook import Rulebook
from autoslay.knowledge_base.effect_profiles import compile_catalog,profile_for,interaction_edges,VERSION
from autoslay.evaluation.entity_effects import evaluate_entity
from autoslay.decision.cache import digest


def registry_summary(book=None):
    book=book or Rulebook()
    try:inventory_path=dataset_path('source_inventory')
    except KeyError:inventory_path=None
    inventory=read_json(inventory_path,default={}) if inventory_path else {}
    key='effect-registry-summary/'+VERSION+'/'+book.fingerprint+'/'+str(document_sha256(inventory_path) if inventory_path else None)
    def build(catalog):
        source_ok=(inventory.get('game_version')==catalog.get('game_version')
                   and bool(catalog.get('game_assembly_sha256'))
                   and inventory.get('game_assembly_sha256')==catalog.get('game_assembly_sha256'))
        registry=compile_catalog(catalog['entries'],inventory if source_ok else {},
                                 {i:book.status(i) for i in book.references})
        return {k:v for k,v in registry.items() if k!='profiles'}|dict(
            source_metadata_matches=source_ok,game_version=catalog.get('game_version'),
            game_assembly_sha256=catalog.get('game_assembly_sha256'))
    return read_view(dataset_path('catalog'),key,build)


def analyze_entities(snapshot,context,entries,policy):
    state=snapshot['state'];observed={};catalog_only=set();origins={}
    def walk(item,path):
        if isinstance(item,dict):
            identity=item.get('entity_id')
            if identity in entries:
                instance=item.get('instance_id')
                # Distinct live instances/modifiers must never collapse to a catalog ID.
                key=(identity,instance or digest(item))
                previous=origins.setdefault(key,set())
                if key not in observed or '.hand[' in path or not any('.hand[' in p for p in previous):
                    observed[key]=item
                previous.add(path)
            relic=item.get('relic_id')
            if relic:catalog_only.add(relic)
            for key,value in item.items():
                if key not in {'map','legal_actions','action_catalog','description','hover_tips'}:
                    walk(value,path+'.'+key)
        elif isinstance(item,list):
            for index,value in enumerate(item):walk(value,path+'['+str(index)+']')
    walk(state,'state')
    for identity in catalog_only:
        if identity not in {key[0] for key in observed}:
            observed[(identity,None)]={'entity_id':identity}
            origins[(identity,None)]={'state.event_screen.options.relic_id'}
    rows=[];profiles={}
    summary=registry_summary()
    try:inventory=read_json(dataset_path('source_inventory'),default={})
    except KeyError:inventory={}
    sources={e['entity_id']:e for e in inventory.get('entries',[])} if summary.get('source_metadata_matches') else {}
    for key,value in observed.items():
        profile=profile_for(value,entries)
        if profile:profiles.setdefault(profile['entity_id'],profile)
        evaluated=evaluate_entity(value,context,entries,policy)
        rows.append(dict(instance_id=value.get('instance_id'),upgrade_level=value.get('upgrade_level',0),
                         origins=sorted(origins[key]),source_metadata=sources.get(value.get('entity_id')),**evaluated))
    return dict(module_version=VERSION,registry=summary,entities=rows,
        interaction_candidates=interaction_edges(list(profiles.values())),
        evaluation_counts=dict(Counter(r['coverage'] for r in rows)),
        execution_authorized=False,limitations=[
            'complete catalog registration does not certify exact simulation or calibrated utility',
            'unparsed and conditional effects remain in clause programs; no zero substitution',
            'interaction edges require target, timing and resource feasibility checks; no additive synergy score'])
